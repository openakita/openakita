"""L4 E2E Tests: Real LLM integration — actual API calls to verify agent behavior.

These tests call REAL LLM APIs. They are skipped when no valid API key is available.
Run with: pytest tests/e2e/test_real_llm.py -v

Supports Record & Replay:
  RECORD=1 pytest tests/e2e/test_real_llm.py   # record real responses
  pytest tests/e2e/test_real_llm.py              # replay from recordings

Environment variables checked (in order):
  - ANTHROPIC_API_KEY
  - OPENAI_API_KEY
  - DASHSCOPE_API_KEY
  - Any key in llm_endpoints.json
"""

import os
import json
import pytest
import asyncio
from pathlib import Path

from openakita.llm.types import (
    LLMResponse,
    EndpointConfig,
    Message,
    TextBlock,
    Tool,
    StopReason,
)
from openakita.llm.client import LLMClient

RECORDING_DIR = Path(__file__).parent.parent / "fixtures" / "recordings" / "real_llm"

# ---------------------------------------------------------------------------
# Infrastructure: detect real LLM availability
# ---------------------------------------------------------------------------

def _find_available_endpoint() -> EndpointConfig | None:
    """Try to find a usable LLM endpoint from project config or env."""
    # 1) Project config (llm_endpoints.json) — most reliable
    try:
        from openakita.llm.config import load_endpoints_config
        main_eps, _, _, _ = load_endpoints_config()
        for ep in main_eps:
            key = ep.api_key or os.environ.get(ep.api_key_env or "", "")
            if key and len(key) > 5 and "tools" in (ep.capabilities or []):
                return ep
        # Fallback: any endpoint with a key (even without tool support)
        for ep in main_eps:
            key = ep.api_key or os.environ.get(ep.api_key_env or "", "")
            if key and len(key) > 5:
                return ep
    except Exception:
        pass
    # 2) Environment variables
    for env_key, provider, api_type, base_url, model in [
        ("ANTHROPIC_API_KEY", "anthropic", "anthropic", "https://api.anthropic.com", "claude-3-5-haiku-20241022"),
        ("OPENAI_API_KEY", "openai", "openai", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), "gpt-4o-mini"),
        ("DASHSCOPE_API_KEY", "dashscope", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ]:
        key = os.environ.get(env_key, "")
        if key and not key.startswith("sk-test"):
            return EndpointConfig(
                name=f"{provider}-test", provider=provider, api_type=api_type,
                base_url=base_url, api_key=key, model=model,
                priority=1, capabilities=["text", "tools"],
            )
    return None


_ENDPOINT = _find_available_endpoint()
_HAS_LLM = _ENDPOINT is not None
_RECORD_MODE = os.environ.get("RECORD", "") == "1"

requires_llm = pytest.mark.skipif(
    not _HAS_LLM and not RECORDING_DIR.exists(),
    reason="No LLM API key available and no recordings found. "
           "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or DASHSCOPE_API_KEY to run."
)


def _get_client() -> LLMClient:
    assert _ENDPOINT is not None
    return LLMClient(endpoints=[_ENDPOINT])


def _get_or_replay_client():
    """Return real client (in RECORD mode) or replay client (if recordings exist)."""
    if _HAS_LLM:
        if _RECORD_MODE:
            from tests.fixtures.mock_llm import LLMRecorder
            real = _get_client()
            return LLMRecorder(real, RECORDING_DIR)
        return _get_client()
    if RECORDING_DIR.exists() and any(RECORDING_DIR.glob("*.json")):
        from tests.fixtures.mock_llm import ReplayLLMClient
        return ReplayLLMClient(RECORDING_DIR)
    return None


# ---------------------------------------------------------------------------
# L4: Real LLM basic conversation tests
# ---------------------------------------------------------------------------

@requires_llm
class TestRealLLMBasicChat:
    """Verify the LLM can actually respond to basic queries."""

    @pytest.mark.asyncio
    async def test_simple_greeting(self):
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="请用一句话回答：1+1等于几？")],
            max_tokens=100,
        )
        assert isinstance(resp, LLMResponse)
        text = resp.content[0].text if resp.content else ""
        assert "2" in text

    @pytest.mark.asyncio
    async def test_chinese_understanding(self):
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="'塞翁失马'这个成语是什么意思？用一句话概括。")],
            max_tokens=200,
        )
        text = resp.content[0].text if resp.content else ""
        assert len(text) > 10  # Got a real response

    @pytest.mark.asyncio
    async def test_multiturn_context(self):
        client = _get_or_replay_client()
        r1 = await client.chat(
            messages=[Message(role="user", content="我的名字叫小明。记住这一点。")],
            max_tokens=100,
        )
        r2 = await client.chat(
            messages=[
                Message(role="user", content="我的名字叫小明。记住这一点。"),
                Message(role="assistant", content=r1.content),
                Message(role="user", content="我的名字是什么？"),
            ],
            max_tokens=100,
        )
        text = r2.content[0].text if r2.content else ""
        assert "小明" in text


# ---------------------------------------------------------------------------
# L4: Real LLM tool calling decision tests
# ---------------------------------------------------------------------------

_SEARCH_TOOL = Tool(
    name="web_search",
    description="搜索互联网获取最新信息",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"],
    },
)

_READ_FILE_TOOL = Tool(
    name="read_file",
    description="读取本地文件内容",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"}
        },
        "required": ["path"],
    },
)

_REMEMBER_TOOL = Tool(
    name="remember",
    description="将重要信息保存到长期记忆中",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容"},
            "importance": {"type": "integer", "description": "重要程度 1-5"},
        },
        "required": ["content"],
    },
)


@requires_llm
class TestRealLLMToolDecision:
    """Verify the LLM correctly decides WHEN and WHICH tool to call."""

    @pytest.mark.asyncio
    async def test_should_call_search_tool(self):
        """When asked about current events, LLM should pick web_search."""
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="今天的天气怎么样？帮我搜索一下。")],
            tools=[_SEARCH_TOOL, _READ_FILE_TOOL, _REMEMBER_TOOL],
            max_tokens=300,
        )
        assert resp.stop_reason == StopReason.TOOL_USE
        tool_calls = [b for b in resp.content if hasattr(b, "name")]
        assert len(tool_calls) >= 1
        assert tool_calls[0].name == "web_search"

    @pytest.mark.asyncio
    async def test_should_call_read_file(self):
        """When asked to read a file, LLM should pick read_file."""
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="帮我读取 /tmp/notes.txt 文件的内容。")],
            tools=[_SEARCH_TOOL, _READ_FILE_TOOL, _REMEMBER_TOOL],
            max_tokens=300,
        )
        assert resp.stop_reason == StopReason.TOOL_USE
        tool_calls = [b for b in resp.content if hasattr(b, "name")]
        assert len(tool_calls) >= 1
        assert tool_calls[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_should_not_call_tool_for_simple_question(self):
        """Simple knowledge questions should NOT trigger tool calls."""
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="Python 的 for 循环语法是什么？")],
            tools=[_SEARCH_TOOL, _READ_FILE_TOOL, _REMEMBER_TOOL],
            max_tokens=500,
        )
        assert resp.stop_reason == StopReason.END_TURN

    @pytest.mark.asyncio
    async def test_correct_tool_parameters(self):
        """Verify tool call has correct parameter structure."""
        client = _get_or_replay_client()
        resp = await client.chat(
            messages=[Message(role="user", content="搜索一下 Python asyncio 教程")],
            tools=[_SEARCH_TOOL],
            max_tokens=300,
        )
        tool_calls = [b for b in resp.content if hasattr(b, "name")]
        if tool_calls:
            assert "query" in tool_calls[0].input
            assert isinstance(tool_calls[0].input["query"], str)


# ---------------------------------------------------------------------------
# L5: Real LLM-as-Judge quality evaluation
# ---------------------------------------------------------------------------

@requires_llm
class TestRealLLMJudge:
    """Use a real LLM to judge the quality of responses (LLM-as-Judge pattern)."""

    @pytest.mark.asyncio
    async def test_judge_good_answer(self):
        """LLM should rate a factually correct answer highly."""
        client = _get_or_replay_client()
        prompt = (
            "你是一个评分助手。请评估以下回答的质量（1-5分）。\n"
            "问题：Python 中 list 和 tuple 的区别是什么？\n"
            "回答：list 是可变的，可以增删改元素；tuple 是不可变的，创建后不能修改。"
            "list 用方括号[]，tuple 用圆括号()。tuple 因为不可变，所以可以作为字典的 key，性能也略好。\n\n"
            "请只回答一个数字（1-5），不要其他内容。"
        )
        resp = await client.chat(
            messages=[Message(role="user", content=prompt)],
            max_tokens=50,
        )
        text = resp.content[0].text.strip() if resp.content else ""
        score = int("".join(c for c in text if c.isdigit())[:1] or "0")
        assert score >= 3, f"Good answer should score >= 3, got {score}"

    @pytest.mark.asyncio
    async def test_judge_bad_answer(self):
        """LLM should rate a factually wrong answer poorly."""
        client = _get_or_replay_client()
        prompt = (
            "你是一个评分助手。请评估以下回答的质量（1-5分）。\n"
            "问题：Python 中 list 和 tuple 的区别是什么？\n"
            "回答：list 和 tuple 完全一样，没有任何区别。它们都是不可变的。\n\n"
            "请只回答一个数字（1-5），不要其他内容。"
        )
        resp = await client.chat(
            messages=[Message(role="user", content=prompt)],
            max_tokens=50,
        )
        text = resp.content[0].text.strip() if resp.content else ""
        score = int("".join(c for c in text if c.isdigit())[:1] or "5")
        assert score <= 3, f"Bad answer should score <= 3, got {score}"

    @pytest.mark.asyncio
    async def test_judge_response_relevance(self):
        """LLM should judge whether a response is relevant to the question."""
        client = _get_or_replay_client()
        prompt = (
            "判断以下回答是否与问题相关。只回答'是'或'否'。\n"
            "问题：如何安装 Python？\n"
            "回答：你可以从 python.org 下载安装包，或者使用包管理器如 apt、brew 安装。"
        )
        resp = await client.chat(
            messages=[Message(role="user", content=prompt)],
            max_tokens=50,
        )
        text = resp.content[0].text.strip() if resp.content else ""
        assert "是" in text


# ---------------------------------------------------------------------------
# Record & Replay mechanism test
# ---------------------------------------------------------------------------

@requires_llm
class TestRecordReplay:
    """Verify the record/replay infrastructure works with real LLM."""

    @pytest.mark.asyncio
    async def test_record_and_replay_cycle(self, tmp_path):
        """Record a real response, then replay it identically."""
        if not _HAS_LLM:
            pytest.skip("Need real LLM for record test")

        from tests.fixtures.mock_llm import LLMRecorder, ReplayLLMClient

        rec_dir = tmp_path / "recordings"
        real_client = _get_client()
        recorder = LLMRecorder(real_client, rec_dir)

        messages = [Message(role="user", content="1加1等于几？只回答数字。")]
        real_resp = await recorder.chat(messages, max_tokens=50)
        real_text = real_resp.content[0].text if real_resp.content else ""

        # Verify recording was saved to disk
        recordings = list(rec_dir.glob("*.json"))
        assert len(recordings) >= 1, "Recording should be saved to disk"

        # Now replay — use same Message objects for consistent hashing
        replay = ReplayLLMClient(rec_dir)
        replay_resp = await replay.chat(messages, max_tokens=50)
        replay_text = replay_resp.content[0].text if replay_resp.content else ""

        # Replay should return the same content (or at least not the fallback)
        assert "[replay] No recording" not in replay_text, "Replay should match the recorded response"
        assert real_text == replay_text
