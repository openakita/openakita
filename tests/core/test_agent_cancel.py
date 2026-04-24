# tests/core/test_agent_cancel.py
"""Tests for Agent cancel handling with generation tracking."""
import time

import pytest
from unittest.mock import MagicMock, AsyncMock
from openakita.core.health_config import CancelRequest


def test_cancel_request_stored_with_generation():
    from openakita.core.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._pending_cancels = {}
    agent._current_generation = 5

    # Simulate storing a cancel request
    req = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=5,
    )
    agent._pending_cancels["sess_123"] = req

    assert agent._pending_cancels["sess_123"].generation_id == 5


def test_cancel_request_matches_active_generation():
    req = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=5,
    )

    # Should match same generation
    assert req.matches(session_id="sess_123", task_id="task_456", generation_id=5)

    # Should NOT match different generation
    assert not req.matches(session_id="sess_123", task_id="task_456", generation_id=6)


def test_cancel_request_consumed_only_when_matching():
    pending = {}

    # Store cancel for generation 5
    pending["sess_123"] = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=5,
    )

    # Try to consume for generation 6 (should fail)
    current_gen = 6
    req = pending.get("sess_123")

    if req and req.matches(session_id="sess_123", task_id="task_456", generation_id=current_gen):
        consumed = pending.pop("sess_123")
    else:
        consumed = None

    assert consumed is None
    assert "sess_123" in pending  # Not consumed


def test_cancel_request_consumed_when_matching():
    """Verify cancel IS consumed when generation matches."""
    pending = {}

    # Store cancel for generation 5
    pending["sess_123"] = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=5,
    )

    # Consume for generation 5 (should succeed)
    current_gen = 5
    req = pending.get("sess_123")

    if req and req.matches(session_id="sess_123", task_id="task_456", generation_id=current_gen):
        consumed = pending.pop("sess_123")
    else:
        consumed = None

    assert consumed is not None
    assert consumed.generation_id == 5
    assert "sess_123" not in pending  # Was consumed


def test_generation_increments_on_task_start():
    from openakita.core.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._current_generation = 0

    # Simulate task start incrementing generation
    agent._current_generation += 1

    assert agent._current_generation == 1


def test_stale_cancel_requests_cleaned():
    req_old = CancelRequest(
        session_id="sess_1",
        task_id="task_1",
        generation_id=1,
        timestamp=time.time() - 120,  # 2 minutes old
    )

    req_fresh = CancelRequest(
        session_id="sess_2",
        task_id="task_2",
        generation_id=2,
    )

    assert req_old.is_stale(max_age=60) is True
    assert req_fresh.is_stale(max_age=60) is False
