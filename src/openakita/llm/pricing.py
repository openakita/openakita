"""Built-in default pricing table for LLM endpoints (Fix-5).

This module provides **fallback** prices for popular providers/models. It is
consulted only when an endpoint's own ``pricing_tiers`` is empty — otherwise
user-configured prices always win.

Design constraints (intentionally restrained):

- We DO NOT pretend to know every model's cost. The table covers a handful of
  flagship/widely-used variants per provider; everything else falls back to
  ``None`` so the UI can render "-" instead of a misleading "0.0".
- Prices are quoted **per 1 million tokens** in the original currency
  (matching the on-file ``pricing_tiers`` shape used by ``EndpointConfig``).
  We do NOT auto-convert currencies — the caller knows the endpoint currency.
- Matching is fuzzy (substring on ``model`` lowercased), provider-scoped to
  reduce cross-provider collisions.

Returned shape mirrors a single ``pricing_tiers`` entry::

    {"max_input": -1, "input_price": 1.2, "output_price": 7.2,
     "cache_read_price": 0.12, "currency": "CNY", "source": "builtin"}
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _BuiltinPrice:
    input_price: float
    output_price: float
    currency: str
    cache_read_price: float | None = None  # None ⇒ default to 10% of input
    note: str = ""


# Provider key uses the lowercase ``EndpointConfig.provider`` slug.
# Model key is matched as a **lowercase substring** of ``EndpointConfig.model``;
# longer keys win (most specific match).
#
# All prices are in the listed currency, per **1 million tokens**.
# Numbers below are best-effort recent public list prices and may drift; users
# should override via the endpoint's own ``pricing_tiers`` for billing-grade
# accuracy.
_BUILTIN_PRICES: dict[str, dict[str, _BuiltinPrice]] = {
    "anthropic": {
        "claude-opus-4": _BuiltinPrice(15.0, 75.0, "USD", note="public_list_2025-04"),
        "claude-sonnet-4": _BuiltinPrice(3.0, 15.0, "USD", note="public_list_2025-04"),
        "claude-3-5-sonnet": _BuiltinPrice(3.0, 15.0, "USD"),
        "claude-3-5-haiku": _BuiltinPrice(0.8, 4.0, "USD"),
        "claude-3-opus": _BuiltinPrice(15.0, 75.0, "USD"),
        "claude-3-haiku": _BuiltinPrice(0.25, 1.25, "USD"),
    },
    "openai": {
        "gpt-4o": _BuiltinPrice(2.5, 10.0, "USD"),
        "gpt-4o-mini": _BuiltinPrice(0.15, 0.6, "USD"),
        "o1-preview": _BuiltinPrice(15.0, 60.0, "USD"),
        "o1-mini": _BuiltinPrice(3.0, 12.0, "USD"),
        "gpt-4-turbo": _BuiltinPrice(10.0, 30.0, "USD"),
    },
    "dashscope": {
        "qwen3.5-plus": _BuiltinPrice(2.4, 24.0, "CNY", note="0.0024元/千 输入, 0.024 输出"),
        "qwen3-plus": _BuiltinPrice(2.4, 24.0, "CNY"),
        "qwen-plus": _BuiltinPrice(0.8, 8.0, "CNY"),
        "qwen-max": _BuiltinPrice(20.0, 60.0, "CNY"),
        "qwen-turbo": _BuiltinPrice(0.3, 6.0, "CNY"),
    },
    "deepseek": {
        "deepseek-chat": _BuiltinPrice(2.0, 8.0, "CNY"),
        "deepseek-reasoner": _BuiltinPrice(4.0, 16.0, "CNY"),
        "deepseek-v3": _BuiltinPrice(2.0, 8.0, "CNY"),
    },
    "moonshot": {
        "moonshot-v1-8k": _BuiltinPrice(12.0, 12.0, "CNY"),
        "moonshot-v1-32k": _BuiltinPrice(24.0, 24.0, "CNY"),
        "moonshot-v1-128k": _BuiltinPrice(60.0, 60.0, "CNY"),
    },
    "zhipuai": {
        "glm-4-plus": _BuiltinPrice(50.0, 50.0, "CNY"),
        "glm-4-flash": _BuiltinPrice(0.1, 0.1, "CNY"),
    },
    "openrouter": {
        # OpenRouter routes to many models — only include common cheap+capable
        # picks; users should override for precise billing.
        "claude-3.5-sonnet": _BuiltinPrice(3.0, 15.0, "USD"),
        "gpt-4o": _BuiltinPrice(2.5, 10.0, "USD"),
        "llama-3.1-70b": _BuiltinPrice(0.5, 0.75, "USD"),
    },
    "orcarouter": {
        # OrcaRouter 的 /models 会返回真实定价，此处只放常用模型兜底（按家族前缀子串匹配）
        "gpt-4o": _BuiltinPrice(2.5, 10.0, "USD"),
        "claude-sonnet-4.6": _BuiltinPrice(3.0, 15.0, "USD"),
        "deepseek-v4-pro": _BuiltinPrice(0.442, 0.884, "USD"),
    },
}


def lookup_builtin_price(
    provider: str | None,
    model: str | None,
) -> dict | None:
    """Return a single ``pricing_tiers``-shaped dict, or ``None`` if unknown.

    Selection rule: within the matched provider, prefer the **longest**
    substring key that appears in the lowercased model name. Returns ``None``
    if no provider entry matches or no model substring matches. Callers should
    treat ``None`` as "do not display 0; render '-' in UI".
    """
    if not model:
        return None
    provider_key = (provider or "").strip().lower()
    model_lc = model.strip().lower()

    candidate_buckets: list[dict[str, _BuiltinPrice]] = []
    if provider_key and provider_key in _BUILTIN_PRICES:
        candidate_buckets.append(_BUILTIN_PRICES[provider_key])
    # Cross-provider safety net for unknown provider slugs (e.g. proxies):
    # only consult other buckets when the provider key didn't match anything.
    if not candidate_buckets:
        candidate_buckets = list(_BUILTIN_PRICES.values())

    best_key: str | None = None
    best_price: _BuiltinPrice | None = None
    for bucket in candidate_buckets:
        for key, price in bucket.items():
            if key in model_lc and (best_key is None or len(key) > len(best_key)):
                best_key = key
                best_price = price

    if best_price is None:
        return None

    cache_read = (
        best_price.cache_read_price
        if best_price.cache_read_price is not None
        else round(best_price.input_price * 0.1, 6)
    )
    return {
        "max_input": -1,
        "input_price": best_price.input_price,
        "output_price": best_price.output_price,
        "cache_read_price": cache_read,
        "currency": best_price.currency,
        "source": "builtin",
        "matched_key": best_key,
    }


def list_builtin_prices() -> dict[str, list[dict]]:
    """Diagnostic helper — return the entire built-in table as plain dicts.

    Used by ``GET /api/llm/pricing/builtin`` for the UI to show defaults.
    """
    out: dict[str, list[dict]] = {}
    for provider_key, bucket in _BUILTIN_PRICES.items():
        out[provider_key] = []
        for key, price in bucket.items():
            out[provider_key].append(
                {
                    "model_pattern": key,
                    "input_price": price.input_price,
                    "output_price": price.output_price,
                    "currency": price.currency,
                    "cache_read_price": price.cache_read_price,
                    "note": price.note,
                }
            )
    return out
