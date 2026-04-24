"""
L5 Quality Tests: Tool selection accuracy.

Uses MockLLMClient to simulate tool selection decisions and verifies
the agent picks the right tool for each scenario. In a real quality run,
these would use a real LLM and evaluate statistically.
"""

import pytest

from tests.fixtures.mock_llm import MockBrain, MockLLMClient, MockResponse
from openakita.llm.types import ToolUseBlock


@pytest.fixture
def tool_brain():
    client = MockLLMClient()
    return MockBrain(client), client


TOOL_SELECTION_CASES = [
    ("帮我读取 /tmp/test.txt 的内容", "read_file", "File read request should select read_file"),
    ("在项目中搜索 TODO 关键字", "search_files", "Code search should select search_files"),
    ("帮我记住我的生日是3月15日", "store_memory", "Memory store request should select store_memory"),
    ("打开百度搜索 Python 教程", "web_search", "Web search should select web_search"),
    ("创建一个定时任务每天早上8点提醒我", "schedule_create", "Scheduling should select schedule_create"),
]


class TestToolSelectionAccuracy:
    @pytest.mark.parametrize("query,expected_tool,description", TOOL_SELECTION_CASES)
    async def test_correct_tool_selected(self, tool_brain, query, expected_tool, description):
        """Verify the mock correctly routes to the expected tool."""
        brain, client = tool_brain
        client.preset_response(
            content=f"I'll use {expected_tool}.",
            tool_calls=[{"name": expected_tool, "input": {"query": query}}],
        )

        resp = await brain.messages_create_async(
            messages=[{"role": "user", "content": query}],
        )

        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) >= 1, f"{description}: no tool call produced"
        assert tool_blocks[0].name == expected_tool, f"{description}: got {tool_blocks[0].name}"


class TestToolSelectionEdgeCases:
    async def test_no_tool_for_simple_question(self, tool_brain):
        """Simple factual question should NOT trigger a tool call."""
        brain, client = tool_brain
        client.preset_response(content="2+2=4")

        resp = await brain.messages_create_async(
            messages=[{"role": "user", "content": "2+2等于几？"}],
        )

        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 0

    async def test_ambiguous_request_selects_some_tool(self, tool_brain):
        """Ambiguous requests should still produce a reasonable tool call."""
        brain, client = tool_brain
        client.preset_response(
            content="Let me search.",
            tool_calls=[{"name": "search_files", "input": {"query": "config"}}],
        )

        resp = await brain.messages_create_async(
            messages=[{"role": "user", "content": "找一下配置文件"}],
        )

        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) >= 1


class TestToolCallFormat:
    async def test_tool_call_has_valid_id(self, tool_brain):
        brain, client = tool_brain
        client.preset_response(
            content="Searching.",
            tool_calls=[{"name": "search_files", "input": {"q": "test"}}],
        )
        resp = await brain.messages_create_async(messages=[{"role": "user", "content": "search"}])
        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert tool_blocks[0].id.startswith("toolu_")

    async def test_tool_call_input_is_dict(self, tool_brain):
        brain, client = tool_brain
        client.preset_response(
            content="Reading.",
            tool_calls=[{"name": "read_file", "input": {"path": "/tmp/x"}}],
        )
        resp = await brain.messages_create_async(messages=[{"role": "user", "content": "read"}])
        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert isinstance(tool_blocks[0].input, dict)

