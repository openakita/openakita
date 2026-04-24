"""L1 Unit Tests: ResponseHandler static/utility methods."""

import pytest

from openakita.core.response_handler import (
    strip_thinking_tags,
    strip_tool_simulation_text,
    clean_llm_response,
    ResponseHandler,
)


class TestStripThinkingTags:
    def test_strip_basic_thinking(self):
        text = "<thinking>I need to analyze this</thinking>Here is my answer."
        result = strip_thinking_tags(text)
        assert "<thinking>" not in result
        assert "Here is my answer" in result

    def test_no_thinking_tags(self):
        text = "Just a normal response."
        result = strip_thinking_tags(text)
        assert result == text

    def test_empty_input(self):
        assert strip_thinking_tags("") == ""


class TestStripToolSimulation:
    def test_strip_tool_sim(self):
        text = "Let me check that for you."
        result = strip_tool_simulation_text(text)
        assert isinstance(result, str)


class TestCleanLLMResponse:
    def test_clean_basic(self):
        result = clean_llm_response("  Hello, how can I help?  ")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_with_thinking(self):
        text = "<thinking>plan</thinking>Here is the answer."
        result = clean_llm_response(text)
        assert "Here is the answer" in result


class TestResponseHandlerStaticMethods:
    def test_should_compile_prompt_simple(self):
        result = ResponseHandler.should_compile_prompt("你好")
        assert isinstance(result, bool)

    def test_should_compile_prompt_complex(self):
        result = ResponseHandler.should_compile_prompt(
            "帮我分析这个项目的架构，然后重构数据库层，最后写测试"
        )
        assert isinstance(result, bool)

    def test_get_last_user_request(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "user", "content": "帮我写代码"},
        ]
        last = ResponseHandler.get_last_user_request(messages)
        assert "写代码" in last

    def test_get_last_user_request_empty(self):
        result = ResponseHandler.get_last_user_request([])
        assert isinstance(result, str)

