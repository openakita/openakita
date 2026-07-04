"""Tests for :mod:`openakita.runtime.llm.multimodal`.

Hand-rolled :class:`LLMResponse` fixtures cover every conversion
branch (text-only, thinking-only via native ThinkingBlock,
thinking-only via ``reasoning_content``, mixed text+thinking,
tool_use, empty-content, stop-reason mapping). No real LLM client.
"""

from __future__ import annotations

from openakita.llm.types import (
    LLMResponse,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)
from openakita.runtime.llm import (
    collect_thinking_texts,
    map_stop_reason,
    response_to_anthropic_message,
)


def _resp(content, *, reasoning="", stop=StopReason.END_TURN, model="m"):
    return LLMResponse(
        id="r-1",
        model=model,
        content=content,
        stop_reason=stop,
        usage=Usage(input_tokens=5, output_tokens=7),
        reasoning_content=reasoning,
    )


def test_text_only_response_round_trips() -> None:
    r = _resp([TextBlock(text="hello world")])
    msg = response_to_anthropic_message(r)
    assert msg.role == "assistant"
    assert len(msg.content) == 1
    assert msg.content[0].text == "hello world"
    assert msg.stop_reason == "end_turn"
    assert msg.usage.input_tokens == 5 and msg.usage.output_tokens == 7


def test_native_thinking_block_is_prepended_into_first_text() -> None:
    r = _resp([
        ThinkingBlock(thinking="I should respond gently"),
        TextBlock(text="hi"),
    ])
    msg = response_to_anthropic_message(r)
    assert len(msg.content) == 1
    text = msg.content[0].text
    assert "<thinking>I should respond gently</thinking>" in text
    assert text.endswith("hi")


def test_reasoning_content_is_used_when_no_native_thinking_block() -> None:
    r = _resp([TextBlock(text="ack")], reasoning="deepseek inner monologue")
    msg = response_to_anthropic_message(r)
    assert msg.content[0].text == "<thinking>deepseek inner monologue</thinking>\nack"


def test_native_thinking_takes_priority_over_reasoning_content() -> None:
    # When both are present, only the native ThinkingBlock is rendered.
    r = _resp(
        [ThinkingBlock(thinking="native"), TextBlock(text="t")],
        reasoning="should-not-appear",
    )
    msg = response_to_anthropic_message(r)
    assert "should-not-appear" not in msg.content[0].text
    assert "<thinking>native</thinking>" in msg.content[0].text


def test_tool_use_block_is_passed_through() -> None:
    r = _resp([
        TextBlock(text="calling tool"),
        ToolUseBlock(id="tu-1", name="run_shell", input={"command": "ls"}),
    ])
    msg = response_to_anthropic_message(r)
    assert len(msg.content) == 2
    assert msg.content[1].type == "tool_use"
    assert msg.content[1].name == "run_shell"
    # Anthropic SDK auto-injects defaults (e.g. block_timeout_ms) so we
    # only assert the keys we explicitly set.
    assert msg.content[1].input["command"] == "ls"


def test_empty_content_yields_one_empty_text_block() -> None:
    r = _resp([])
    msg = response_to_anthropic_message(r)
    assert len(msg.content) == 1
    assert msg.content[0].text == ""


def test_thinking_only_inserts_thinking_as_first_block() -> None:
    r = _resp([ThinkingBlock(thinking="x"), ToolUseBlock(id="tu-1", name="x", input={})])
    msg = response_to_anthropic_message(r)
    # The first block has no .text -> thinking should be inserted before it.
    assert len(msg.content) == 2
    assert msg.content[0].text == "<thinking>x</thinking>"
    assert msg.content[1].type == "tool_use"


def test_stop_reason_mapping_known_and_unknown() -> None:
    assert map_stop_reason(StopReason.END_TURN) == "end_turn"
    assert map_stop_reason(StopReason.MAX_TOKENS) == "max_tokens"
    assert map_stop_reason(StopReason.TOOL_USE) == "tool_use"
    assert map_stop_reason(StopReason.STOP_SEQUENCE) == "stop_sequence"


def test_collect_thinking_texts_combines_native_and_skips_reasoning_when_native_present() -> None:
    r = _resp(
        [ThinkingBlock(thinking="a"), ThinkingBlock(thinking="b")],
        reasoning="ignored",
    )
    assert collect_thinking_texts(r) == ["<thinking>a</thinking>", "<thinking>b</thinking>"]


def test_metadata_passthrough_for_failover_attributes() -> None:
    r = _resp([TextBlock(text="ok")])
    # Stamp metadata as the brain does today before this conversion.
    r.endpoint_name = "primary"  # type: ignore[attr-defined]
    r._failover_from = "secondary"  # type: ignore[attr-defined]
    msg = response_to_anthropic_message(r)
    assert msg.endpoint_name == "primary"  # type: ignore[attr-defined]
    assert msg._failover_from == "secondary"  # type: ignore[attr-defined]
