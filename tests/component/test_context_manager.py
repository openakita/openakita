"""L2 Component Tests: ContextManager compression and message grouping."""

import asyncio

import pytest

from openakita.core.context_manager import ContextManager


@pytest.fixture
def ctx_manager(mock_brain):
    mock_brain.llm_client.set_default_response("Summary: earlier conversation discussed Python.")
    return ContextManager(brain=mock_brain)


class TestEstimateTokens:
    def test_empty(self, ctx_manager):
        assert ctx_manager.estimate_tokens("") == 0

    def test_english(self, ctx_manager):
        tokens = ctx_manager.estimate_tokens("hello world test")
        assert tokens > 0

    def test_chinese(self, ctx_manager):
        tokens = ctx_manager.estimate_tokens("你好世界")
        assert tokens > 0

    def test_chinese_more_expensive(self, ctx_manager):
        en_tokens = ctx_manager.estimate_tokens("a" * 100)
        cn_tokens = ctx_manager.estimate_tokens("你" * 100)
        assert cn_tokens > en_tokens


class TestEstimateMessagesTokens:
    def test_empty_list(self, ctx_manager):
        tokens = ctx_manager.estimate_messages_tokens([])
        assert tokens >= 1

    def test_single_message(self, ctx_manager):
        msgs = [{"role": "user", "content": "Hello"}]
        tokens = ctx_manager.estimate_messages_tokens(msgs)
        assert tokens > 10  # at least overhead

    def test_includes_overhead(self, ctx_manager):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        tokens = ctx_manager.estimate_messages_tokens(msgs)
        assert tokens >= 20  # 10 per message overhead

    def test_list_content(self, ctx_manager):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ]
        tokens = ctx_manager.estimate_messages_tokens(msgs)
        assert tokens > 0


class TestGroupMessages:
    def test_empty(self):
        assert ContextManager.group_messages([]) == []

    def test_simple_messages(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        groups = ContextManager.group_messages(msgs)
        assert len(groups) == 2
        assert groups[0] == [msgs[0]]
        assert groups[1] == [msgs[1]]

    def test_tool_call_and_result_grouped(self):
        msgs = [
            {"role": "user", "content": "search files"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me search."},
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "Found 3 files"},
                ],
            },
            {"role": "assistant", "content": "Found 3 files for you."},
        ]
        groups = ContextManager.group_messages(msgs)
        assert len(groups) == 3
        assert len(groups[1]) == 2  # tool_call + tool_result together

    def test_multiple_tool_results_grouped(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
                    {"type": "tool_use", "id": "t2", "name": "b", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "r2"},
                ],
            },
        ]
        groups = ContextManager.group_messages(msgs)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_tool_role_messages_grouped(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
                ],
            },
            {"role": "tool", "content": "result", "tool_use_id": "t1"},
        ]
        groups = ContextManager.group_messages(msgs)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_non_tool_assistant_stays_separate(self):
        msgs = [
            {"role": "assistant", "content": "just text, no tools"},
            {"role": "user", "content": "ok"},
        ]
        groups = ContextManager.group_messages(msgs)
        assert len(groups) == 2


class TestCompressIfNeeded:
    async def test_short_conversation_not_compressed(self, ctx_manager):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = await ctx_manager.compress_if_needed(msgs, max_tokens=100000)
        assert len(result) == 2

    async def test_returns_list(self, ctx_manager):
        msgs = [{"role": "user", "content": "test"}]
        result = await ctx_manager.compress_if_needed(msgs, max_tokens=100000)
        assert isinstance(result, list)

