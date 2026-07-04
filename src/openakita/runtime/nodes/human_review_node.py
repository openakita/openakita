"""Human-in-the-loop checkpoint node.

Per ADR-0007, a ``HumanReviewNode`` is what makes the v2 runtime
*pausable*. When the supervisor delegates to it, the node:

* emits a ``human_review_requested`` event on the ``messages`` channel
  carrying the question and any structured payload the upstream node
  produced;
* writes a checkpoint (the supervisor does the writing; this node
  cooperates by reporting that it is now in
  :class:`runtime.models.NodeStatus.SUSPECT`);
* awaits a reviewer decision via an injected :class:`ReviewQueue`;
* returns a :class:`DelegationResult` reflecting the reviewer's
  verdict (``approve``, ``reject``, or ``edit``).

The :class:`ReviewQueue` is a small async primitive — anything that
can produce a :class:`ReviewDecision` for a given correlation id
will work. Tests use the in-memory :class:`InMemoryReviewQueue`.
The setup-center frontend will plug in a websocket-backed
implementation in Phase 6.

Cancellation is cooperative: a cancel mid-await pops the pending
review and returns a failure result so the supervisor can write a
cancelled checkpoint and the user's stop button is honoured even
while waiting on a human.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..messenger import NodeMessage
from ..models import NodeStatus
from ..supervisor import DelegationResult
from .base import BaseNode, NodeContext, NodeLifecycleEvent

__all__ = [
    "HumanReviewNode",
    "InMemoryReviewQueue",
    "ReviewDecision",
    "ReviewQueue",
    "ReviewVerdict",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict enum + decision record
# ---------------------------------------------------------------------------


class ReviewVerdict(StrEnum):
    """Three terminal states for a human review."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


@dataclass(frozen=True)
class ReviewDecision:
    """A reviewer's response to a pending review.

    ``edited_payload`` is required for :attr:`ReviewVerdict.EDIT`; it
    is what the supervisor will hand to the next node in place of the
    upstream node's output. ``reason`` is optional human prose that
    flows into the audit trail.
    """

    verdict: ReviewVerdict
    reason: str | None = None
    edited_payload: dict[str, Any] | None = None
    decided_by: str = "anonymous"
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.verdict is ReviewVerdict.EDIT and self.edited_payload is None:
            raise ValueError("EDIT verdict requires edited_payload")


# ---------------------------------------------------------------------------
# Queue protocol + reference implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class ReviewQueue(Protocol):
    """Async producer/consumer for human review decisions.

    Producers (the node) call :meth:`request` with the question and
    payload, and ``await`` the returned future for the decision.
    Consumers (the frontend / CLI) call :meth:`pending` to enumerate
    open requests and :meth:`resolve` to deliver a decision."""

    async def request(
        self,
        *,
        correlation_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> Awaitable[ReviewDecision]: ...

    async def resolve(
        self, correlation_id: str, decision: ReviewDecision
    ) -> None: ...


class InMemoryReviewQueue:
    """Reference :class:`ReviewQueue` for tests and single-process runs."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[ReviewDecision]] = {}
        self._questions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def request(
        self,
        *,
        correlation_id: str,
        question: str,
        payload: dict[str, Any],
    ) -> Awaitable[ReviewDecision]:
        async with self._lock:
            if correlation_id in self._pending:
                raise ValueError(
                    f"duplicate review request for {correlation_id!r}"
                )
            fut: asyncio.Future[ReviewDecision] = asyncio.get_running_loop().create_future()
            self._pending[correlation_id] = fut
            self._questions[correlation_id] = {
                "question": question,
                "payload": dict(payload),
                "issued_at": datetime.now(UTC).isoformat(),
            }
        return fut

    async def resolve(
        self, correlation_id: str, decision: ReviewDecision
    ) -> None:
        async with self._lock:
            fut = self._pending.pop(correlation_id, None)
            self._questions.pop(correlation_id, None)
        if fut is None:
            raise KeyError(f"no pending review for {correlation_id!r}")
        if not fut.done():
            fut.set_result(decision)

    async def pending(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                {"correlation_id": cid, **details}
                for cid, details in self._questions.items()
            ]

    async def cancel(self, correlation_id: str, reason: str) -> None:
        async with self._lock:
            fut = self._pending.pop(correlation_id, None)
            self._questions.pop(correlation_id, None)
        if fut is not None and not fut.done():
            fut.set_exception(asyncio.CancelledError(reason))


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class HumanReviewNode(BaseNode):
    """Pause the run until a human delivers a :class:`ReviewDecision`."""

    node_type = "human_review"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        queue: ReviewQueue,
        question_template: str = "Please review and choose a verdict.",
        role: str | None = None,
    ) -> None:
        super().__init__(node_id=node_id, org_id=org_id, role=role)
        self._queue = queue
        self._question_template = question_template

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        ctx.cancel_token.raise_if_cancelled()
        question = msg.metadata.get("question") or self._question_template
        payload = dict(msg.metadata.get("payload") or {})
        if not payload:
            payload = {"instruction": msg.instruction}
        future = await self._queue.request(
            correlation_id=msg.correlation_id,
            question=question,
            payload=payload,
        )
        await ctx.stream.emit(
            "messages",
            "human_review_requested",
            {
                "node_id": self.node_id,
                "correlation_id": msg.correlation_id,
                "question": question,
                "payload": payload,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        # Mark suspect so the dashboard can reflect "waiting on human" without
        # the supervisor's stall detector escalating it as a hung node.
        self.status = NodeStatus.SUSPECT
        await self._emit_lifecycle(
            NodeLifecycleEvent.SUSPECT,
            {
                "reason": "awaiting_human_review",
                "correlation_id": msg.correlation_id,
            },
        )
        decision = await self._await_with_cancel(future, ctx, msg)
        if decision is None:
            return DelegationResult(
                success=False,
                speaker=self.node_id,
                message="human review cancelled",
                metadata={
                    "correlation_id": msg.correlation_id,
                    "verdict": None,
                },
            )
        await ctx.stream.emit(
            "messages",
            "human_review_resolved",
            {
                "node_id": self.node_id,
                "correlation_id": msg.correlation_id,
                "verdict": decision.verdict.value,
                "decided_by": decision.decided_by,
                "reason": decision.reason,
            },
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
        return self._result_from_decision(msg, decision)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _await_with_cancel(
        self,
        future: Awaitable[ReviewDecision],
        ctx: NodeContext,
        msg: NodeMessage,
    ) -> ReviewDecision | None:
        cancel_event = asyncio.Event()

        def _on_cancel() -> None:
            cancel_event.set()

        ctx.cancel_token.add_callback(_on_cancel)
        cancel_task = asyncio.create_task(cancel_event.wait())
        review_task: asyncio.Task[ReviewDecision] = asyncio.ensure_future(future)
        try:
            done, _ = await asyncio.wait(
                {review_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            cancel_task.cancel()
            with _suppress_cancel():
                await cancel_task
        if review_task in done and not review_task.cancelled():
            try:
                return review_task.result()
            except asyncio.CancelledError:
                return None
        # Cancel won the race.
        review_task.cancel()
        with _suppress_cancel():
            await review_task
        if hasattr(self._queue, "cancel"):
            try:
                await self._queue.cancel(msg.correlation_id, "node cancelled")
            except Exception:  # noqa: BLE001
                logger.debug(
                    "queue.cancel raised during cleanup; safe to ignore",
                    exc_info=True,
                )
        return None

    def _result_from_decision(
        self, msg: NodeMessage, decision: ReviewDecision
    ) -> DelegationResult:
        success = decision.verdict is not ReviewVerdict.REJECT
        message: str
        metadata: dict[str, Any] = {
            "correlation_id": msg.correlation_id,
            "verdict": decision.verdict.value,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
        }
        if decision.verdict is ReviewVerdict.APPROVE:
            message = decision.reason or "approved"
        elif decision.verdict is ReviewVerdict.EDIT:
            payload = decision.edited_payload or {}
            metadata["edited_payload"] = payload
            message = decision.reason or "edited"
        else:  # REJECT
            message = decision.reason or "rejected"
        return DelegationResult(
            success=success,
            speaker=self.node_id,
            message=message,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# small util — context manager that swallows asyncio.CancelledError
# ---------------------------------------------------------------------------


@contextmanager
def _suppress_cancel():
    try:
        yield
    except asyncio.CancelledError:
        pass
