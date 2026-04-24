"""L2 Component Tests: Plan system - creation, step management, completion, cancellation."""

import pytest

from openakita.tools.handlers.plan import (
    require_todo_for_session,
    is_todo_required,
    has_active_todo,
    register_active_todo,
    unregister_active_todo,
    clear_session_todo_state,
    auto_close_todo,
    cancel_todo,
    should_require_todo,
    get_active_todo_prompt,
)


class TestPlanSessionState:
    def test_initial_state(self):
        sid = "test-plan-session-1"
        clear_session_todo_state(sid)
        assert has_active_todo(sid) is False
        assert is_todo_required(sid) is False

    def test_require_todo(self):
        sid = "test-plan-session-2"
        clear_session_todo_state(sid)
        require_todo_for_session(sid, True)
        assert is_todo_required(sid) is True
        require_todo_for_session(sid, False)
        assert is_todo_required(sid) is False

    def test_register_and_unregister_todo(self):
        sid = "test-plan-session-3"
        clear_session_todo_state(sid)
        register_active_todo(sid, "plan-001")
        assert has_active_todo(sid) is True
        unregister_active_todo(sid)
        assert has_active_todo(sid) is False

    def test_cancel_todo(self):
        sid = "test-plan-session-4"
        clear_session_todo_state(sid)
        register_active_todo(sid, "plan-002")
        result = cancel_todo(sid)
        assert isinstance(result, bool)

    def test_auto_close_todo(self):
        sid = "test-plan-session-5"
        clear_session_todo_state(sid)
        result = auto_close_todo(sid)
        assert isinstance(result, bool)

    def test_clear_state(self):
        sid = "test-plan-session-6"
        register_active_todo(sid, "plan-003")
        require_todo_for_session(sid, True)
        clear_session_todo_state(sid)
        assert has_active_todo(sid) is False
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


class TestActiveTodoPrompt:
    def test_no_active_todo(self):
        sid = "test-plan-prompt-1"
        clear_session_todo_state(sid)
        prompt = get_active_todo_prompt(sid)
        assert isinstance(prompt, str)

