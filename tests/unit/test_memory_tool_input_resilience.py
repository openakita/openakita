from types import SimpleNamespace
from unittest.mock import MagicMock

from openakita.memory.types import MemoryPriority
from openakita.tools.handlers.memory import MemoryHandler
from openakita.tools.input_normalizer import normalize_tool_input


def test_normalize_tool_input_coerces_schema_scalar_strings():
    schema = {
        "type": "object",
        "properties": {
            "importance": {"type": "number"},
            "limit": {"type": "integer"},
            "clear": {"type": "boolean"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "enabled": {"type": "boolean"},
                    },
                },
            },
        },
    }

    normalized = normalize_tool_input(
        "demo_tool",
        {
            "importance": "0.8",
            "limit": "3",
            "clear": "false",
            "items": '[{"score": "0.6", "enabled": "yes"}]',
        },
        schema=schema,
    )

    assert normalized["importance"] == 0.8
    assert normalized["limit"] == 3
    assert normalized["clear"] is False
    assert normalized["items"][0]["score"] == 0.6
    assert normalized["items"][0]["enabled"] is True


def test_normalize_tool_input_leaves_invalid_scalar_strings_unchanged():
    schema = {
        "type": "object",
        "properties": {
            "importance": {"type": "number"},
            "limit": {"type": "integer"},
            "clear": {"type": "boolean"},
        },
    }

    normalized = normalize_tool_input(
        "demo_tool",
        {"importance": "high", "limit": "3.5", "clear": "maybe"},
        schema=schema,
    )

    assert normalized == {"importance": "high", "limit": "3.5", "clear": "maybe"}


def test_add_memory_accepts_string_importance():
    mm = MagicMock()
    mm.store.search_semantic.return_value = []
    mm.add_memory.return_value = "mem-1"
    agent = SimpleNamespace(memory_manager=mm, profile_manager=None)
    handler = MemoryHandler(agent)

    result = handler._add_memory({"content": "用户喜欢简洁回答", "importance": "0.8"})

    assert "已记住" in result
    saved_memory = mm.add_memory.call_args.args[0]
    assert saved_memory.importance_score == 0.8
    assert saved_memory.priority == MemoryPriority.PERMANENT


def test_add_memory_defaults_invalid_importance_without_blocking():
    mm = MagicMock()
    mm.store.search_semantic.return_value = []
    mm.add_memory.return_value = "mem-1"
    agent = SimpleNamespace(memory_manager=mm, profile_manager=None)
    handler = MemoryHandler(agent)

    result = handler._add_memory({"content": "用户偏好直接结论", "importance": "high"})

    assert "已记住" in result
    saved_memory = mm.add_memory.call_args.args[0]
    assert saved_memory.importance_score == 0.5
    assert saved_memory.priority == MemoryPriority.SHORT_TERM


def test_add_memory_defaults_boolean_importance_without_promoting_priority():
    mm = MagicMock()
    mm.store.search_semantic.return_value = []
    mm.add_memory.return_value = "mem-1"
    agent = SimpleNamespace(memory_manager=mm, profile_manager=None)
    handler = MemoryHandler(agent)

    result = handler._add_memory({"content": "用户希望少打扰", "importance": True})

    assert "已记住" in result
    saved_memory = mm.add_memory.call_args.args[0]
    assert saved_memory.importance_score == 0.5
    assert saved_memory.priority == MemoryPriority.SHORT_TERM


def test_add_memory_keeps_one_off_task_facts_in_current_session_scope():
    mm = MagicMock()
    mm._current_write_scope.return_value = ("session", "session-1")
    mm.store.search_semantic.return_value = []
    mm.add_memory.return_value = "mem-1"
    agent = SimpleNamespace(memory_manager=mm, profile_manager=None)
    handler = MemoryHandler(agent)

    result = handler._add_memory({
        "content": "用户希望生成一份本周活动报告",
        "type": "fact",
    })

    assert "当前会话" in result
    assert mm.add_memory.call_args.kwargs["scope"] == "session"
    assert mm.add_memory.call_args.kwargs["scope_owner"] == "session-1"
    saved_memory = mm.add_memory.call_args.args[0]
    assert "session-only" in saved_memory.tags
