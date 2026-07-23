"""Plugin-backed multi-function node (a "workbench").

Per ADR-0007 + ADR-0009, a ``WorkbenchNode`` makes a plugin behave
like a first-class node type. It owns:

* a validated :class:`WorkbenchManifest` (the plugin's typed
  declaration of modes, tools, UI, and per-mode prompt overrides);
* a :class:`NodeBrain` that drives the per-message reasoning loop;
* a :class:`ToolRunner` shared with the plugin so the brain can call
  the tools in its currently-active mode but no others.

The node enforces three things the previous implementation did not:

1. Tool allow-list per mode. Brain requests a tool not in the
   active mode's allow-list -> rejection turn in transcript, runner
   never invoked. Replaces "trust the prompt" enforcement.

2. Mode switching is explicit. The brain switches modes by
   returning a :class:`BrainResponse` whose
   ``metadata["switch_to"]`` names a different mode. The node
   re-emits ``workbench_mode_switched`` and re-runs the loop with
   the new mode's prompt and allow-list. Mode switching does NOT
   reset the transcript so the next mode sees prior context.

3. UI surface is announced. ``workbench_ready`` lifecycle event
   carries ``ui_url``, ``mode``, ``ui_panel`` so the front-end
   iframe and activity feed pick the right panel without inferring.

The implementation deliberately does not subclass
:class:`runtime.nodes.LLMNode`: WorkbenchNode owns its own loop so
mode-aware behaviour stays in one place. Tool budget, transcript
shape, and ToolCallRequest plumbing match LLMNode so a brain
implementation works with either node.
"""

from __future__ import annotations

import logging
from typing import Any

from ..messenger import NodeMessage
from ..supervisor import DelegationResult
from .base import BaseNode, NodeContext
from .llm_node import (
    BrainPrompt,
    NodeBrain,
    ToolCallRequest,
    TranscriptTurn,
)
from .manifest import WorkbenchManifest, WorkbenchMode
from .tool_node import ToolInvocation, ToolResult, ToolRunner

__all__ = ["WorkbenchNode"]

logger = logging.getLogger(__name__)


class WorkbenchNode(BaseNode):
    """A plugin behaving as a multi-function node."""

    node_type = "workbench"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        manifest: WorkbenchManifest,
        brain: NodeBrain,
        tool_runner: ToolRunner,
        initial_mode: str | None = None,
        max_tool_calls: int = 8,
        role: str | None = None,
    ) -> None:
        mode_id = initial_mode or manifest.default_mode
        try:
            initial = manifest.mode(mode_id)
        except KeyError as exc:
            raise ValueError(
                f"unknown initial_mode {mode_id!r} for workbench {manifest.id!r}; "
                f"valid: {[m.id for m in manifest.modes]}"
            ) from exc
        super().__init__(
            node_id=node_id,
            org_id=org_id,
            role=role,
            workbench=(manifest.id, initial.id),
        )
        self.manifest = manifest
        self.active_mode: WorkbenchMode = initial
        self._brain = brain
        self._tool_runner = tool_runner
        self._max_tool_calls = max_tool_calls

    # ------------------------------------------------------------------
    # Lifecycle: announce UI surface and active mode at activation
    # ------------------------------------------------------------------

    async def on_activate(self, ctx: NodeContext) -> None:
        await super().on_activate(ctx)
        await self._emit_workbench_ready()

    async def _emit_workbench_ready(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        await ctx.stream.emit(
            "lifecycle",
            "workbench_ready",
            {
                "node_id": self.node_id,
                "plugin_id": self.manifest.id,
                "title": self.manifest.title,
                "mode": self.active_mode.id,
                "ui_url": self.manifest.ui.url,
                "ui_panel": self.active_mode.ui_panel,
                "tools": list(self.active_mode.tools),
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )

    # ------------------------------------------------------------------
    # Per-message reasoning loop
    # ------------------------------------------------------------------

    async def handle_message(self, ctx: NodeContext, msg: NodeMessage) -> DelegationResult:
        # An explicit per-message mode override wins over the active mode.
        requested_mode = (msg.metadata or {}).get("mode")
        if isinstance(requested_mode, str) and requested_mode != self.active_mode.id:
            await self._switch_mode(requested_mode)
        transcript: list[TranscriptTurn] = [TranscriptTurn(speaker="user", content=msg.instruction)]
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
            switch_to = response.metadata.get("switch_to") if response.metadata else None
            if isinstance(switch_to, str) and switch_to != self.active_mode.id:
                await self._switch_mode(switch_to)
                # The brain implicitly continues; we feed it again with the
                # same transcript but new mode context. To prevent loops we
                # treat the switch as one tool-call equivalent.
                tool_calls += 1
                if tool_calls > self._max_tool_calls:
                    return self._budget_exhausted(msg, tool_calls - 1)
                continue
            if response.answer is not None:
                await self._emit_assistant(response.answer)
                return DelegationResult(
                    success=True,
                    speaker=self.node_id,
                    message=response.answer,
                    metadata={
                        "correlation_id": msg.correlation_id,
                        "tool_calls": tool_calls,
                        "active_mode": self.active_mode.id,
                        "plugin_id": self.manifest.id,
                        **{k: v for k, v in response.metadata.items() if k != "switch_to"},
                    },
                )
            assert response.tool_call is not None
            tool_calls += 1
            if tool_calls > self._max_tool_calls:
                return self._budget_exhausted(msg, tool_calls - 1)
            tool_turn = await self._run_tool(ctx, response.tool_call, msg)
            transcript.append(
                TranscriptTurn(
                    speaker="assistant",
                    content=response.tool_call.rationale or "",
                    metadata={
                        "tool_name": response.tool_call.tool_name,
                        "tool_arguments": response.tool_call.arguments,
                        "active_mode": self.active_mode.id,
                    },
                )
            )
            transcript.append(tool_turn)
            await self.emit_progress(
                {
                    "kind": "workbench_iteration",
                    "active_mode": self.active_mode.id,
                    "tool_name": response.tool_call.tool_name,
                    "iteration": tool_calls,
                }
            )

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    async def _switch_mode(self, mode_id: str) -> None:
        try:
            new_mode = self.manifest.mode(mode_id)
        except KeyError:
            logger.warning(
                "WorkbenchNode %s: brain requested unknown mode %r; ignoring (valid: %s)",
                self.node_id,
                mode_id,
                [m.id for m in self.manifest.modes],
            )
            return
        prev = self.active_mode
        self.active_mode = new_mode
        self._workbench = (self.manifest.id, new_mode.id)
        ctx = self._ctx
        if ctx is None:
            return
        await ctx.stream.emit(
            "lifecycle",
            "workbench_mode_switched",
            {
                "node_id": self.node_id,
                "plugin_id": self.manifest.id,
                "from_mode": prev.id,
                "to_mode": new_mode.id,
                "ui_panel": new_mode.ui_panel,
                "tools": list(new_mode.tools),
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )

    # ------------------------------------------------------------------
    # Tool routing — mode-scoped allow-list
    # ------------------------------------------------------------------

    async def _run_tool(
        self,
        ctx: NodeContext,
        request: ToolCallRequest,
        msg: NodeMessage,
    ) -> TranscriptTurn:
        if request.tool_name not in self.active_mode.tools:
            return TranscriptTurn(
                speaker="tool",
                content=(
                    f"tool {request.tool_name!r} not available in mode "
                    f"{self.active_mode.id!r} of workbench "
                    f"{self.manifest.id!r}; allowed: {list(self.active_mode.tools)}"
                ),
                metadata={
                    "tool_name": request.tool_name,
                    "rejected": True,
                    "active_mode": self.active_mode.id,
                },
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
                "active_mode": self.active_mode.id,
                "arguments": request.arguments,
                "correlation_id": msg.correlation_id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        result: ToolResult = await self._tool_runner(invocation)
        await ctx.stream.emit(
            "tasks",
            "tool_completed" if result.success else "tool_failed",
            {
                "node_id": self.node_id,
                "tool_name": request.tool_name,
                "active_mode": self.active_mode.id,
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
                "active_mode": self.active_mode.id,
                "success": result.success,
                "data": result.data,
            },
        )

    # ------------------------------------------------------------------
    # Cancel hook — emit workbench_cancelled in addition to base behaviour
    # ------------------------------------------------------------------

    async def on_cancel(self, reason: str) -> None:
        was_cancelled_already = self.status.value == "cancelled"
        await super().on_cancel(reason)
        if was_cancelled_already:
            return
        ctx = self._ctx
        if ctx is None:
            return
        await ctx.stream.emit(
            "lifecycle",
            "workbench_cancelled",
            {
                "node_id": self.node_id,
                "plugin_id": self.manifest.id,
                "active_mode": self.active_mode.id,
                "reason": reason,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _budget_exhausted(self, msg: NodeMessage, used: int) -> DelegationResult:
        return DelegationResult(
            success=False,
            speaker=self.node_id,
            message=(
                f"workbench tool/mode-switch budget exhausted "
                f"({used} >= max_tool_calls={self._max_tool_calls}) "
                f"in mode {self.active_mode.id!r}"
            ),
            metadata={
                "correlation_id": msg.correlation_id,
                "tool_calls": used,
                "reason": "tool_budget_exhausted",
                "active_mode": self.active_mode.id,
                "plugin_id": self.manifest.id,
            },
        )

    async def _emit_assistant(self, answer: str) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        await ctx.stream.emit(
            "messages",
            "assistant_answer",
            {
                "node_id": self.node_id,
                "preview": answer[:512],
                "length": len(answer),
                "active_mode": self.active_mode.id,
                "plugin_id": self.manifest.id,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )

    def save_state_extra(self) -> dict[str, Any]:
        # Convenience hook so future supervisor checkpoint composition can
        # easily attach the active_mode to the per-node snapshot.
        return {
            "active_mode": self.active_mode.id,
            "plugin_id": self.manifest.id,
        }
