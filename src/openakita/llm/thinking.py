"""Shared thinking-depth normalization helpers for LLM providers."""

from __future__ import annotations

_THINKING_BUDGET_BY_DEPTH = {
    "low": 1024,
    "medium": 4096,
    "high": 16384,
    # User-facing "max" maps to the largest broadly-supported token budget.
    # Provider-specific enum support is handled by reasoning_effort_for_depth().
    "max": 16384,
    "xhigh": 16384,
}


def normalize_thinking_depth(depth: object) -> str | None:
    """Normalize OpenAkita's user-facing thinking depth aliases."""
    value = str(depth or "").strip().lower()
    if value == "xhigh":
        return "max"
    if value in {"low", "medium", "high", "max"}:
        return value
    return None


def thinking_budget_for_depth(depth: object) -> int | None:
    """Return a conservative thinking_budget for providers using token budgets."""
    normalized = normalize_thinking_depth(depth)
    return _THINKING_BUDGET_BY_DEPTH.get(normalized or "")


def supports_max_reasoning_effort(provider: str, base_url: str, model: str) -> bool:
    """Whether this OpenAI-compatible endpoint documents reasoning_effort=max."""
    provider_l = (provider or "").lower()
    base_l = (base_url or "").lower()
    model_l = (model or "").lower()
    return (
        model_l == "deepseek-v4-pro"
        and (provider_l == "deepseek" or "api.deepseek.com" in base_l)
    )


def is_minimax_endpoint(provider: str, base_url: str, model: str) -> bool:
    """Whether this endpoint targets MiniMax."""
    provider_l = (provider or "").lower()
    base_l = (base_url or "").lower()
    model_l = (model or "").lower()
    return (
        provider_l in {"minimax", "minimax-cn", "minimax-int"}
        or "minimax" in provider_l
        or "minimaxi" in base_l
        or "minimax.io" in base_l
        or "minimax" in model_l
    )


def minimax_thinking_depth(depth: object) -> str | None:
    """Map OpenAkita thinking depth to MiniMax's documented low/medium/high enum."""
    normalized = normalize_thinking_depth(depth)
    if not normalized:
        return None
    if normalized == "max":
        return "high"
    return normalized


def reasoning_effort_for_depth(
    *,
    provider: str,
    base_url: str,
    model: str,
    depth: object,
    allow_max_effort: bool = True,
) -> str | None:
    """Map OpenAkita thinking depth to a provider-safe reasoning_effort."""
    normalized = normalize_thinking_depth(depth)
    if not normalized:
        return None
    supports_max = allow_max_effort and supports_max_reasoning_effort(provider, base_url, model)
    if normalized == "max":
        return "max" if supports_max else "high"
    return "high" if supports_max else normalized
