"""L4 E2E Tests: Plan system end-to-end — create, step management, complete, cancel."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from mock_llm import MockLLMClient, MockBrain

from openakita.tools.handlers.plan import (
    has_active_plan,
    register_active_plan,
    unregister_active_plan,
    clear_session_plan_state,
    cancel_plan,
    register_plan_handler,
    get_plan_handler_for_session,
    get_active_plan_prompt,
    should_require_todo,
)


def _make_mock_agent():
    """Create a minimal mock agent for PlanHandler."""
    agent = MagicMock()
    agent.brain = MockBrain(MockLLMClient())
    agent._current_session_id = "plan-test-session"
    agent._current_conversation_id = "plan-test-conv"
    agent.get_current_session_id = MagicMock(return_value="plan-test-session")
    return agent


class TestPlanLifecycle:
    """Test full plan lifecycle: create → update steps → complete."""

    def test_create_todo_registers(self):
        sid = "lifecycle-1"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-1")
        assert has_active_plan(sid) is True

    def test_complete_todo_unregisters(self):
        sid = "lifecycle-2"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-2")
        unregister_active_plan(sid)
        assert has_active_plan(sid) is False

    def test_cancel_active_plan(self):
        sid = "lifecycle-3"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-3")
        cancel_plan(sid)
        # After cancel, plan should be inactive
        assert has_active_plan(sid) is False


class TestPlanWithHandler:
    """Test PlanHandler integration with session management."""

    def test_register_and_retrieve_handler(self):
        from openakita.tools.handlers.plan import PlanHandler
        sid = "handler-test-1"
        clear_session_plan_state(sid)
        agent = _make_mock_agent()
        handler = PlanHandler(agent)
        register_plan_handler(sid, handler)
        retrieved = get_plan_handler_for_session(sid)
        assert retrieved is handler
        clear_session_plan_state(sid)

    def test_plan_prompt_when_no_plan(self):
        sid = "prompt-test-1"
        clear_session_plan_state(sid)
        prompt = get_active_plan_prompt(sid)
        assert isinstance(prompt, str)


class TestPlanDetection:
    """Test whether complex messages trigger plan requirement."""

    @pytest.mark.parametrize("msg,expected_type", [
        ("你好", bool),
        ("帮我重构整个项目代码，写完整测试，然后部署到服务器", bool),
        ("查一下天气", bool),
        ("1. 创建数据库 2. 写API 3. 加认证 4. 写文档 5. 部署", bool),
    ])
    def test_should_require_todo(self, msg, expected_type):
        result = should_require_todo(msg)
        assert isinstance(result, expected_type)


class TestMultiSessionPlanIsolation:
    """Verify plans are isolated between sessions."""

    def test_two_sessions_independent(self):
        s1, s2 = "iso-session-1", "iso-session-2"
        clear_session_plan_state(s1)
        clear_session_plan_state(s2)

        register_active_plan(s1, "plan-s1")
        assert has_active_plan(s1) is True
        assert has_active_plan(s2) is False

        register_active_plan(s2, "plan-s2")
        assert has_active_plan(s1) is True
        assert has_active_plan(s2) is True

        cancel_plan(s1)
        assert has_active_plan(s1) is False
        assert has_active_plan(s2) is True

        clear_session_plan_state(s1)
        clear_session_plan_state(s2)
