"""L2 Component Tests: Plan system - creation, step management, completion, cancellation."""

import pytest

from openakita.tools.handlers.plan import (
    require_todo_for_session,
    is_todo_required,
    has_active_plan,
    register_active_plan,
    unregister_active_plan,
    clear_session_plan_state,
    auto_close_plan,
    cancel_plan,
    should_require_todo,
    get_active_plan_prompt,
)


class TestPlanSessionState:
    def test_initial_state(self):
        sid = "test-plan-session-1"
        clear_session_plan_state(sid)
        assert has_active_plan(sid) is False
        assert is_todo_required(sid) is False

    def test_require_todo(self):
        sid = "test-plan-session-2"
        clear_session_plan_state(sid)
        require_todo_for_session(sid, True)
        assert is_todo_required(sid) is True
        require_todo_for_session(sid, False)
        assert is_todo_required(sid) is False

    def test_register_and_unregister_plan(self):
        sid = "test-plan-session-3"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-001")
        assert has_active_plan(sid) is True
        unregister_active_plan(sid)
        assert has_active_plan(sid) is False

    def test_cancel_plan(self):
        sid = "test-plan-session-4"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-002")
        result = cancel_plan(sid)
        assert isinstance(result, bool)

    def test_auto_close_plan(self):
        sid = "test-plan-session-5"
        clear_session_plan_state(sid)
        result = auto_close_plan(sid)
        assert isinstance(result, bool)

    def test_clear_state(self):
        sid = "test-plan-session-6"
        register_active_plan(sid, "plan-003")
        require_todo_for_session(sid, True)
        clear_session_plan_state(sid)
        assert has_active_plan(sid) is False
        assert is_todo_required(sid) is False


class TestShouldRequireTodo:
    def test_simple_message(self):
        result = should_require_todo("你好")
        assert isinstance(result, bool)

    def test_complex_task(self):
        result = should_require_todo("帮我重构整个数据库层，然后写单元测试，最后部署到生产环境")
        assert isinstance(result, bool)

    def test_empty_message(self):
        result = should_require_todo("")
        assert isinstance(result, bool)


class TestActivePlanPrompt:
    def test_no_active_plan(self):
        sid = "test-plan-prompt-1"
        clear_session_plan_state(sid)
        prompt = get_active_plan_prompt(sid)
        assert isinstance(prompt, str)
