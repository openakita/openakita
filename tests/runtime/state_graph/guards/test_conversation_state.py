"""Tests for runtime/state_graph/guards/conversation_state."""

from __future__ import annotations

import pytest

from openakita.runtime.state_graph.guards.conversation_state import (
    HARD_USER_BLOCKER_TOOL_MARKERS,
    RECOVERABLE_TOOL_ERROR_MARKERS,
    USER_BLOCKED_ACTIONS,
    USER_BLOCKED_MARKERS,
    has_recoverable_tool_issue,
    looks_like_waiting_for_user_response,
)


def _legacy():
    import openakita.agent.brain  # noqa: F401
    from openakita.core.reasoning_engine import (
        _has_recoverable_tool_issue,
        _looks_like_waiting_for_user_response,
    )
    return _looks_like_waiting_for_user_response, _has_recoverable_tool_issue


def test_word_list_anchor_terms() -> None:
    assert "\u9700\u8981\u7528\u6237" in USER_BLOCKED_MARKERS
    assert "\u5361\u4f4f" in USER_BLOCKED_ACTIONS
    assert "unknown_tool" in RECOVERABLE_TOOL_ERROR_MARKERS
    assert "\u9a8c\u8bc1\u7801" in HARD_USER_BLOCKER_TOOL_MARKERS


def test_looks_returns_false_for_empty_text() -> None:
    assert looks_like_waiting_for_user_response("") is False
    assert looks_like_waiting_for_user_response(None) is False  # type: ignore[arg-type]


def test_looks_returns_true_for_chinese_blocker_marker() -> None:
    assert looks_like_waiting_for_user_response("\u9700\u8981\u4f60\u63d0\u4f9b\u622a\u56fe") is True
    assert looks_like_waiting_for_user_response("\u8bf7\u624b\u52a8\u786e\u8ba4") is True


def test_looks_returns_true_for_english_blocker_phrase() -> None:
    assert looks_like_waiting_for_user_response("Cannot continue without your help") is True
    assert looks_like_waiting_for_user_response("Please confirm before I proceed") is True


def test_looks_returns_false_for_plain_progress_text() -> None:
    assert looks_like_waiting_for_user_response("Task complete; here is the output.") is False
    assert looks_like_waiting_for_user_response("\u4efb\u52a1\u5b8c\u6210\u4e86\uff0c\u8be5\u6587\u4ef6\u5728\u8fd9\u91cc") is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "\u9700\u8981\u4f60",
        "Please provide a password",
        "\u4efb\u52a1\u5df2\u5b8c\u6210",
        "\u6d4f\u89c8\u5668\u88ab\u5173\u95ed\u4e86",
    ],
)
def test_looks_parity_with_legacy(text: str) -> None:
    legacy_looks, _ = _legacy()
    assert looks_like_waiting_for_user_response(text) == legacy_looks(text)


def test_recoverable_returns_false_when_no_results() -> None:
    assert has_recoverable_tool_issue(None) is False
    assert has_recoverable_tool_issue([]) is False


def test_recoverable_true_on_unknown_tool_error() -> None:
    assert has_recoverable_tool_issue(
        [{"content": "unknown_tool: foo", "is_error": True}]
    ) is True


def test_recoverable_false_on_hard_user_blocker() -> None:
    assert has_recoverable_tool_issue(
        [{"content": "\u6d4f\u89c8\u5668\u8fde\u63a5\u5df2\u65ad\u5f00", "is_error": True}]
    ) is False


def test_recoverable_false_on_success() -> None:
    assert has_recoverable_tool_issue(
        [{"content": "ok", "is_error": False}]
    ) is False


@pytest.mark.parametrize(
    "tr",
    [
        None,
        [],
        [{"content": "unknown_tool foo", "is_error": True}],
        [{"content": "\u6d4f\u89c8\u5668\u8fde\u63a5\u5df2\u65ad\u5f00", "is_error": True}],
        [{"content": "must first call tool_search", "is_error": True}],
        [{"content": "ok", "is_error": False}],
    ],
)
def test_recoverable_parity_with_legacy(tr) -> None:
    _, legacy_recov = _legacy()
    assert has_recoverable_tool_issue(tr) == legacy_recov(tr)
