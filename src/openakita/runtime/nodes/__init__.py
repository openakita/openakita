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
from .condition_node import BranchInputs, ConditionNode, ConditionPredicate
from .human_review_node import (
    HumanReviewNode,
    InMemoryReviewQueue,
    ReviewDecision,
    ReviewQueue,
    ReviewVerdict,
)
from .llm_node import (
    BrainPrompt,
    BrainResponse,
    LLMNode,
    NodeBrain,
    ToolCallRequest,
    TranscriptTurn,
)
from .manifest import (
    WorkbenchManifest,
    WorkbenchManifestError,
    WorkbenchMode,
    WorkbenchUI,
)
from .tool_node import ToolInvocation, ToolNode, ToolResult, ToolRunner
from .workbench_node import WorkbenchNode

__all__ = [
    "BaseNode",
    "BranchInputs",
    "BrainPrompt",
    "BrainResponse",
    "ConditionNode",
    "ConditionPredicate",
    "HumanReviewNode",
    "InMemoryReviewQueue",
    "LLMNode",
    "NodeBrain",
    "NodeContext",
    "NodeLifecycleEvent",
    "NodeProtocol",
    "NodeRegistration",
    "ReviewDecision",
    "ReviewQueue",
    "ReviewVerdict",
    "ToolCallRequest",
    "ToolInvocation",
    "ToolNode",
    "ToolResult",
    "ToolRunner",
    "TranscriptTurn",
    "WorkbenchManifest",
    "WorkbenchManifestError",
    "WorkbenchMode",
    "WorkbenchNode",
    "WorkbenchUI",
]
