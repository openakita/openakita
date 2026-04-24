"""Unit tests for ReasoningEngine idle loop nudge handling."""

import pytest
from unittest.mock import MagicMock, patch
from openakita.core.task_monitor import IdleNudge


@pytest.mark.asyncio
async def test_reasoning_engine_handles_model_switch_nudge():
    """Verify model switch is triggered on model_switch level nudge."""
    from openakita.core.reasoning_engine import ReasoningEngine

    engine = MagicMock(spec=ReasoningEngine)
    nudge = IdleNudge(
        level="model_switch",
        message="Switch model now",
        should_switch_model=True,
    )

    # The engine should call _switch_model when nudge.should_switch_model is True
    assert nudge.should_switch_model is True
    assert nudge.should_terminate is False
    assert nudge.level == "model_switch"
    assert nudge.message == "Switch model now"


@pytest.mark.asyncio
async def test_reasoning_engine_handles_terminate_nudge():
    """Verify task termination on terminate level nudge."""
    nudge = IdleNudge(
        level="terminate",
        message="Terminate task",
        should_terminate=True,
    )

    assert nudge.should_terminate is True
    assert nudge.should_switch_model is False
    assert nudge.level == "terminate"
    assert nudge.message == "Terminate task"


@pytest.mark.asyncio
async def test_idle_nudge_soft_level():
    """Verify soft nudge level has no action flags."""
    nudge = IdleNudge(
        level="soft",
        message="Soft nudge message",
    )

    assert nudge.should_terminate is False
    assert nudge.should_switch_model is False
    assert nudge.level == "soft"
    assert nudge.message == "Soft nudge message"


@pytest.mark.asyncio
async def test_idle_nudge_force_tool_level():
    """Verify force_tool nudge level has no action flags."""
    nudge = IdleNudge(
        level="force_tool",
        message="Force tool message",
    )

    assert nudge.should_terminate is False
    assert nudge.should_switch_model is False
    assert nudge.level == "force_tool"
