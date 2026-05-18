"""Runtime nodes for the v2 fork.

Per ADR-0007, every behaviour-bearing component in a v2 organization
implements the :class:`NodeProtocol` defined in :mod:`base`. The
supervisor (ADR-0004) and messenger (ADR-0006/ADR-0007) drive nodes
exclusively through that protocol; nothing in the supervisor reaches
into a node's internals.

This package is populated incrementally during Phase 4. Each node type
lives in its own module so future authors only have to read one file
to understand a single concern.
"""

from __future__ import annotations

from .base import (
    BaseNode,
    NodeContext,
    NodeLifecycleEvent,
    NodeProtocol,
    NodeRegistration,
)
from .tool_node import ToolInvocation, ToolNode, ToolResult, ToolRunner

__all__ = [
    "BaseNode",
    "NodeContext",
    "NodeLifecycleEvent",
    "NodeProtocol",
    "NodeRegistration",
    "ToolInvocation",
    "ToolNode",
    "ToolResult",
    "ToolRunner",
]
