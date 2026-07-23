"""Tests for runtime/state_graph/guards/tool_failure_ack.

Covers the dual helpers:

* :func:`check_tool_failure_acknowledgement` -- banner when a tool
  failed and the LLM text never acknowledges any failure.
* :func:`successful_tool_names` -- aggregate retry-success behaviour.

Includes 8 parity cases against the legacy aliases re-imported from
``core/reasoning_engine.py`` so a future regression that diverges
v1 vs v2 fails loudly.
"""

from __future__ import annotations

import pytest

from openakita.runtime.state_graph.guards.tool_failure_ack import (
    FAILURE_ACKNOWLEDGE_EN,
    FAILURE_ACKNOWLEDGE_ZH,
    check_tool_failure_acknowledgement,
    successful_tool_names,
)


def _legacy():
    import openakita.agent.brain  # noqa: F401  (warm up)
    from openakita.core._reasoning_runtime import (
        _check_tool_failure_acknowledgement,
        _successful_tool_names,
    )

    return _check_tool_failure_acknowledgement, _successful_tool_names


@pytest.mark.parametrize(
    "text, tool_results",
    [
        ("", [{"tool_name": "x", "is_error": True}]),
        ("\u4efb\u52a1\u5b8c\u6210", None),
        ("\u4efb\u52a1\u5b8c\u6210", []),
        ("\u4efb\u52a1\u5b8c\u6210", [{"tool_name": "x", "is_error": True}]),
        ("There was an error during execution", [{"tool_name": "x", "is_error": True}]),
        ("\u51fa\u9519\u4e86", [{"tool_name": "x", "is_error": True}]),
        (
            "ok",
            [
                {"tool_name": "x", "is_error": True},
                {"tool_name": "x", "is_error": False},
            ],
        ),
        (
            "success",
            [
                {"tool_name": "x", "is_error": True},
                {"tool_name": "y", "is_error": True},
            ],
        ),
    ],
)
def test_check_parity_with_legacy(text, tool_results) -> None:
    legacy_check, _ = _legacy()
    assert check_tool_failure_acknowledgement(text, tool_results) == legacy_check(
        text, tool_results
    )


def test_check_returns_none_for_empty_text() -> None:
    assert check_tool_failure_acknowledgement("", None) is None
    assert check_tool_failure_acknowledgement("", [{"tool_name": "x", "is_error": True}]) is None


def test_check_returns_none_when_no_failures() -> None:
    assert check_tool_failure_acknowledgement("ok", [{"tool_name": "x", "is_error": False}]) is None


def test_check_returns_none_when_acknowledged_in_chinese() -> None:
    for kw in ["\u5931\u8d25", "\u51fa\u9519", "\u62a5\u9519", "\u9519\u8bef", "\u5f02\u5e38"]:
        assert (
            check_tool_failure_acknowledgement(
                f"\u5de5\u5177{kw}\u4e86\u3002", [{"tool_name": "x", "is_error": True}]
            )
            is None
        ), f"unexpected banner for keyword {kw}"


def test_check_returns_none_when_acknowledged_in_english() -> None:
    for kw in ["failed", "error", "unable", "could not", "not found"]:
        assert (
            check_tool_failure_acknowledgement(
                f"The tool {kw} this time.", [{"tool_name": "x", "is_error": True}]
            )
            is None
        ), f"unexpected banner for keyword {kw}"


def test_check_banner_lists_failed_tools_capped_at_five() -> None:
    failures = [{"tool_name": f"t{i}", "is_error": True} for i in range(8)]
    banner = check_tool_failure_acknowledgement("done", failures)
    assert banner is not None
    assert banner.count("t") >= 5  # at least the first five named


def test_word_lists_have_expected_anchor_terms() -> None:
    assert "\u5931\u8d25" in FAILURE_ACKNOWLEDGE_ZH
    assert "fail" in FAILURE_ACKNOWLEDGE_EN
    assert "unable" in FAILURE_ACKNOWLEDGE_EN


@pytest.mark.parametrize(
    "names, tool_results, expected",
    [
        ([], None, set()),
        (["a"], None, {"a"}),
        (["a", "b"], [{"tool_name": "a", "is_error": False}], {"a", "b"}),
        (["a"], [{"tool_name": "a", "is_error": True}], set()),
        (
            ["a"],
            [
                {"tool_name": "a", "is_error": True},
                {"tool_name": "a", "is_error": False},
            ],
            {"a"},
        ),
    ],
)
def test_successful_tool_names(names, tool_results, expected) -> None:
    assert successful_tool_names(names, tool_results) == expected


def test_successful_tool_names_parity_with_legacy() -> None:
    _, legacy = _legacy()
    cases = [
        (["a", "b"], [{"tool_name": "a", "is_error": True}, {"tool_name": "b", "is_error": False}]),
        (["x", "y", "z"], None),
        (["x"], [{"tool_name": "x", "is_error": True}, {"tool_name": "x", "is_error": False}]),
    ]
    for names, tr in cases:
        assert successful_tool_names(names, tr) == legacy(names, tr)
