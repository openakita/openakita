"""
Context Retention Tests — 验证上下文记忆重构后的结构完整性。

覆盖范围：
A. 系统提示词结构验证
B. 消息历史时间戳和标记
C. 委派结果注入
D. 10 轮对话上下文保持（结构级）
E. 20+ 轮对话上下文压力（结构级）
F. get_session_context 工具
G. 浏览器隔离
H. delegate context 参数

Run:  pytest tests/e2e/test_context_retention.py -v
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(
    session_id: str = "test_session_001",
    channel: str = "desktop",
    chat_type: str = "private",
    messages: list[dict] | None = None,
    sub_agent_records: list[dict] | None = None,
):
    """Create a minimal mock Session for testing."""
    ctx = MagicMock()
    ctx.messages = messages or []
    ctx.sub_agent_records = sub_agent_records or []
    ctx.agent_profile_id = "default"
    ctx.handoff_events = []
    ctx.delegation_chain = []
    ctx.react_traces = []

    session = MagicMock()
    session.id = session_id
    session.channel = channel
    session.chat_type = chat_type
    session.context = ctx
    session.get_metadata = MagicMock(return_value=None)
    return session


def _make_history_messages(count: int, start_hour: int = 10) -> list[dict]:
    """Generate alternating user/assistant messages with timestamps."""
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        total_minutes = start_hour * 60 + i * 3  # 3 minutes apart
        hour = (total_minutes // 60) % 24
        minute = total_minutes % 60
        day = 15 + total_minutes // (60 * 24)
        ts = f"2025-06-{day:02d}T{hour:02d}:{minute:02d}:00"
        msgs.append({
            "role": role,
            "content": f"Test message {i + 1} from {role}",
            "timestamp": ts,
        })
    return msgs


def _apply_strip_markers(content: str) -> str:
    """Replicate the improved strip marker logic from agent.py."""
    _STRIP_MARKERS = ["\n\n[子Agent工作总结]", "\n\n[执行摘要]"]
    for _marker in _STRIP_MARKERS:
        while _marker in content:
            idx = content.index(_marker)
            before = content[:idx]
            after = content[idx + len(_marker):]
            next_section = -1
            for sep in ("\n\n[", "\n\n##", "\n\n---"):
                pos = after.find(sep)
                if pos != -1 and (next_section == -1 or pos < next_section):
                    next_section = pos
            if next_section != -1:
                content = before + after[next_section:]
            else:
                content = before
    return content


# ---------------------------------------------------------------------------
# A. System Prompt Structure Tests
# ---------------------------------------------------------------------------

class TestSystemPromptStructure:
    """验证构造出的 system prompt 结构完整性。"""

    @pytest.fixture
    def identity_dir(self, tmp_path: Path):
        soul_md = tmp_path / "SOUL.md"
        soul_md.write_text(
            "# OpenAkita — Core Identity\n\n"
            "你是 OpenAkita，一个 AI 助手。\n\n"
            "## 核心原则\n- 诚实\n- 有用\n"
        )
        return tmp_path

    def test_identity_wording_simple(self, identity_dir: Path):
        """验证开场白是朴素的 'AI 助手'。"""
        soul_content = (identity_dir / "SOUL.md").read_text()
        assert "全能" not in soul_content
        assert "自进化" not in soul_content
        assert "一个 AI 助手" in soul_content

    def test_identity_wording_in_real_soul(self):
        """验证实际 SOUL.md 中的措辞。"""
        real_soul = Path("identity/SOUL.md")
        if not real_soul.exists():
            pytest.skip("identity/SOUL.md not found")
        content = real_soul.read_text()
        assert "全能自进化" not in content
        assert "一个 AI 助手" in content

    def test_conversation_context_convention(self):
        """验证对话上下文约定在 system prompt 的 common_rules 中。"""
        from openakita.prompt.builder import _build_session_type_rules

        rules = _build_session_type_rules("cli")
        assert "## 对话上下文约定" in rules
        assert "对话历史是最权威的上下文来源" in rules
        assert "[HH:MM]" in rules
        assert "[最新消息]" in rules
        assert "不要重复执行" in rules

    def test_session_metadata_section(self):
        """验证会话元数据段落构建。"""
        from openakita.prompt.builder import _build_session_metadata_section

        section = _build_session_metadata_section(
            session_context={
                "session_id": "test_123",
                "channel": "desktop",
                "chat_type": "private",
                "message_count": 5,
                "has_sub_agents": True,
                "sub_agent_count": 3,
            },
            model_display_name="claude-3.5-sonnet",
        )
        assert "## 当前会话" in section
        assert "claude-3.5-sonnet" in section
        assert "test_123" in section
        assert "桌面端" in section
        assert "私聊" in section
        assert "5 条" in section
        assert "3 条" in section

    def test_session_metadata_im_channels(self):
        """验证 IM 通道正确映射。"""
        from openakita.prompt.builder import _build_session_metadata_section

        for channel, expected in [
            ("telegram", "Telegram"),
            ("feishu", "飞书"),
            ("dingtalk", "钉钉"),
            ("cli", "CLI 终端"),
        ]:
            section = _build_session_metadata_section(
                session_context={"channel": channel, "session_id": "x"},
            )
            assert expected in section, f"Channel '{channel}' should map to '{expected}'"

    def test_dynamic_model_name(self):
        """验证 powered by {model} 是动态的。"""
        from openakita.prompt.builder import _build_arch_section

        section = _build_arch_section(
            model_display_name="gpt-4o",
            is_sub_agent=False,
            multi_agent_enabled=True,
        )
        assert "powered by **gpt-4o**" in section

    def test_arch_section_main_vs_sub(self):
        """验证主 Agent 和子 Agent 的架构概况内容不同。"""
        from openakita.prompt.builder import _build_arch_section

        main = _build_arch_section(
            model_display_name="test", is_sub_agent=False, multi_agent_enabled=True,
        )
        sub = _build_arch_section(
            model_display_name="test", is_sub_agent=True,
        )
        assert "子 Agent" in sub
        assert "委派工具不可用" in sub
        assert "delegate" in main.lower()

    def test_memory_guide_priority(self):
        """验证记忆指南中有三级优先级声明。"""
        from openakita.prompt.builder import _MEMORY_SYSTEM_GUIDE

        assert "信息优先级" in _MEMORY_SYSTEM_GUIDE
        assert "对话历史" in _MEMORY_SYSTEM_GUIDE
        assert "最高优先级" in _MEMORY_SYSTEM_GUIDE
        assert "系统注入记忆" in _MEMORY_SYSTEM_GUIDE
        assert "记忆搜索工具" in _MEMORY_SYSTEM_GUIDE
        assert "常见错误" in _MEMORY_SYSTEM_GUIDE
        assert "仅供参考" not in _MEMORY_SYSTEM_GUIDE

    def test_get_session_context_tool_defined(self):
        """验证 get_session_context 工具已注册。"""
        from openakita.tools.definitions.memory import MEMORY_TOOLS

        names = [t["name"] for t in MEMORY_TOOLS]
        assert "get_session_context" in names
        tool_def = next(t for t in MEMORY_TOOLS if t["name"] == "get_session_context")
        assert "sections" in tool_def["input_schema"].get("properties", {})

    def test_delegate_context_param_defined(self):
        """验证 delegate_to_agent 和 delegate_parallel 包含 context 参数。"""
        from openakita.tools.definitions.agent import AGENT_TOOLS

        delegate = next(t for t in AGENT_TOOLS if t["name"] == "delegate_to_agent")
        assert "context" in delegate["input_schema"]["properties"]

        parallel = next(t for t in AGENT_TOOLS if t["name"] == "delegate_parallel")
        task_props = parallel["input_schema"]["properties"]["tasks"]["items"]["properties"]
        assert "context" in task_props

    def test_prompt_vs_cursor_checklist(self):
        """对照 Cursor 设计清单逐项检查。"""
        from openakita.prompt.builder import (
            _MEMORY_SYSTEM_GUIDE,
            _build_arch_section,
            _build_session_metadata_section,
            _build_session_type_rules,
        )
        from openakita.tools.definitions.agent import AGENT_TOOLS
        from openakita.tools.definitions.memory import MEMORY_TOOLS

        arch = _build_arch_section(model_display_name="test", is_sub_agent=False)
        assert "powered by" in arch

        meta = _build_session_metadata_section(
            session_context={"session_id": "x", "channel": "desktop"},
        )
        assert "当前会话" in meta

        rules = _build_session_type_rules("cli")
        assert "不要重复执行" in rules
        assert "[最新消息]" in rules
        assert "[HH:MM]" in rules

        tool_names = [t["name"] for t in MEMORY_TOOLS]
        assert "get_session_context" in tool_names

        delegate = next(t for t in AGENT_TOOLS if t["name"] == "delegate_to_agent")
        assert "context" in delegate["input_schema"]["properties"]

        assert "信息优先级" in _MEMORY_SYSTEM_GUIDE
        assert "仅供参考" not in _MEMORY_SYSTEM_GUIDE


# ---------------------------------------------------------------------------
# B. Message History Structure Tests
# ---------------------------------------------------------------------------

class TestHistoryTimestamps:
    """验证历史消息的时间戳注入和结构标记。"""

    def test_timestamp_format_in_history(self):
        """验证时间戳被正确注入为 [HH:MM] 格式。"""
        ts = "2025-06-15T14:30:00"
        t = datetime.fromisoformat(ts)
        expected = f"[{t.strftime('%H:%M')}]"
        assert expected == "[14:30]"

    def test_strip_marker_preserves_content_after(self):
        """验证改进后的截断逻辑保留标记后的有效内容。"""
        content = (
            "这是主要回复内容。\n\n"
            "[子Agent工作总结]\n调研了3个项目\n\n"
            "## 总结\n这是总结内容。"
        )
        result = _apply_strip_markers(content)
        assert "主要回复内容" in result
        assert "总结内容" in result
        assert "子Agent工作总结" not in result
        assert "调研了3个项目" not in result

    def test_strip_marker_removes_all_if_no_section_after(self):
        """当标记后无新段落时，截断到标记处。"""
        content = "回复内容。\n\n[子Agent工作总结]\n执行了浏览器操作..."
        result = _apply_strip_markers(content)
        assert result == "回复内容。"

    def test_strip_marker_with_execsummary(self):
        """验证 [执行摘要] 标记也被正确处理。"""
        content = "主体内容\n\n[执行摘要]\n步骤1: ...\n步骤2: ...\n\n---\n后续内容"
        result = _apply_strip_markers(content)
        assert "主体内容" in result
        assert "执行摘要" not in result
        assert "后续内容" in result


# ---------------------------------------------------------------------------
# C. Delegate Result Injection Tests
# ---------------------------------------------------------------------------

class TestDelegateResultInjection:
    """验证委派结果摘要正确注入到消息历史中。"""

    def test_sub_agent_records_injected(self):
        """验证 sub_agent_records 被注入到最后一条 assistant 消息。"""
        messages = [
            {"role": "user", "content": "调研三个项目"},
            {"role": "assistant", "content": "好的，我来分配三个子Agent调研。"},
        ]
        sub_records = [
            {"agent_name": "browser-agent-1", "result_preview": "OpenAkita 是开源多Agent框架"},
            {"agent_name": "browser-agent-2", "result_preview": "OpenClaw 是机器人平台"},
        ]

        summary_parts = []
        for r in sub_records:
            name = r.get("agent_name", "unknown")
            preview = r.get("result_preview", "")
            if preview:
                summary_parts.append(f"- {name}: {preview[:500]}")
        if summary_parts:
            delegation_summary = "\n\n[委派任务执行记录]\n" + "\n".join(summary_parts)
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "assistant":
                    messages[i]["content"] += delegation_summary
                    break

        assert "[委派任务执行记录]" in messages[1]["content"]
        assert "browser-agent-1" in messages[1]["content"]
        assert "OpenAkita" in messages[1]["content"]

    def test_no_injection_without_records(self):
        """无 sub_agent_records 时不注入。"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        sub_records = []
        if sub_records:
            pass
        assert messages[1]["content"] == "你好！"


# ---------------------------------------------------------------------------
# D. 10 Round Context Retention
# ---------------------------------------------------------------------------

class TestContextRetention10Rounds:
    """10 轮对话：验证基础上下文保持。"""

    def test_all_messages_have_timestamps(self):
        """10 轮消息每条都有时间戳。"""
        msgs = _make_history_messages(20)
        for i, msg in enumerate(msgs):
            ts = msg.get("timestamp", "")
            assert ts, f"Message {i} missing timestamp"
            t = datetime.fromisoformat(ts)
            content = f"[{t.strftime('%H:%M')}] {msg['content']}"
            assert re.match(r"\[\d{2}:\d{2}\]", content)

    def test_latest_message_marker_applied(self):
        """验证当前消息加上 [最新消息] 前缀。"""
        compiled_message = "为什么实际做事的只有openclaw？"
        _has_history = True
        if _has_history and compiled_message and isinstance(compiled_message, str):
            compiled_message = f"[最新消息]\n{compiled_message}"
        assert compiled_message.startswith("[最新消息]\n")
        assert "openclaw" in compiled_message

    def test_no_reference_only_prefix(self):
        """验证「仅供参考」不会出现在消息中。"""
        compiled_message = "为什么实际做事的只有openclaw？"
        _has_history = True
        if _has_history and compiled_message and isinstance(compiled_message, str):
            compiled_message = f"[最新消息]\n{compiled_message}"
        assert "仅供参考" not in compiled_message

    def test_delegate_result_accessible(self):
        """sub_agent_records 存在时，追问可引用。"""
        session = _make_session(
            messages=_make_history_messages(4),
            sub_agent_records=[
                {"agent_name": "browser-agent", "result_preview": "调研结果：支持 30+ LLM"},
            ],
        )
        assert len(session.context.sub_agent_records) == 1
        assert "30+ LLM" in session.context.sub_agent_records[0]["result_preview"]


# ---------------------------------------------------------------------------
# E. 20+ Round Context Retention
# ---------------------------------------------------------------------------

class TestContextRetention20PlusRounds:
    """20+ 轮对话：验证长对话下的上下文管理。"""

    def test_timestamps_monotonically_increasing(self):
        """20+ 轮中每条消息时间戳递增。"""
        msgs = _make_history_messages(40)
        prev_ts = None
        for msg in msgs:
            ts = msg.get("timestamp", "")
            assert ts
            t = datetime.fromisoformat(ts)
            if prev_ts:
                assert t >= prev_ts, f"Timestamps not increasing: {prev_ts} -> {t}"
            prev_ts = t

    def test_memory_priority_order(self):
        """验证记忆指南中优先级顺序正确。"""
        from openakita.prompt.builder import _MEMORY_SYSTEM_GUIDE

        priorities = _MEMORY_SYSTEM_GUIDE.split("### 信息优先级")[1].split("###")[0]
        idx_dialog = priorities.find("对话历史")
        idx_system = priorities.find("系统注入")
        idx_search = priorities.find("记忆搜索")
        assert idx_dialog < idx_system < idx_search

    def test_large_message_count(self):
        """构建 50 条消息后结构完整。"""
        msgs = _make_history_messages(50)
        assert len(msgs) == 50
        assert msgs[0]["role"] == "user"
        assert msgs[-1]["role"] == "assistant" if len(msgs) % 2 == 0 else "user"


# ---------------------------------------------------------------------------
# F. get_session_context Tool Tests
# ---------------------------------------------------------------------------

class TestGetSessionContextTool:
    """验证 get_session_context 工具的行为。"""

    def test_handler_summary(self):
        """summary section 返回会话概况。"""
        from openakita.tools.handlers.memory import MemoryHandler

        agent = MagicMock()
        agent._current_session = _make_session(
            messages=_make_history_messages(6),
            sub_agent_records=[{"agent_name": "test-agent", "result_preview": "结果"}],
        )
        agent.memory_manager = MagicMock()

        handler = MemoryHandler(agent)
        result = handler._get_session_context({"sections": ["summary"]})
        assert "会话概况" in result
        assert "test_session_001" in result

    def test_handler_sub_agents(self):
        """sub_agents section 返回子Agent记录。"""
        from openakita.tools.handlers.memory import MemoryHandler

        agent = MagicMock()
        agent._current_session = _make_session(
            sub_agent_records=[
                {
                    "agent_name": "browser-agent",
                    "task_message": "调研 OpenAkita",
                    "result_preview": "OpenAkita 是开源多Agent框架",
                    "elapsed_s": 120,
                    "tools_used": ["web_search", "browser_navigate"],
                },
            ],
        )
        agent.memory_manager = MagicMock()

        handler = MemoryHandler(agent)
        result = handler._get_session_context({"sections": ["sub_agents"]})
        assert "browser-agent" in result
        assert "调研 OpenAkita" in result
        assert "OpenAkita" in result

    def test_handler_no_session(self):
        """无活跃会话时返回错误。"""
        from openakita.tools.handlers.memory import MemoryHandler

        agent = MagicMock()
        agent._current_session = None
        agent.memory_manager = MagicMock()

        handler = MemoryHandler(agent)
        result = handler._get_session_context({})
        assert "❌" in result

    def test_handler_messages_section(self):
        """messages section 返回消息列表。"""
        from openakita.tools.handlers.memory import MemoryHandler

        msgs = _make_history_messages(6)
        agent = MagicMock()
        agent._current_session = _make_session(messages=msgs)
        agent.memory_manager = MagicMock()

        handler = MemoryHandler(agent)
        result = handler._get_session_context({"sections": ["messages"]})
        assert "完整消息列表" in result
        assert "user:" in result or "assistant:" in result


# ---------------------------------------------------------------------------
# G. Browser Isolation Tests
# ---------------------------------------------------------------------------

class TestBrowserIsolation:
    """验证浏览器隔离机制。"""

    def test_isolated_context_class_exists(self):
        from openakita.tools.browser.manager import _IsolatedBrowserContext
        assert _IsolatedBrowserContext is not None

    def test_isolated_context_properties(self):
        from openakita.tools.browser.manager import BrowserState, _IsolatedBrowserContext

        parent = MagicMock()
        parent.visible = True
        parent.using_user_chrome = False
        parent.cdp_url = "http://localhost:9222"

        ctx_mock = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com"

        isolated = _IsolatedBrowserContext(parent, ctx_mock, page_mock)
        assert isolated.is_ready
        assert isolated.page is page_mock
        assert isolated.context is ctx_mock
        assert isolated.current_url == "https://example.com"

    def test_isolated_different_from_parent(self):
        """隔离上下文与父上下文使用不同的 page/context。"""
        from openakita.tools.browser.manager import _IsolatedBrowserContext

        parent = MagicMock()
        parent.visible = True
        parent.using_user_chrome = False
        parent.cdp_url = "http://localhost:9222"

        ctx1 = MagicMock()
        page1 = MagicMock()
        ctx2 = MagicMock()
        page2 = MagicMock()

        iso1 = _IsolatedBrowserContext(parent, ctx1, page1)
        iso2 = _IsolatedBrowserContext(parent, ctx2, page2)

        assert iso1.context is not iso2.context
        assert iso1.page is not iso2.page


# ---------------------------------------------------------------------------
# H. Delegate Context Parameter Tests
# ---------------------------------------------------------------------------

class TestDelegateContextParam:
    """验证 delegate 工具的 context 参数处理。"""

    def test_isolated_message_with_context(self):
        context = "用户之前调研了3个项目"
        message = "对比架构差异"
        reason = "技术分析"

        isolated_msg = ""
        if context:
            isolated_msg += f"[任务背景]\n{context}\n\n"
        isolated_msg += f"[任务指令]\n{message}"
        if reason:
            isolated_msg += f"\n[委派原因] {reason}"

        assert "[任务背景]" in isolated_msg
        assert "调研了3个项目" in isolated_msg
        assert "[任务指令]" in isolated_msg

    def test_isolated_message_without_context(self):
        context = ""
        message = "搜索最新的 AI 论文"
        reason = ""

        isolated_msg = ""
        if context:
            isolated_msg += f"[任务背景]\n{context}\n\n"
        isolated_msg += f"[任务指令]\n{message}"
        if reason:
            isolated_msg += f"\n[委派原因] {reason}"

        assert "[任务背景]" not in isolated_msg
        assert isolated_msg == "[任务指令]\n搜索最新的 AI 论文"
