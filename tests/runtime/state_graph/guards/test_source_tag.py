"""Tests for runtime/state_graph/guards/source_tag.check_source_tag_consistency.

Each case is also exercised against the legacy
``openakita.core.reasoning_engine._check_source_tag_consistency`` to
guarantee byte-for-byte parity (the legacy alias is now a re-import
of this module; the assertion catches a future regression that
re-introduces a divergent implementation).
"""

from __future__ import annotations

import pytest

from openakita.runtime.state_graph.guards.source_tag import (
    check_source_tag_consistency,
)


def _legacy():
    """Import legacy alias lazily to avoid a circular import at test collection."""
    import openakita.agent.brain  # noqa: F401  (warm-up to break the cycle)
    from openakita.core._reasoning_runtime import _check_source_tag_consistency as legacy

    return legacy


@pytest.mark.parametrize(
    "text, n",
    [
        ("[\u6765\u6e90:\u5de5\u5177] short answer", 0),
        ("[\u6765\u6e90\uff1a\u5de5\u5177] answer", 0),
        ("[\u6765\u6e90:\u5de5\u5177] answer", 3),
        ("\u5df2\u67e5\u5230\u4e86\u8be5\u4fe1\u606f", 0),
        ("\u5df2\u67e5\u5230\u4e86\u8be5\u4fe1\u606f", 2),
        ("[\u6765\u6e90:\u5e38\u8bc6] common knowledge", 0),
        ("plain answer no tags", 0),
        ("", 0),
        ("", 5),
        ("[\u6765\u6e90:\u5de5\u5177] and \u5df2\u67e5\u5230 both", 0),
    ],
)
def test_parity_with_legacy(text: str, n: int) -> None:
    """v1 ``_check_source_tag_consistency`` vs v2 ``check_source_tag_consistency``."""
    legacy = _legacy()
    assert check_source_tag_consistency(text, n) == legacy(text, n)


def test_returns_none_for_empty_text() -> None:
    assert check_source_tag_consistency("", 0) is None
    assert check_source_tag_consistency("", 99) is None


def test_returns_none_when_tools_executed_and_tag_is_consistent() -> None:
    # A tool-source tag is legitimate when a tool actually ran.
    assert check_source_tag_consistency("[\u6765\u6e90:\u5de5\u5177] X", 1) is None


def test_banner_for_tool_tag_without_tool_call_has_warning_icon() -> None:
    """The banner is intentionally explicit about the inconsistency."""
    banner = check_source_tag_consistency("[\u6765\u6e90:\u5de5\u5177] X", 0)
    assert banner is not None
    assert "\u26a0" in banner  # warning sign
    assert "[\u6765\u6e90:\u5de5\u5177]" in banner


def test_implicit_done_banner_for_action_phrase_without_tool() -> None:
    banner = check_source_tag_consistency("\u5df2\u67e5\u5230\u4e86", 0)
    assert banner is not None
    assert "\u26a0" in banner
    assert "\u672a\u5b9e\u9645\u8c03\u7528\u4efb\u4f55\u5de5\u5177" in banner
