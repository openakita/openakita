"""LLM-backed reasoning node.

Per ADR-0007, an ``LLMNode`` wraps a *brain* — a small, async-only
protocol that takes a prompt and returns a response — and turns its
output into a :class:`DelegationResult` for the supervisor.

The node is intentionally decoupled from any concrete LLM client:

* It accepts a ``brain: NodeBrain`` callable.
* It accepts an optional ``tool_runner: ToolRunner`` used when the
  brain decides to call a tool. Tool calls are strictly bounded by
  ``max_tool_calls`` (default ``8``) so a buggy brain cannot loop.
* It exposes a small, declarative contract for *what* the brain
  returns: either a final ``answer`` (delegation done) or a
  ``tool_call`` (run the tool, feed the result back to the brain,
  repeat). This contract mirrors AutoGen's AssistantAgent <-> Tool
  pattern but trimmed to what OpenAkita actually needs in v2.

The brain is responsible for *its own* prompt construction. The
node only supplies:

* the user instruction (verbatim from the delegation),
* the conversation transcript built up over tool calls in this
  delegation,
* a snapshot of the cancellation token so the brain can hand it to
  whatever provider client it uses.

Long-running tool calls can emit progress via the supervisor's
stream by passing a ``progress`` callable to the runner; this node
does not introspect tool runs further.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..cancel_token import CancellationToken
from ..messenger import NodeMessage
from ..supervisor import DelegationResult
from .base import BaseNode, NodeContext
from .tool_node import ToolInvocation, ToolResult, ToolRunner

__all__ = [
    "BrainPrompt",
    "BrainResponse",
    "LLMNode",
    "NodeBrain",
    "ToolCallRequest",
    "TranscriptTurn",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Brain protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranscriptTurn:
    """One turn in the per-delegation conversation.

    ``speaker`` is one of ``"user"``, ``"assistant"``, or
    ``"tool"``. The brain builds a chat-completion-style payload from
    this list; the node never builds a prompt itself.
    """

    speaker: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainPrompt:
    """Input handed to the brain on every step."""

    instruction: str
    transcript: tuple[TranscriptTurn, ...]
    cancel_token: CancellationToken
    org_id: str
    command_id: str


@dataclass(frozen=True)
class ToolCallRequest:
    """Brain's request to invoke a tool. The node validates and runs it."""

    tool_name: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass(frozen=True)
class BrainResponse:
    """Brain's reply. Exactly one of ``answer`` / ``tool_call`` MUST be set."""

    answer: str | None = None
    tool_call: ToolCallRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.answer is None) == (self.tool_call is None):
            raise ValueError(
                "BrainResponse must set exactly one of answer or tool_call"
            )


NodeBrain = Callable[[BrainPrompt], Awaitable[BrainResponse]]
"""``await brain(prompt) -> BrainResponse``."""


# ---------------------------------------------------------------------------
# LLMNode
# ---------------------------------------------------------------------------


class LLMNode(BaseNode):
    """A node that delegates reasoning to a :class:`NodeBrain`.

    The lifecycle is fixed:

    1. Build an initial transcript with the user's instruction.
    2. Call ``brain(prompt)``.
    3. If the brain returns an ``answer``, finish.
    4. If the brain returns a ``tool_call``, run it via
       ``tool_runner``, append the tool turn to the transcript, and
       loop back to (2). Cooperative cancel is checked between steps.
    5. If we exceed ``max_tool_calls``, fail the delegation with a
       deterministic error and let the supervisor's stall detector
       decide whether to replan.
    """

    node_type = "llm"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        brain: NodeBrain,
        tool_runner: ToolRunner | None = None,
        allowed_tools: frozenset[str] | None = None,
        max_tool_calls: int = 8,
        role: str | None = None,
    ) -> None:
        super().__init__(node_id=node_id, org_id=org_id, role=role)
        self._brain = brain
        self._tool_runner = tool_runner
        self._allowed_tools = allowed_tools
        self._max_tool_calls = max_tool_calls

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        transcript: list[TranscriptTurn] = [
            TranscriptTurn(speaker="user", content=msg.instruction)
        ]
        tool_calls = 0
        while True:
            ctx.cancel_token.raise_if_cancelled()
            prompt = BrainPrompt(
                instruction=msg.instruction,
                transcript=tuple(transcript),
                cancel_token=ctx.cancel_token,
                org_id=ctx.org_id,
                command_id=ctx.command_id,
            )
            response = await self._brain(prompt)
            if response.answer is not None:
                await self._emit_assistant(ctx, response.answer)
                return DelegationResult(
                    success=True,
                    speaker=self.node_id,
                    message=response.answer,
                    metadata={
                        "correlation_id": msg.correlation_id,
                        "tool_calls": tool_calls,
                        **response.metadata,
                    },
                )
            assert response.tool_call is not None  # invariant per BrainResponse
            tool_calls += 1
            if tool_calls > self._max_tool_calls:
                return self._tool_budget_exhausted(msg, tool_calls - 1)
            tool_turn = await self._run_tool(ctx, response.tool_call, msg)
            transcript.append(
                TranscriptTurn(
                    speaker="assistant",
                    content=(response.tool_call.rationale or ""),
                    metadata={
                        "tool_name": response.tool_call.tool_name,
                        "tool_arguments": response.tool_call.arguments,
                    },
                )
            )
            transcript.append(tool_turn)
            await self.emit_progress(
                {
                    "kind": "tool_iteration",
                    "tool_name": response.tool_call.tool_name,
                    "iteration": tool_calls,
                }
            )

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def _run_tool(
        self,
        ctx: NodeContext,
        request: ToolCallRequest,
        msg: NodeMessage,
    ) -> TranscriptTurn:
        if self._tool_runner is None:
            return TranscriptTurn(
                speaker="tool",
                content=(
                    f"tool call rejected: node {self.node_id} has no tool runner"
                ),
                metadata={"tool_name": request.tool_name, "rejected": True},
            )
        if (
            self._allowed_tools is not None
            and request.tool_name not in self._allowed_tools
        ):
            return TranscriptTurn(
                speaker="tool",
                content=(
                    f"tool call rejected: tool {request.tool_name!r} not in "
                    f"allow-list {sorted(self._allowed_tools)!r}"
                ),
                metadata={"tool_name": request.tool_name, "rejected": True},
            )
        invocation = ToolInvocation(
            tool_name=request.tool_name,
            arguments=request.arguments,
            correlation_id=msg.correlation_id,
            org_id=ctx.org_id,
            command_id=ctx.command_id,
        )
        await ctx.stream.emit(
            "tasks",
            "tool_started",
            {
                "node_id": self.node_id,
                "tool_name": request.tool_name,
                "arguments": request.arguments,
                "correlation_id": msg.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        try:
            result: ToolResult = await self._tool_runner(invocation)
        except BaseException as exc:  # promoted to ERROR by BaseNode
            logger.exception(
                "LLMNode %s tool %s raised %s",
                self.node_id,
                request.tool_name,
                type(exc).__name__,
            )
            raise
        await ctx.stream.emit(
            "tasks",
            "tool_completed" if result.success else "tool_failed",
            {
                "node_id": self.node_id,
                "tool_name": request.tool_name,
                "success": result.success,
                "output_preview": result.output[:512] if result.output else "",
                "error": result.error,
                "correlation_id": msg.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        return TranscriptTurn(
            speaker="tool",
            content=result.output if result.success else (result.error or "tool failed"),
            metadata={
                "tool_name": request.tool_name,
                "success": result.success,
                "data": result.data,
            },
        )

    def _tool_budget_exhausted(
        self, msg: NodeMessage, used: int
    ) -> DelegationResult:
        return DelegationResult(
            success=False,
            speaker=self.node_id,
            message=(
                f"tool call budget exhausted ({used} >= max_tool_calls="
                f"{self._max_tool_calls})"
            ),
            metadata={
                "correlation_id": msg.correlation_id,
                "tool_calls": used,
                "reason": "tool_budget_exhausted",
            },
        )

    async def _emit_assistant(
        self, ctx: NodeContext, answer: str
    ) -> None:
        await ctx.stream.emit(
            "messages",
            "assistant_answer",
            {
                "node_id": self.node_id,
                "preview": answer[:512],
                "length": len(answer),
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
