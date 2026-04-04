"""PluginAPI — the interface handle passed to plugins, and PluginBase."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compat import PLUGIN_API_VERSION
from .manifest import (
    BASIC_PERMISSIONS,
    PluginManifest,
)

if TYPE_CHECKING:
    from .hooks import HookRegistry
    from .protocols import MemoryBackendProtocol, RetrievalSource

logger = logging.getLogger(__name__)


class PluginPermissionError(PermissionError):
    """Raised when a plugin attempts an unauthorized operation."""


def _normalize_tool_definition(defn: dict) -> dict | None:
    """Convert a plugin tool definition to the internal (Anthropic) format.

    Plugins typically use the OpenAI format::

        {"type": "function", "function": {"name": ..., "parameters": {...}}}

    The internal system uses::

        {"name": ..., "description": ..., "input_schema": {...}}

    If ``defn`` is already in Anthropic format (has top-level "name"), it is
    returned as-is.  Returns ``None`` if the name cannot be determined.
    """
    if "name" in defn:
        if "input_schema" not in defn and "parameters" in defn:
            defn = {**defn, "input_schema": defn["parameters"]}
            del defn["parameters"]
        return defn

    func = defn.get("function", {})
    name = func.get("name", "")
    if not name:
        return None

    return {
        "name": name,
        "description": func.get("description", ""),
        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
    }


class PluginAPI:
    """API handle passed to each plugin — limits interaction to declared permissions.

    Each plugin gets its own PluginAPI instance with:
    - Isolated logger writing to data/plugins/{id}/logs/
    - Permission checks before every privileged operation
    - Access to host subsystems via register_* methods
    """

    def __init__(
        self,
        plugin_id: str,
        manifest: PluginManifest,
        granted_permissions: list[str],
        *,
        data_dir: Path,
        host_refs: dict[str, Any] | None = None,
        hook_registry: HookRegistry | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._manifest = manifest
        self._granted_permissions = set(granted_permissions)
        self._data_dir = data_dir
        self._host = dict(host_refs or {})
        self._hook_registry = hook_registry

        # Wrap skill_loader with capability-scoped proxy
        if "skill_loader" in self._host and self._host["skill_loader"] is not None:
            self._host["skill_loader"] = _ScopedSkillLoader(
                self._host["skill_loader"], plugin_id=plugin_id,
            )
        self._registered_tools: list[str] = []
        self._registered_channels: list[str] = []
        self._registered_hooks: list[str] = []
        self._registered_llm_slugs: list[str] = []
        self._registered_search_backends: list[str] = []
        self._pending_permissions: set[str] = set()

        self._logger = logging.getLogger(f"openakita.plugin.{plugin_id}")
        if self._logger.level == logging.NOTSET:
            self._logger.setLevel(logging.DEBUG)
        self._setup_file_logging()

    def _setup_file_logging(self) -> None:
        log_dir = self._data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{self._plugin_id}.log"

        if not any(
            isinstance(h, RotatingFileHandler)
            and getattr(h, "baseFilename", "") == str(log_path)
            for h in self._logger.handlers
        ):
            handler = RotatingFileHandler(
                log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            self._logger.addHandler(handler)

    def _check_permission(self, required: str, *, raise_on_deny: bool = False) -> bool:
        """Check if the plugin has the required permission.

        Returns True if granted, False if denied.
        When raise_on_deny=True, raises PluginPermissionError instead of returning False.
        By default (raise_on_deny=False), denied permissions are logged and skipped,
        allowing the plugin to load with reduced capabilities.
        """
        if required in BASIC_PERMISSIONS:
            return True
        if required in self._granted_permissions:
            return True
        if raise_on_deny:
            raise PluginPermissionError(
                f"Plugin '{self._plugin_id}' requires permission '{required}' "
                f"which was not granted. Add it to plugin.json permissions."
            )
        self.log(
            f"Permission '{required}' not granted — skipping this registration. "
            f"Grant it in plugin settings to enable this feature.",
            "warning",
        )
        if required not in self._pending_permissions:
            self._pending_permissions.add(required)
        return False

    # --- Logging (basic, always available) ---

    def log(self, msg: str, level: str = "info") -> None:
        getattr(self._logger, level, self._logger.info)(msg)

    def log_error(self, msg: str, exc: Exception | None = None) -> None:
        self._logger.error(msg, exc_info=exc)

    def log_debug(self, msg: str) -> None:
        self._logger.debug(msg)

    # --- Config / Data (basic) ---

    def get_config(self) -> dict:
        if not self._check_permission("config.read"):
            return {}
        return self._read_config_file()

    def _read_config_file(self) -> dict:
        """Read config.json without permission check (internal use)."""
        import json

        config_path = self._data_dir / "config.json"
        if not config_path.exists():
            return {}
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.log(f"Corrupt config.json, returning empty config: {e}", "warning")
            return {}

    def set_config(self, updates: dict) -> None:
        if not self._check_permission("config.write"):
            return
        import json

        config = self._read_config_file()
        config.update(updates)
        config_path = self._data_dir / "config.json"
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_data_dir(self) -> Path | None:
        if not self._check_permission("data.own"):
            return None
        data = self._data_dir / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data

    # --- Tool registration (basic) ---

    def register_tools(
        self, definitions: list[dict], handler: Callable
    ) -> None:
        if not self._check_permission("tools.register"):
            return
        tool_registry = self._host.get("tool_registry")
        if tool_registry is None:
            self.log("No tool_registry available, tools not registered", "warning")
            return

        handler_name = f"plugin_{self._plugin_id}"
        tool_names = []
        normalized_defs = []
        existing_tools = set()
        tool_defs_list = self._host.get("tool_definitions")
        if tool_defs_list is not None:
            existing_tools = {
                t.get("name") or t.get("function", {}).get("name", "")
                for t in tool_defs_list
                if isinstance(t, dict)
            }
        for d in definitions:
            defn = _normalize_tool_definition(d)
            if defn is None:
                self.log(f"Skipping tool definition with no name: {d!r}", "warning")
                continue
            name = defn["name"]
            if name in existing_tools and name not in self._registered_tools:
                self.log(
                    f"Tool '{name}' already registered by another source, skipping",
                    "warning",
                )
                continue
            tool_names.append(name)
            normalized_defs.append(defn)

        if not tool_names:
            self.log("No valid tool definitions provided", "warning")
            return

        tool_registry.register(handler_name, handler, tool_names=tool_names)
        self._registered_tools.extend(tool_names)

        tool_defs = self._host.get("tool_definitions")
        if tool_defs is not None and hasattr(tool_defs, "extend"):
            tool_defs.extend(normalized_defs)

        tool_catalog = self._host.get("tool_catalog")
        if tool_catalog is not None and hasattr(tool_catalog, "add_tool"):
            source = f"plugin:{self._plugin_id}"
            for defn in normalized_defs:
                try:
                    tool_catalog.add_tool(defn, source=source)
                except Exception as e:
                    self.log(f"Failed to add tool to catalog: {e}", "warning")

        self.log(f"Registered {len(tool_names)} tools: {tool_names}")

    # --- Hook registration ---

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        if not callable(callback):
            self.log(f"register_hook: callback is not callable: {callback!r}", "error")
            return

        basic_hooks = {"on_init", "on_shutdown", "on_schedule", "on_config_change", "on_error"}
        message_hooks = {
            "on_message_received", "on_message_sending",
            "on_session_start", "on_session_end",
        }
        retrieve_hooks = {
            "on_retrieve", "on_prompt_build", "on_tool_result",
            "on_before_tool_use", "on_after_tool_use",
        }

        if hook_name in basic_hooks:
            if not self._check_permission("hooks.basic"):
                return
        elif hook_name in message_hooks:
            if not self._check_permission("hooks.message"):
                return
        elif hook_name in retrieve_hooks:
            if not self._check_permission("hooks.retrieve"):
                return
        else:
            if not self._check_permission("hooks.all"):
                return

        if self._hook_registry is None:
            self.log("No hook_registry available", "warning")
            return

        self._hook_registry.register(
            hook_name, callback, plugin_id=self._plugin_id
        )
        timeout = self._manifest.hook_timeout
        self._hook_registry.set_timeout(hook_name, self._plugin_id, timeout)
        self._registered_hooks.append(hook_name)

    # --- API routes (advanced) ---

    def register_api_routes(self, router) -> None:
        if not self._check_permission("routes.register"):
            return
        api_server = self._host.get("api_app")
        if api_server is not None:
            try:
                api_server.include_router(router, prefix=f"/api/plugins/{self._plugin_id}")
                self.log(f"Registered API routes under /api/plugins/{self._plugin_id}")
                return
            except Exception as e:
                self.log_error(f"Failed to register API routes: {e}", e)
                return

        pending = self._host.setdefault("_pending_plugin_routers", [])
        pending.append((self._plugin_id, router))
        self.log(
            f"API app not yet available, routes queued for /api/plugins/{self._plugin_id}"
        )

    # --- Channel registration (advanced) ---

    def register_channel(self, type_name: str, factory: Callable) -> None:
        if not self._check_permission("channel.register"):
            return
        if not type_name:
            self.log("register_channel: type_name cannot be empty", "error")
            return
        channel_registry = self._host.get("channel_registry")
        if channel_registry is not None:
            try:
                import inspect

                owner = f"plugin:{self._plugin_id}"
                try:
                    params = inspect.signature(channel_registry).parameters
                except (TypeError, ValueError):
                    params = {}

                if "owner" in params:
                    channel_registry(type_name, factory, owner=owner)
                else:
                    channel_registry(type_name, factory)
                self._registered_channels.append(type_name)
                self.log(f"Registered channel type: {type_name}")
            except Exception as e:
                self.log_error(f"Failed to register channel '{type_name}': {e}", e)
        else:
            self.log("No channel_registry available", "warning")

    # --- Memory backend (advanced / system) ---

    def register_memory_backend(self, backend: MemoryBackendProtocol) -> None:
        replace_mode = "memory.replace" in self._granted_permissions
        if replace_mode:
            if not self._check_permission("memory.replace"):
                return
        else:
            if not self._check_permission("memory.write"):
                return

        memory_backends = self._host.get("memory_backends")
        if memory_backends is not None:
            memory_backends[self._plugin_id] = {
                "backend": backend,
                "replace": replace_mode,
            }
            self.log(
                f"Registered memory backend (replace={replace_mode})"
            )
        else:
            self.log("No memory_backends registry available", "warning")

    # --- Search backend (advanced) ---

    def register_search_backend(self, name: str, backend) -> None:
        if not self._check_permission("search.register"):
            return
        search_backends = self._host.get("search_backends")
        if search_backends is not None:
            qualified = f"{self._plugin_id}:{name}"
            search_backends[qualified] = backend
            self._registered_search_backends.append(qualified)
            self.log(f"Registered search backend: {qualified}")
        else:
            self.log("No search_backends registry available", "warning")

    # --- LLM provider dual registration (advanced) ---

    def register_llm_provider(self, api_type: str, provider_class: type) -> None:
        if not self._check_permission("llm.register"):
            return
        if not isinstance(provider_class, type):
            self.log(
                f"register_llm_provider: expected a class, got {type(provider_class).__name__}",
                "error",
            )
            return
        from . import PLUGIN_PROVIDER_MAP

        provider_class.__plugin_id__ = self._plugin_id  # type: ignore[attr-defined]
        PLUGIN_PROVIDER_MAP[api_type] = provider_class
        self.log(f"Registered LLM provider for api_type: {api_type}")

    def register_llm_registry(self, slug: str, registry) -> None:
        if not self._check_permission("llm.register"):
            return
        from . import PLUGIN_REGISTRY_MAP

        PLUGIN_REGISTRY_MAP[slug] = registry
        self._registered_llm_slugs.append(slug)
        self.log(f"Registered LLM vendor registry: {slug}")

    # --- Retrieval source (advanced) ---

    def register_retrieval_source(self, source: RetrievalSource) -> None:
        if not self._check_permission("retrieval.register"):
            return
        if source is None:
            self.log("register_retrieval_source: source cannot be None", "error")
            return
        external_sources = self._host.get("external_retrieval_sources")
        if external_sources is not None:
            try:
                source._plugin_id = self._plugin_id  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            external_sources.append(source)
            source_name = getattr(source, "source_name", "unknown")
            self.log(f"Registered retrieval source: {source_name}")
        else:
            self.log("No external_retrieval_sources list available", "warning")

    # --- Host access (advanced) ---

    def get_brain(self):
        if not self._check_permission("brain.access"):
            return None
        return self._host.get("brain")

    def get_memory_manager(self):
        if not self._check_permission("memory.read"):
            return None
        return self._host.get("memory_manager")

    def get_vector_store(self):
        if not self._check_permission("vector.access"):
            return None
        mm = self._host.get("memory_manager")
        if mm and hasattr(mm, "vector_store"):
            return mm.vector_store
        return None

    def get_settings(self):
        if not self._check_permission("settings.read"):
            return None
        try:
            from ..config import settings

            return settings
        except ImportError:
            return None

    def send_message(self, channel: str, chat_id: str, text: str) -> None:
        if not self._check_permission("channel.send"):
            return
        gateway = self._host.get("gateway")
        if gateway is None:
            self.log("No gateway available for send_message", "warning")
            return
        adapter = gateway.get_adapter(channel)
        if adapter is None:
            self.log(f"No adapter found for channel '{channel}'", "warning")
            return
        import asyncio

        async def _safe_send() -> None:
            try:
                await adapter.send_text(chat_id, text)
            except Exception as e:
                self.log(f"send_message failed: {e}", "error")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_safe_send())
        except RuntimeError:
            self.log("No event loop for send_message", "warning")

    # --- Cleanup ---

    def _cleanup(self) -> None:
        """Called by PluginManager during unload.

        Each section is independently guarded — one failure does not block
        subsequent cleanup steps.
        """
        try:
            if self._hook_registry:
                removed = self._hook_registry.unregister_plugin(self._plugin_id)
                if removed:
                    self.log(f"Unregistered {removed} hooks")
        except Exception as e:
            logger.debug("Plugin '%s' hook cleanup error: %s", self._plugin_id, e)

        try:
            for h in self._logger.handlers[:]:
                h.flush()
                h.close()
                self._logger.removeHandler(h)
        except Exception:
            pass

        try:
            self._cleanup_tools()
        except Exception as e:
            logger.debug("Plugin '%s' tool cleanup error: %s", self._plugin_id, e)

        try:
            self._cleanup_channels()
        except Exception as e:
            logger.debug("Plugin '%s' channel cleanup error: %s", self._plugin_id, e)

        try:
            self._cleanup_mcp()
        except Exception as e:
            logger.debug("Plugin '%s' MCP cleanup error: %s", self._plugin_id, e)

        try:
            memory_backends = self._host.get("memory_backends")
            if memory_backends is not None:
                memory_backends.pop(self._plugin_id, None)
        except Exception:
            pass

        try:
            search_backends = self._host.get("search_backends")
            if search_backends is not None:
                for name in self._registered_search_backends:
                    search_backends.pop(name, None)
        except Exception:
            pass

        try:
            external_sources = self._host.get("external_retrieval_sources")
            if external_sources is not None:
                to_remove = [
                    s for s in external_sources
                    if getattr(s, "_plugin_id", None) == self._plugin_id
                ]
                for s in to_remove:
                    try:
                        external_sources.remove(s)
                    except ValueError:
                        pass
        except Exception:
            pass

        try:
            from . import PLUGIN_PROVIDER_MAP, PLUGIN_REGISTRY_MAP

            for api_type, cls in list(PLUGIN_PROVIDER_MAP.items()):
                if getattr(cls, "__plugin_id__", "") == self._plugin_id:
                    del PLUGIN_PROVIDER_MAP[api_type]
            for slug in self._registered_llm_slugs:
                PLUGIN_REGISTRY_MAP.pop(slug, None)
        except Exception:
            pass

    def _cleanup_tools(self) -> None:
        """Remove plugin-registered tools from all host registries."""
        if not self._registered_tools:
            return

        tool_registry = self._host.get("tool_registry")
        if tool_registry:
            handler_name = f"plugin_{self._plugin_id}"
            try:
                tool_registry.unregister(handler_name)
            except Exception:
                pass

        tool_defs = self._host.get("tool_definitions")
        if tool_defs is not None:
            registered = set(self._registered_tools)
            to_remove = [
                d for d in tool_defs
                if d.get("name", d.get("function", {}).get("name", ""))
                in registered
            ]
            for d in to_remove:
                try:
                    tool_defs.remove(d)
                except ValueError:
                    pass

        tool_catalog = self._host.get("tool_catalog")
        if tool_catalog is not None and hasattr(tool_catalog, "remove_tool"):
            for name in self._registered_tools:
                try:
                    tool_catalog.remove_tool(name)
                except Exception:
                    pass


    def _cleanup_channels(self) -> None:
        """Remove plugin-registered channel types from the adapter registry."""
        if not self._registered_channels:
            return
        try:
            from ..channels.registry import unregister_adapter
        except ImportError:
            return
        owner = f"plugin:{self._plugin_id}"
        for type_name in self._registered_channels:
            try:
                unregister_adapter(type_name, owner=owner)
            except Exception:
                pass
        self._registered_channels.clear()

    def _cleanup_mcp(self) -> None:
        """Disconnect and remove MCP server registered by this plugin."""
        mcp_client = self._host.get("mcp_client")
        if mcp_client is None:
            return
        server_name = self._plugin_id
        if not hasattr(mcp_client, "get_server") or mcp_client.get_server(server_name) is None:
            return
        import asyncio

        async def _do_cleanup():
            try:
                if hasattr(mcp_client, "disconnect"):
                    await mcp_client.disconnect(server_name)
            except Exception:
                pass
            if hasattr(mcp_client, "remove_server"):
                mcp_client.remove_server(server_name)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_cleanup())
        except RuntimeError:
            if hasattr(mcp_client, "remove_server"):
                mcp_client.remove_server(server_name)


    def __getattr__(self, name: str) -> Any:
        logger.warning(
            "[PluginAPI] Plugin '%s' accessed non-existent attribute '%s' — "
            "this may indicate an API mismatch or version skew.",
            self._plugin_id,
            name,
        )
        raise AttributeError(
            f"PluginAPI has no attribute {name!r}. "
            f"Check the plugin API documentation for available methods."
        )


class _ScopedSkillLoader:
    """Capability-scoped wrapper around SkillLoader.

    Only exposes safe methods; blocks access to internal references like
    parser, registry, or private attributes.
    """

    _ALLOWED = frozenset({"load_skill", "unload_skill", "get_tool_definitions",
                          "get_skill", "get_skill_body", "loaded_count"})

    def __init__(self, real_loader: Any, plugin_id: str) -> None:
        self._real = real_loader
        self._plugin_id = plugin_id

    def __getattr__(self, name: str) -> Any:
        if name in self._ALLOWED:
            return getattr(self._real, name)
        logger.warning(
            "[ScopedSkillLoader] Plugin '%s' tried to access '%s' — blocked",
            self._plugin_id, name,
        )
        raise AttributeError(
            f"ScopedSkillLoader does not expose '{name}'. "
            f"Allowed: {sorted(self._ALLOWED)}"
        )


class PluginBase(ABC):
    """Base class for Python plugins.

    Subclass this and implement ``on_load``.
    Optionally override ``on_unload`` for cleanup.
    """

    @abstractmethod
    def on_load(self, api: PluginAPI) -> None:
        """Called when the plugin is loaded. Register capabilities here."""

    def on_unload(self) -> None:  # noqa: B027
        """Called when the plugin is being unloaded. Clean up resources."""
