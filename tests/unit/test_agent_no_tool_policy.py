from openakita.core.agent import _looks_like_explicit_no_tool_request


def test_explicit_no_tool_request_recognizes_natural_english_phrase():
    assert _looks_like_explicit_no_tool_request("Answer from history without using tools.")


def test_explicit_no_tool_request_recognizes_chinese_phrase():
    assert _looks_like_explicit_no_tool_request("请不要调用工具，直接回答。")
