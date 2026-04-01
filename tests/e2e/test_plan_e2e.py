"""L4 E2E Tests: Plan system end-to-end — create, step management, complete, cancel."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from mock_llm import MockBrain, MockLLMClient

from openakita.tools.handlers.plan import (
    cancel_todo,
    clear_session_todo_state,
    get_active_todo_prompt,
    get_todo_handler_for_session,
    has_active_todo,
    register_active_todo,
    register_plan_handler,
    should_require_todo,
    unregister_active_todo,
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
        clear_session_todo_state(sid)
        register_active_todo(sid, "plan-lc-1")
        assert has_active_todo(sid) is True

    def test_complete_todo_unregisters(self):
        sid = "lifecycle-2"
        clear_session_todo_state(sid)
        register_active_todo(sid, "plan-lc-2")
        unregister_active_todo(sid)
        assert has_active_todo(sid) is False

    def test_cancel_active_todo(self):
        sid = "lifecycle-3"
        clear_session_todo_state(sid)
        register_active_todo(sid, "plan-lc-3")
        cancel_todo(sid)
        assert has_active_todo(sid) is False


class TestPlanWithHandler:
    """Test PlanHandler integration with session management."""

    def test_register_and_retrieve_handler(self):
        from openakita.tools.handlers.plan import PlanHandler
        sid = "handler-test-1"
        clear_session_todo_state(sid)
        agent = _make_mock_agent()
        handler = PlanHandler(agent)
        register_plan_handler(sid, handler)
        retrieved = get_todo_handler_for_session(sid)
        assert retrieved is handler
        clear_session_todo_state(sid)

    def test_todo_prompt_when_no_todo(self):
        sid = "prompt-test-1"
        clear_session_todo_state(sid)
        prompt = get_active_todo_prompt(sid)
        assert isinstance(prompt, str)

    @pytest.mark.asyncio
    async def test_create_todo_accepts_stringified_steps(self):
        from openakita.tools.handlers.plan import PlanHandler

        agent = _make_mock_agent()
        handler = PlanHandler(agent)
        result = await handler.handle(
            "create_todo",
            {
                "task_summary": "demo",
                "steps": '[{"id":"step_1","description":"first"},{"id":"step_2","description":"second"}]',
            },
        )

        assert "Created todo" in result
        plan = handler.get_plan_for("plan-test-conv")
        assert plan is not None
        assert len(plan["steps"]) == 2
        assert plan["steps"][0]["description"] == "first"
        assert plan["steps"][1]["description"] == "second"
        clear_session_todo_state("plan-test-conv")

    @pytest.mark.asyncio
    async def test_create_todo_rejects_invalid_steps_shape(self):
        from openakita.tools.handlers.plan import PlanHandler

        agent = _make_mock_agent()
        handler = PlanHandler(agent)

        # object instead of array
        result = await handler.handle(
            "create_todo",
            {"task_summary": "demo", "steps": '{"id":"only_step"}'},
        )
        assert "steps 参数格式错误" in result

        # array item must be object
        result = await handler.handle(
            "create_todo",
            {"task_summary": "demo", "steps": '["bad_item"]'},
        )
        assert "steps[0] 格式错误" in result


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
        clear_session_todo_state(s1)
        clear_session_todo_state(s2)

        register_active_todo(s1, "plan-s1")
        assert has_active_todo(s1) is True
        assert has_active_todo(s2) is False

        register_active_todo(s2, "plan-s2")
        assert has_active_todo(s1) is True
        assert has_active_todo(s2) is True

        cancel_todo(s1)
        assert has_active_todo(s1) is False
        assert has_active_todo(s2) is True

        clear_session_todo_state(s1)
        clear_session_todo_state(s2)
