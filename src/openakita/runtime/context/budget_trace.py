"""Context budget and payload-size helpers.

The runtime uses three small pure helpers for sizing decisions:

* ``_calc_context_budget`` -- derive an effective context budget
  from an endpoint's declared window + output reserve;
* ``estimate_tokens`` -- shared (CJK-aware) token estimator;
* ``_payload_size_bytes`` -- JSON-serialised byte size of a message
  list.

They are module-level functions so callers do not have to instantiate a
``ContextManager`` for simple arithmetic.
"""

from __future__ import annotations

import json
from typing import Any

from openakita.core.context_utils import estimate_tokens as _shared_estimate_tokens

DEFAULT_MAX_CONTEXT_TOKENS = 100_000


def calc_context_budget(endpoint: Any, fallback_window: int) -> int:
    """Return the effective context-window budget for ``endpoint``.

    Implements the ``ContextManager._calc_context_budget`` contract:

    * declared ``context_window`` < 8192 -> fall back to
      ``fallback_window`` (typically the model's default);
    * reserve up to ``max_tokens`` (capped at ``window / 3``) for
      generation;
    * apply a 5 % safety margin on the remaining input budget;
    * never return less than 4096 -- fall through to
      :data:`DEFAULT_MAX_CONTEXT_TOKENS` instead.
    """
    ctx = getattr(endpoint, "context_window", 0) or 0
    if ctx < 8192:
        ctx = fallback_window
    output_reserve = getattr(endpoint, "max_tokens", None) or 4096
    output_reserve = min(output_reserve, ctx // 3)
    result = int((ctx - output_reserve) * 0.95)
    if result < 4096:
        return DEFAULT_MAX_CONTEXT_TOKENS
    return result


def estimate_tokens(text: str) -> int:
    """CJK-aware token estimator; re-anchored shared estimator."""
    return _shared_estimate_tokens(text)


def payload_size_bytes(messages: list[dict]) -> int:
    """Return the JSON-serialised byte size of a message list."""
    return sum(
        len(json.dumps(msg, ensure_ascii=False, default=str).encode("utf-8")) for msg in messages
    )


__all__ = [
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "calc_context_budget",
    "estimate_tokens",
    "payload_size_bytes",
]
