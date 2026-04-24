"""Verify provider_extra (thought_signature) survives the full message pipeline."""

from openakita.llm.converters.messages import (
    convert_messages_from_openai,
    convert_messages_to_openai,
)
from openakita.llm.converters.tools import convert_tool_calls_from_openai
from openakita.llm.types import Message, TextBlock, ToolUseBlock

SAMPLE_EXTRA = {"google": {"thought_signature": "abc123"}}


def test_to_dict_includes_provider_extra():
    tb = ToolUseBlock(
        id="call_1", name="check", input={"x": 1}, provider_extra=SAMPLE_EXTRA,
    )
    d = tb.to_dict()
    assert d["provider_extra"] == SAMPLE_EXTRA


def test_to_dict_omits_none_provider_extra():
    tb = ToolUseBlock(id="call_1", name="check", input={"x": 1})
    d = tb.to_dict()
    assert "provider_extra" not in d


def test_convert_tool_calls_from_openai_captures_extra_content():
    tc_list = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "check", "arguments": '{"x": 1}'},
            "extra_content": SAMPLE_EXTRA,
        }
    ]
    blocks = convert_tool_calls_from_openai(tc_list)
    assert len(blocks) == 1
    assert blocks[0].provider_extra == SAMPLE_EXTRA


def test_convert_messages_to_openai_emits_extra_content():
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="thinking..."),
            ToolUseBlock(
                id="call_1", name="check", input={"x": 1},
                provider_extra=SAMPLE_EXTRA,
            ),
        ],
    )
    openai_msgs = convert_messages_to_openai([msg])
    assistant_msg = [m for m in openai_msgs if m.get("role") == "assistant"][0]
    tc = assistant_msg["tool_calls"][0]
    assert tc["extra_content"] == SAMPLE_EXTRA


def test_full_openai_roundtrip():
    """extra_content survives: OpenAI response → internal Message → OpenAI request."""
    openai_history = [
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "check", "arguments": '{"x": 1}'},
                    "extra_content": SAMPLE_EXTRA,
                }
            ],
        }
    ]
    internal_msgs, _ = convert_messages_from_openai(openai_history)
    assert internal_msgs[0].content[1].provider_extra == SAMPLE_EXTRA

    openai_out = convert_messages_to_openai(internal_msgs)
    out_tc = openai_out[0]["tool_calls"][0]
    assert out_tc["extra_content"] == SAMPLE_EXTRA


def test_decision_dict_roundtrip():
    """Simulate _parse_decision dict construction → brain reconstruction."""
    block = ToolUseBlock(
        id="call_1", name="check", input={"x": 1}, provider_extra=SAMPLE_EXTRA,
    )

    # _parse_decision constructs these dicts
    tc_dict = {"id": block.id, "name": block.name, "input": block.input}
    if getattr(block, "provider_extra", None):
        tc_dict["provider_extra"] = block.provider_extra
    assistant_content_item = {"type": "tool_use", **tc_dict}

    # brain._convert_messages_to_llm reconstructs ToolUseBlock from dict
    reconstructed = ToolUseBlock(
        id=assistant_content_item["id"],
        name=assistant_content_item["name"],
        input=assistant_content_item["input"],
        provider_extra=assistant_content_item.get("provider_extra"),
    )
    assert reconstructed.provider_extra == SAMPLE_EXTRA

    # Then convert_messages_to_openai emits extra_content
    msg = Message(role="assistant", content=[reconstructed])
    openai_out = convert_messages_to_openai([msg])
    out_tc = openai_out[0]["tool_calls"][0]
    assert out_tc["extra_content"] == SAMPLE_EXTRA
