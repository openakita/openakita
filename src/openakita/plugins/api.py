"""PluginAPI — the interface handle passed to plugins, and PluginBase."""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compat import PLUGIN_UI_API_VERSION
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
                self._host["skill_loader"],
                plugin_id=plugin_id,
            )
        self._registered_tools: list[str] = []
        self._registered_channels: list[str] = []
        self._registered_hooks: list[str] = []
        self._registered_llm_slugs: list[str] = []
        self._registered_search_backends: list[str] = []
        self._pending_permissions: set[str] = set()
        # Background tasks scheduled via spawn_task — cancelled on unload.
        self._spawned_tasks: set[asyncio.Task[Any]] = set()

        self._logger = logging.getLogger(f"openakita.plugin.{plugin_id}")
        if self._logger.level == logging.NOTSET:
            self._logger.setLevel(logging.DEBUG)
        self._setup_file_logging()

    def _setup_file_logging(self) -> None:
        log_dir = self._data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{self._plugin_id}.log"

        if not any(
            isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path)
            for h in self._logger.handlers
        ):
            handler = RotatingFileHandler(
                log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
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

    def has_permission(self, name: str) -> bool:
        """Public, side-effect-free check for whether a permission is granted.

        Use this in plugin code paths that want to *gracefully degrade* and
        produce a domain-specific error message (e.g. "AI optimize disabled
        because brain.access not granted"), instead of relying on
        ``get_brain()`` returning ``None`` — which conflates "permission
        missing" with "host has no brain".

        Unlike ``_check_permission`` this never logs and never marks the
        permission as pending; it's purely a read.
        """
        return name in BASIC_PERMISSIONS or name in self._granted_permissions

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
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_data_dir(self) -> Path | None:
        if not self._check_permission("data.own"):
            return None
        data = self._data_dir / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data

    # --- Tool registration (basic) ---

    def register_tools(self, definitions: list[dict], handler: Callable) -> None:
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

    def register_hook(
        self,
        hook_name: str,
        callback: Callable,
        *,
        match: Callable[..., bool] | None = None,
    ) -> None:
        """Register a lifecycle hook callback.

        Args:
            hook_name: One of the 15 supported hook names. Permission tier is
                inferred from the name (basic/message/retrieve, otherwise
                requires ``hooks.all``).
            callback: Sync or async ``f(**kwargs) -> Any``. Each hook event
                passes specific kwargs documented in the plugin guide.
            match: Optional predicate ``f(**kwargs) -> bool``. When provided,
                the dispatcher invokes the predicate first and skips the
                callback when it returns False. Predicates are evaluated
                cheaply (no timeout, no thread off-load); raising counts as
                no-match and is recorded as a low-weight error.

        Example::

            api.register_hook(
                "on_message_received",
                self._on_msg,
                match=lambda **kw: kw.get("channel") == "wecom",
            )
        """
        if not callable(callback):
            self.log(f"register_hook: callback is not callable: {callback!r}", "error")
            return

        basic_hooks = {"on_init", "on_shutdown", "on_schedule", "on_config_change", "on_error"}
        message_hooks = {
            "on_message_received",
            "on_message_sending",
            "on_session_start",
            "on_session_end",
        }
        retrieve_hooks = {
            "on_retrieve",
            "on_prompt_build",
            "on_tool_result",
            "on_before_tool_use",
            "on_after_tool_use",
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

        if match is not None and not callable(match):
            self.log(
                f"register_hook: match is not callable, ignoring: {match!r}",
                "warning",
            )
            match = None

        self._hook_registry.register(
            hook_name, callback, plugin_id=self._plugin_id, match=match
        )
        timeout = self._manifest.hook_timeout
        self._hook_registry.set_timeout(hook_name, self._plugin_id, timeout)
        self._registered_hooks.append(hook_name)

    # --- API routes (advanced) ---

    def register_api_routes(self, router) -> None:
        if not self._check_permission("routes.register"):
            return
        # ``_admin/*`` is a reserved namespace owned by the host's plugin
        # management API (see ``src/openakita/api/routes/plugins.py``). Drop
        # any plugin route that would shadow / collide with it before mounting,
        # and warn loudly so the developer knows to rename.
        self._strip_reserved_admin_routes(router)
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
        self.log(f"API app not yet available, routes queued for /api/plugins/{self._plugin_id}")

    def _strip_reserved_admin_routes(self, router) -> None:
        """Remove any plugin route under the reserved ``_admin/*`` prefix.

        Plugins must NOT register routes under ``_admin/`` because the host
        already exposes its plugin-management API there
        (``/api/plugins/{plugin_id}/_admin/...``). Allowing both would
        re-introduce the FastAPI route-shadowing bug that caused plugin
        ``GET /tasks`` endpoints to be silently masked by the host's
        ``GET /{plugin_id}/_admin/spawned-tasks`` (formerly ``/tasks``).
        """
        routes = getattr(router, "routes", None)
        if not routes:
            return
        kept = []
        dropped: list[str] = []
        for route in routes:
            path = getattr(route, "path", "") or ""
            normalized = path if path.startswith("/") else "/" + path
            if normalized == "/_admin" or normalized.startswith("/_admin/"):
                dropped.append(path)
                continue
            kept.append(route)
        if dropped:
            router.routes = kept
            self.log(
                "Refused to register reserved-namespace routes "
                f"(prefix /_admin is owned by the host): {dropped}",
                "warning",
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
            self.log(f"Registered memory backend (replace={replace_mode})")
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

    # --- File serving utilities (Plugin 2.0) ---

    def create_file_response(
        self,
        source: str | Path,
        *,
        filename: str | None = None,
        media_type: str = "application/octet-stream",
        as_download: bool = False,
    ):
        """Create a FastAPI response for serving a file, handling encoding correctly.

        Works with both local file paths and remote URLs. Automatically handles:
        - Content-Disposition with RFC 5987 encoding for non-ASCII filenames
        - Local file serving via FileResponse
        - Remote URL streaming via StreamingResponse

        Args:
            source: Local file path (str/Path) or remote URL (http/https).
            filename: Download filename. If None, derived from source.
            media_type: MIME type. Default: application/octet-stream.
            as_download: If True, adds Content-Disposition: attachment header.

        Returns:
            A FileResponse or StreamingResponse ready to return from a route.
        """
        from urllib.parse import quote
        from fastapi.responses import FileResponse, StreamingResponse

        headers: dict[str, str] = {}
        source_str = str(source)

        if as_download:
            raw_name = filename or Path(source_str).name or "download"
            ascii_safe = raw_name.encode("ascii", "replace").decode("ascii")
            headers["Content-Disposition"] = (
                f'attachment; filename="{ascii_safe}"; '
                f"filename*=UTF-8''{quote(raw_name)}"
            )

        if source_str.startswith("http://") or source_str.startswith("https://"):
            import httpx

            async def _stream():
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("GET", source_str) as resp:
                        async for chunk in resp.aiter_bytes(8192):
                            yield chunk

            return StreamingResponse(_stream(), media_type=media_type, headers=headers)

        local_path = Path(source_str)
        if not local_path.is_file():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(str(local_path), media_type=media_type, headers=headers)

    # --- UI event methods (Plugin 2.0) ---

    @property
    def ui_api_version(self) -> str:
        """Current host UI API version (safe to read even from 1.0 plugins)."""
        return PLUGIN_UI_API_VERSION

    def register_ui_event_handler(
        self, event_type: str, handler: Callable, **kwargs: Any,
    ) -> None:
        """Register a handler for bridge events sent from the plugin UI."""
        handlers: dict = self._host.get("_ui_event_handlers", {})
        handlers.setdefault(self._plugin_id, {})[event_type] = handler
        self._host["_ui_event_handlers"] = handlers
        self.log(f"Registered UI event handler for '{event_type}'")

    def broadcast_ui_event(self, event_type: str, data: dict, **kwargs: Any) -> None:
        """Push an event to the plugin UI via the WebSocket bridge."""
        import asyncio

        gateway = self._host.get("gateway")
        if gateway is None:
            return

        event_payload = {
            "type": f"plugin:{self._plugin_id}:{event_type}",
            "data": data,
        }

        async def _push() -> None:
            try:
                await gateway.broadcast(event_payload)
            except Exception as e:
                self.log(f"broadcast_ui_event failed: {e}", "warning")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_push())
        except RuntimeError:
            self.log("No event loop for broadcast_ui_event", "warning")

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
            self._cleanup_routes()
        except Exception as e:
            logger.debug("Plugin '%s' route cleanup error: %s", self._plugin_id, e)

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
                    s for s in external_sources if getattr(s, "_plugin_id", None) == self._plugin_id
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

    def _cleanup_routes(self) -> None:
        """Remove API routes registered by this plugin from the FastAPI app."""
        api_server = self._host.get("api_app")
        if api_server is None:
            return
        prefix = f"/api/plugins/{self._plugin_id}"
        original_routes = api_server.routes[:]
        removed = 0
        for route in original_routes:
            route_path = getattr(route, "path", "")
            route_prefix = getattr(route, "prefix", "")
            if route_path.startswith(prefix) or route_prefix.startswith(prefix):
                try:
                    api_server.routes.remove(route)
                    removed += 1
                except ValueError:
                    pass
        if removed:
            self.log(f"Removed {removed} API routes under {prefix}")

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
                d
                for d in tool_defs
                if d.get("name", d.get("function", {}).get("name", "")) in registered
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
        """Synchronous fallback: just remove the server entry without disconnecting.

        The async variant ``_aclose_mcp`` performs a graceful disconnect.
        This sync method is kept so the legacy ``_cleanup()`` path
        (used in error/teardown branches that don't have an event loop) still
        de-registers the server entry, even if the actual subprocess can't be
        torn down cleanly here.
        """
        mcp_client = self._host.get("mcp_client")
        if mcp_client is None:
            return
        server_name = self._plugin_id
        if not hasattr(mcp_client, "get_server") or mcp_client.get_server(server_name) is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Best-effort schedule; aclose() should be preferred for awaited cleanup.
            loop.create_task(self._aclose_mcp())
            return
        if hasattr(mcp_client, "remove_server"):
            try:
                mcp_client.remove_server(server_name)
            except Exception:
                pass

    async def _aclose_mcp(self) -> None:
        """Awaitable MCP cleanup: disconnect subprocess, then drop server entry."""
        mcp_client = self._host.get("mcp_client")
        if mcp_client is None:
            return
        server_name = self._plugin_id
        if not hasattr(mcp_client, "get_server") or mcp_client.get_server(server_name) is None:
            return
        try:
            if hasattr(mcp_client, "disconnect"):
                await mcp_client.disconnect(server_name)
        except Exception as e:
            logger.debug("Plugin '%s' MCP disconnect error: %s", self._plugin_id, e)
        if hasattr(mcp_client, "remove_server"):
            try:
                mcp_client.remove_server(server_name)
            except Exception:
                pass

    # --- Background task tracking ---

    def spawn_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Schedule a background task tied to this plugin's lifecycle.

        Tasks registered via this helper are cancelled (and awaited) when the
        plugin is unloaded, preventing leaked workers that keep file handles
        and network connections alive.

        Plugins **must** use this instead of raw ``asyncio.create_task`` for
        any long-running background work (poll loops, schedulers, etc.).
        """
        if not inspect.iscoroutine(coro):
            raise TypeError(
                f"spawn_task expects a coroutine, got {type(coro).__name__}"
            )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise RuntimeError(
                f"Plugin '{self._plugin_id}': spawn_task requires a running event loop"
            ) from e
        task_name = name or f"plugin:{self._plugin_id}:bg"
        task = loop.create_task(coro, name=task_name)
        self._spawned_tasks.add(task)
        task.add_done_callback(self._spawned_tasks.discard)
        return task

    async def _cancel_spawned_tasks(self, *, timeout: float = 5.0) -> None:
        """Cancel all background tasks registered via ``spawn_task``."""
        if not self._spawned_tasks:
            return
        pending = [t for t in list(self._spawned_tasks) if not t.done()]
        for t in pending:
            t.cancel()
        if not pending:
            return
        try:
            await asyncio.wait(pending, timeout=timeout)
        except Exception as e:
            logger.debug(
                "Plugin '%s' background task cancel error: %s", self._plugin_id, e
            )

    def list_spawned_tasks(self) -> list[dict[str, Any]]:
        """Diagnostics helper: snapshot of background tasks for this plugin."""
        out: list[dict[str, Any]] = []
        for t in list(self._spawned_tasks):
            coro = t.get_coro()
            coro_name = (
                getattr(coro, "__qualname__", None)
                or getattr(coro, "__name__", "")
                or repr(coro)
            )
            out.append(
                {
                    "name": t.get_name(),
                    "done": t.done(),
                    "cancelled": t.cancelled(),
                    "coro": str(coro_name)[:200],
                }
            )
        return out

    async def aclose(self) -> None:
        """Awaitable, ordered teardown — preferred entry point for unload.

        Steps:
          1. Cancel & await background tasks scheduled via ``spawn_task``.
          2. Gracefully disconnect MCP subprocess (if any).
          3. Run the synchronous ``_cleanup`` for routes/tools/hooks/channels.
        """
        try:
            await self._cancel_spawned_tasks()
        except Exception as e:
            logger.debug("Plugin '%s' task cancel error: %s", self._plugin_id, e)
        try:
            await self._aclose_mcp()
        except Exception as e:
            logger.debug("Plugin '%s' mcp aclose error: %s", self._plugin_id, e)
        try:
            self._cleanup()
        except Exception as e:
            logger.debug("Plugin '%s' sync cleanup error: %s", self._plugin_id, e)

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

    _ALLOWED = frozenset(
        {
            "load_skill",
            "unload_skill",
            "get_tool_definitions",
            "get_skill",
            "get_skill_body",
            "loaded_count",
        }
    )

    def __init__(self, real_loader: Any, plugin_id: str) -> None:
        self._real = real_loader
        self._plugin_id = plugin_id

    def __getattr__(self, name: str) -> Any:
        if name in self._ALLOWED:
            return getattr(self._real, name)
        logger.warning(
            "[ScopedSkillLoader] Plugin '%s' tried to access '%s' — blocked",
            self._plugin_id,
            name,
        )
        raise AttributeError(
            f"ScopedSkillLoader does not expose '{name}'. Allowed: {sorted(self._ALLOWED)}"
        )


class PluginBase(ABC):
    """Base class for Python plugins.

    Subclass this and implement ``on_load``.
    Optionally override ``on_unload`` for cleanup.

    ``on_unload`` may be either a synchronous method or an ``async def``
    coroutine — the framework awaits it on the main event loop so plugins can
    cleanly close database connections, HTTP clients, etc.
    """

    @abstractmethod
    def on_load(self, api: PluginAPI) -> None:
        """Called when the plugin is loaded. Register capabilities here."""

    def on_unload(self) -> Any:  # noqa: B027
        """Called when the plugin is being unloaded. Clean up resources.

        May return ``None`` (sync cleanup) or an awaitable / coroutine
        (``async def on_unload``) — the framework will detect and await it.
        """
