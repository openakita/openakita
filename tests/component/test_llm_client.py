"""L2 Component Tests: MockLLMClient behavior and LLM type contracts."""

import pytest

from openakita.llm.types import (
    ContentBlockType,
    EndpointConfig,
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    Tool,
    ToolUseBlock,
    Usage,
)
from tests.fixtures.mock_llm import MockLLMClient, MockResponse


class TestMockLLMClientBasic:
    async def test_default_response(self):
        client = MockLLMClient()
        client.set_default_response("fallback")
        resp = await client.chat([Message(role="user", content="hi")])
        assert resp.content[0].text == "fallback"

    async def test_preset_response(self):
        client = MockLLMClient()
        client.preset_response("Hello!")
        resp = await client.chat([Message(role="user", content="hi")])
        assert resp.content[0].text == "Hello!"

    async def test_preset_sequence(self):
        client = MockLLMClient()
        client.preset_sequence([
            MockResponse(content="First"),
            MockResponse(content="Second"),
        ])
        r1 = await client.chat([Message(role="user", content="q1")])
        r2 = await client.chat([Message(role="user", content="q2")])
        assert r1.content[0].text == "First"
        assert r2.content[0].text == "Second"

    async def test_call_log(self):
        client = MockLLMClient()
        client.set_default_response("ok")
        await client.chat([Message(role="user", content="test")])
        assert client.total_calls == 1
        assert client.last_call["messages"][0].content == "test"

    async def test_reset(self):
        client = MockLLMClient()
        client.preset_response("queued")
        client.reset()
        assert client.total_calls == 0


class TestMockLLMClientToolCalls:
    async def test_tool_call_response(self):
        client = MockLLMClient()
        client.preset_response(
            content="Let me search.",
            tool_calls=[{"name": "search", "input": {"q": "test"}}],
        )
        resp = await client.chat([Message(role="user", content="search for test")])
        assert resp.stop_reason == StopReason.TOOL_USE
        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "search"
        assert tool_blocks[0].input == {"q": "test"}

    async def test_mixed_text_and_tools(self):
        client = MockLLMClient()
        client.preset_response(
            content="Thinking...",
            tool_calls=[{"name": "read_file", "input": {"path": "/tmp/x"}}],
        )
        resp = await client.chat([])
        text_blocks = [b for b in resp.content if isinstance(b, TextBlock)]
        tool_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        assert len(text_blocks) == 1
        assert len(tool_blocks) == 1


class TestLLMTypes:
    def test_message_creation(self):
        msg = Message(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_tool_creation(self):
        tool = Tool(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        assert tool.name == "search"

    def test_usage_defaults(self):
        u = Usage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_creation_input_tokens == 0

    def test_endpoint_config(self):
        ep = EndpointConfig(
            name="test",
            provider="openai",
            api_type="openai",
            base_url="https://api.test.com/v1",
            model="gpt-4",
        )
        assert ep.priority == 1
        assert ep.context_window == 200000
        assert ep.timeout == 180

    def test_stop_reason_values(self):
        assert StopReason.END_TURN.value == "end_turn"
        assert StopReason.TOOL_USE.value == "tool_use"
        assert StopReason.MAX_TOKENS.value == "max_tokens"

    def test_llm_response_structure(self):
        resp = LLMResponse(
            id="msg_123",
            content=[TextBlock(text="hi")],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test",
        )
        assert resp.content[0].text == "hi"
        assert resp.usage.input_tokens == 10


class TestMockBrain:
    async def test_messages_create_async(self, mock_brain):
        mock_brain.llm_client.preset_response("brain reply")
        resp = await mock_brain.messages_create_async(
            messages=[{"role": "user", "content": "test"}],
            system="You are helpful.",
        )
        assert resp.content[0].text == "brain reply"

    async def test_think(self, mock_brain):
        mock_brain.llm_client.preset_response("thought result")
        resp = await mock_brain.think("What is 2+2?")
        assert resp.content[0].text == "thought result"

    async def test_system_list_format(self, mock_brain):
        mock_brain.llm_client.preset_response("ok")
        await mock_brain.messages_create_async(
            messages=[],
            system=[{"type": "text", "text": "You are helpful."}],
        )
        call = mock_brain.llm_client.call_log[-1]
        assert "You are helpful." in call["system"]
