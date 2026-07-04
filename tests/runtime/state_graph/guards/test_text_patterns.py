"""Tests for runtime/state_graph/guards/_text_patterns.py."""

from __future__ import annotations

from openakita.runtime.state_graph.guards._text_patterns import (
    action_done_re,
    source_tag_re,
)


def test_source_tag_re_matches_all_four_tag_kinds() -> None:
    pat = source_tag_re()
    for s in [
        "[\u6765\u6e90:\u5de5\u5177]",
        "[\u6765\u6e90:\u5386\u53f2]",
        "[\u6765\u6e90:\u5e38\u8bc6]",
        "[\u6765\u6e90:\u4e0d\u786e\u5b9a]",
        "[\u6765\u6e90\uff1a\u5de5\u5177]",
        "[\u6765\u6e90:  \u5de5\u5177]",
    ]:
        assert pat.search(s), f"failed to match: {s!r}"


def test_source_tag_re_does_not_match_other_brackets() -> None:
    pat = source_tag_re()
    assert pat.search("[ref: tool]") is None
    assert pat.search("\u6765\u6e90:\u5de5\u5177") is None  # no brackets
    assert pat.search("[\u6765\u6e90:\u5176\u4ed6]") is None  # unknown kind


def test_action_done_re_matches_chinese_completed_phrases() -> None:
    pat = action_done_re()
    for s in [
        "\u5df2\u67e5\u5230\u4e86\u8be5\u4fe1\u606f",
        "\u5df2\u7ecf\u8bfb\u4e86\u6587\u4ef6",
        "\u5df2\u6267\u884c\u5b8c\u6210",
        "\u6211\u521a\u624d\u67e5\u5230",
        "\u6211\u521a\u521a\u6267\u884c",
        "\u5df2\u4fdd\u5b58",
    ]:
        assert pat.search(s), f"failed to match: {s!r}"


def test_action_done_re_does_not_match_future_or_unrelated() -> None:
    pat = action_done_re()
    assert pat.search("\u5c06\u8981\u67e5\u8be2") is None
    assert pat.search("\u8ba1\u5212\u8bfb\u53d6") is None
    assert pat.search("normal English text") is None


def test_caches_are_function_singletons() -> None:
    """Re-call returns same compiled pattern object (avoids re-compile cost)."""
    assert source_tag_re() is source_tag_re()
    assert action_done_re() is action_done_re()
