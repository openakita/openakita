"""Testing helpers for plugin development."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .core import PluginAPI, PluginBase


class MockPluginAPI(PluginAPI):
    """In-memory mock of PluginAPI for unit testing plugins."""

    def __init__(
        self,
        plugin_id: str = "test-plugin",
        granted_permissions: list[str] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.logs: list[tuple[str, str]] = []
        self.config: dict = {}
        self.registered_tools: list[str] = []
        self.registered_hooks: dict[str, list[Callable]] = {}
        self.registered_channels: list[str] = []
        self.registered_retrieval_sources: list = []
        self.registered_llm_providers: dict[str, type] = {}
        self.registered_llm_registries: dict[str, Any] = {}
        self.registered_search_backends: dict[str, Any] = {}
        self.registered_memory_backends: dict[str, Any] = {}
        self.registered_routes: list = []
        self.sent_messages: list[dict] = []
        self._data_dir = Path(tempfile.mkdtemp())
        self.registered_ui_event_handlers: dict[str, list[Callable]] = {}
        self.broadcast_events: list[dict] = []
        # ``None`` means "all permissions granted" so the vast majority of
        # existing tests keep passing unchanged. Pass an explicit list to
        # exercise the not-granted path.
        self._granted_permissions: set[str] | None = (
            set(granted_permissions) if granted_permissions is not None else None
        )

    def log(self, msg: str, level: str = "info") -> None:
        self.logs.append((level, msg))

    def log_error(self, msg: str, exc: Exception | None = None) -> None:
        detail = f"{msg}: {exc}" if exc else msg
        self.logs.append(("error", detail))

    def log_debug(self, msg: str) -> None:
        self.logs.append(("debug", msg))

    def get_config(self) -> dict:
        return dict(self.config)

    def set_config(self, updates: dict) -> None:
        self.config.update(updates)

    def get_data_dir(self) -> Path | None:
        return self._data_dir

    def register_tools(self, definitions: list[dict], handler: Callable) -> None:
        for d in definitions:
            name = d.get("name", d.get("function", {}).get("name", ""))
            self.registered_tools.append(name)

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        self.registered_hooks.setdefault(hook_name, []).append(callback)

    def register_api_routes(self, router: Any) -> None:
        self.registered_routes.append(router)

    def register_channel(self, type_name: str, factory: Callable) -> None:
        self.registered_channels.append(type_name)

    def register_memory_backend(self, backend: Any) -> None:
        self.registered_memory_backends[self.plugin_id] = backend

    def register_search_backend(self, name: str, backend: Any) -> None:
        self.registered_search_backends[name] = backend

    def register_llm_provider(self, api_type: str, provider_class: type) -> None:
        self.registered_llm_providers[api_type] = provider_class

    def register_llm_registry(self, slug: str, registry: Any) -> None:
        self.registered_llm_registries[slug] = registry

    def register_retrieval_source(self, source: Any) -> None:
        self.registered_retrieval_sources.append(source)

    def get_brain(self) -> Any:
        return None

    def get_memory_manager(self) -> Any:
        return None

    def get_vector_store(self) -> Any:
        return None

    def get_settings(self) -> Any:
        return None

    def send_message(self, channel: str, chat_id: str, text: str) -> None:
        self.sent_messages.append({"channel": channel, "chat_id": chat_id, "text": text})

    def has_permission(self, name: str) -> bool:
        if self._granted_permissions is None:
            return True
        return name in self._granted_permissions

    # --- Plugin 2.0: UI support ---

    def broadcast_ui_event(self, event_type: str, data: dict, **kwargs: Any) -> None:
        self.broadcast_events.append({"type": event_type, "data": data})

    def register_ui_event_handler(
        self, event_type: str, handler: Callable, **kwargs: Any,
    ) -> None:
        self.registered_ui_event_handlers.setdefault(event_type, []).append(handler)


def assert_plugin_loads(plugin: PluginBase, api: MockPluginAPI | None = None) -> MockPluginAPI:
    """Assert a plugin loads without errors. Returns the API for inspection."""
    if api is None:
        api = MockPluginAPI()
    plugin.on_load(api)
    error_logs = [msg for level, msg in api.logs if level == "error"]
    assert not error_logs, f"Plugin logged errors during on_load: {error_logs}"
    return api
