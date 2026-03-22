# tests/unit/bestpractice/test_engine_stream.py
"""Tests for BPEngine._run_subtask_stream() and answer()."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from seeagent.bestpractice.models import (
    BPInstanceSnapshot, BestPracticeConfig, SubtaskConfig,
    SubtaskStatus, RunMode,
)
from seeagent.bestpractice.engine import BPEngine


def _make_config():
    return BestPracticeConfig(
        id="test_bp", name="Test BP",
        subtasks=[
            SubtaskConfig(
                id="s1", name="Step 1", agent_profile="default",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
        ],
        final_output_schema={"type": "object"},
    )


def _make_snap(cfg):
    return BPInstanceSnapshot(
        bp_id=cfg.id, instance_id="bp-test", session_id="sess-1",
        created_at=0.0, current_subtask_index=0,
        run_mode=RunMode.MANUAL,
        subtask_statuses={"s1": SubtaskStatus.WAITING_INPUT.value},
        initial_input={"q": "hello"},
        subtask_outputs={}, context_summary="",
        supplemented_inputs={},
    )


@pytest.mark.asyncio
class TestRunSubtaskStream:
    async def test_streams_events_from_delegate(self):
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)

        # Mock orchestrator.delegate to return result
        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='```json\n{"answer": "42"}\n```')
        engine.set_orchestrator(mock_orch)

        # Mock session with event_bus
        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        # Should have _internal_output as last event
        assert any(e["type"] == "_internal_output" for e in events)
        output_ev = next(e for e in events if e["type"] == "_internal_output")
        assert "answer" in output_ev["data"]

    async def test_delegate_task_exposed_on_context(self):
        """R17: delegate_task is stored on session.context._bp_delegate_task."""
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)
        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='{"ok": true}')
        engine.set_orchestrator(mock_orch)

        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "test"}, cfg, session
        ):
            events.append(ev)

        # After completion, _bp_delegate_task should be cleaned up (None)
        assert session.context._bp_delegate_task is None

    async def test_yields_error_when_no_orchestrator(self):
        """When no orchestrator is available, yields an error event."""
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)
        # Do NOT set orchestrator
        engine._get_orchestrator = MagicMock(return_value=None)

        session = MagicMock()
        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_passthrough_events_from_event_bus(self):
        """Whitelist tools generate step_cards; hidden tools are filtered;
        raw events (thinking, done) are skipped."""
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)

        async def fake_delegate(**kwargs):
            session = kwargs.get("session")
            bus = session.context._sse_event_bus
            # step_card passes through directly
            await bus.put({"type": "step_card", "step_id": "card1", "title": "existing", "status": "completed"})
            # web_search is WHITELIST → generates step_card via aggregator
            await bus.put({"type": "tool_call_start", "tool": "web_search", "id": "t1", "args": {"query": "test"}})
            await bus.put({"type": "tool_call_end", "tool": "web_search", "id": "t1", "is_error": False})
            # read_file is HIDDEN → no step_card generated
            await bus.put({"type": "tool_call_start", "tool": "read_file", "id": "t2", "args": {"path": "x"}})
            await bus.put({"type": "tool_call_end", "tool": "read_file", "id": "t2", "is_error": False})
            # Raw events filtered
            await bus.put({"type": "thinking", "data": "hmm"})
            await bus.put({"type": "done"})
            return '{"result": "ok"}'

        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(side_effect=fake_delegate)
        engine.set_orchestrator(mock_orch)

        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        event_types = [e["type"] for e in events]
        step_cards = [e for e in events if e["type"] == "step_card"]

        # Original step_card passes through
        assert any(c.get("step_id") == "card1" for c in step_cards)
        # web_search generates step_card (WHITELIST) with humanized title
        web_cards = [c for c in step_cards if "搜索" in (c.get("title") or "")]
        assert len(web_cards) >= 1
        # read_file does NOT generate step_card (HIDDEN)
        assert not any("read_file" in (c.get("title") or "") for c in step_cards)
        # Raw events filtered
        assert "thinking" not in event_types
        assert "done" not in event_types
        assert "_internal_output" in event_types

    async def test_skill_trigger_absorbs_inner_calls(self):
        """Skill trigger creates one card, inner tool calls are absorbed."""
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)

        async def fake_delegate(**kwargs):
            session = kwargs.get("session")
            bus = session.context._sse_event_bus
            # Skill trigger → creates aggregated card
            await bus.put({"type": "tool_call_start", "tool": "load_skill", "id": "sk1", "args": {"name": "researcher"}})
            # Inner calls → absorbed
            await bus.put({"type": "tool_call_start", "tool": "web_search", "id": "ws1", "args": {"query": "test"}})
            await bus.put({"type": "tool_call_end", "tool": "web_search", "id": "ws1", "is_error": False})
            await bus.put({"type": "tool_call_start", "tool": "read_file", "id": "rf1", "args": {"path": "x"}})
            await bus.put({"type": "tool_call_end", "tool": "read_file", "id": "rf1", "is_error": False})
            # Skill completes
            await bus.put({"type": "tool_call_end", "tool": "load_skill", "id": "sk1", "is_error": False})
            # text_delta triggers aggregation flush
            await bus.put({"type": "text_delta", "content": "result"})
            return '{"result": "ok"}'

        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(side_effect=fake_delegate)
        engine.set_orchestrator(mock_orch)

        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = None
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        events = []
        async for ev in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            events.append(ev)

        step_cards = [e for e in events if e["type"] == "step_card"]
        # Skill creates exactly 2 cards: running + completed
        skill_cards = [c for c in step_cards if c.get("source_type") == "skill"]
        assert len(skill_cards) == 2
        running = [c for c in skill_cards if c["status"] == "running"]
        completed = [c for c in skill_cards if c["status"] == "completed"]
        assert len(running) == 1
        assert len(completed) == 1
        # Completed card should have absorbed_calls
        assert len(completed[0].get("absorbed_calls", [])) >= 2
        # No independent web_search or read_file cards
        independent = [c for c in step_cards if c.get("source_type") == "tool"]
        assert len(independent) == 0

    async def test_restores_old_event_bus(self):
        """After stream completes, the old event_bus is restored."""
        cfg = _make_config()
        sm = MagicMock()
        engine = BPEngine(sm)

        mock_orch = AsyncMock()
        mock_orch.delegate = AsyncMock(return_value='{"ok": true}')
        engine.set_orchestrator(mock_orch)

        old_bus = asyncio.Queue()
        session = MagicMock()
        ctx = MagicMock()
        ctx._sse_event_bus = old_bus
        ctx._bp_delegate_task = None
        session.context = ctx

        subtask = cfg.subtasks[0]
        async for _ in engine._run_subtask_stream(
            "bp-test", subtask, {"q": "hello"}, cfg, session
        ):
            pass

        # Old bus should be restored
        assert session.context._sse_event_bus is old_bus


@pytest.mark.asyncio
class TestAnswer:
    async def test_answer_merges_supplemented_inputs_and_resets_status(self):
        cfg = _make_config()
        snap = _make_snap(cfg)
        snap.bp_config = cfg
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        engine._get_config = MagicMock(return_value=cfg)

        # Mock _run_subtask_stream so advance() works
        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"answer": "yes"}}
        engine._run_subtask_stream = mock_stream

        session = MagicMock()
        events = []
        async for ev in engine.answer("bp-test", "s1", {"extra_field": "val"}, session):
            events.append(ev)

        # supplemented_inputs should be updated
        assert snap.supplemented_inputs["s1"] == {"extra_field": "val"}
        # Status should have been reset to PENDING
        sm.update_subtask_status.assert_any_call(
            "bp-test", "s1", SubtaskStatus.PENDING
        )

    async def test_answer_instance_not_found(self):
        sm = MagicMock()
        sm.get.return_value = None
        engine = BPEngine(sm)

        events = []
        async for ev in engine.answer("bp-missing", "s1", {"q": "test"}, MagicMock()):
            events.append(ev)

        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_answer_merges_with_existing_supplemented_data(self):
        """When supplemented_inputs already has data, it should be merged."""
        cfg = _make_config()
        snap = _make_snap(cfg)
        snap.bp_config = cfg
        snap.supplemented_inputs["s1"] = {"old_field": "old_val"}
        sm = MagicMock()
        sm.get.return_value = snap
        sm.update_subtask_status = MagicMock()
        engine = BPEngine(sm)
        engine._get_config = MagicMock(return_value=cfg)

        async def mock_stream(*args, **kwargs):
            yield {"type": "_internal_output", "data": {"answer": "yes"}}
        engine._run_subtask_stream = mock_stream

        session = MagicMock()
        events = []
        async for ev in engine.answer("bp-test", "s1", {"new_field": "new_val"}, session):
            events.append(ev)

        # Both old and new fields should exist
        assert snap.supplemented_inputs["s1"]["old_field"] == "old_val"
        assert snap.supplemented_inputs["s1"]["new_field"] == "new_val"
