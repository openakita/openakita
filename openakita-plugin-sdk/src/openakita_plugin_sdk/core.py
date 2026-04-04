"""Core plugin abstractions — PluginBase, PluginAPI (abstract), PluginManifest."""

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
    load_timeout: float = 10.0
    hook_timeout: float = 5.0
    retrieve_timeout: float = 3.0


class PluginAPI(ABC):
    """Abstract PluginAPI — the interface handle plugins interact with.

    The runtime provides a concrete implementation. SDK users can use this
    for type hints and MockPluginAPI for testing.
    """

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

    @abstractmethod
    def register_tools(
        self, definitions: list[dict], handler: Callable
    ) -> None: ...

    @abstractmethod
    def register_hook(self, hook_name: str, callback: Callable) -> None: ...

    @abstractmethod
    def register_api_routes(self, router: Any) -> None: ...

    @abstractmethod
    def register_channel(self, type_name: str, factory: Callable) -> None: ...

    @abstractmethod
    def register_memory_backend(self, backend: Any) -> None: ...

    @abstractmethod
    def register_search_backend(self, name: str, backend: Any) -> None: ...

    @abstractmethod
    def register_llm_provider(self, api_type: str, provider_class: type) -> None: ...

    @abstractmethod
    def register_llm_registry(self, slug: str, registry: Any) -> None: ...

    @abstractmethod
    def register_retrieval_source(self, source: Any) -> None: ...

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


class PluginBase(ABC):
    """Base class for Python plugins. Subclass and implement ``on_load``."""

    @abstractmethod
    def on_load(self, api: PluginAPI) -> None:
        """Called when plugin is loaded. Register all capabilities here."""

    def on_unload(self) -> None:  # noqa: B027
        """Called when plugin is being unloaded. Override for cleanup."""
