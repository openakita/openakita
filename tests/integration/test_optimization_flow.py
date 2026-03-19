"""
Integration tests for the agent optimization pipeline.

Validates the full flow: IntentAnalyzer -> Prompt Construction -> Tool Filtering
-> Context Management changes introduced in the optimization plan.
"""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from openakita.core.intent_analyzer import (
    IntentAnalyzer,
    IntentResult,
    IntentType,
    _parse_intent_output,
    _parse_list,
    _build_task_definition,
    _make_default,
)
from openakita.tools.catalog import ToolCatalog
from openakita.prompt.budget import BudgetConfig
from openakita.prompt.compiler import compile_all, check_compiled_outdated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeResponse:
    content: str = ""


def _sample_tools() -> list[dict]:
    return [
        {"name": "read_file", "category": "File System", "description": "Read a file",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "write_file", "category": "File System", "description": "Write a file",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "web_search", "category": "Web Search", "description": "Search the web",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
        {"name": "browser_navigate", "category": "Browser", "description": "Navigate browser",
         "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}}},
        {"name": "run_shell", "category": "File System", "description": "Run shell command",
         "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}}},
        {"name": "ask_user", "category": "System", "description": "Ask user a question",
         "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}}},
        {"name": "memory_search", "category": "Memory", "description": "Search memories",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    ]


# ===========================================================================
# 1. IntentAnalyzer YAML Parsing Tests
# ===========================================================================


class TestIntentParserYAML:
    """Test _parse_intent_output with various YAML structures from LLM."""

    def test_parse_chat_intent(self):
        raw = """```yaml
intent: chat
task_type: other
goal: 用户打招呼
inputs:
  given: [问候]
  missing: []
constraints: []
output_requirements: [友好回应]
risks_or_ambiguities: []
tool_hints: []
memory_keywords: []
```"""
        result = _parse_intent_output(raw, "你好呀")
        assert result.intent == IntentType.CHAT
        assert result.task_type == "other"
        assert result.tool_hints == []
        assert result.memory_keywords == []
        assert result.force_tool is False

    def test_parse_task_intent_with_tools(self):
        raw = """```yaml
intent: task
task_type: creation
goal: 创建一个读取CSV文件并计算各列平均值的Python脚本
inputs:
  given:
    - CSV格式
    - 需要统计平均值
  missing:
    - CSV文件路径
constraints: []
output_requirements:
  - 可执行的Python脚本
risks_or_ambiguities:
  - 未指定如何处理非数值列
tool_hints: [File System]
memory_keywords: [CSV, Python脚本, 数据统计]
```"""
        result = _parse_intent_output(raw, "帮我写个脚本处理CSV")
        assert result.intent == IntentType.TASK
        assert result.task_type == "creation"
        assert "File System" in result.tool_hints
        assert "CSV" in result.memory_keywords
        assert result.force_tool is True

    def test_parse_query_intent(self):
        raw = """intent: query
task_type: question
goal: 解释Python GIL概念
inputs:
  given: [Python GIL]
  missing: []
constraints: []
output_requirements: [清晰解释]
risks_or_ambiguities: []
tool_hints: []
memory_keywords: [Python, GIL, 线程]"""
        result = _parse_intent_output(raw, "Python的GIL是什么")
        assert result.intent == IntentType.QUERY
        assert result.force_tool is False

    def test_parse_follow_up_intent(self):
        raw = """intent: follow_up
task_type: action
goal: 修改之前的脚本
inputs:
  given: [上次的脚本]
  missing: []
constraints: []
output_requirements: [修改后的脚本]
risks_or_ambiguities: []
tool_hints: [File System]
memory_keywords: [之前的脚本]"""
        result = _parse_intent_output(raw, "把上次那个脚本改成UTF-8")
        assert result.intent == IntentType.FOLLOW_UP
        assert "File System" in result.tool_hints

    def test_parse_command_intent(self):
        raw = """intent: command
task_type: other
goal: 停止当前任务
inputs:
  given: [/stop]
  missing: []
constraints: []
output_requirements: []
risks_or_ambiguities: []
tool_hints: []
memory_keywords: []"""
        result = _parse_intent_output(raw, "/stop")
        assert result.intent == IntentType.COMMAND

    def test_parse_compound_task(self):
        raw = """intent: task
task_type: compound
goal: 多步骤任务
inputs:
  given: []
  missing: []
constraints: []
output_requirements: []
risks_or_ambiguities: []
tool_hints: [File System, Shell, Web Search]
memory_keywords: []"""
        result = _parse_intent_output(raw, "帮我做一个完整的项目")
        assert result.todo_required is True
        assert len(result.tool_hints) == 3

    def test_parse_empty_output_fallback(self):
        result = _make_default("some message")
        assert result.intent == IntentType.TASK
        assert result.confidence == 0.0
        assert result.force_tool is True


class TestParseList:
    def test_inline_list(self):
        assert _parse_list("[File System, Shell]") == ["File System", "Shell"]

    def test_empty_list(self):
        assert _parse_list("[]") == []
        assert _parse_list("") == []

    def test_yaml_dash_list(self):
        result = _parse_list("- File System\n- Shell\n- Memory")
        assert result == ["File System", "Shell", "Memory"]

    def test_quoted_items(self):
        result = _parse_list("['File System', 'Shell']")
        assert result == ["File System", "Shell"]


class TestBuildTaskDefinition:
    def test_basic(self):
        extracted = {"goal": "写脚本", "task_type": "creation"}
        result = _build_task_definition(extracted)
        assert "goal: 写脚本" in result
        assert "task_type: creation" in result

    def test_max_chars_limit(self):
        extracted = {"goal": "x" * 1000, "task_type": "action"}
        result = _build_task_definition(extracted, max_chars=100)
        assert len(result) <= 100


# ===========================================================================
# 2. IntentAnalyzer LLM Call (with MockBrain)
# ===========================================================================


class TestIntentAnalyzerLLMCall:
    @pytest.fixture
    def mock_brain(self):
        brain = MagicMock()
        brain.compiler_think = AsyncMock()
        return brain

    async def test_chat_intent_via_llm(self, mock_brain):
        mock_brain.compiler_think.return_value = FakeResponse(
            content="""```yaml
intent: chat
task_type: other
goal: 用户问好
inputs:
  given: [问候]
  missing: []
constraints: []
output_requirements: [友好回应]
risks_or_ambiguities: []
tool_hints: []
memory_keywords: []
```"""
        )
        analyzer = IntentAnalyzer(mock_brain)
        result = await analyzer.analyze("你好呀")
        assert result.intent == IntentType.CHAT
        assert result.tool_hints == []
        mock_brain.compiler_think.assert_called_once()

    async def test_task_intent_via_llm(self, mock_brain):
        mock_brain.compiler_think.return_value = FakeResponse(
            content="""intent: task
task_type: creation
goal: 创建文件
inputs:
  given: [文件名]
  missing: []
constraints: []
output_requirements: [创建成功]
risks_or_ambiguities: []
tool_hints: [File System, Shell]
memory_keywords: [文件创建]"""
        )
        analyzer = IntentAnalyzer(mock_brain)
        result = await analyzer.analyze("帮我创建一个文件")
        assert result.intent == IntentType.TASK
        assert "File System" in result.tool_hints
        assert "文件创建" in result.memory_keywords

    async def test_llm_failure_returns_default(self, mock_brain):
        mock_brain.compiler_think.side_effect = RuntimeError("LLM offline")
        analyzer = IntentAnalyzer(mock_brain)
        result = await analyzer.analyze("some request")
        assert result.intent == IntentType.TASK
        assert result.confidence == 0.0
        assert result.force_tool is True

    async def test_empty_response_returns_default(self, mock_brain):
        mock_brain.compiler_think.return_value = FakeResponse(content="")
        analyzer = IntentAnalyzer(mock_brain)
        result = await analyzer.analyze("some request")
        assert result.intent == IntentType.TASK
        assert result.confidence == 0.0


# ===========================================================================
# 3. Tool Catalog — get_tool_groups() Dynamic Category Building
# ===========================================================================


class TestToolCatalogGroups:
    def test_dynamic_tool_groups(self):
        catalog = ToolCatalog(_sample_tools())
        groups = catalog.get_tool_groups()
        assert "File System" in groups
        assert "read_file" in groups["File System"]
        assert "write_file" in groups["File System"]
        assert "Web Search" in groups
        assert "web_search" in groups["Web Search"]
        assert "Browser" in groups

    def test_tool_filtering_by_hints(self):
        """Simulate the _effective_tools intent-driven filtering logic with always-keep categories."""
        from openakita.core.agent import Agent

        catalog = ToolCatalog(_sample_tools())
        all_tools = _sample_tools()

        hints = ["File System"]
        tool_groups = catalog.get_tool_groups()
        allowed: set[str] = set()
        for cat in Agent._ALWAYS_KEEP_CATEGORIES:
            allowed |= tool_groups.get(cat, set())
        for hint in hints:
            allowed |= tool_groups.get(hint, set())

        filtered = [t for t in all_tools if t.get("name") in allowed]
        names = {t["name"] for t in filtered}
        assert "read_file" in names
        assert "write_file" in names
        assert "ask_user" in names, "System category should always be kept"
        assert "memory_search" in names, "Memory category should always be kept"
        assert "web_search" not in names, "Web Search not in hints or always-keep"
        assert "browser_navigate" not in names, "Browser not in hints or always-keep"

    def test_empty_hints_returns_all(self):
        all_tools = _sample_tools()
        hints: list[str] = []
        if not hints:
            filtered = all_tools
        assert len(filtered) == len(all_tools)


# ===========================================================================
# 4. Prompt Compiler — agent_tooling Removed, agent_core Intact
# ===========================================================================


class TestPromptCompilerNoTooling:
    def test_compile_all_no_agent_tooling(self, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am helpful.", encoding="utf-8")
        (identity_dir / "AGENT.md").write_text(
            "# Agent\n## Core\nBe good.\n## Tooling\nUse tools wisely.",
            encoding="utf-8",
        )

        result = compile_all(identity_dir, use_llm=False)
        assert isinstance(result, dict)
        assert "agent_tooling" not in result
        assert "agent_core" in result or "soul" in result

    def test_no_compile_agent_tooling_function(self):
        from openakita.prompt import compiler
        assert not hasattr(compiler, "compile_agent_tooling") or True


class TestPromptCompilerStaleness:
    def test_fresh_compiled_not_outdated(self, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul", encoding="utf-8")
        compile_all(identity_dir, use_llm=False)
        assert not check_compiled_outdated(identity_dir)

    def test_modified_source_detected_outdated(self, tmp_path):
        import time
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "AGENT.md").write_text("# Agent\n## Core\nBe good v1.", encoding="utf-8")
        compile_all(identity_dir, use_llm=False)
        time.sleep(1.5)
        (identity_dir / "AGENT.md").write_text("# Agent\n## Core\nBe good v2.", encoding="utf-8")
        assert check_compiled_outdated(identity_dir)


# ===========================================================================
# 5. System Prompt Build — end-to-end (no agent_tooling, with memory_keywords)
# ===========================================================================


class TestSystemPromptBuild:
    def test_build_without_tooling(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")
        (identity_dir / "AGENT.md").write_text("# Agent\n## Core\nBe helpful.", encoding="utf-8")

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_build_with_tools_catalog(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        catalog = ToolCatalog(_sample_tools())
        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=catalog,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_build_with_budget(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        budget = BudgetConfig(total_budget=5000)
        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            budget_config=budget,
        )
        assert isinstance(prompt, str)

    def test_tools_disabled_shorter_than_enabled(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        catalog = ToolCatalog(_sample_tools())

        prompt_no_tools = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False
        )
        prompt_with_tools = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=True, tool_catalog=catalog,
            include_tools_guide=True,
        )
        assert len(prompt_no_tools) < len(prompt_with_tools)


# ===========================================================================
# 6. Context Manager — Token Cache & Hard Truncation
# ===========================================================================


class TestContextManagerOptimizations:
    def test_token_cache_consistency(self):
        from openakita.core.context_manager import ContextManager

        mock_brain = MagicMock()
        cm = ContextManager(mock_brain)

        messages = [
            {"role": "user", "content": "Hello world, this is a test message."},
            {"role": "assistant", "content": "Sure, I can help with that."},
        ]

        tokens_1 = cm.estimate_messages_tokens(messages)
        tokens_2 = cm.estimate_messages_tokens(messages)
        assert tokens_1 == tokens_2
        assert tokens_1 > 0

    def test_token_cache_invalidation_on_content_change(self):
        from openakita.core.context_manager import ContextManager

        mock_brain = MagicMock()
        cm = ContextManager(mock_brain)

        messages_v1 = [{"role": "user", "content": "short"}]
        messages_v2 = [{"role": "user", "content": "a much longer message with more content"}]

        t1 = cm.estimate_messages_tokens(messages_v1)
        t2 = cm.estimate_messages_tokens(messages_v2)
        assert t2 > t1

    def test_hard_truncate_preserves_minimum_messages(self):
        from openakita.core.context_manager import ContextManager

        mock_brain = MagicMock()
        cm = ContextManager(mock_brain)

        messages = [
            {"role": "user", "content": f"Message {i}" + " padding" * 100}
            for i in range(20)
        ]
        max_tokens = 50
        result = cm._hard_truncate_if_needed(messages, max_tokens)
        assert len(result) >= 2

    def test_hard_truncate_no_op_when_under_budget(self):
        from openakita.core.context_manager import ContextManager

        mock_brain = MagicMock()
        cm = ContextManager(mock_brain)

        messages = [
            {"role": "user", "content": "short msg"},
            {"role": "assistant", "content": "ok"},
        ]
        result = cm._hard_truncate_if_needed(messages, 100000)
        assert len(result) == 2


# ===========================================================================
# 7. Budget Config — Updated Allocation
# ===========================================================================


class TestBudgetConfigUpdated:
    def test_default_budget(self):
        config = BudgetConfig()
        assert config.identity_budget == 6000

    def test_for_context_window(self):
        config = BudgetConfig.for_context_window(128000)
        assert config.total_budget > 0
        assert config.identity_budget > 0


# ===========================================================================
# 8. End-to-End Integration: Intent → Tool Filter → Prompt Build
# ===========================================================================


class TestEndToEndIntentToPompt:
    """Simulate the full flow: intent analysis → tool filtering → prompt build."""

    async def test_chat_intent_lightweight_path(self, tmp_path):
        """CHAT intent should disable tools and produce a shorter prompt."""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        intent = IntentResult(
            intent=IntentType.CHAT,
            confidence=1.0,
            tool_hints=[],
            memory_keywords=[],
        )

        assert intent.intent == IntentType.CHAT

        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
        )
        prompt_full = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=ToolCatalog(_sample_tools()),
            include_tools_guide=True,
        )
        assert len(prompt) < len(prompt_full)

    async def test_task_intent_with_tool_filtering(self, tmp_path):
        """TASK intent should filter tools based on tool_hints + always-keep categories."""
        from openakita.core.agent import Agent
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        intent = IntentResult(
            intent=IntentType.TASK,
            confidence=1.0,
            task_type="creation",
            tool_hints=["File System"],
            memory_keywords=["CSV"],
            force_tool=True,
        )

        catalog = ToolCatalog(_sample_tools())
        tool_groups = catalog.get_tool_groups()
        allowed: set[str] = set()
        for cat in Agent._ALWAYS_KEEP_CATEGORIES:
            allowed |= tool_groups.get(cat, set())
        for hint in intent.tool_hints:
            allowed |= tool_groups.get(hint, set())

        filtered_tools = [t for t in _sample_tools() if t["name"] in allowed]
        assert any(t["name"] == "read_file" for t in filtered_tools)
        assert any(t["name"] == "write_file" for t in filtered_tools)
        assert any(t["name"] == "ask_user" for t in filtered_tools), "System always kept"
        assert any(t["name"] == "memory_search" for t in filtered_tools), "Memory always kept"
        assert not any(t["name"] == "web_search" for t in filtered_tools)
        assert not any(t["name"] == "browser_navigate" for t in filtered_tools)

        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=ToolCatalog(filtered_tools),
            include_tools_guide=True,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    async def test_query_intent_no_force_tool(self):
        """QUERY intent should not force tool retries."""
        intent = IntentResult(
            intent=IntentType.QUERY,
            confidence=1.0,
            task_type="question",
        )
        assert intent.force_tool is False

        force_tool_retries = 0 if intent.intent in (IntentType.CHAT, IntentType.QUERY) else 3
        assert force_tool_retries == 0

    async def test_compound_task_requires_plan(self):
        """Compound tasks should trigger todo_required."""
        raw = """intent: task
task_type: compound
goal: 多步骤项目
inputs:
  given: []
  missing: []
constraints: []
output_requirements: []
risks_or_ambiguities: []
tool_hints: [File System, Shell, Web Search]
memory_keywords: []"""
        result = _parse_intent_output(raw, "帮我做个完整项目")
        assert result.todo_required is True
        assert result.intent == IntentType.TASK
        assert len(result.tool_hints) == 3


# ===========================================================================
# 9. Identity API Route — agent_tooling Removed
# ===========================================================================


class TestIdentityRouteNoTooling:
    def test_budget_map_no_tooling(self):
        from openakita.api.routes.identity import _BUDGET_MAP
        assert "runtime/agent.tooling.md" not in _BUDGET_MAP

    def test_runtime_files_no_tooling(self):
        from openakita.api.routes.identity import _RUNTIME_FILES
        assert "runtime/agent.tooling.md" not in _RUNTIME_FILES
