"""
Model capabilities registry

Centrally manages model metadata such as context windows, output limits,
and capability flags, eliminating hardcoded magic numbers scattered
throughout the codebase.

Supports:
- Exact match -> prefix match -> default fallback (three-level lookup)
- Runtime registration of custom models
- Thinking budget range queries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCapabilities:
    """Model capability description."""

    context_window: int = 200_000
    max_output_tokens: int = 16_384
    default_output_tokens: int = 4_096
    supports_thinking: bool = False
    supports_vision: bool = True
    supports_tools: bool = True
    supports_cache: bool = False
    supports_streaming: bool = True
    thinking_budget_range: tuple[int, int] = (0, 0)


_DEFAULT = ModelCapabilities()

# Built-in model registry
_REGISTRY: dict[str, ModelCapabilities] = {
    # ── Anthropic Claude 4.x ──
    "claude-sonnet-4-20250514": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=64_000,
        default_output_tokens=16_384,
        supports_thinking=True,
        supports_cache=True,
        thinking_budget_range=(1024, 128_000),
    ),
    "claude-opus-4-20250514": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=32_000,
        default_output_tokens=16_384,
        supports_thinking=True,
        supports_cache=True,
        thinking_budget_range=(1024, 128_000),
    ),
    # ── Anthropic Claude 3.5/3.7 ──
    "claude-3-7-sonnet": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=64_000,
        default_output_tokens=8_192,
        supports_thinking=True,
        supports_cache=True,
        thinking_budget_range=(1024, 128_000),
    ),
    "claude-3-5-sonnet": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=8_192,
        default_output_tokens=4_096,
        supports_cache=True,
    ),
    "claude-3-5-haiku": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=8_192,
        default_output_tokens=4_096,
        supports_cache=True,
    ),
    # ── OpenAI GPT-4o ──
    "gpt-4o": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        default_output_tokens=4_096,
    ),
    "gpt-4o-mini": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        default_output_tokens=4_096,
    ),
    # ── OpenAI o-series ──
    "o1": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=100_000,
        default_output_tokens=16_384,
        supports_thinking=True,
    ),
    "o3": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=100_000,
        default_output_tokens=16_384,
        supports_thinking=True,
    ),
    "o3-mini": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=65_536,
        default_output_tokens=16_384,
        supports_thinking=True,
    ),
    "o4-mini": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=100_000,
        default_output_tokens=16_384,
        supports_thinking=True,
    ),
    # ── DeepSeek ──
    "deepseek-chat": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=8_192,
        default_output_tokens=4_096,
    ),
    "deepseek-reasoner": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        default_output_tokens=8_192,
        supports_thinking=True,
    ),
    # ── Qwen ──
    "qwen-max": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=8_192,
        default_output_tokens=4_096,
    ),
    "qwen-plus": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=8_192,
        default_output_tokens=4_096,
    ),
    "qwen3-235b-a22b": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        default_output_tokens=8_192,
        supports_thinking=True,
    ),
    "qwen3.5-plus": ModelCapabilities(
        context_window=131_072,
        max_output_tokens=16_384,
        default_output_tokens=8_192,
        supports_thinking=True,
        thinking_budget_range=(1024, 38_000),
    ),
    "qwen-max-thinking": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        default_output_tokens=8_192,
        supports_thinking=True,
    ),
    # ── GLM ──
    "glm-4-plus": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=4_096,
        default_output_tokens=4_096,
    ),
}

# User-registered models at runtime
_CUSTOM_REGISTRY: dict[str, ModelCapabilities] = {}


def get_model_capabilities(model: str) -> ModelCapabilities:
    """Look up model capabilities.

    Lookup order: custom registration -> exact match -> prefix match -> default.
    """
    if not model:
        return _DEFAULT

    model_lower = model.lower()

    # 1. Custom registry (exact)
    if model in _CUSTOM_REGISTRY:
        return _CUSTOM_REGISTRY[model]

    # 2. Built-in exact match
    if model in _REGISTRY:
        return _REGISTRY[model]

    # 3. Case-insensitive exact
    for key, caps in _REGISTRY.items():
        if key.lower() == model_lower:
            return caps

    # 4. Prefix match (longest wins)
    best_match: str | None = None
    best_len = 0
    for key in _REGISTRY:
        if model_lower.startswith(key.lower()) and len(key) > best_len:
            best_match = key
            best_len = len(key)

    if best_match:
        return _REGISTRY[best_match]

    # 5. Default
    return _DEFAULT


def register_model(model: str, capabilities: ModelCapabilities) -> None:
    """Register custom model capabilities at runtime."""
    _CUSTOM_REGISTRY[model] = capabilities
    logger.debug("Registered model capabilities for %s", model)


def get_context_window(model: str) -> int:
    """Return the context window size for a model."""
    return get_model_capabilities(model).context_window


def get_max_output_tokens(model: str) -> int:
    """Return the maximum output token count for a model."""
    return get_model_capabilities(model).max_output_tokens


def get_default_output_tokens(model: str) -> int:
    """Return the default output token count for a model."""
    return get_model_capabilities(model).default_output_tokens


def get_thinking_budget(model: str, depth: str | None = None) -> int:
    """Return the thinking budget for a model and depth level.

    Args:
        model: Model name.
        depth: 'low' / 'medium' / 'high' / None (defaults to medium).
    """
    caps = get_model_capabilities(model)
    if not caps.supports_thinking or caps.thinking_budget_range == (0, 0):
        return 0

    low, high = caps.thinking_budget_range
    depth_map = {
        "low": low,
        "medium": (low + high) // 2,
        "high": high,
    }
    return depth_map.get(depth or "medium", (low + high) // 2)
