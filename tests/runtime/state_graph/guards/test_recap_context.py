"""Tests for runtime/state_graph/guards/recap_context."""

from __future__ import annotations

import pytest

from openakita.runtime.state_graph.guards.recap_context import (
    RECAP_NEAR_RE,
    is_recap_context,
)


def _legacy():
    import openakita.agent.brain  # noqa: F401
    from openakita.core._reasoning_runtime import _is_recap_context as legacy

    return legacy


def test_recap_re_matches_timestamps_and_adverbs() -> None:
    for s in [
        "[17:30]",
        "[2026-05-09 12:00]",
        "\u4e4b\u524d",
        "\u521a\u624d",
        "\u5386\u53f2\u4e2d",
        "\u4e0a\u6587",
        "\u56de\u987e",
        "\u603b\u7ed3",
    ]:
        assert RECAP_NEAR_RE.search(s), f"failed to match: {s!r}"


def test_recap_re_does_not_match_unrelated_text() -> None:
    assert RECAP_NEAR_RE.search("hello world") is None
    assert RECAP_NEAR_RE.search("\u4eca\u5929") is None


def test_is_recap_context_returns_false_for_empty_inputs() -> None:
    assert is_recap_context("", "x") is False
    assert is_recap_context("text", "") is False
    assert is_recap_context("", "") is False


def test_is_recap_context_window_around_verb() -> None:
    text = "\u4e4b\u524d\u5df2\u7ecf\u5220\u9664\u4e86\u8be5\u6587\u4ef6"  # "Previously already deleted the file"
    assert is_recap_context(text, "\u5220\u9664") is True


def test_is_recap_context_not_a_recap_when_verb_isolated() -> None:
    text = (
        "\u6211\u5c06\u5220\u9664\u8be5\u6587\u4ef6"  # "I will delete the file" - no recap markers
    )
    assert is_recap_context(text, "\u5220\u9664") is False


def test_is_recap_context_window_is_48_chars_each_side() -> None:
    far = "x" * 100 + "\u5220\u9664" + "x" * 100 + "\u4e4b\u524d"
    # The recap marker sits more than 48 chars after the verb -> not detected
    assert is_recap_context(far, "\u5220\u9664") is False


@pytest.mark.parametrize(
    "text, verb",
    [
        ("\u4e4b\u524d\u5df2\u7ecf\u5220\u9664", "\u5220\u9664"),
        ("\u6211\u5c06\u5220\u9664\u6587\u4ef6", "\u5220\u9664"),
        ("[17:30] \u521a\u521a\u5b89\u88c5\u4e86\u63d2\u4ef6", "\u5b89\u88c5"),
        ("normal English no recap", "delete"),
        ("", "\u5220\u9664"),
    ],
)
def test_is_recap_context_parity_with_legacy(text: str, verb: str) -> None:
    assert is_recap_context(text, verb) == _legacy()(text, verb)
