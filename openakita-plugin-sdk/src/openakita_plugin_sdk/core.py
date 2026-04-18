"""Core plugin abstractions — PluginBase, PluginAPI (abstract), PluginManifest.

PluginAPI v1.0 covers tool/channel/memory/hook/llm/rag registration.
PluginAPI v2.0 (Plugin 2.0) adds UI-related methods: file responses,
UI event broadcasting, and UI event handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PluginManifest:
    """Parsed plugin.json metadata."""

    id: str
    name: str
    version: str
    plugin_type: str
    entry: str = "plugin.py"
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    permissions: list[str] = field(default_factory=list)
    requires: dict[str, Any] = field(default_factory=dict)
    provides: dict[str, Any] = field(default_factory=dict)
    replaces: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str = ""
    display_name_zh: str = ""
    display_name_en: str = ""
    description_i18n: dict[str, str] = field(default_factory=dict)
    load_timeout: float = 10.0
    hook_timeout: float = 5.0
    retrieve_timeout: float = 3.0
    ui: dict[str, Any] | None = None


class PluginAPI(ABC):
    """Abstract PluginAPI — the interface handle plugins interact with.

    The runtime provides a concrete implementation. SDK users can use this
    for type hints and MockPluginAPI for testing.

    v1.0 methods: log, config, data, tools, hooks, channels, memory, llm, rag, search.
    v2.0 methods (Plugin 2.0): create_file_response, broadcast_ui_event,
        register_ui_event_handler, ui_api_version.
    """

    # --- Basic tier (auto-granted) ---

    @abstractmethod
    def log(self, msg: str, level: str = "info") -> None: ...

    @abstractmethod
    def log_error(self, msg: str, exc: Exception | None = None) -> None: ...

    @abstractmethod
    def log_debug(self, msg: str) -> None: ...

    @abstractmethod
    def get_config(self) -> dict: ...

    @abstractmethod
    def set_config(self, updates: dict) -> None: ...

    @abstractmethod
    def get_data_dir(self) -> Path | None: ...

    # --- Tool registration ---

    @abstractmethod
    def register_tools(
        self, definitions: list[dict], handler: Callable
    ) -> None: ...

    # --- Hook registration ---

    @abstractmethod
    def register_hook(self, hook_name: str, callback: Callable) -> None: ...

    # --- Route registration ---

    @abstractmethod
    def register_api_routes(self, router: Any) -> None: ...

    # --- Channel registration ---

    @abstractmethod
    def register_channel(self, type_name: str, factory: Callable) -> None: ...

    # --- Memory / Search / Retrieval ---

    @abstractmethod
    def register_memory_backend(self, backend: Any) -> None: ...

    @abstractmethod
    def register_search_backend(self, name: str, backend: Any) -> None: ...

    @abstractmethod
    def register_retrieval_source(self, source: Any) -> None: ...

    # --- LLM provider ---

    @abstractmethod
    def register_llm_provider(self, api_type: str, provider_class: type) -> None: ...

    @abstractmethod
    def register_llm_registry(self, slug: str, registry: Any) -> None: ...

    # --- Host service access ---

    @abstractmethod
    def get_brain(self) -> Any: ...

    @abstractmethod
    def get_memory_manager(self) -> Any: ...

    @abstractmethod
    def get_vector_store(self) -> Any: ...

    @abstractmethod
    def get_settings(self) -> Any: ...

    @abstractmethod
    def send_message(self, channel: str, chat_id: str, text: str) -> None: ...

    # --- Plugin 2.0: UI support ---

    @property
    def ui_api_version(self) -> str:
        """Current host UI API version string (e.g. '1.0.0').

        Safe to read even from v1.0 plugins — returns '0.0.0' if the host
        does not support Plugin 2.0 UI features.
        """
        return "0.0.0"

    def create_file_response(
        self,
        source: str | Path,
        *,
        filename: str | None = None,
        media_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Create a streaming file download response (FastAPI FileResponse).

        Handles UTF-8 filenames via RFC 5987 encoding automatically.
        Requires permission: ``api_routes.register``.

        Args:
            source: Path to the file on disk.
            filename: Download filename (defaults to basename of source).
            media_type: MIME type (auto-detected if omitted).
            headers: Extra response headers.

        Returns:
            A FastAPI FileResponse suitable for returning from a route handler.
        """
        raise NotImplementedError("Host does not support create_file_response")

    def broadcast_ui_event(self, event_type: str, data: dict, **kwargs: Any) -> None:
        """Push an event to the plugin's frontend UI via the Bridge protocol.

        The frontend receives this as a ``bridge:event`` postMessage.
        Requires an active WebSocket connection from the desktop app.

        Args:
            event_type: Event name (e.g. 'task_complete', 'progress').
            data: JSON-serializable payload.
        """

    def register_ui_event_handler(
        self, event_type: str, handler: Callable, **kwargs: Any,
    ) -> None:
        """Register a handler for events sent from the plugin's frontend UI.

        Args:
            event_type: Event name to listen for.
            handler: Async callable ``(data: dict) -> None``.
        """


class PluginBase(ABC):
    """Base class for Python plugins.

    The entry file MUST export a class named ``Plugin`` that inherits from
    ``PluginBase``. Any other name will cause a load failure.

    ``on_load`` is called synchronously by the host — avoid long blocking
    operations (exceeding ``load_timeout`` causes termination).
    ``on_unload`` is optional with a default no-op.
    """

    @abstractmethod
    def on_load(self, api: PluginAPI) -> None:
        """Called when plugin is loaded. Register all capabilities here."""

    def on_unload(self) -> None:  # noqa: B027
        """Called when plugin is being unloaded. Override for cleanup."""
