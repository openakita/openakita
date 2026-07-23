"""Focused context grouping, budgeting, compression, and continuity helpers.

Three submodules host the leaf-level concerns:

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
from .continuity import (
    CompactionCheckpoint,
    CompactionContribution,
    CompactionContributor,
    ContextEpoch,
    WorkspaceSnapshot,
    capture_workspace_snapshot,
    content_digest,
)
from .grouping import group_messages

__all__ = [
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "calc_context_budget",
    "capture_workspace_snapshot",
    "CompactionCheckpoint",
    "CompactionContribution",
    "CompactionContributor",
    "content_digest",
    "ContextEpoch",
    "estimate_tokens",
    "group_messages",
    "payload_size_bytes",
    "pre_request_cleanup",
    "sanitize_tool_pairs",
    "WorkspaceSnapshot",
]
