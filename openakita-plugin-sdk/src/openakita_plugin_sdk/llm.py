"""LLM provider abstractions for LLM protocol plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract base for LLM providers.

    Mirrors ``openakita.llm.providers.base.LLMProvider`` so plugin authors
    can implement new wire protocols without installing the full runtime.

    A plugin registers two things:

    1. A **provider class** (this) via ``api.register_llm_provider(api_type, cls)``
       — handles the actual API calls for a new ``api_type``.
    2. A **registry entry** via ``api.register_llm_registry(slug, registry)``
       — provides model discovery and default configuration.
    """

    @abstractmethod
    def __init__(self, config: Any) -> None:
        """Initialize with an EndpointConfig."""

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs: Any) -> Any:
        """Send a chat completion request and return the response."""

    @abstractmethod
    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        """Send a streaming chat completion request, yielding chunks."""


class ProviderRegistryInfo:
    """Metadata for a provider registry entry.

    Matches the shape expected by ``api.register_llm_registry()``.
    """

    def __init__(
        self,
        slug: str,
        name: str,
        api_type: str,
        default_base_url: str = "",
        api_key_env: str = "",
    ) -> None:
        self.slug = slug
        self.name = name
        self.api_type = api_type
        self.default_base_url = default_base_url
        self.api_key_env = api_key_env


class ProviderRegistry:
    """Skeleton provider registry for SDK usage.

    Plugin authors should subclass and implement ``list_models()`` to provide
    model discovery. The registry is registered via
    ``api.register_llm_registry(slug, registry_instance)``.
    """

    def __init__(self, info: ProviderRegistryInfo) -> None:
        self.info = info

    def list_models(self) -> list[dict]:
        """Return available models. Override in subclass."""
        return []
