"""
Provider registry base class.

Defines the interface that all provider registries must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

_shared_registry_client: httpx.AsyncClient | None = None


def get_registry_client() -> httpx.AsyncClient:
    """Get the shared registry httpx client (connection pool reuse, avoids creating/destroying per request)."""
    global _shared_registry_client
    if _shared_registry_client is None or _shared_registry_client.is_closed:
        _shared_registry_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=10,
            ),
        )
    return _shared_registry_client


@dataclass
class ProviderInfo:
    """Provider information."""

    name: str  # Display name
    slug: str  # Identifier (anthropic, dashscope, ...)
    api_type: str  # "anthropic" | "openai"
    default_base_url: str  # Default API endpoint
    api_key_env_suggestion: str  # Suggested environment variable name
    supports_model_list: bool  # Whether the model list API is supported
    supports_capability_api: bool  # Whether the API returns capability info
    requires_api_key: bool = True  # Whether an API key is required (False for local services like Ollama)
    is_local: bool = False  # Whether this is a local provider
    coding_plan_base_url: str | None = None  # Dedicated API endpoint for Coding Plan (None means unsupported)
    coding_plan_api_type: str | None = (
        None  # Protocol type under Coding Plan mode (None means same as api_type)
    )
    note: str | None = None  # Frontend i18n key — provider note (e.g., "NVIDIA free model output limits")


@dataclass
class ModelInfo:
    """Model information."""

    id: str  # Model ID (qwen-max, claude-3-opus, ...)
    name: str  # Display name
    capabilities: dict = field(default_factory=dict)  # {"text": True, "vision": True, ...}
    context_window: int | None = None  # Context window
    max_output_tokens: int | None = None
    pricing: dict | None = None  # Pricing information
    thinking_only: bool = False  # Whether only thinking mode is supported


class ProviderRegistry(ABC):
    """Provider registry base class."""

    info: ProviderInfo

    @abstractmethod
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        Get the list of available models.

        Args:
            api_key: API Key

        Returns:
            List of models
        """
        pass

    def get_model_capabilities(self, model_id: str) -> dict:
        """
        Get model capabilities.

        Priority: API response > built-in capability table > defaults
        """
        from ..capabilities import infer_capabilities

        return infer_capabilities(model_id, provider_slug=self.info.slug)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} slug={self.info.slug}>"
