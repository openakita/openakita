"""Deterministic single-tool-step node.

Per ADR-0007, a ``ToolNode`` is the simplest concrete node type:

* It has no LLM frontend.
* It does not branch.
* It calls one tool, exactly once, with arguments parsed from the
  delegation instruction (or supplied verbatim by the supervisor).
* It emits a ``tasks`` event for the tool start, a ``tasks`` event for
  the tool result, and the standard lifecycle envelope inherited from
  :class:`BaseNode`.

The node accepts an injected ``tool_runner`` callable so the runtime
does not import any concrete tool registry. Concrete plugin tools
will provide their own ``tool_runner`` adapters in Phase 4.4 (the
WorkbenchNode pulls these adapters from the manifest).

Argument parsing follows a small, documented JSON-or-text contract:

1. If the instruction is a JSON object, it is treated as the tool
   arguments verbatim.
2. Otherwise the instruction is wrapped as ``{"input": <instruction>}``.
3. The supervisor MAY override (1)+(2) by passing a ``tool_arguments``
   key in :attr:`NodeMessage.metadata`. That dict wins and is used as
   the tool arguments without further interpretation.

Schema validation, retry policy, and timeout are out of scope for
this node — they belong to the tool runner contract or to the
supervisor's :class:`runtime.retry_policy.RetryPolicy`. ToolNode is
deliberately thin.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..messenger import NodeMessage
from ..supervisor import DelegationResult
from .base import BaseNode, NodeContext

__all__ = [
    "ToolInvocation",
    "ToolNode",
    "ToolResult",
    "ToolRunner",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool runner protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolInvocation:
    """Fully resolved invocation handed to the runner."""

    tool_name: str
    arguments: dict[str, Any]
    correlation_id: str
    org_id: str
    command_id: str


@dataclass(frozen=True)
class ToolResult:
    """The runner's reply.

    ``success=False`` plus ``error`` is treated as a *failure* by the
    enclosing node and propagated through :class:`DelegationResult`.
    The runner SHOULD NOT raise on tool failure — it should set
    ``success=False`` and populate ``error`` with a human-readable
    string. Raising is reserved for *infrastructure* errors that the
    runtime's retry policy should handle.
    """

    success: bool
    output: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


ToolRunner = Callable[[ToolInvocation], Awaitable[ToolResult]]
"""``run(invocation) -> ToolResult``."""


# ---------------------------------------------------------------------------
# Node implementation
# ---------------------------------------------------------------------------


class ToolNode(BaseNode):
    """A node that runs exactly one tool per delegation."""

    node_type = "tool"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        tool_name: str,
        tool_runner: ToolRunner,
        role: str | None = None,
    ) -> None:
        super().__init__(node_id=node_id, org_id=org_id, role=role)
        self.tool_name = tool_name
        self._runner = tool_runner

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        ctx.cancel_token.raise_if_cancelled()
        arguments = self._parse_arguments(msg)
        invocation = ToolInvocation(
            tool_name=self.tool_name,
            arguments=arguments,
            correlation_id=msg.correlation_id,
            org_id=ctx.org_id,
            command_id=ctx.command_id,
        )
        await self._emit_started(ctx, invocation)
        try:
            result = await self._runner(invocation)
        finally:
            ctx.cancel_token.raise_if_cancelled()
        await self._emit_completed(ctx, invocation, result)
        return DelegationResult(
            success=result.success,
            speaker=self.node_id,
            message=result.output if result.success else (result.error or "tool failed"),
            metadata={
                "tool_name": self.tool_name,
                "correlation_id": msg.correlation_id,
                "data": result.data,
            },
        )

    # ------------------------------------------------------------------
    # Argument parsing — see module docstring
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_arguments(msg: NodeMessage) -> dict[str, Any]:
        attached = (msg.metadata or {}).get("tool_arguments")
        if isinstance(attached, dict):
            return dict(attached)
        text = (msg.instruction or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                logger.debug(
                    "ToolNode: instruction looked like JSON but failed to parse; "
                    "falling back to {'input': ...}"
                )
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return {"input": text}

    # ------------------------------------------------------------------
    # Stream emission
    # ------------------------------------------------------------------

    async def _emit_started(
        self, ctx: NodeContext, invocation: ToolInvocation
    ) -> None:
        await ctx.stream.emit(
            "tasks",
            "tool_started",
            {
                "node_id": self.node_id,
                "tool_name": invocation.tool_name,
                "arguments": invocation.arguments,
                "correlation_id": invocation.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )

    async def _emit_completed(
        self,
        ctx: NodeContext,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> None:
        await ctx.stream.emit(
            "tasks",
            "tool_completed" if result.success else "tool_failed",
            {
                "node_id": self.node_id,
                "tool_name": invocation.tool_name,
                "success": result.success,
                "output_preview": result.output[:512] if result.output else "",
                "error": result.error,
                "correlation_id": invocation.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
