"""L2 Component Tests: ReasoningEngine decision routing and loop detection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.core.agent_state import AgentState, TaskState, TaskStatus
from openakita.core.reasoning_engine import DecisionType


class TestDecisionType:
    def test_enum_values(self):
        assert DecisionType.FINAL_ANSWER.value == "final_answer"
        assert DecisionType.TOOL_CALLS.value == "tool_calls"


class TestTaskState:
    def test_initial_status(self):
        ts = TaskState(task_id="t1")
        assert ts.status == TaskStatus.IDLE

    def test_transition(self):
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        assert ts.status == TaskStatus.REASONING

    def test_cancel(self):
        ts = TaskState(task_id="t1")
        ts.cancel("user stopped")
        assert ts.cancelled is True
        assert ts.cancel_event.is_set()
        assert ts.cancel_reason == "user stopped"

    def test_skip(self):
        ts = TaskState(task_id="t1")
        ts.request_skip("skip this step")
        assert ts.skip_event.is_set()
        assert ts.skip_reason == "skip this step"

    def test_clear_skip(self):
        ts = TaskState(task_id="t1")
        ts.request_skip("skip")
        ts.clear_skip()
        assert not ts.skip_event.is_set()
        assert ts.skip_reason == ""

    async def test_user_insert(self):
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("new message")
        inserts = await ts.drain_user_inserts()
        assert inserts == ["new message"]

    async def test_drain_clears_inserts(self):
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("msg1")
        await ts.add_user_insert("msg2")
        inserts = await ts.drain_user_inserts()
        assert len(inserts) == 2
        empty = await ts.drain_user_inserts()
        assert empty == []

    def test_record_tool_execution(self):
        ts = TaskState(task_id="t1")
        ts.record_tool_execution(["read_file", "write_file"])
        assert "read_file" in ts.tools_executed
        assert ts.tools_executed_in_task is True

    def test_record_tool_signature(self):
        ts = TaskState(task_id="t1")
        for _ in range(10):
            ts.record_tool_signature("search:query=test")
        assert len(ts.recent_tool_signatures) <= ts.tool_pattern_window

    def test_is_active_property(self):
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        assert ts.is_active is True
        ts.transition(TaskStatus.COMPLETED)
        assert ts.is_active is False

    def test_is_terminal_property(self):
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.transition(TaskStatus.COMPLETED)
        assert ts.is_terminal is True
        ts2 = TaskState(task_id="t2")
        ts2.transition(TaskStatus.REASONING)
        ts2.transition(TaskStatus.FAILED)
        assert ts2.is_terminal is True


class TestAgentState:
    def test_begin_task(self):
        state = AgentState()
        ts = state.begin_task(session_id="s1")
        assert isinstance(ts, TaskState)
        ts.transition(TaskStatus.REASONING)
        assert state.has_active_task is True

    def test_reset_task(self):
        state = AgentState()
        state.begin_task(session_id="s1")
        state.reset_task()
        assert state.has_active_task is False

    def test_cancel_task(self):
        state = AgentState()
        ts = state.begin_task(session_id="s1")
        state.cancel_task("user stopped")
        assert state.is_task_cancelled is True
        assert state.task_cancel_reason == "user stopped"

    def test_skip_current_step(self):
        state = AgentState()
        ts = state.begin_task(session_id="s1")
        state.skip_current_step("skip")
        assert ts.skip_event.is_set()

    async def test_insert_user_message(self):
        state = AgentState()
        ts = state.begin_task(session_id="s1")
        await state.insert_user_message("hello")
        inserts = await ts.drain_user_inserts()
        assert inserts == ["hello"]

    def test_get_task_for_session(self):
        state = AgentState()
        state.begin_task(session_id="s1")
        task = state.get_task_for_session("s1")
        assert task is not None
        assert task.session_id == "s1"

    def test_get_task_for_unknown_session(self):
        state = AgentState()
        task = state.get_task_for_session("unknown")
        assert task is None

