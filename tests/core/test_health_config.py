# tests/core/test_health_config.py
import time

import pytest
from openakita.core.health_config import CancelRequest, HealthConfig, EscalationThresholds


def test_health_config_defaults():
    config = HealthConfig()
    assert config.stale_task_age == 3600
    assert config.stale_delegation_age == 1800
    assert config.check_interval == 300
    assert config.max_sub_agent_states == 1000


def test_health_config_custom_values():
    config = HealthConfig(stale_task_age=1800, check_interval=60)
    assert config.stale_task_age == 1800
    assert config.check_interval == 60


def test_escalation_thresholds_defaults():
    thresholds = EscalationThresholds()
    assert thresholds.soft_nudge == 3
    assert thresholds.force_tool == 5
    assert thresholds.model_switch == 7
    assert thresholds.terminate == 10


def test_escalation_level_for_count():
    thresholds = EscalationThresholds()
    assert thresholds.level_for_count(2) is None
    assert thresholds.level_for_count(3) == "soft_nudge"
    assert thresholds.level_for_count(5) == "force_tool"
    assert thresholds.level_for_count(7) == "model_switch"
    assert thresholds.level_for_count(10) == "terminate"
    assert thresholds.level_for_count(15) == "terminate"


def test_cancel_request_creation():
    req = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=7,
    )
    assert req.session_id == "sess_123"
    assert req.task_id == "task_456"
    assert req.generation_id == 7
    assert req.timestamp > 0


def test_cancel_request_matches():
    req = CancelRequest(
        session_id="sess_123",
        task_id="task_456",
        generation_id=7,
    )
    assert req.matches(session_id="sess_123", task_id="task_456", generation_id=7)
    assert not req.matches(session_id="sess_123", task_id="task_456", generation_id=8)
    assert not req.matches(session_id="sess_999", task_id="task_456", generation_id=7)


def test_cancel_request_is_stale():
    old_req = CancelRequest(
        session_id="s",
        task_id="t",
        generation_id=1,
        timestamp=time.time() - 120,
    )
    assert old_req.is_stale(max_age=60) is True

    fresh_req = CancelRequest(
        session_id="s",
        task_id="t",
        generation_id=1,
    )
    assert fresh_req.is_stale(max_age=60) is False
