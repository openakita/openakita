"""Regression tests for text-based tool call parsing."""

from openakita.llm.converters.tools import has_text_tool_calls, parse_text_tool_calls


def test_parse_minimax_kimi_hybrid_tool_call():
    text = (
        '<minimax:tool_call> browser_open:3 <|tool_call_argument_begin|> {"visible": true} '
        "<|tool_call_end|> <|tool_calls_section_end|>"
    )

    assert has_text_tool_calls(text) is True

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "browser_open"
    assert tool_calls[0].input == {"visible": True}


def test_parse_plain_minimax_kimi_hybrid_tool_call():
    text = (
        'minimax:tool_call functions.browser_open:3 <|tool_call_argument_begin|> {"visible": true} '
        "<|tool_call_end|> <|tool_calls_section_end|>"
    )

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "browser_open"
    assert tool_calls[0].input == {"visible": True}
