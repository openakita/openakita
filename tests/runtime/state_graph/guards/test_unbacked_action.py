"""Tests for runtime/state_graph/guards/unbacked_action."""

from __future__ import annotations

import pytest

from openakita.runtime.state_graph.guards.unbacked_action import (
    action_claim_re,
    extract_unbacked_verbs,
    guard_unbacked_action_claim,
)


def _legacy():
    import openakita.agent.brain  # noqa: F401
    from openakita.core._reasoning_engine_legacy import (
        _extract_unbacked_verbs,
        _guard_unbacked_action_claim,
    )
    return _extract_unbacked_verbs, _guard_unbacked_action_claim


def test_action_claim_re_matches_save_phrases() -> None:
    pat = action_claim_re()
    for s in [
        "\u5df2\u4fdd\u5b58",
        "\u6210\u529f\u4fdd\u5b58",
        "\u6211\u5df2\u7ecf\u5e2e\u4f60\u4fdd\u5b58",
        "\u987a\u5229\u521b\u5efa",
        "write_file\u5df2\u8c03\u7528",
    ]:
        assert pat.search(s), f"failed to match: {s!r}"


def test_action_claim_re_does_not_match_planning_text() -> None:
    pat = action_claim_re()
    assert pat.search("\u6211\u5c06\u53bb\u67e5\u8be2") is None
    assert pat.search("\u8ba1\u5212\u521b\u5efa") is None


def test_extract_no_unbacked_when_text_empty() -> None:
    assert extract_unbacked_verbs("", set()) == []


def test_extract_passes_when_recap_window() -> None:
    text = "\u4e4b\u524d\u5df2\u4fdd\u5b58\u4e86\u8be5\u6587\u4ef6"  # "previously already saved"
    # No backing tools, but recap-context -> verb suppressed
    assert "\u4fdd\u5b58" not in extract_unbacked_verbs(text, set())


def test_extract_flags_claim_without_backing_tool() -> None:
    text = "\u5df2\u4fdd\u5b58\u4e86"  # "already saved"
    unbacked = extract_unbacked_verbs(text, set())
    assert "\u4fdd\u5b58" in unbacked


def test_extract_silenced_when_backing_tool_present() -> None:
    text = "\u5df2\u4fdd\u5b58\u4e86"
    unbacked = extract_unbacked_verbs(text, {"write_file"})
    assert "\u4fdd\u5b58" not in unbacked


def test_guard_returns_text_unchanged_when_no_claim() -> None:
    text = "Hello world."
    assert guard_unbacked_action_claim(text, []) == text


def test_guard_appends_banner_when_no_tools_at_all() -> None:
    text = "\u5df2\u5e2e\u4f60\u4fdd\u5b58\u4e86\u3002"  # "already saved for you"
    result = guard_unbacked_action_claim(text, [])
    assert result != text
    assert "\u26a0" in result  # warning icon
    assert "\u4fdd\u5b58" in result or "\u4e00\u81f4\u6027\u63d0\u793a" in result


def test_guard_returns_text_unchanged_when_recap_only() -> None:
    text = "[17:30] \u4e4b\u524d\u5df2\u4fdd\u5b58\u4e86"
    assert guard_unbacked_action_claim(text, []) == text


def test_guard_appends_banner_when_wrong_tool_ran() -> None:
    text = "\u5df2\u5e2e\u4f60\u5220\u9664\u4e86\u3002"
    # Tool ran but it was not a delete tool -> banner expected
    result = guard_unbacked_action_claim(
        text, ["get_tool_info"], [{"tool_name": "get_tool_info", "is_error": False}]
    )
    assert "\u26a0" in result
    assert "get_tool_info" in result


def test_guard_silent_when_matching_tool_ran() -> None:
    text = "\u5df2\u5e2e\u4f60\u4fdd\u5b58\u4e86\u3002"
    result = guard_unbacked_action_claim(
        text, ["write_file"], [{"tool_name": "write_file", "is_error": False}]
    )
    assert result == text


def test_guard_flags_unbacked_update_memory_claim() -> None:
    # F1 turn-17 shape: model claims "已更新记录" but no memory tool ran.
    text = "\u597d\u7684\uff0c\u6211\u5df2\u66f4\u65b0\u8bb0\u5f55\u3002"  # "already updated the record"
    result = guard_unbacked_action_claim(text, [])
    assert result != text
    assert "\u26a0" in result


def test_guard_silent_when_update_backed_by_profile_tool() -> None:
    text = "\u5df2\u66f4\u65b0\u8bb0\u5f55\u3002"  # "already updated the record"
    result = guard_unbacked_action_claim(
        text,
        ["update_user_profile"],
        [{"tool_name": "update_user_profile", "is_error": False}],
    )
    assert result == text


@pytest.mark.parametrize(
    "text, tools, results",
    [
        ("", [], None),
        ("hello", [], None),
        ("\u5df2\u4fdd\u5b58\u4e86", [], None),
        ("\u5df2\u5220\u9664\u4e86", ["get_tool_info"], [{"tool_name": "get_tool_info", "is_error": False}]),
        ("\u4e4b\u524d\u5df2\u4fdd\u5b58", [], None),
    ],
)
def test_guard_parity_with_legacy(text, tools, results) -> None:
    _, legacy = _legacy()
    assert guard_unbacked_action_claim(text, tools, results) == legacy(text, tools, results)


def test_extract_parity_with_legacy() -> None:
    extract_legacy, _ = _legacy()
    cases = [
        ("\u5df2\u4fdd\u5b58\u4e86", set()),
        ("\u5df2\u5220\u9664\u4e86", {"write_file"}),
        ("\u5df2\u4fdd\u5b58\u4e86", {"write_file"}),
        ("write_file\u5df2\u8c03\u7528", set()),
        ("\u4e4b\u524d\u5df2\u4fdd\u5b58", set()),
    ]
    for text, tools in cases:
        assert extract_unbacked_verbs(text, tools) == extract_legacy(text, tools)
