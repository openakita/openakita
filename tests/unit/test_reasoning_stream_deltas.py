from openakita.core.stream_accumulator import StreamAccumulator
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.providers.openai_responses import OpenAIResponsesProvider


def test_stream_accumulator_accepts_reasoning_delta_alias():
    acc = StreamAccumulator()

    events = acc.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "reasoning", "text": "checking sources"},
        }
    )

    assert events == [{"type": "thinking_delta", "content": "checking sources"}]
    assert acc.build_decision().thinking_content == "checking sources"


def test_openai_stream_extracts_nested_reasoning_details():
    provider = OpenAIProvider.__new__(OpenAIProvider)

    converted = provider._convert_stream_event(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_details": [
                            {"type": "reasoning.text.delta", "delta": "search first"},
                            {"type": "reasoning.text.delta", "text": ", then summarize"},
                        ]
                    }
                }
            ]
        }
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "search first, then summarize"},
    }


def test_responses_stream_reasoning_summary_becomes_thinking_delta():
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)

    converted = provider._convert_stream_event(
        {"type": "response.reasoning_summary_text.delta", "delta": "checking citations"}
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "checking citations"},
    }


def test_responses_done_reasoning_item_summary_is_preserved():
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)

    converted = provider._convert_stream_event(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "summary": [{"text": "tool result is enough; answer now"}],
            },
        }
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "tool result is enough; answer now"},
    }
