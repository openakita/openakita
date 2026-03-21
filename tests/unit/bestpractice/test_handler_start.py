"""Test that bp_start only creates instance, does not execute subtask."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_bp_start_does_not_call_execute_subtask():
    from seeagent.bestpractice.handler import BPToolHandler
    from seeagent.bestpractice.models import RunMode

    mock_engine = MagicMock()
    mock_engine.execute_subtask = AsyncMock()  # Should NOT be called
    mock_sm = MagicMock()
    mock_sm.get_active.return_value = None
    mock_sm.create_instance.return_value = "bp-new123"
    mock_cb = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.name = "Test BP"
    mock_cfg.default_run_mode = RunMode.MANUAL
    mock_cfg.subtasks = [MagicMock(id="s1", name="Step 1")]
    config_registry = {"test_bp": mock_cfg}

    handler = BPToolHandler(mock_engine, mock_sm, mock_cb, config_registry)

    agent = MagicMock()
    agent._current_session = MagicMock()
    agent._current_session.id = "sess-1"
    bus = asyncio.Queue()
    agent._current_session.context = MagicMock(_sse_event_bus=bus)

    result = await handler.handle("bp_start", {"bp_id": "test_bp"}, agent)

    # Should NOT have called execute_subtask (old behavior)
    mock_engine.execute_subtask.assert_not_called()
    # Should have created instance
    mock_sm.create_instance.assert_called_once()
    # Should have pushed bp_instance_created to event_bus
    event = bus.get_nowait()
    assert event["type"] == "bp_instance_created"
    assert event["instance_id"] == "bp-new123"
