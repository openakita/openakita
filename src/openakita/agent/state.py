"""Agent-level state for the v2 runtime.

The legacy ``core/agent_state.py`` (431 lines) absorbed many concerns
that the v2 architecture now solves elsewhere:

* cancel mechanics moved to :class:`runtime.cancel_token.CancellationToken`;
* skip / user-insert routing moves to the messenger and stream bus;
* loop detection moves to the supervisor's stall detector and the
  progress ledger;
* per-turn iteration count is the supervisor's ``n_turns``.

What remains is a small, well-named record of the agent's *own*
context: which task it is currently servicing, which session and
conversation that belongs to, the sub-status within a single ReAct
turn, and a few counters that only the agent itself uses.

This module is the leaf of the agent layering described in ADR-0003.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.models import TaskLifecycleState

__all__ = [
    "ReasoningPhase",
    "TaskState",
    "AgentState",
]


class ReasoningPhase(StrEnum):
    """Sub-status inside a single ReAct turn.

    Distinct from :class:`runtime.models.TaskLifecycleState`, which
    tracks the *task* (across many turns). ReasoningPhase tracks the
    *current turn* of an agent's thought / act / observe cycle. Both
    move together: a task can be ``EXECUTING`` while a single turn
    moves through ``THINK -> ACT -> OBSERVE -> THINK``.
    """

    IDLE = "idle"
    COMPILING = "compiling"
    THINK = "think"
    ACT = "act"
    OBSERVE = "observe"
    VERIFY = "verify"
    AWAITING_USER = "awaiting_user"
    DONE = "done"


_VALID_PHASE_TRANSITIONS: dict[ReasoningPhase, frozenset[ReasoningPhase]] = {
    ReasoningPhase.IDLE: frozenset(
        {ReasoningPhase.COMPILING, ReasoningPhase.THINK, ReasoningPhase.DONE}
    ),
    ReasoningPhase.COMPILING: frozenset(
        {ReasoningPhase.THINK, ReasoningPhase.DONE}
    ),
    ReasoningPhase.THINK: frozenset(
        {
            ReasoningPhase.ACT,
            ReasoningPhase.VERIFY,
            ReasoningPhase.AWAITING_USER,
            ReasoningPhase.DONE,
        }
    ),
    ReasoningPhase.ACT: frozenset(
        {
            ReasoningPhase.OBSERVE,
            ReasoningPhase.AWAITING_USER,
            ReasoningPhase.DONE,
        }
    ),
    ReasoningPhase.OBSERVE: frozenset(
        {ReasoningPhase.THINK, ReasoningPhase.VERIFY, ReasoningPhase.DONE}
    ),
    ReasoningPhase.VERIFY: frozenset(
        {ReasoningPhase.DONE, ReasoningPhase.THINK}
    ),
    ReasoningPhase.AWAITING_USER: frozenset(
        {ReasoningPhase.THINK, ReasoningPhase.DONE}
    ),
    ReasoningPhase.DONE: frozenset({ReasoningPhase.IDLE}),
}


@dataclass
class TaskState:
    """Per-task state held inside a single agent.

    Mutates in place during a chat turn. The token field is the agent's
    handle to cooperative cancellation: stopping a task is
    ``state.cancel_token.cancel(reason)``, never raise-by-surprise.
    """

    task_id: str
    session_id: str = ""
    conversation_id: str = ""
    lifecycle: TaskLifecycleState = TaskLifecycleState.RECEIVED
    phase: ReasoningPhase = ReasoningPhase.IDLE
    cancel_token: CancellationToken = field(default_factory=CancellationToken)

    # Per-turn counters consumed by the agent's own self-checks. The
    # supervisor has its own counters; these are local hints.
    iteration: int = 0
    consecutive_tool_calls: int = 0
    tools_executed: list[str] = field(default_factory=list)
    last_tool_signature: str | None = None

    # Identifiers we need to surface to telemetry but the agent does not
    # mutate after task creation.
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def transition(self, target: ReasoningPhase) -> None:
        """Move to ``target``, validating the transition.

        Raises ``ValueError`` on illegal transitions. The legacy code
        logged a warning and forced the move; v2 raises so bugs surface
        in tests rather than corrupting state silently.
        """
        allowed = _VALID_PHASE_TRANSITIONS.get(self.phase, frozenset())
        if target not in allowed:
            raise ValueError(
                f"illegal ReasoningPhase transition: "
                f"{self.phase.value} -> {target.value} "
                f"(allowed: {sorted(s.value for s in allowed)})"
            )
        self.phase = target

    # ------------------------------------------------------------------
    # Cooperative cancel adapter
    # ------------------------------------------------------------------

    def cancel(self, reason: str = "") -> bool:
        """Cancel the task cooperatively. Idempotent."""
        if self.cancel_token.cancel(reason):
            self.lifecycle = TaskLifecycleState.CANCELLED
            return True
        return False

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_token.is_cancelled()

    # ------------------------------------------------------------------
    # Tool history (used by the agent's local stall heuristic)
    # ------------------------------------------------------------------

    def record_tool_call(self, name: str, signature: str | None = None) -> None:
        self.tools_executed.append(name)
        self.consecutive_tool_calls += 1
        if signature is not None:
            self.last_tool_signature = signature

    def reset_tool_streak(self) -> None:
        self.consecutive_tool_calls = 0

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def mark_done(self, *, success: bool) -> None:
        self.lifecycle = (
            TaskLifecycleState.DONE if success else TaskLifecycleState.FAILED
        )
        self.phase = ReasoningPhase.DONE
        self.finished_at = datetime.now(UTC)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "lifecycle": self.lifecycle.value,
            "phase": self.phase.value,
            "iteration": self.iteration,
            "consecutive_tool_calls": self.consecutive_tool_calls,
            "tools_executed": list(self.tools_executed),
            "last_tool_signature": self.last_tool_signature,
            "started_at": self.started_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "is_cancelled": self.is_cancelled,
        }


# ---------------------------------------------------------------------------
# AgentState — registry across sessions
# ---------------------------------------------------------------------------


def _new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


class AgentState:
    """Per-agent state holding a registry of in-flight tasks by session.

    A single :class:`Agent` instance can serve many sessions
    concurrently; we key tasks by ``session_id``. The legacy code added
    a ``current_task`` shim for back-compat; v2 drops the shim because
    callers always know which session they are in (the supervisor and
    messenger pass it through).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._lock = threading.RLock()
        self._initialized: bool = False
        self._running: bool = False

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def begin_task(
        self,
        *,
        session_id: str = "",
        conversation_id: str = "",
        task_id: str | None = None,
    ) -> TaskState:
        """Register a new TaskState for ``session_id``.

        Replaces any pre-existing task for the same session (same
        semantic as the legacy code, but explicit rather than
        background-cleared).
        """
        state = TaskState(
            task_id=task_id or _new_task_id(),
            session_id=session_id,
            conversation_id=conversation_id,
        )
        with self._lock:
            self._tasks[session_id or state.task_id] = state
        return state

    def get_task(self, session_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.get(session_id)

    def end_task(self, session_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.pop(session_id, None)

    def list_tasks(self) -> list[TaskState]:
        with self._lock:
            return list(self._tasks.values())

    def cancel_task(self, session_id: str, reason: str = "") -> bool:
        with self._lock:
            task = self._tasks.get(session_id)
        return task.cancel(reason) if task else False

    def cancel_all(self, reason: str = "agent shutdown") -> int:
        n = 0
        with self._lock:
            tasks = list(self._tasks.values())
        for t in tasks:
            if t.cancel(reason):
                n += 1
        return n

    # ------------------------------------------------------------------
    # Agent-level flags
    # ------------------------------------------------------------------

    @property
    def initialized(self) -> bool:
        return self._initialized

    @initialized.setter
    def initialized(self, value: bool) -> None:
        self._initialized = bool(value)

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = bool(value)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def to_jsonable(self) -> dict[str, Any]:
        with self._lock:
            tasks = [t.to_jsonable() for t in self._tasks.values()]
        return {
            "initialized": self._initialized,
            "running": self._running,
            "task_count": len(tasks),
            "tasks": tasks,
        }
