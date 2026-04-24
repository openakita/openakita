# tests/core/test_task_monitor.py
import pytest

from openakita.core.task_monitor import TaskMonitor, IdleNudge


@pytest.fixture
def monitor():
    """Create a TaskMonitor instance for testing."""
    return TaskMonitor(
        task_id="test_task",
        description="Test task for idle nudge tests",
    )


def test_idle_nudge_soft_at_3_iterations(monitor):
    monitor._consecutive_zero_tool_iterations = 3

    nudge = monitor.get_idle_loop_nudge()

    assert nudge is not None
    assert nudge.level == "soft_nudge"
    assert "3 consecutive iterations" in nudge.message


def test_idle_nudge_force_tool_at_5_iterations(monitor):
    monitor._consecutive_zero_tool_iterations = 5

    nudge = monitor.get_idle_loop_nudge()

    assert nudge is not None
    assert nudge.level == "force_tool"
    assert "MUST use a tool NOW" in nudge.message


def test_idle_nudge_model_switch_at_7_iterations(monitor):
    monitor._consecutive_zero_tool_iterations = 7

    nudge = monitor.get_idle_loop_nudge()

    assert nudge is not None
    assert nudge.level == "model_switch"
    assert nudge.should_switch_model is True


def test_idle_nudge_terminate_at_10_iterations(monitor):
    monitor._consecutive_zero_tool_iterations = 10

    nudge = monitor.get_idle_loop_nudge()

    assert nudge is not None
    assert nudge.level == "terminate"
    assert nudge.should_terminate is True


def test_idle_nudge_none_below_threshold(monitor):
    monitor._consecutive_zero_tool_iterations = 2

    nudge = monitor.get_idle_loop_nudge()

    assert nudge is None


def test_idle_nudge_dataclass_fields():
    """Test IdleNudge dataclass structure."""
    nudge = IdleNudge(
        level="force_tool",
        message="Test message",
        should_switch_model=False,
        should_terminate=False,
    )
    assert nudge.level == "force_tool"
    assert nudge.message == "Test message"
    assert nudge.should_switch_model is False
    assert nudge.should_terminate is False
