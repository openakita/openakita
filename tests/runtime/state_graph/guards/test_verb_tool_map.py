"""Tests for runtime/state_graph/guards/_verb_tool_map data shape."""

from __future__ import annotations

from openakita.runtime.state_graph.guards._verb_tool_map import (
    CLAIMED_TOOL_TO_FRAGMENTS,
    VERB_TO_TOOL_FRAGMENTS,
)


def test_claimed_tool_map_anchor_entries() -> None:
    """Pin a few critical mappings so a deletion fails loudly."""
    assert CLAIMED_TOOL_TO_FRAGMENTS["write_file"] == ("write_file",)
    assert CLAIMED_TOOL_TO_FRAGMENTS["edit_file"] == ("edit_file",)
    assert CLAIMED_TOOL_TO_FRAGMENTS["delete_file"] == ("delete_file",)


def test_claimed_tool_map_keys_are_lowercase_tool_names() -> None:
    for key in CLAIMED_TOOL_TO_FRAGMENTS:
        assert key.islower() or "_" in key
        assert " " not in key


def test_verb_tool_map_has_chinese_high_risk_verbs() -> None:
    for verb in [
        "\u5220\u9664",
        "\u4fdd\u5b58",
        "\u5199\u5165",
        "\u53d1\u9001",
        "\u521b\u5efa",
        "\u8c03\u5ea6",
    ]:
        assert verb in VERB_TO_TOOL_FRAGMENTS, f"missing verb {verb}"


def test_verb_tool_map_values_are_tool_fragment_tuples() -> None:
    for verb, fragments in VERB_TO_TOOL_FRAGMENTS.items():
        assert isinstance(fragments, tuple), f"{verb}: {type(fragments)}"
        assert len(fragments) > 0, f"{verb} has empty fragments"
        for frag in fragments:
            assert isinstance(frag, str) and frag, f"{verb}: bad fragment {frag!r}"


def test_legacy_aliases_resolve_to_same_objects() -> None:
    """The re-import in core/reasoning_engine must point at our dicts."""
    import openakita.agent.brain  # noqa: F401
    from openakita.core._reasoning_runtime import (
        _CLAIMED_TOOL_TO_FRAGMENTS,
        _VERB_TO_TOOL_FRAGMENTS,
    )

    assert _CLAIMED_TOOL_TO_FRAGMENTS is CLAIMED_TOOL_TO_FRAGMENTS
    assert _VERB_TO_TOOL_FRAGMENTS is VERB_TO_TOOL_FRAGMENTS
