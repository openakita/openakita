# tests/unit/bestpractice/test_engine_advance.py
"""Tests for BPEngine.advance() async generator."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from seeagent.bestpractice.models import (
    BPInstanceSnapshot, BestPracticeConfig, SubtaskConfig,
    SubtaskStatus, RunMode, BPStatus,
)
from seeagent.bestpractice.engine import BPEngine


def _make_config(subtask_count=2):
    subtasks = [
        SubtaskConfig(
            id=f"s{i+1}", name=f"Step {i+1}", agent_profile="default",
            input_schema={"type": "object", "properties": {"data": {"type": "string"}}},
        )
        for i in range(subtask_count)
    ]
    return BestPracticeConfig(
        id="test_bp", name="Test BP", subtasks=subtasks,
        final_output_schema={"type": "object"},
    )


def _make_snap(cfg, current_index=0, run_mode=RunMode.MANUAL, statuses=None):
    sids = [s.id for s in cfg.subtasks]
    sts = statuses or {sid: SubtaskStatus.PENDING.value for sid in sids}
    snap = BPInstanceSnapshot(
        bp_id=cfg.id, instance_id="bp-test", session_id="sess-1",
        created_at=0.0, current_subtask_index=current_index,
        run_mode=run_mode, subtask_statuses=sts,
        initial_input={"data": "hello"}, subtask_outputs={},
        context_summary="", supplemented_inputs={},
    )
    snap.bp_config = cfg
    return snap


async def _collect_events(engine, instance_id, session):
    events = []
    async for ev in engine.advance(instance_id, session):
        events.append(ev)
    return events


@pytest.mark.asyncio
class TestAdvanceManualMode:
    async def test_yields_subtask_start_and_complete(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        sm = MagicMock()
        sm.get.return_value = snap
        sm.complete = MagicMock()
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        # Mock _run_subtask_stream to yield output directly
        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"data": "result1"}}
        engine._run_subtask_stream = mock_stream
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        types = [e["type"] for e in events]
        assert "bp_subtask_start" in types
        assert "bp_subtask_complete" in types
        assert "bp_waiting_next" in types
        # Should NOT have bp_complete (only 1 of 2 done)
        assert "bp_complete" not in types

    async def test_yields_bp_complete_on_last_subtask(self):
        cfg = _make_config(subtask_count=1)
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        sm = MagicMock()
        sm.get.return_value = snap
        sm.complete = MagicMock()
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)

        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"result": "final"}}
        engine._run_subtask_stream = mock_stream
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        types = [e["type"] for e in events]
        assert "bp_complete" in types
        assert "bp_waiting_next" not in types


@pytest.mark.asyncio
class TestAdvanceAutoMode:
    async def test_auto_executes_all_subtasks(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snap(cfg, run_mode=RunMode.AUTO)
        sm = MagicMock()
        sm.get.return_value = snap
        sm.complete = MagicMock()
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)

        call_count = 0
        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            yield {"type": "_internal_output", "data": {"data": f"out{call_count}"}}
        engine._run_subtask_stream = mock_stream
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        types = [e["type"] for e in events]
        # Should have 2 subtask_start + 2 subtask_complete + bp_complete
        assert types.count("bp_subtask_start") == 2
        assert types.count("bp_subtask_complete") == 2
        assert "bp_complete" in types
        assert "bp_waiting_next" not in types


@pytest.mark.asyncio
class TestAdvanceAskUser:
    async def test_missing_required_field_yields_ask_user(self):
        cfg = _make_config(subtask_count=1)
        cfg.subtasks[0].input_schema = {
            "type": "object",
            "properties": {"data": {"type": "string"}},
            "required": ["data"],
        }
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        snap.initial_input = {}  # Missing "data" field
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        types = [e["type"] for e in events]
        assert "bp_ask_user" in types
        ask_ev = next(e for e in events if e["type"] == "bp_ask_user")
        assert "data" in ask_ev["missing_fields"]

    async def test_ask_user_includes_input_schema(self):
        cfg = BestPracticeConfig(
            id="test_bp", name="Test BP",
            subtasks=[
                SubtaskConfig(
                    id="s1", name="Step 1", agent_profile="default",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "主题"},
                        },
                        "required": ["topic"],
                    },
                ),
                SubtaskConfig(id="s2", name="Step 2", agent_profile="default"),
            ],
        )
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        snap.initial_input = {}  # topic 缺失
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        ask_events = [e for e in events if e["type"] == "bp_ask_user"]
        assert len(ask_events) == 1
        assert "input_schema" in ask_events[0]
        assert ask_events[0]["input_schema"]["properties"]["topic"]["description"] == "主题"


@pytest.mark.asyncio
class TestAdvanceErrorHandling:
    async def test_delegate_exception_marks_failed(self):
        """R20: delegate exception -> mark FAILED + yield bp_error."""
        cfg = _make_config(subtask_count=1)
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        snap.initial_input = {"data": "hello"}
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        engine._get_config = MagicMock(return_value=cfg)

        async def mock_stream_raises(*args, **kwargs):
            raise RuntimeError("SubAgent crashed")
            yield  # make it a generator
        engine._run_subtask_stream = mock_stream_raises

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        types = [e["type"] for e in events]
        assert "bp_error" in types
        # Verify FAILED status was set
        sm.update_subtask_status.assert_any_call(
            "bp-test", "s1", SubtaskStatus.FAILED
        )

    async def test_instance_not_found_yields_error(self):
        sm = MagicMock()
        sm.get.return_value = None
        engine = BPEngine(sm)

        session = MagicMock()
        events = await _collect_events(engine, "bp-missing", session)
        assert len(events) == 1
        assert events[0]["type"] == "error"


@pytest.mark.asyncio
class TestAdvanceInitialProgress:
    async def test_first_event_is_bp_progress(self):
        """Gap 1: advance() must yield bp_progress before any subtask work."""
        cfg = _make_config(subtask_count=2)
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        sm = MagicMock()
        sm.get.return_value = snap
        sm.complete = MagicMock()
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)

        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"data": "result1"}}
        engine._run_subtask_stream = mock_stream
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        # First event must be bp_progress
        assert events[0]["type"] == "bp_progress"
        assert events[0]["instance_id"] == "bp-test"
        assert events[0]["bp_name"] == "Test BP"


@pytest.mark.asyncio
class TestAdvanceDelegateCards:
    async def test_yields_delegate_card_running_and_completed(self):
        """Gap 5: advance() yields a delegate step_card before/after subtask stream."""
        cfg = _make_config(subtask_count=1)
        snap = _make_snap(cfg, run_mode=RunMode.MANUAL)
        sm = MagicMock()
        sm.get.return_value = snap
        sm.complete = MagicMock()
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)

        async def mock_stream(*args, **kwargs):
            yield {"type": "step_card", "step_id": "tool_1", "status": "completed"}
            yield {"type": "_internal_output", "data": {"result": "done"}}
        engine._run_subtask_stream = mock_stream
        engine._get_config = MagicMock(return_value=cfg)

        session = MagicMock()
        events = await _collect_events(engine, "bp-test", session)

        step_cards = [e for e in events if e["type"] == "step_card"]
        # Should have: 1 delegate running + 1 tool completed + 1 delegate completed
        delegate_cards = [c for c in step_cards if c.get("card_type") == "delegate"]
        assert len(delegate_cards) == 2
        assert delegate_cards[0]["status"] == "running"
        assert delegate_cards[1]["status"] == "completed"
        assert delegate_cards[1]["duration"] is not None
        # Same step_id for both
        assert delegate_cards[0]["step_id"] == delegate_cards[1]["step_id"]
