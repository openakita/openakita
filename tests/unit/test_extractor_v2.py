"""补充 extractor 测试: v2 提取, quick_facts, generate_episode, update_scratchpad."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from openakita.memory.extractor import MemoryExtractor
from openakita.memory.types import (
    ConversationTurn,
    Episode,
    MemoryType,
    Scratchpad,
    SemanticMemory,
)
from tests.fixtures.mock_llm import MockBrain, MockLLMClient, MockResponse


@dataclass
class SimpleResponse:
    """Mimics Brain.Response where content is a plain string."""
    content: str = ""
    tool_calls: list = field(default_factory=list)
    stop_reason: str = "end_turn"


class SimpleMockBrain:
    """Brain mock that returns SimpleResponse (string content), matching real Brain.think()."""

    def __init__(self):
        self._responses: list[str] = []
        self._default = "NONE"

    def preset(self, content: str):
        self._responses.append(content)

    async def think(self, prompt: str, **kwargs):
        text = self._responses.pop(0) if self._responses else self._default
        return SimpleResponse(content=text)


@pytest.fixture
def mock_client():
    return MockLLMClient()


@pytest.fixture
def mock_brain_simple():
    return SimpleMockBrain()


@pytest.fixture
def extractor(mock_brain_simple):
    return MemoryExtractor(brain=mock_brain_simple)


@pytest.fixture
def extractor_no_brain():
    return MemoryExtractor(brain=None)


class TestExtractFromTurnV2:
    def test_returns_empty_without_brain(self, extractor_no_brain):
        turn = ConversationTurn(role="user", content="我喜欢深色主题，用 Python 3.12 开发")
        result = asyncio.get_event_loop().run_until_complete(
            extractor_no_brain.extract_from_turn_v2(turn)
        )
        assert result == []

    def test_returns_empty_for_short_content(self, extractor):
        turn = ConversationTurn(role="user", content="好的")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert result == []

    def test_extracts_from_ai_response(self, extractor, mock_brain_simple):
        mock_brain_simple.preset(json.dumps([
            {
                "type": "PREFERENCE",
                "subject": "用户",
                "predicate": "主题偏好",
                "content": "用户喜欢深色主题",
                "importance": 0.8,
                "is_update": False,
            }
        ]))
        turn = ConversationTurn(role="user", content="我一直使用深色主题来编程，护眼又好看")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert len(result) == 1
        assert result[0]["type"] == "PREFERENCE"
        assert result[0]["subject"] == "用户"

    def test_handles_none_response(self, extractor, mock_brain_simple):
        mock_brain_simple.preset("NONE")
        turn = ConversationTurn(role="user", content="今天天气真好啊，适合出去走走")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert result == []

    def test_handles_malformed_json(self, extractor, mock_brain_simple):
        mock_brain_simple.preset("not json at all")
        turn = ConversationTurn(role="user", content="随便说一句话来测试一下这个功能")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert result == []

    def test_with_tool_calls(self, extractor, mock_brain_simple):
        mock_brain_simple.preset(json.dumps([
            {
                "type": "SKILL",
                "subject": "代码格式化",
                "predicate": "工具使用",
                "content": "成功使用 black 格式化代码",
                "importance": 0.6,
            }
        ]))
        turn = ConversationTurn(
            role="assistant",
            content="已经用 black 格式化了代码",
            tool_calls=[{"name": "execute_command", "input": {"command": "black ."}, "id": "t1"}],
            tool_results=[{"tool_use_id": "t1", "content": "All done!"}],
        )
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert len(result) >= 1

    def test_importance_clamped(self, extractor, mock_brain_simple):
        mock_brain_simple.preset(json.dumps([
            {"type": "FACT", "content": "Clamped importance value", "importance": 99},
        ]))
        turn = ConversationTurn(role="user", content="记住这个重要信息，非常非常重要")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        if result:
            assert result[0]["importance"] <= 1.0

    def test_filters_short_content(self, extractor, mock_brain_simple):
        mock_brain_simple.preset(json.dumps([
            {"type": "FACT", "content": "hi"},
        ]))
        turn = ConversationTurn(role="user", content="这段话用来测试短内容被过滤的逻辑")
        result = asyncio.get_event_loop().run_until_complete(
            extractor.extract_from_turn_v2(turn)
        )
        assert all(len(r["content"]) >= 5 for r in result)


class TestExtractQuickFacts:
    """extract_quick_facts is deprecated (always returns []).
    Tests verify the deprecated stub behaves correctly."""

    def test_deprecated_returns_empty(self, extractor_no_brain):
        messages = [{"role": "user", "content": "我喜欢使用 Vim 编辑器"}]
        facts = extractor_no_brain.extract_quick_facts(messages)
        assert facts == []

    def test_limit_to_5(self, extractor_no_brain):
        messages = [
            {"role": "user", "content": f"我喜欢工具 {i}，必须使用它"} for i in range(10)
        ]
        facts = extractor_no_brain.extract_quick_facts(messages)
        assert len(facts) <= 5

    def test_empty_messages(self, extractor_no_brain):
        assert extractor_no_brain.extract_quick_facts([]) == []


class TestGenerateEpisode:
    def test_no_turns_returns_none(self, extractor):
        result = asyncio.get_event_loop().run_until_complete(
            extractor.generate_episode([], "sess-1")
        )
        assert result is None

    def test_generates_episode_with_brain(self, extractor, mock_brain_simple):
        mock_brain_simple.preset(json.dumps({
            "summary": "用户请求重构记忆系统",
            "goal": "记忆系统重构",
            "outcome": "success",
            "entities": ["memory", "storage.py"],
            "tools_used": ["write_file"],
        }))

        turns = [
            ConversationTurn(role="user", content="帮我重构记忆系统"),
            ConversationTurn(
                role="assistant", content="好的，我来重构",
                tool_calls=[{"name": "write_file", "input": {"path": "storage.py"}, "id": "t1"}],
            ),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            extractor.generate_episode(turns, "sess-1")
        )
        assert result is not None
        assert isinstance(result, Episode)
        assert result.summary == "用户请求重构记忆系统"
        assert result.session_id == "sess-1"
        assert "write_file" in result.tools_used

    def test_fallback_without_brain(self, extractor_no_brain):
        turns = [
            ConversationTurn(role="user", content="帮我写一个排序算法"),
            ConversationTurn(role="assistant", content="好的"),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            extractor_no_brain.generate_episode(turns, "sess-2")
        )
        assert result is not None
        assert "排序算法" in result.summary

    def test_extracts_action_nodes(self, extractor):
        turns = [
            ConversationTurn(
                role="assistant", content="done",
                tool_calls=[
                    {"name": "read_file", "input": {"path": "a.py"}, "id": "t1"},
                    {"name": "write_file", "input": {"path": "b.py"}, "id": "t2"},
                ],
                tool_results=[
                    {"tool_use_id": "t1", "content": "file content"},
                    {"tool_use_id": "t2", "content": "written ok"},
                ],
            ),
        ]
        nodes = extractor._extract_action_nodes(turns)
        assert len(nodes) == 2
        assert nodes[0].tool_name == "read_file"
        assert nodes[1].tool_name == "write_file"


class TestUpdateScratchpad:
    def test_with_brain(self, extractor, mock_brain_simple):
        mock_brain_simple.preset("""
## 当前项目
- 记忆系统重构

## 未解决的问题
- FTS5 中文分词效果待验证

## 下一步
- 编写单元测试
""")
        episode = Episode(
            session_id="s1", summary="重构了记忆系统",
            goal="记忆系统重构", outcome="success",
        )
        result = asyncio.get_event_loop().run_until_complete(
            extractor.update_scratchpad(None, episode)
        )
        assert isinstance(result, Scratchpad)
        assert "记忆系统重构" in result.active_projects

    def test_without_brain_appends(self, extractor_no_brain):
        current = Scratchpad(content="## 近期进展\n- 之前的内容")
        episode = Episode(
            session_id="s1", summary="完成了新功能",
            goal="新功能", outcome="success",
        )
        result = asyncio.get_event_loop().run_until_complete(
            extractor_no_brain.update_scratchpad(current, episode)
        )
        assert "完成了新功能" in result.content

    def test_without_brain_creates_new(self, extractor_no_brain):
        episode = Episode(
            session_id="s1", summary="第一次对话",
            goal="hello", outcome="success",
        )
        result = asyncio.get_event_loop().run_until_complete(
            extractor_no_brain.update_scratchpad(None, episode)
        )
        assert "第一次对话" in result.content


class TestBuildToolContext:
    def test_empty_calls(self, extractor):
        assert extractor._build_tool_context(None, None) == ""
        assert extractor._build_tool_context([], []) == ""

    def test_with_calls_and_results(self, extractor):
        calls = [{"name": "read_file", "input": {"path": "test.py"}, "id": "t1"}]
        results = [{"tool_use_id": "t1", "content": "file contents here"}]
        ctx = extractor._build_tool_context(calls, results)
        assert "read_file" in ctx
        assert "test.py" in ctx

    def test_error_result(self, extractor):
        calls = [{"name": "execute_command", "input": {"command": "bad"}, "id": "t1"}]
        results = [{"tool_use_id": "t1", "content": "command not found", "is_error": True}]
        ctx = extractor._build_tool_context(calls, results)
        assert "错误" in ctx


class TestHelperMethods:
    def test_parse_list_section(self, extractor):
        text = "## 当前项目\n- 项目A\n- 项目B\n\n## 其他"
        items = extractor._parse_list_section(text, "当前项目")
        assert items == ["项目A", "项目B"]

    def test_parse_list_section_not_found(self, extractor):
        assert extractor._parse_list_section("no sections", "不存在") == []

    def test_parse_first_item(self, extractor):
        text = "## 当前项目\n- 项目A\n- 项目B"
        assert extractor._parse_first_item(text, "当前项目") == "项目A"

    def test_append_to_section_existing(self, extractor):
        content = "## 近期进展\n- old item"
        result = extractor._append_to_section(content, "近期进展", "- new item")
        assert "- new item" in result
        assert "- old item" in result

    def test_append_to_section_new(self, extractor):
        content = "no sections here"
        result = extractor._append_to_section(content, "近期进展", "- first item")
        assert "## 近期进展" in result
        assert "- first item" in result
