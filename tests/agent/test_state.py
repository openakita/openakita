"""Tests for :mod:`openakita.agent.state`.

Phase 2 commit 1. Asserts the v2 minimal-state design promised by
ADR-0003:

* ReasoningPhase transitions are validated; illegal moves raise
  rather than corrupting state silently;
* TaskState integrates with the runtime CancellationToken and exposes
  cooperative cancel without raising on the spot;
* mark_done sets lifecycle to DONE / FAILED, finished_at, and a
  computable duration;
* AgentState multi-session registry: begin_task replaces a session's
  prior task; cancel_task only affects the named session;
  cancel_all sweeps all in-flight tasks and returns the count;
* to_jsonable round-trips the public surface needed by the runtime
  debug endpoint.
"""

from __future__ import annotations

import asyncio

import pytest

from openakita.agent.state import (
    AgentState,
    ReasoningPhase,
    TaskState,
)
from openakita.runtime.models import TaskLifecycleState

# ---------------------------------------------------------------------------
# Phase machine
# ---------------------------------------------------------------------------


def test_phase_starts_idle_and_can_progress_to_think() -> None:
    state = TaskState(task_id="task_x")
    assert state.phase == ReasoningPhase.IDLE
    state.transition(ReasoningPhase.COMPILING)
    state.transition(ReasoningPhase.THINK)
    state.transition(ReasoningPhase.ACT)
    state.transition(ReasoningPhase.OBSERVE)
    state.transition(ReasoningPhase.THINK)


def test_phase_illegal_transition_raises() -> None:
    state = TaskState(task_id="task_x")
    state.transition(ReasoningPhase.THINK)
    # THINK -> COMPILING is illegal (compile is the entry phase only)
    with pytest.raises(ValueError) as info:
        state.transition(ReasoningPhase.COMPILING)
    msg = str(info.value)
    assert "think" in msg and "compiling" in msg


def test_done_returns_to_idle_only() -> None:
    state = TaskState(task_id="t")
    state.transition(ReasoningPhase.THINK)
    state.transition(ReasoningPhase.DONE)
    with pytest.raises(ValueError):
        state.transition(ReasoningPhase.THINK)
    state.transition(ReasoningPhase.IDLE)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_is_cooperative_does_not_raise() -> None:
    state = TaskState(task_id="t")
    assert state.is_cancelled is False
    assert state.cancel("user") is True
    assert state.is_cancelled is True
    assert state.lifecycle == TaskLifecycleState.CANCELLED


def test_cancel_is_idempotent() -> None:
    state = TaskState(task_id="t")
    assert state.cancel("first") is True
    assert state.cancel("second") is False
    assert state.cancel_token.reason == "first"


# ---------------------------------------------------------------------------
# Tool history
# ---------------------------------------------------------------------------


def test_record_tool_call_updates_streak_and_signature() -> None:
    state = TaskState(task_id="t")
    state.record_tool_call("hh_t2i", signature="t2i:portrait")
    state.record_tool_call("hh_t2i", signature="t2i:portrait")
    assert state.consecutive_tool_calls == 2
    assert state.last_tool_signature == "t2i:portrait"
    assert state.tools_executed == ["hh_t2i", "hh_t2i"]


def test_reset_tool_streak() -> None:
    state = TaskState(task_id="t")
    state.record_tool_call("a")
    state.record_tool_call("b")
    state.reset_tool_streak()
    assert state.consecutive_tool_calls == 0
    # tools_executed history is preserved
    assert state.tools_executed == ["a", "b"]


# ---------------------------------------------------------------------------
# mark_done
# ---------------------------------------------------------------------------


def test_mark_done_success() -> None:
    state = TaskState(task_id="t")
    state.transition(ReasoningPhase.THINK)
    state.mark_done(success=True)
    assert state.lifecycle == TaskLifecycleState.DONE
    assert state.phase == ReasoningPhase.DONE
    assert state.finished_at is not None
    assert state.duration_seconds is not None
    assert state.duration_seconds >= 0


def test_mark_done_failure() -> None:
    state = TaskState(task_id="t")
    state.transition(ReasoningPhase.THINK)
    state.mark_done(success=False)
    assert state.lifecycle == TaskLifecycleState.FAILED
    assert state.phase == ReasoningPhase.DONE


# ---------------------------------------------------------------------------
# AgentState registry
# ---------------------------------------------------------------------------


def test_begin_task_replaces_prior_session_task() -> None:
    agent = AgentState()
    a = agent.begin_task(session_id="sess_1")
    b = agent.begin_task(session_id="sess_1")
    assert a.task_id != b.task_id
    assert agent.get_task("sess_1") is b


def test_get_unknown_session_returns_none() -> None:
    agent = AgentState()
    assert agent.get_task("nope") is None


def test_cancel_task_only_targets_named_session() -> None:
    agent = AgentState()
    a = agent.begin_task(session_id="sess_a")
    b = agent.begin_task(session_id="sess_b")
    assert agent.cancel_task("sess_a", "stop") is True
    assert a.is_cancelled is True
    assert b.is_cancelled is False


def test_cancel_all_returns_first_cancel_count() -> None:
    agent = AgentState()
    a = agent.begin_task(session_id="sess_a")
    b = agent.begin_task(session_id="sess_b")
    c = agent.begin_task(session_id="sess_c")
    a.cancel("already")
    n = agent.cancel_all("shutdown")
    # b and c get cancelled; a was already cancelled and should not
    # double-count
    assert n == 2
    assert b.is_cancelled and c.is_cancelled


def test_end_task_returns_and_clears() -> None:
    agent = AgentState()
    a = agent.begin_task(session_id="sess_1")
    out = agent.end_task("sess_1")
    assert out is a
    assert agent.get_task("sess_1") is None


def test_list_tasks_returns_snapshot() -> None:
    agent = AgentState()
    agent.begin_task(session_id="sess_a")
    agent.begin_task(session_id="sess_b")
    snap = agent.list_tasks()
    assert {t.session_id for t in snap} == {"sess_a", "sess_b"}


def test_to_jsonable_exposes_task_count() -> None:
    agent = AgentState()
    agent.initialized = True
    agent.running = True
    agent.begin_task(session_id="sess_a")
    payload = agent.to_jsonable()
    assert payload["initialized"] is True
    assert payload["running"] is True
    assert payload["task_count"] == 1
    assert payload["tasks"][0]["session_id"] == "sess_a"


# ---------------------------------------------------------------------------
# CancellationToken integration with asyncio (legacy bug class)
# ---------------------------------------------------------------------------


async def test_cancel_token_unblocks_async_listener() -> None:
    """Cancelling a task must unblock anyone awaiting on
    state.cancel_token.wait_cancelled. This replaces the legacy
    cross-loop asyncio.Event hack."""
    state = TaskState(task_id="t")

    async def consumer() -> str:
        await state.cancel_token.wait_cancelled(poll_interval=0.01)
        return state.cancel_token.reason

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.02)
    state.cancel("user")
    out = await asyncio.wait_for(task, timeout=1.0)
    assert out == "user"
