"""V2 context helpers extracted from ``core.context_manager`` (P-RC-4 P4.13).

Three focused submodules host the leaf-level concerns lifted from
the legacy 1799-LOC ContextManager:

* :mod:`runtime.context.grouping` -- :func:`group_messages`
* :mod:`runtime.context.budget_trace` -- :func:`calc_context_budget`,
  :func:`estimate_tokens`, :func:`payload_size_bytes`
* :mod:`runtime.context.compress` -- :func:`pre_request_cleanup`,
  :func:`sanitize_tool_pairs`
"""

from __future__ import annotations

from .budget_trace import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    calc_context_budget,
    estimate_tokens,
    payload_size_bytes,
)
from .compress import pre_request_cleanup, sanitize_tool_pairs
from .grouping import group_messages

__all__ = [
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "calc_context_budget",
    "estimate_tokens",
    "group_messages",
    "payload_size_bytes",
    "pre_request_cleanup",
    "sanitize_tool_pairs",
]
