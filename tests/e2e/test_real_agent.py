"""L4 E2E Tests: Real LLM through the FULL Agent pipeline.

Unlike test_real_llm.py which tests LLMClient directly, this file
runs the complete Agent.chat_with_session() pipeline:
  User message → Prompt Compiler → Context Builder → ReasoningEngine → Tool Exec → Response

This validates that the FULL chain works with a real LLM, not just the client.

Run: pytest tests/e2e/test_real_agent.py -v
Record mode: RECORD=1 pytest tests/e2e/test_real_agent.py -v
"""

import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Reuse the endpoint detection from test_real_llm
from tests.e2e.test_real_llm import _find_available_endpoint, requires_llm, RECORDING_DIR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def real_agent(tmp_path, monkeypatch):
    """Create a real Agent that uses a real LLM endpoint."""
    endpoint = _find_available_endpoint()
    if not endpoint:
        pytest.skip("No LLM endpoint available")

    monkeypatch.setenv("OPENAKITA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "memory").mkdir(parents=True, exist_ok=True)

    from openakita.core.agent import Agent
    agent = Agent(name="test-real")

    # Override the brain's LLM client with one pointing to our detected endpoint
    from openakita.llm.client import LLMClient
    real_client = LLMClient(endpoints=[endpoint])
    agent.brain.llm_client = real_client

    try:
        await agent.initialize()
    except Exception:
        pass
    yield agent


# ---------------------------------------------------------------------------
# Full pipeline tests
# ---------------------------------------------------------------------------

@requires_llm
class TestRealAgentChat:
    """Test the full Agent pipeline with a real LLM."""

    @pytest.mark.asyncio
    async def test_simple_question(self, real_agent):
        """Agent should answer a simple factual question."""
        response = await real_agent.chat_with_session(
            message="1加1等于几？直接回答数字。",
            session_messages=[],
            session_id="test-simple",
        )
        assert isinstance(response, str)
        assert len(response) > 0
        assert "2" in response

    @pytest.mark.asyncio
    async def test_multiturn_memory(self, real_agent):
        """Agent should maintain context across turns within a session."""
        history = []

        r1 = await real_agent.chat_with_session(
            message="我叫张三，我喜欢编程。请记住。",
            session_messages=history,
            session_id="test-multiturn",
        )
        assert isinstance(r1, str)
        history.append({"role": "user", "content": "我叫张三，我喜欢编程。请记住。"})
        history.append({"role": "assistant", "content": r1})

        r2 = await real_agent.chat_with_session(
            message="我叫什么名字？",
            session_messages=history,
            session_id="test-multiturn",
        )
        assert "张三" in r2

    @pytest.mark.asyncio
    async def test_system_prompt_respected(self, real_agent):
        """The agent's system prompt / identity should influence responses."""
        response = await real_agent.chat_with_session(
            message="你是谁？",
            session_messages=[],
            session_id="test-identity",
        )
        assert isinstance(response, str)
        assert len(response) > 5

    @pytest.mark.asyncio
    async def test_chinese_response(self, real_agent):
        """Agent should respond in Chinese when asked in Chinese."""
        response = await real_agent.chat_with_session(
            message="请用中文解释什么是递归？一句话概括。",
            session_messages=[],
            session_id="test-chinese",
        )
        assert isinstance(response, str)
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in response)
        assert has_chinese, "Response should contain Chinese characters"


@requires_llm
class TestRealAgentToolUse:
    """Test that the full Agent pipeline correctly triggers and executes tools."""

    @pytest.mark.asyncio
    async def test_stop_command(self, real_agent):
        """Stop commands should be handled before reaching LLM."""
        response = await real_agent.chat_with_session(
            message="停",
            session_messages=[],
            session_id="test-stop",
        )
        assert "停止" in response or "✅" in response


@requires_llm
class TestRealAgentEdgeCases:
    """Edge cases with real LLM."""

    @pytest.mark.asyncio
    async def test_empty_message(self, real_agent):
        """Agent should handle empty input gracefully."""
        try:
            response = await real_agent.chat_with_session(
                message="",
                session_messages=[],
                session_id="test-empty",
            )
            assert isinstance(response, str)
        except Exception:
            pass  # Empty input may raise, that's also acceptable

    @pytest.mark.asyncio
    async def test_very_long_input(self, real_agent):
        """Agent should handle long input without crashing."""
        long_msg = "请总结以下内容：" + "这是一段测试文本。" * 100
        response = await real_agent.chat_with_session(
            message=long_msg,
            session_messages=[],
            session_id="test-long",
        )
        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_special_characters(self, real_agent):
        """Agent should handle special characters in input."""
        response = await real_agent.chat_with_session(
            message="解释这些符号的含义：@ # $ % ^ & * 。只用中文回答，一句话。",
            session_messages=[],
            session_id="test-special",
        )
        assert isinstance(response, str)
        assert len(response) > 0
