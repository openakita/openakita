"""PluginManager — discover, load, manage plugin lifecycle."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .api import PluginAPI, PluginBase
from .compat import check_compatibility
from .hooks import HookRegistry
from .manifest import ManifestError, PluginManifest, parse_manifest
from .sandbox import PluginErrorTracker
from .state import PluginState

logger = logging.getLogger(__name__)

UNLOAD_TIMEOUT = 5.0


class PluginManager:
    """Discover, load, and manage plugin lifecycle.

    Key guarantees:
    - Each plugin is loaded independently; one failure never blocks others.
    - All load/unload operations have timeouts.
    - Error accumulation triggers auto-disable.
    - The host system boots normally even if every plugin fails.
    """

    def __init__(
        self,
        plugins_dir: Path,
        state_path: Path | None = None,
        host_refs: dict[str, Any] | None = None,
    ) -> None:
        self._plugins_dir = plugins_dir
        self._state_path = state_path or (plugins_dir.parent / "plugin_state.json")
        self._host_refs = host_refs or {}

        self._state = PluginState.load(self._state_path)
        self._error_tracker = PluginErrorTracker()
        self._error_tracker.set_auto_disable_callback(self._on_plugin_auto_disabled)
        self._hook_registry = HookRegistry(error_tracker=self._error_tracker)

        self._loaded: dict[str, _LoadedPlugin] = {}
        self._failed: dict[str, str] = {}

    # --- Properties ---

    @property
    def hook_registry(self) -> HookRegistry:
        return self._hook_registry

    @property
    def loaded_count(self) -> int:
        return len(self._loaded)

    @property
    def failed_count(self) -> int:
        return len(self._failed)

    @property
    def state(self) -> PluginState:
        return self._state

    # --- Version checking ---

    @staticmethod
    def _check_openakita_version(manifest: PluginManifest) -> bool:
        """Check plugin compatibility (system version, API version, Python, SDK)."""
        result = check_compatibility(manifest)
        for w in result.warnings:
            logger.warning(w)
        for e in result.errors:
            logger.error(e)
        if not result.ok:
            logger.warning("Plugin '%s' skipped due to compatibility errors", manifest.id)
        return result.ok

    # --- Discovery ---

    def _discover_plugins(self) -> list[Path]:
        """Find all plugin directories containing plugin.json."""
        if not self._plugins_dir.exists():
            return []
        dirs = []
        for child in sorted(self._plugins_dir.iterdir()):
            if child.is_dir() and (child / "plugin.json").exists():
                dirs.append(child)
        return dirs

    # --- Loading ---

    async def load_all(self) -> None:
        """Load all discovered and enabled plugins.

        Each plugin is loaded in its own try/except with a timeout.
        Failures are logged and tracked, never propagated.
        """
        plugin_dirs = self._discover_plugins()
        if not plugin_dirs:
            logger.debug("No plugins found in %s", self._plugins_dir)
            return

        for plugin_dir in plugin_dirs:
            try:
                manifest = parse_manifest(plugin_dir)
            except ManifestError as e:
                logger.error("Skipping %s: %s", plugin_dir.name, e)
                self._failed[plugin_dir.name] = str(e)
                continue

            if not self._check_openakita_version(manifest):
                continue

            if not self._state.is_enabled(manifest.id):
                reason = ""
                entry = self._state.get_entry(manifest.id)
                if entry:
                    reason = entry.disabled_reason
                logger.info(
                    "Plugin '%s' is disabled (%s), skipping",
                    manifest.id, reason or "user",
                )
                continue

            if manifest.conflicts:
                conflict = next(
                    (c for c in manifest.conflicts if c in self._loaded), None
                )
                if conflict:
                    logger.warning(
                        "Plugin '%s' conflicts with loaded '%s', skipping",
                        manifest.id, conflict,
                    )
                    self._failed[manifest.id] = f"conflicts with {conflict}"
                    continue

            try:
                await asyncio.wait_for(
                    self._load_single(manifest, plugin_dir),
                    timeout=manifest.load_timeout,
                )
                logger.info("Plugin '%s' v%s loaded", manifest.id, manifest.version)
            except TimeoutError:
                msg = f"load timeout ({manifest.load_timeout}s)"
                logger.error("Plugin '%s' %s, skipped", manifest.id, msg)
                self._failed[manifest.id] = msg
                self._state.record_error(manifest.id, msg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                logger.error(
                    "Plugin '%s' failed to load: %s", manifest.id, msg, exc_info=True
                )
                self._failed[manifest.id] = msg
                self._state.record_error(manifest.id, msg)

        self._refresh_skill_catalog()
        self._save_state()

    async def _load_single(
        self, manifest: PluginManifest, plugin_dir: Path
    ) -> None:
        state_entry = self._state.ensure_entry(manifest.id)
        granted = self._resolve_permissions(manifest, state_entry.granted_permissions)
        state_entry.granted_permissions = granted

        data_dir = plugin_dir
        api = PluginAPI(
            plugin_id=manifest.id,
            manifest=manifest,
            granted_permissions=granted,
            data_dir=data_dir,
            host_refs=self._host_refs,
            hook_registry=self._hook_registry,
        )

        plugin_instance: PluginBase | None = None

        try:
            if manifest.plugin_type == "python":
                plugin_instance = self._load_python_plugin(manifest, plugin_dir)
                plugin_instance.on_load(api)
                self._try_load_plugin_skill(manifest, plugin_dir, api)
            elif manifest.plugin_type == "mcp":
                self._load_mcp_plugin(manifest, plugin_dir, api)
            elif manifest.plugin_type == "skill":
                self._load_skill_plugin(manifest, plugin_dir, api)
        except Exception:
            api._cleanup()
            raise

        self._loaded[manifest.id] = _LoadedPlugin(
            manifest=manifest,
            api=api,
            instance=plugin_instance,
            plugin_dir=plugin_dir,
        )

    def _load_python_plugin(
        self, manifest: PluginManifest, plugin_dir: Path
    ) -> PluginBase:
        entry_path = plugin_dir / manifest.entry
        if not entry_path.exists():
            raise FileNotFoundError(
                f"Plugin entry '{manifest.entry}' not found in {plugin_dir}"
            )

        module_name = f"openakita_plugin_{manifest.id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {entry_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        plugin_dir_str = str(plugin_dir)
        added_to_path = False
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            added_to_path = True

        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            if added_to_path:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass
            raise

        plugin_class = getattr(module, "Plugin", None)
        if plugin_class is None:
            sys.modules.pop(module_name, None)
            if added_to_path:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass
            raise AttributeError(
                f"Plugin module {entry_path} must export a 'Plugin' class"
            )

        if not (isinstance(plugin_class, type) and issubclass(plugin_class, PluginBase)):
            sys.modules.pop(module_name, None)
            if added_to_path:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass
            raise TypeError(
                f"Plugin.Plugin must be a subclass of PluginBase, got {type(plugin_class)}"
            )

        return plugin_class()

    def _load_mcp_plugin(
        self, manifest: PluginManifest, plugin_dir: Path, api: PluginAPI
    ) -> None:
        config_path = plugin_dir / manifest.entry
        if not config_path.exists():
            raise FileNotFoundError(
                f"MCP config '{manifest.entry}' not found in {plugin_dir}"
            )

        mcp_config = json.loads(config_path.read_text(encoding="utf-8"))
        mcp_client = self._host_refs.get("mcp_client")
        if mcp_client is None or not hasattr(mcp_client, "add_server"):
            api.log("No MCP client available for MCP plugin", "warning")
            return

        try:
            from ..tools.mcp import MCPServerConfig

            server_cfg = MCPServerConfig(
                name=manifest.id,
                command=mcp_config.get("command", ""),
                args=mcp_config.get("args", []),
                env=mcp_config.get("env", {}),
                description=mcp_config.get("description", manifest.description),
                transport=mcp_config.get("transport", "stdio"),
                url=mcp_config.get("url", ""),
                headers=mcp_config.get("headers", {}),
                cwd=mcp_config.get("cwd", str(plugin_dir)),
            )
            mcp_client.add_server(server_cfg)
            api.log(f"MCP server '{manifest.id}' registered")
        except Exception as e:
            api.log_error(f"Failed to register MCP server: {e}", e)

    def _load_skill_plugin(
        self, manifest: PluginManifest, plugin_dir: Path, api: PluginAPI
    ) -> None:
        skill_path = plugin_dir / manifest.entry
        if not skill_path.exists():
            raise FileNotFoundError(
                f"Skill entry '{manifest.entry}' not found in {plugin_dir}"
            )
        skill_loader = self._host_refs.get("skill_loader")
        if skill_loader is None:
            api.log("No skill_loader available", "warning")
            return

        if hasattr(skill_loader, "load_skill"):
            skill_loader.load_skill(skill_path.parent)
            api.log(f"Skill loaded from {skill_path.parent}")
        elif hasattr(skill_loader, "load_from_directory"):
            skill_loader.load_from_directory(skill_path.parent)
            api.log(f"Skill directory loaded from {skill_path.parent}")
        else:
            api.log(
                f"skill_loader ({type(skill_loader).__name__}) has no load_skill method",
                "warning",
            )

    def _try_load_plugin_skill(
        self, manifest: PluginManifest, plugin_dir: Path, api: PluginAPI
    ) -> None:
        """Load a skill file bundled with a Python plugin (via provides.skill)."""
        skill_file = manifest.provides.get("skill", "")
        if not skill_file:
            return

        skill_path = plugin_dir / skill_file
        if not skill_path.exists():
            api.log(f"Declared skill '{skill_file}' not found in {plugin_dir}", "warning")
            return

        skill_loader = self._host_refs.get("skill_loader")
        if skill_loader is None:
            api.log("No skill_loader available for plugin skill", "warning")
            return

        try:
            if hasattr(skill_loader, "load_skill"):
                skill_loader.load_skill(skill_path.parent)
            elif hasattr(skill_loader, "load_from_directory"):
                skill_loader.load_from_directory(skill_path.parent)
            else:
                api.log("skill_loader has no load_skill method", "warning")
                return
            api.log(f"Plugin skill loaded from {skill_path.parent}")
            self._skills_loaded = True
        except Exception as e:
            api.log(f"Failed to load plugin skill: {e}", "warning")

    def _refresh_skill_catalog(self) -> None:
        """Invalidate skill catalog cache if any plugin loaded a skill."""
        if not getattr(self, "_skills_loaded", False):
            return
        skill_catalog = self._host_refs.get("skill_catalog")
        if skill_catalog is not None and hasattr(skill_catalog, "invalidate_cache"):
            try:
                skill_catalog.invalidate_cache()
                logger.debug("Skill catalog cache invalidated after plugin skill load")
            except Exception as e:
                logger.warning("Failed to refresh skill catalog: %s", e)

    # --- Permissions ---

    def _resolve_permissions(
        self, manifest: PluginManifest, previously_granted: list[str]
    ) -> list[str]:
        """Resolve which permissions are granted.

        Basic permissions are always granted. Advanced/system require prior approval
        (stored in state). If new advanced/system perms are requested but not yet
        approved, they are NOT granted — the frontend must prompt the user.
        """
        from .manifest import BASIC_PERMISSIONS

        granted = list(BASIC_PERMISSIONS)
        for perm in manifest.permissions:
            if perm in BASIC_PERMISSIONS:
                continue
            if perm in previously_granted:
                granted.append(perm)
            else:
                logger.info(
                    "Plugin '%s' requests '%s' (not yet approved)",
                    manifest.id, perm,
                )
        return granted

    def approve_permissions(
        self, plugin_id: str, permissions: list[str]
    ) -> None:
        """Grant additional permissions (called from UI approval flow)."""
        entry = self._state.ensure_entry(plugin_id)
        for perm in permissions:
            if perm not in entry.granted_permissions:
                entry.granted_permissions.append(perm)

        loaded = self._loaded.get(plugin_id)
        if loaded:
            loaded.api._granted_permissions.update(permissions)

        self._save_state()

    def revoke_permissions(
        self, plugin_id: str, permissions: list[str]
    ) -> None:
        """Revoke previously granted permissions."""
        entry = self._state.get_entry(plugin_id)
        if entry is not None:
            entry.granted_permissions = [
                p for p in entry.granted_permissions if p not in permissions
            ]

        loaded = self._loaded.get(plugin_id)
        if loaded:
            loaded.api._granted_permissions -= set(permissions)

        self._save_state()

    # --- Unloading ---

    async def unload_plugin(self, plugin_id: str) -> bool:
        loaded = self._loaded.pop(plugin_id, None)
        if loaded is None:
            return False

        try:
            if loaded.instance:
                await asyncio.wait_for(
                    asyncio.to_thread(loaded.instance.on_unload),
                    timeout=UNLOAD_TIMEOUT,
                )
        except (TimeoutError, Exception) as e:
            logger.warning(
                "Plugin '%s' on_unload error: %s", plugin_id, e
            )

        loaded.api._cleanup()
        logger.info("Plugin '%s' unloaded", plugin_id)
        return True

    async def disable_plugin(
        self, plugin_id: str, reason: str = "user"
    ) -> None:
        self._state.disable(plugin_id, reason)
        await self.unload_plugin(plugin_id)
        self._save_state()

    async def enable_plugin(self, plugin_id: str) -> None:
        self._state.enable(plugin_id)
        self._error_tracker.reset(plugin_id)
        self._save_state()
        if plugin_id not in self._loaded:
            try:
                await self.reload_plugin(plugin_id)
            except Exception as e:
                logger.warning("Failed to auto-reload plugin '%s' on enable: %s", plugin_id, e)

    def _on_plugin_auto_disabled(self, plugin_id: str) -> None:
        """Callback when PluginErrorTracker auto-disables a plugin.

        Cleans up registered tools from the agent's tool list/catalog.
        """
        loaded = self._loaded.get(plugin_id)
        if loaded and hasattr(loaded.api, "_cleanup_tools"):
            try:
                loaded.api._cleanup_tools()
                logger.info(
                    "Auto-disable: cleaned up tools for plugin '%s'", plugin_id
                )
            except Exception as e:
                logger.warning(
                    "Auto-disable: tool cleanup failed for plugin '%s': %s",
                    plugin_id, e,
                )

    # --- State ---

    def _save_state(self) -> None:
        try:
            self._state.save(self._state_path)
        except Exception as e:
            logger.error("Failed to save plugin state: %s", e)

    # --- Query ---

    def get_loaded(self, plugin_id: str) -> _LoadedPlugin | None:
        return self._loaded.get(plugin_id)

    def list_loaded(self) -> list[dict]:
        result = []
        for lp in self._loaded.values():
            pending = list(lp.api._pending_permissions) if lp.api._pending_permissions else []
            granted = list(lp.api._granted_permissions)
            result.append({
                "id": lp.manifest.id,
                "name": lp.manifest.name,
                "version": lp.manifest.version,
                "type": lp.manifest.plugin_type,
                "category": lp.manifest.category,
                "permissions": lp.manifest.permissions,
                "permission_level": lp.manifest.max_permission_level,
                "granted_permissions": granted,
                "pending_permissions": pending,
            })
        return result

    async def reload_plugin(self, plugin_id: str) -> None:
        """Unload then re-load a plugin (e.g. after granting new permissions)."""
        loaded = self._loaded.get(plugin_id)
        if loaded is not None:
            plugin_dir = loaded.plugin_dir
            manifest = loaded.manifest
            await self.unload_plugin(plugin_id)
        else:
            plugin_dir = self._plugins_dir / plugin_id
            if not (plugin_dir / "plugin.json").exists():
                logger.warning("Cannot reload '%s': plugin dir not found", plugin_id)
                return
            try:
                manifest = parse_manifest(plugin_dir)
            except ManifestError as e:
                logger.error("Cannot reload '%s': %s", plugin_id, e)
                return

        self._failed.pop(plugin_id, None)
        try:
            await asyncio.wait_for(
                self._load_single(manifest, plugin_dir),
                timeout=manifest.load_timeout,
            )
            logger.info("Plugin '%s' reloaded after permission grant", plugin_id)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            logger.error("Plugin '%s' reload failed: %s", plugin_id, msg)
            self._failed[plugin_id] = msg
        self._save_state()

    def list_failed(self) -> dict[str, str]:
        return dict(self._failed)

    def get_plugin_logs(self, plugin_id: str, lines: int = 100) -> str:
        loaded = self._loaded.get(plugin_id)
        if loaded is None:
            log_dir = self._plugins_dir / plugin_id / "logs"
        else:
            log_dir = loaded.plugin_dir / "logs"

        log_file = log_dir / f"{plugin_id}.log"
        if not log_file.exists():
            return f"No logs found for plugin '{plugin_id}'"

        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:]
        return "\n".join(tail)


class _LoadedPlugin:
    """Internal record for a loaded plugin."""

    __slots__ = ("manifest", "api", "instance", "plugin_dir")

    def __init__(
        self,
        manifest: PluginManifest,
        api: PluginAPI,
        instance: PluginBase | None,
        plugin_dir: Path,
    ) -> None:
        self.manifest = manifest
        self.api = api
        self.instance = instance
        self.plugin_dir = plugin_dir
