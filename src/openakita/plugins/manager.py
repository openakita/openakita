"""PluginManager — discover, load, manage plugin lifecycle."""

from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import inspect
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
_ALLOWED_HOST_REFS = frozenset(
    {
        "api_app",
        "brain",
        "channel_registry",
        "external_retrieval_sources",
        "gateway",
        "mcp_client",
        "memory_backends",
        "memory_manager",
        "search_backends",
        "skill_catalog",
        "skill_loader",
        "tool_catalog",
        "tool_definitions",
        "tool_registry",
    }
)


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
        self._host_refs = self._filter_host_refs(host_refs or {})

        self._state = PluginState.load(self._state_path)
        self._error_tracker = PluginErrorTracker()
        self._error_tracker.set_auto_disable_callback(self._on_plugin_auto_disabled)
        self._hook_registry = HookRegistry(error_tracker=self._error_tracker)

        self._loaded: dict[str, _LoadedPlugin] = {}
        self._failed: dict[str, str] = {}

    @staticmethod
    def _filter_host_refs(host_refs: dict[str, Any]) -> dict[str, Any]:
        """Expose only the host references that plugins are expected to use."""
        filtered = {k: v for k, v in host_refs.items() if k in _ALLOWED_HOST_REFS}
        dropped = sorted(set(host_refs) - set(filtered))
        if dropped:
            logger.debug("PluginManager filtered host_refs: %s", dropped)
        return filtered

    # --- Properties ---

    @property
    def hook_registry(self) -> HookRegistry:
        return self._hook_registry

    @property
    def loaded_count(self) -> int:
        return len(self._loaded)

    @property
    def loaded_plugins(self) -> dict[str, _LoadedPlugin]:
        """Expose loaded plugins dict (read-only access for AgentFactory filtering)."""
        return self._loaded

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

    @staticmethod
    def _topological_sort(
        manifests: list[tuple[Path, PluginManifest]],
    ) -> tuple[list[tuple[Path, PluginManifest]], list[str]]:
        """Sort plugins by dependency order using Kahn's algorithm.

        Returns (sorted_list, cyclic_ids).
        Plugins involved in cycles are excluded and their IDs returned.
        """
        by_id: dict[str, tuple[Path, PluginManifest]] = {m.id: (d, m) for d, m in manifests}
        in_degree: dict[str, int] = dict.fromkeys(by_id, 0)
        dependents: dict[str, list[str]] = {mid: [] for mid in by_id}

        for mid, (_, m) in by_id.items():
            for dep in m.depends:
                if dep in by_id:
                    in_degree[mid] += 1
                    dependents[dep].append(mid)

        queue = [mid for mid, deg in in_degree.items() if deg == 0]
        sorted_ids: list[str] = []
        while queue:
            node = queue.pop(0)
            sorted_ids.append(node)
            for child in dependents.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        cyclic = [mid for mid in by_id if mid not in sorted_ids]
        result = [by_id[mid] for mid in sorted_ids]
        return result, cyclic

    async def load_all(self) -> None:
        """Load all discovered and enabled plugins.

        Each plugin is loaded in its own try/except with a timeout.
        Failures are logged and tracked, never propagated.
        """
        plugin_dirs = self._discover_plugins()
        if not plugin_dirs:
            logger.debug("No plugins found in %s", self._plugins_dir)
            return

        # Parse all manifests first for topological sorting
        parsed: list[tuple[Path, PluginManifest]] = []
        for plugin_dir in plugin_dirs:
            try:
                manifest = parse_manifest(plugin_dir)
                parsed.append((plugin_dir, manifest))
            except ManifestError as e:
                logger.error("Skipping %s: %s", plugin_dir.name, e)
                self._failed[plugin_dir.name] = str(e)

        sorted_plugins, cyclic_ids = self._topological_sort(parsed)
        for cid in cyclic_ids:
            msg = "cyclic dependency detected, skipped"
            logger.error("Plugin '%s' %s", cid, msg)
            self._failed[cid] = msg

        for plugin_dir, manifest in sorted_plugins:
            if not self._check_openakita_version(manifest):
                continue

            if not self._state.is_enabled(manifest.id):
                reason = ""
                entry = self._state.get_entry(manifest.id)
                if entry:
                    reason = entry.disabled_reason
                logger.info(
                    "Plugin '%s' is disabled (%s), skipping",
                    manifest.id,
                    reason or "user",
                )
                continue

            if manifest.conflicts:
                conflict = next((c for c in manifest.conflicts if c in self._loaded), None)
                if conflict:
                    logger.warning(
                        "Plugin '%s' conflicts with loaded '%s', skipping",
                        manifest.id,
                        conflict,
                    )
                    self._failed[manifest.id] = f"conflicts with {conflict}"
                    continue

            if manifest.depends:
                missing = [d for d in manifest.depends if d not in self._loaded]
                if missing:
                    msg = f"missing dependencies: {', '.join(missing)}"
                    logger.warning("Plugin '%s' skipped: %s", manifest.id, msg)
                    self._failed[manifest.id] = msg
                    continue

            try:
                await asyncio.wait_for(
                    self._load_single(manifest, plugin_dir),
                    timeout=manifest.load_timeout,
                )
                logger.info("Plugin '%s' v%s loaded", manifest.id, manifest.version)
            except (asyncio.TimeoutError, TimeoutError):
                msg = f"load timeout ({manifest.load_timeout}s)"
                logger.error("Plugin '%s' %s, skipped", manifest.id, msg)
                self._failed[manifest.id] = msg
                self._state.record_error(manifest.id, msg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                logger.error("Plugin '%s' failed to load: %s", manifest.id, msg, exc_info=True)
                self._failed[manifest.id] = msg
                self._state.record_error(manifest.id, msg)

        self._refresh_skill_catalog()
        self._reload_llm_registries()
        self._save_state()

    def _reload_llm_registries(self) -> None:
        """Notify LLM registries to pick up plugin-provided providers."""
        try:
            from ..llm.registries import reload_registries

            reload_registries()
        except Exception as e:
            logger.debug("LLM registry reload skipped: %s", e)

    async def _load_single(self, manifest: PluginManifest, plugin_dir: Path) -> None:
        state_entry = self._state.ensure_entry(manifest.id)
        granted = self._resolve_permissions(manifest, state_entry.granted_permissions)
        state_entry.granted_permissions = granted

        data_dir = self._plugins_dir.parent / "plugin_data" / manifest.id
        data_dir.mkdir(parents=True, exist_ok=True)
        api = PluginAPI(
            plugin_id=manifest.id,
            manifest=manifest,
            granted_permissions=granted,
            data_dir=data_dir,
            host_refs=self._host_refs,
            hook_registry=self._hook_registry,
        )

        plugin_instance: PluginBase | None = None
        module_name = ""
        sys_path_entry = ""
        imported_modules: set[str] = set()

        try:
            if manifest.plugin_type == "python":
                (
                    plugin_instance,
                    module_name,
                    sys_path_entry,
                    imported_modules,
                ) = self._load_python_plugin(manifest, plugin_dir)
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
            module_name=module_name,
            sys_path_entry=sys_path_entry,
            imported_modules=imported_modules,
        )

        plugin_pending = api._host.pop("_pending_plugin_routers", [])
        if plugin_pending:
            shared_pending = self._host_refs.setdefault("_pending_plugin_routers", [])
            if plugin_pending is not shared_pending:
                for entry in plugin_pending:
                    if entry not in shared_pending:
                        shared_pending.append(entry)

        if manifest.has_ui:
            self._mount_plugin_ui(manifest, plugin_dir)

    def _mount_plugin_ui(
        self, manifest: PluginManifest, plugin_dir: Path
    ) -> None:
        """Mount plugin UI static files to the FastAPI app."""
        assert manifest.ui is not None
        ui_entry = manifest.ui.entry
        ui_dist_dir = (plugin_dir / ui_entry).parent
        if not ui_dist_dir.is_dir():
            logger.warning(
                "Plugin '%s' declares UI but dist dir '%s' not found, skipping UI mount",
                manifest.id, ui_dist_dir,
            )
            return
        index_file = plugin_dir / ui_entry
        if not index_file.is_file():
            logger.warning(
                "Plugin '%s' UI entry '%s' not found, skipping UI mount",
                manifest.id, ui_entry,
            )
            return

        app = self._host_refs.get("api_app")
        if app is None:
            logger.debug(
                "Plugin '%s' has UI but api_app not yet available; will mount later",
                manifest.id,
            )
            pending = self._host_refs.setdefault("_pending_plugin_ui_mounts", [])
            pending.append((manifest.id, str(ui_dist_dir)))
            return

        self._do_mount_plugin_ui(app, manifest.id, str(ui_dist_dir))

    @staticmethod
    def _do_mount_plugin_ui(app: Any, plugin_id: str, ui_dist_dir: str) -> None:
        from fastapi.staticfiles import StaticFiles
        from starlette.responses import Response

        mount_path = f"/api/plugins/{plugin_id}/ui"

        class NoCacheStaticFiles(StaticFiles):
            async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
                resp = await super().get_response(path, scope)
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
                return resp

        try:
            app.mount(
                mount_path,
                NoCacheStaticFiles(directory=ui_dist_dir, html=True),
                name=f"plugin-ui-{plugin_id}",
            )
            logger.info("Mounted plugin UI for '%s' at %s", plugin_id, mount_path)
        except Exception as e:
            logger.warning("Failed to mount UI for plugin '%s': %s", plugin_id, e)

    def _load_python_plugin(
        self, manifest: PluginManifest, plugin_dir: Path
    ) -> tuple[PluginBase, str, str, set[str]]:
        """Load a Python plugin module.

        Returns ``(instance, module_name, sys_path_entry, imported_modules)``.
        ``imported_modules`` lists submodules newly registered in
        ``sys.modules`` whose source file lives under ``plugin_dir`` — so the
        unloader can purge them and avoid stale-module reuse on reinstall.
        """
        entry_path = plugin_dir / manifest.entry
        if not entry_path.exists():
            raise FileNotFoundError(f"Plugin entry '{manifest.entry}' not found in {plugin_dir}")

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
            plugin_dir_resolved = plugin_dir.resolve()
        except OSError:
            plugin_dir_resolved = plugin_dir

        pre_modules = set(sys.modules.keys())

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

        # Collect plugin-local submodules pulled into sys.modules during exec.
        imported_modules: set[str] = set()
        for new_name in set(sys.modules.keys()) - pre_modules:
            if new_name == module_name:
                continue
            mod = sys.modules.get(new_name)
            mod_file = getattr(mod, "__file__", None) or ""
            if not mod_file:
                continue
            try:
                if Path(mod_file).resolve().is_relative_to(plugin_dir_resolved):
                    imported_modules.add(new_name)
            except (OSError, ValueError):
                continue

        plugin_class = getattr(module, "Plugin", None)
        if plugin_class is None:
            sys.modules.pop(module_name, None)
            for m in imported_modules:
                sys.modules.pop(m, None)
            if added_to_path:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass
            raise AttributeError(f"Plugin module {entry_path} must export a 'Plugin' class")

        if not (isinstance(plugin_class, type) and issubclass(plugin_class, PluginBase)):
            sys.modules.pop(module_name, None)
            for m in imported_modules:
                sys.modules.pop(m, None)
            if added_to_path:
                try:
                    sys.path.remove(plugin_dir_str)
                except ValueError:
                    pass
            raise TypeError(
                f"Plugin.Plugin must be a subclass of PluginBase, got {type(plugin_class)}"
            )

        return (
            plugin_class(),
            module_name,
            plugin_dir_str if added_to_path else "",
            imported_modules,
        )

    def _load_mcp_plugin(self, manifest: PluginManifest, plugin_dir: Path, api: PluginAPI) -> None:
        config_path = plugin_dir / manifest.entry
        if not config_path.exists():
            raise FileNotFoundError(f"MCP config '{manifest.entry}' not found in {plugin_dir}")

        mcp_config = json.loads(config_path.read_text(encoding="utf-8"))
        mcp_client = self._host_refs.get("mcp_client")
        if mcp_client is None or not hasattr(mcp_client, "add_server"):
            api.log("No MCP client available for MCP plugin", "warning")
            return

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

    def _load_skill_plugin(
        self, manifest: PluginManifest, plugin_dir: Path, api: PluginAPI
    ) -> None:
        skill_path = plugin_dir / manifest.entry
        if not skill_path.exists():
            raise FileNotFoundError(f"Skill entry '{manifest.entry}' not found in {plugin_dir}")
        skill_loader = self._host_refs.get("skill_loader")
        if skill_loader is None:
            api.log("No skill_loader available", "warning")
            return

        if hasattr(skill_loader, "load_skill"):
            skill_loader.load_skill(skill_path.parent, plugin_source=f"plugin:{manifest.id}")
            api.log(f"Skill loaded from {skill_path.parent}")
        elif hasattr(skill_loader, "load_from_directory"):
            skill_loader.load_from_directory(skill_path.parent)
            api.log(f"Skill directory loaded from {skill_path.parent}")
        else:
            api.log(
                f"skill_loader ({type(skill_loader).__name__}) has no load_skill method",
                "warning",
            )
            return

        self._skills_loaded = True
        self._tag_skill_source(skill_path.parent.name, manifest.id)

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
                skill_loader.load_skill(skill_path.parent, plugin_source=f"plugin:{manifest.id}")
            elif hasattr(skill_loader, "load_from_directory"):
                skill_loader.load_from_directory(skill_path.parent)
            else:
                api.log("skill_loader has no load_skill method", "warning")
                return
            api.log(f"Plugin skill loaded from {skill_path.parent}")
            self._skills_loaded = True
            self._tag_skill_source(skill_path.parent.name, manifest.id)
        except Exception as e:
            api.log(f"Failed to load plugin skill: {e}", "warning")

    def _tag_skill_source(self, skill_id: str, plugin_id: str) -> None:
        """Mark a skill entry in the registry as coming from a plugin."""
        skill_loader = self._host_refs.get("skill_loader")
        if skill_loader is None:
            return
        registry = getattr(skill_loader, "registry", None)
        if registry is None:
            return
        entry = registry.get(skill_id)
        if entry is not None and hasattr(entry, "plugin_source"):
            entry.plugin_source = f"plugin:{plugin_id}"
        else:
            logger.warning(
                "Cannot tag plugin source: skill '%s' not found in registry after load",
                skill_id,
            )

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

    def _unload_plugin_skills(self, loaded: _LoadedPlugin) -> None:
        """Remove skills contributed by this plugin and reset _skills_loaded if needed."""
        had_skill = loaded.manifest.plugin_type == "skill" or loaded.manifest.provides.get("skill")
        if had_skill:
            skill_loader = self._host_refs.get("skill_loader")
            if skill_loader is not None:
                registry = getattr(skill_loader, "registry", None)
                if registry is not None:
                    skill_ids = [
                        sid
                        for sid, entry in list(registry.items())
                        if getattr(entry, "plugin_source", "") == f"plugin:{loaded.manifest.id}"
                    ]
                    for sid in skill_ids:
                        try:
                            if hasattr(skill_loader, "unload_skill"):
                                skill_loader.unload_skill(sid)
                            else:
                                registry.pop(sid, None)
                        except Exception:
                            registry.pop(sid, None)
                    if skill_ids:
                        logger.debug(
                            "Removed %d skill(s) from plugin '%s'",
                            len(skill_ids),
                            loaded.manifest.id,
                        )
            self._refresh_skill_catalog()

        if getattr(self, "_skills_loaded", False):
            has_other_skills = any(
                lp.manifest.plugin_type == "skill" or lp.manifest.provides.get("skill")
                for lp in self._loaded.values()
            )
            if not has_other_skills:
                self._skills_loaded = False

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
                    manifest.id,
                    perm,
                )
        return granted

    def approve_permissions(self, plugin_id: str, permissions: list[str]) -> None:
        """Grant additional permissions (called from UI approval flow)."""
        from .manifest import ALL_PERMISSIONS

        entry = self._state.ensure_entry(plugin_id)
        for perm in permissions:
            if perm not in ALL_PERMISSIONS:
                logger.warning("Ignoring unknown permission '%s' for plugin '%s'", perm, plugin_id)
                continue
            if perm not in entry.granted_permissions:
                entry.granted_permissions.append(perm)

        loaded = self._loaded.get(plugin_id)
        if loaded:
            loaded.api._granted_permissions = set(entry.granted_permissions)

        self._save_state()

    def revoke_permissions(self, plugin_id: str, permissions: list[str]) -> None:
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

    @staticmethod
    async def _invoke_on_unload(instance: PluginBase, plugin_id: str) -> None:
        """Run ``on_unload`` supporting both sync and async signatures.

        CRITICAL design note (do NOT regress to running sync handlers in a
        worker thread with a temporary loop):

        Most legacy plugins do:

            def on_unload(self):
                loop = asyncio.get_event_loop()
                loop.create_task(self._client.close())   # httpx
                loop.create_task(self._tm.close())       # aiosqlite

        ``self._client`` / ``self._tm`` were created on the **main** event
        loop during ``on_load``. Awaiting their ``close()`` from a different
        loop raises ``Future attached to a different loop`` (or silently
        deadlocks), leaving the underlying file handles / sockets open —
        which is what causes Windows ``WinError 32`` on the subsequent
        rmtree. So the sync handler MUST run inline on the main loop's task,
        and any ``loop.create_task(...)`` it issues MUST be drained on the
        same main loop.
        """
        handler = instance.on_unload
        loop = asyncio.get_running_loop()

        if inspect.iscoroutinefunction(handler):
            before = set(asyncio.all_tasks(loop))
            try:
                await asyncio.wait_for(handler(), timeout=UNLOAD_TIMEOUT)
            finally:
                # Even if the coroutine raises/timeouts, drain anything it
                # scheduled (mirrors the sync path).
                after = set(asyncio.all_tasks(loop))
                new_tasks = {t for t in (after - before) if not t.done()}
                if new_tasks:
                    try:
                        await asyncio.wait(new_tasks, timeout=UNLOAD_TIMEOUT)
                    except Exception as e:
                        logger.debug(
                            "Plugin '%s' drain async-on_unload tasks error: %s",
                            plugin_id,
                            e,
                        )
            return

        # Sync handler — run inline so create_task() targets the main loop.
        before = set(asyncio.all_tasks(loop))
        result: Any = None
        try:
            result = handler()
        except Exception as e:
            logger.warning("Plugin '%s' sync on_unload raised: %s", plugin_id, e)

        # Defensive: a sync def that returns a coroutine (mistakenly
        # forgotten ``async``) — await it instead of leaking the coroutine.
        if inspect.iscoroutine(result):
            try:
                await asyncio.wait_for(result, timeout=UNLOAD_TIMEOUT)
            except Exception as e:
                logger.warning(
                    "Plugin '%s' on_unload-returned coroutine error: %s",
                    plugin_id,
                    e,
                )

        # Drain any tasks the handler scheduled on the main loop.
        after = set(asyncio.all_tasks(loop))
        new_tasks = {t for t in (after - before) if not t.done()}
        if new_tasks:
            try:
                await asyncio.wait(new_tasks, timeout=UNLOAD_TIMEOUT)
            except Exception as e:
                logger.debug(
                    "Plugin '%s' drain sync-on_unload tasks error: %s",
                    plugin_id,
                    e,
                )

    async def unload_plugin(self, plugin_id: str) -> bool:
        loaded = self._loaded.pop(plugin_id, None)
        if loaded is None:
            return False

        # 1. Plugin's own on_unload — best effort, never blocks the rest.
        try:
            if loaded.instance:
                await self._invoke_on_unload(loaded.instance, plugin_id)
        except (TimeoutError, Exception) as e:
            logger.warning("Plugin '%s' on_unload error: %s", plugin_id, e)

        # 2. Cancel framework-tracked background tasks, then run async/sync
        #    capability cleanup (routes, hooks, MCP, etc.) on the main loop.
        try:
            await loaded.api.aclose()
        except Exception as e:
            logger.warning("Plugin '%s' aclose error: %s", plugin_id, e)

        # 2b. Sweep up "stray" tasks the plugin scheduled itself — e.g.
        #     ``asyncio.get_event_loop().create_task(self._poll_loop())`` from
        #     on_load, which never went through ``api.spawn_task`` and is
        #     therefore invisible to ``_cancel_spawned_tasks``. We identify
        #     them by checking whether their coroutine's source module belongs
        #     to this plugin (main module + tracked submodules). Without this,
        #     a polling task continues to use the plugin's httpx/SQLite
        #     connections after unload, which keeps the Windows file handles
        #     open and breaks the subsequent rmtree.
        try:
            await self._cancel_stray_plugin_tasks(plugin_id, loaded)
        except Exception as e:
            logger.debug("Plugin '%s' stray-task sweep error: %s", plugin_id, e)

        # 3. Drop plugin module and its plugin-local submodules so a reinstall
        #    or hot-reload sees fresh code (and releases SQLite/HTTP file handles).
        if loaded.module_name:
            sys.modules.pop(loaded.module_name, None)
        for mod_name in list(loaded.imported_modules):
            sys.modules.pop(mod_name, None)
        if loaded.sys_path_entry:
            try:
                sys.path.remove(loaded.sys_path_entry)
            except ValueError:
                pass

        # 4. Force GC — some C-extensions (sqlite3, ssl) only release OS
        #    handles when their Python wrapper is collected. We do TWO passes
        #    with a brief yield in between because aiosqlite + httpx tend to
        #    have one layer of cyclic refs through their connection pools.
        try:
            gc.collect()
            await asyncio.sleep(0)
            gc.collect()
        except Exception:
            pass

        self._unload_plugin_skills(loaded)
        self._unmount_plugin_ui(plugin_id)

        logger.info("Plugin '%s' unloaded", plugin_id)
        return True

    @staticmethod
    async def _cancel_stray_plugin_tasks(
        plugin_id: str, loaded: _LoadedPlugin
    ) -> None:
        """Cancel & await any task whose coroutine lives in a plugin module.

        This is a safety net for plugins that bypass ``api.spawn_task`` and
        use ``asyncio.create_task`` directly (very common in third-party
        code). Without canceling these, they keep referencing the plugin's
        ``httpx.AsyncClient`` / ``aiosqlite.Connection`` and prevent file
        handle release.
        """
        plugin_modules: set[str] = set(loaded.imported_modules)
        if loaded.module_name:
            plugin_modules.add(loaded.module_name)
        if not plugin_modules:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        stray: list[asyncio.Task[Any]] = []
        for t in asyncio.all_tasks(loop):
            if t.done():
                continue
            coro = t.get_coro()
            # The reliable way to get a coroutine's module is via the frame
            # globals — coro.__module__ is almost always ``None`` for
            # ``async def`` functions, and ``cr_code.co_filename`` would
            # require a path-to-module mapping we don't keep.
            mod: str | None = None
            frame = getattr(coro, "cr_frame", None)
            if frame is not None:
                f_globals = getattr(frame, "f_globals", None) or {}
                mod = f_globals.get("__name__")
            if not isinstance(mod, str):
                continue
            # Match exact module name or ``<plugin>.<sub>`` prefix; avoid
            # over-collecting siblings like ``foo_bar`` when plugin is ``foo``.
            if mod in plugin_modules or any(
                mod.startswith(pm + ".") for pm in plugin_modules
            ):
                stray.append(t)

        if not stray:
            return

        logger.info(
            "Plugin '%s': cancelling %d stray task(s) created outside spawn_task",
            plugin_id,
            len(stray),
        )
        for t in stray:
            t.cancel()
        # Suppress CancelledError surfacing to the gather/wait result.
        try:
            await asyncio.wait(stray, timeout=UNLOAD_TIMEOUT)
        except Exception as e:
            logger.debug(
                "Plugin '%s' awaiting stray task cancellation: %s", plugin_id, e
            )

    async def disable_plugin(self, plugin_id: str, reason: str = "user") -> None:
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

        Performs full unload (tools, hooks, channels, MCP, etc.) and marks
        the plugin as disabled in persistent state.
        """
        self._state.disable(plugin_id, reason="auto_disabled")
        self._save_state()

        async def _do_unload():
            try:
                await self.unload_plugin(plugin_id)
                logger.info("Auto-disable: fully unloaded plugin '%s'", plugin_id)
            except Exception as e:
                logger.warning(
                    "Auto-disable: unload failed for plugin '%s': %s",
                    plugin_id,
                    e,
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_unload())
        except RuntimeError:
            loaded = self._loaded.get(plugin_id)
            if loaded and hasattr(loaded.api, "_cleanup_tools"):
                try:
                    loaded.api._cleanup_tools()
                except Exception:
                    pass

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
            result.append(
                {
                    "id": lp.manifest.id,
                    "capability_id": lp.manifest.capability_id,
                    "namespace": lp.manifest.namespace,
                    "origin": lp.manifest.origin,
                    "name": lp.manifest.name,
                    "version": lp.manifest.version,
                    "type": lp.manifest.plugin_type,
                    "category": lp.manifest.category,
                    "permissions": lp.manifest.permissions,
                    "permission_level": lp.manifest.max_permission_level,
                    "review_status": lp.manifest.review_status,
                    "granted_permissions": granted,
                    "pending_permissions": pending,
                }
            )
        return result

    def _find_plugin_dir(self, plugin_id: str) -> Path | None:
        """Locate the on-disk directory for a plugin by its manifest ID.

        Checks the obvious path first (plugins_dir/plugin_id), then scans all
        plugin directories for a matching manifest.id.
        """
        direct = self._plugins_dir / plugin_id
        if (direct / "plugin.json").exists():
            return direct
        if not self._plugins_dir.exists():
            return None
        for child in self._plugins_dir.iterdir():
            if not child.is_dir():
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.exists():
                continue
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                if raw.get("id") == plugin_id:
                    return child
            except Exception:
                continue
        return None

    async def reload_plugin(self, plugin_id: str) -> None:
        """Unload then re-load a plugin (e.g. after granting new permissions)."""
        loaded = self._loaded.get(plugin_id)
        if loaded is not None:
            plugin_dir = loaded.plugin_dir
            manifest = loaded.manifest
            await self.unload_plugin(plugin_id)
        else:
            plugin_dir = self._find_plugin_dir(plugin_id)
            if plugin_dir is None:
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

    def _unmount_plugin_ui(self, plugin_id: str) -> None:
        """Remove the plugin UI static-file mount from the FastAPI app."""
        app = self._host_refs.get("api_app")
        if app is None:
            return
        mount_path = f"/api/plugins/{plugin_id}/ui"
        mount_name = f"plugin-ui-{plugin_id}"
        try:
            app.routes[:] = [
                r for r in app.routes
                if not (hasattr(r, "name") and r.name == mount_name)
            ]
            logger.debug("Unmounted plugin UI '%s' at %s", plugin_id, mount_path)
        except Exception as e:
            logger.debug("Plugin '%s' UI unmount error: %s", plugin_id, e)

    def list_ui_plugins(self) -> list[dict]:
        """Return metadata for all loaded plugins that have a UI."""
        result = []
        for lp in self._loaded.values():
            if not lp.manifest.has_ui:
                continue
            ui = lp.manifest.ui
            assert ui is not None
            icon_url = ""
            if ui.icon:
                icon_url = f"/api/plugins/{lp.manifest.id}/ui/{ui.icon}"
            result.append({
                "id": lp.manifest.id,
                "title": ui.title or lp.manifest.name,
                "title_i18n": dict(ui.title_i18n) if ui.title_i18n else {},
                "icon_url": icon_url,
                "sidebar_group": ui.sidebar_group,
                "enabled": True,
                "status": "loaded",
            })
        return result

    def list_failed(self) -> dict[str, str]:
        return dict(self._failed)

    def get_plugin_logs(self, plugin_id: str, lines: int = 100) -> str:
        loaded = self._loaded.get(plugin_id)
        if loaded is not None:
            log_dir = loaded.api._data_dir / "logs"
        else:
            data_dir = self._plugins_dir.parent / "plugin_data" / plugin_id
            log_dir = data_dir / "logs"

        log_file = log_dir / f"{plugin_id}.log"
        if not log_file.exists():
            return f"No logs found for plugin '{plugin_id}'"

        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:]
        return "\n".join(tail)


class _LoadedPlugin:
    """Internal record for a loaded plugin."""

    __slots__ = (
        "manifest",
        "api",
        "instance",
        "plugin_dir",
        "module_name",
        "sys_path_entry",
        "imported_modules",
    )

    def __init__(
        self,
        manifest: PluginManifest,
        api: PluginAPI,
        instance: PluginBase | None,
        plugin_dir: Path,
        module_name: str = "",
        sys_path_entry: str = "",
        imported_modules: set[str] | None = None,
    ) -> None:
        self.manifest = manifest
        self.api = api
        self.instance = instance
        self.plugin_dir = plugin_dir
        self.module_name = module_name
        self.sys_path_entry = sys_path_entry
        # Submodules imported by the plugin from its own directory; cleared on unload
        # so reinstall picks up fresh code instead of cached stale modules.
        self.imported_modules: set[str] = imported_modules or set()
