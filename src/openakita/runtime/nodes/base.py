"""Node protocol, context, and lifecycle hooks.

Implements ADR-0007's contract for every behaviour-bearing node in a
v2 organization. Five node types build on this base in this Phase 4
work:

* :class:`runtime.nodes.tool_node.ToolNode`
* :class:`runtime.nodes.llm_node.LLMNode`
* :class:`runtime.nodes.condition_node.ConditionNode`
* :class:`runtime.nodes.human_review_node.HumanReviewNode`
* :class:`runtime.nodes.workbench_node.WorkbenchNode`

The protocol layering is strict (ADR-0002):

* nodes import from :mod:`runtime.models`, :mod:`runtime.cancel_token`,
  :mod:`runtime.stream`, :mod:`runtime.checkpoint`, and the messenger
  envelope :class:`NodeMessage`/:class:`DelegationResult`;
* nodes never import :mod:`runtime.supervisor`;
* nodes never reach into other nodes — coordination is the supervisor
  / state graph's job.

``BaseNode`` is a small concrete helper: it implements the lifecycle
state machine (created → idle → busy → suspect → cancelled / error /
offline) and the per-turn lifecycle event emission, leaving only
``handle_message`` for subclasses to implement. Tests can drive a
plain ``BaseNode`` subclass without touching the supervisor at all.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..cancel_token import CancellationToken, CancelledByToken
from ..checkpoint import BaseCheckpointer
from ..messenger import NodeMessage
from ..models import NodeStatus
from ..stream import StreamBus
from ..supervisor import DelegationResult

__all__ = [
    "BaseNode",
    "NodeContext",
    "NodeLifecycleEvent",
    "NodeProtocol",
    "NodeRegistration",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifecycle events emitted on the lifecycle channel
# ---------------------------------------------------------------------------


class NodeLifecycleEvent(StrEnum):
    """Typed event names every node MUST emit at the right moment."""

    ACTIVATED = "node_activated"
    BUSY = "node_busy"
    PROGRESS = "node_progress"
    SUSPECT = "node_suspect"
    IDLE = "node_idle"
    CANCELLED = "node_cancelled"
    ERROR = "node_error"
    OFFLINE = "node_offline"


# ---------------------------------------------------------------------------
# NodeContext — handed to every protocol method
# ---------------------------------------------------------------------------


@dataclass
class NodeContext:
    """Per-call context for a node implementation.

    Held only for the duration of one ``on_activate`` /
    ``on_message`` / ``on_cancel`` call so a node implementation does
    not accumulate runtime references; this keeps tests trivial.

    Required fields are everything a typical node needs to do its job
    without any global lookup: stream, checkpointer, cancel token,
    plus the org and command identifiers needed to address stream
    events back to the supervisor.
    """

    node_id: str
    org_id: str
    command_id: str
    stream: StreamBus
    cancel_token: CancellationToken
    checkpointer: BaseCheckpointer | None = None
    superstep: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registration record (used by the runtime facade in Phase 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeRegistration:
    """How a concrete node should be wired into the messenger registry.

    Built once when a node is created. The messenger uses these
    fields to populate its by-id, by-role, and by-(plugin, mode)
    indexes (see :class:`runtime.messenger.InMemoryNodeRegistry`).
    """

    node_id: str
    role: str | None = None
    workbench: tuple[str, str] | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NodeProtocol(Protocol):
    """Lifecycle and execution contract for a runtime node (ADR-0007).

    The protocol is *messenger-compatible*: ``on_message`` and
    ``on_cancel`` take a single argument because the messenger
    (ADR-0007 + :mod:`runtime.messenger`) does not know about
    per-organization context. The node binds its
    :class:`NodeContext` once in :meth:`on_activate` and re-reads it
    on every message.
    """

    node_id: str
    node_type: str
    org_id: str

    async def on_activate(self, ctx: NodeContext) -> None: ...
    """Bind the per-run context and announce readiness.

    MUST emit :class:`NodeLifecycleEvent.ACTIVATED` on the lifecycle
    channel and store ``ctx`` so subsequent ``on_message`` / ``on_cancel``
    calls have access to the stream, checkpointer, cancel token, and
    command/org identifiers."""

    async def on_message(self, message: NodeMessage) -> DelegationResult: ...
    """Process exactly one delegation.

    Signature matches :class:`runtime.messenger.MessengerNode` so the
    messenger can dispatch without an extra context argument."""

    async def on_cancel(self, reason: str) -> None: ...
    """Cooperative cancel hook. Idempotent."""

    async def save_state(self) -> dict[str, Any]: ...
    """Return JSON-serialisable state for checkpointing."""

    async def load_state(self, state: dict[str, Any]) -> None: ...
    """Restore state previously returned by :meth:`save_state`."""

    def registration(self) -> NodeRegistration: ...
    """Describe how the messenger should index this node."""


# ---------------------------------------------------------------------------
# BaseNode — concrete helper that subclasses extend
# ---------------------------------------------------------------------------


class BaseNode:
    """Concrete helper implementing the boilerplate of NodeProtocol.

    Subclasses implement :meth:`handle_message` (the *business logic*
    of the node) and may override :meth:`on_activate` /
    :meth:`save_state` / :meth:`load_state` if they want. The base
    class drives:

    * the lifecycle state machine (``status``);
    * stream emission of every lifecycle event with the correct envelope;
    * cancel-aware execution of :meth:`handle_message`;
    * defensive try/except wrapping that promotes unexpected exceptions
      to ``NodeStatus.ERROR`` and a failure :class:`DelegationResult`
      with the actual exception message — the supervisor will *not*
      re-delegate by surprise.

    The class is small on purpose; nodes that need fancier behaviour
    (e.g. WorkbenchNode mode switching) override the appropriate
    hook rather than re-doing the boilerplate.
    """

    node_type: str = "base"

    def __init__(
        self,
        *,
        node_id: str,
        org_id: str,
        role: str | None = None,
        workbench: tuple[str, str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.org_id = org_id
        self._role = role
        self._workbench = workbench
        self.status: NodeStatus = NodeStatus.CREATED
        self.last_seen: datetime | None = None
        self.last_progress_at: datetime | None = None
        self._activated: bool = False
        self._ctx: NodeContext | None = None

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        """Concrete behaviour. Subclasses MUST implement.

        Implementations should NOT manage the lifecycle status
        themselves — :meth:`on_message` handles that. They should also
        not emit ``node_busy`` / ``node_idle`` lifecycle events; the
        base class emits those automatically. They MAY emit
        :class:`NodeLifecycleEvent.PROGRESS` to refresh the UI on long
        operations, and SHOULD emit ``updates`` events for any
        intermediate deliverable.
        """
        raise NotImplementedError

    async def on_activate(self, ctx: NodeContext) -> None:
        """Bind ``ctx`` and emit ACTIVATED + IDLE. Override to pre-load."""
        self._ctx = ctx
        self._activated = True
        self.status = NodeStatus.IDLE
        self.last_seen = datetime.now(UTC)
        await self._emit_lifecycle(NodeLifecycleEvent.ACTIVATED, {})
        await self._emit_lifecycle(NodeLifecycleEvent.IDLE, {})

    async def on_cancel(self, reason: str) -> None:
        """Default cooperative cancel. Idempotent."""
        if self.status is NodeStatus.CANCELLED:
            return
        self.status = NodeStatus.CANCELLED
        await self._emit_lifecycle(NodeLifecycleEvent.CANCELLED, {"reason": reason})

    async def save_state(self) -> dict[str, Any]:
        """Default: surface the public lifecycle fields. Override to add."""
        return {
            "node_id": self.node_id,
            "org_id": self.org_id,
            "status": self.status.value,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_progress_at": (
                self.last_progress_at.isoformat()
                if self.last_progress_at
                else None
            ),
            "activated": self._activated,
        }

    async def load_state(self, state: dict[str, Any]) -> None:
        self.node_id = state.get("node_id", self.node_id)
        self.org_id = state.get("org_id", self.org_id)
        self.status = NodeStatus(state.get("status", NodeStatus.CREATED.value))
        ls = state.get("last_seen")
        self.last_seen = datetime.fromisoformat(ls) if ls else None
        lp = state.get("last_progress_at")
        self.last_progress_at = datetime.fromisoformat(lp) if lp else None
        self._activated = bool(state.get("activated", False))

    # ------------------------------------------------------------------
    # Protocol entry points
    # ------------------------------------------------------------------

    async def on_message(self, message: NodeMessage) -> DelegationResult:
        """Lifecycle-wrapped invocation of :meth:`handle_message`.

        Reads the per-run :class:`NodeContext` previously bound by
        :meth:`on_activate`. If a node receives a message before it
        has been activated, it returns a failure result rather than
        raising — the supervisor will surface that to the user via the
        normal failure path.
        """
        if not self._activated or self._ctx is None:
            return DelegationResult(
                success=False,
                speaker=self.node_id,
                message=(
                    f"node {self.node_id} received a message before activation"
                ),
                metadata={"correlation_id": message.correlation_id},
            )
        ctx = self._ctx
        if self.status in (NodeStatus.OFFLINE, NodeStatus.CANCELLED):
            return DelegationResult(
                success=False,
                speaker=self.node_id,
                message=(
                    f"node {self.node_id} is in terminal status "
                    f"{self.status.value}"
                ),
                metadata={"correlation_id": message.correlation_id},
            )
        self.status = NodeStatus.BUSY
        self.last_seen = datetime.now(UTC)
        await self._emit_lifecycle(
            NodeLifecycleEvent.BUSY,
            {"correlation_id": message.correlation_id},
        )
        try:
            result = await self.handle_message(ctx, message)
        except CancelledByToken as exc:
            await self.on_cancel(exc.reason)
            return DelegationResult(
                success=False,
                speaker=self.node_id,
                message=f"cancelled: {exc.reason}",
                metadata={"correlation_id": message.correlation_id},
            )
        except BaseException as exc:  # noqa: BLE001 — see docstring
            logger.exception(
                "Node %s handle_message raised %s", self.node_id, type(exc).__name__
            )
            self.status = NodeStatus.ERROR
            await self._emit_lifecycle(
                NodeLifecycleEvent.ERROR,
                {
                    "exception": type(exc).__name__,
                    "message": str(exc)[:512],
                    "correlation_id": message.correlation_id,
                },
            )
            return DelegationResult(
                success=False,
                speaker=self.node_id,
                message=f"{type(exc).__name__}: {exc}",
                metadata={"correlation_id": message.correlation_id},
            )
        # Success-path lifecycle bookkeeping.
        self.last_progress_at = datetime.now(UTC)
        self.status = NodeStatus.IDLE
        await self._emit_lifecycle(
            NodeLifecycleEvent.IDLE,
            {
                "correlation_id": message.correlation_id,
                "success": result.success,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def registration(self) -> NodeRegistration:
        return NodeRegistration(
            node_id=self.node_id, role=self._role, workbench=self._workbench
        )

    # ------------------------------------------------------------------
    # Helpers exposed to subclasses
    # ------------------------------------------------------------------

    async def emit_progress(self, payload: dict[str, Any]) -> None:
        """Refresh the lifecycle UI on long operations.

        Subclasses call this from inside :meth:`handle_message` for
        long-running tool calls so the activity feed shows the node is
        still alive. Updates :attr:`last_progress_at` so the
        supervisor's stall detector (which is per-command, not
        per-node) does not promote the node to SUSPECT prematurely.
        """
        self.last_progress_at = datetime.now(UTC)
        await self._emit_lifecycle(NodeLifecycleEvent.PROGRESS, payload)

    async def _emit_lifecycle(
        self, type_: NodeLifecycleEvent, payload: dict[str, Any]
    ) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        await ctx.stream.emit(
            "lifecycle",
            type_.value,
            {"node_id": self.node_id, "status": self.status.value, **payload},
            command_id=ctx.command_id,
            org_id=ctx.org_id,
            superstep=ctx.superstep,
        )
