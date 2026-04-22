"""Tests for openakita_plugin_sdk.contrib.verification (D2.10).

Covers the trust-badge schema attached to plugin output:

* badge derivation (green / yellow / red) under every combination of
  ``verified``, ``verifier_id`` and flagged-fields,
* per-kind counters that drive UI tooltips,
* dict round-trip (the wire format consumed by the host UI),
* terminal-safe ``render_verification_badge`` helper,
* ``merge_verifications`` aggregation semantics (per-section → deck).
"""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import (
    BADGE_GREEN,
    BADGE_RED,
    BADGE_YELLOW,
    KIND_DATE,
    KIND_NUMBER,
    KIND_PERSON,
    LowConfidenceField,
    Verification,
    merge_verifications,
    render_verification_badge,
)


# ── LowConfidenceField construction ───────────────────────────────────


def test_low_confidence_field_rejects_unknown_kind() -> None:
    """Free-form ``kind`` strings would defeat the host's icon mapping —
    the dataclass must reject anything outside ``ALLOWED_KINDS``."""
    with pytest.raises(ValueError, match="kind must be one of"):
        LowConfidenceField(path="$.x", value=1, kind="banana")


def test_low_confidence_field_rejects_empty_path() -> None:
    """An empty path means the UI has nowhere to scroll — fail loudly."""
    with pytest.raises(ValueError, match="path must be non-empty"):
        LowConfidenceField(path="", value=1, kind=KIND_NUMBER)


def test_low_confidence_field_to_dict_omits_missing_suggested_value() -> None:
    """Smaller wire payload + the host UI distinguishes "no suggestion"
    from "suggested null" by absence of the key."""
    f = LowConfidenceField(path="$.x", value=42, kind=KIND_NUMBER)
    d = f.to_dict()
    assert "suggested_value" not in d
    assert d["path"] == "$.x"
    assert d["kind"] == KIND_NUMBER


def test_low_confidence_field_round_trip() -> None:
    f = LowConfidenceField(
        path="$.slides[2].stats.market_size",
        value="$4.2B",
        kind=KIND_NUMBER,
        reason="midpoint of $3.8B–$4.5B range",
        suggested_value="$4.0B",
    )
    restored = LowConfidenceField.from_dict(f.to_dict())
    assert restored == f


# ── Verification.badge derivation ─────────────────────────────────────


def test_badge_green_when_verified_and_no_flags() -> None:
    v = Verification.green(verifier_id="claude-3-5-sonnet")
    assert v.badge == BADGE_GREEN


def test_badge_yellow_when_any_field_flagged_even_if_verified() -> None:
    """D2.10 guard-rail: a flagged number must drag the badge to yellow
    even when the producer claimed ``verified=True``.  Otherwise the UI
    would render green and silently swallow the warning."""
    v = Verification(
        verified=True,
        verifier_id="gpt-4o",
        low_confidence_fields=[
            LowConfidenceField(path="$.x", value=1, kind=KIND_NUMBER),
        ],
    )
    assert v.badge == BADGE_YELLOW


def test_badge_yellow_when_unverified_but_verifier_ran() -> None:
    """``verified=False`` + ``verifier_id`` set means "the verifier ran
    and disagreed" — that's yellow (caution), not red (no signal)."""
    v = Verification(verified=False, verifier_id="gpt-4o")
    assert v.badge == BADGE_YELLOW


def test_badge_red_when_no_verifier_ran() -> None:
    """Default constructor: no verifier ran → red (unverified)."""
    assert Verification().badge == BADGE_RED
    assert Verification.red(notes="LLM call timed out").badge == BADGE_RED


# ── field_count_by_kind / dict round-trip ─────────────────────────────


def test_field_count_by_kind_groups_correctly() -> None:
    v = Verification(
        verified=False,
        verifier_id="gpt-4o",
        low_confidence_fields=[
            LowConfidenceField(path="$.a", value=1, kind=KIND_NUMBER),
            LowConfidenceField(path="$.b", value=2, kind=KIND_NUMBER),
            LowConfidenceField(path="$.c", value="2024-01-01", kind=KIND_DATE),
        ],
    )
    assert v.field_count_by_kind == {KIND_NUMBER: 2, KIND_DATE: 1}


def test_to_dict_includes_badge_and_counts() -> None:
    """The dict view is the wire format — host UI reads ``badge`` and
    ``field_count_by_kind`` directly without recomputing."""
    v = Verification(
        verified=True,
        verifier_id="claude-3-5",
        low_confidence_fields=[
            LowConfidenceField(path="$.x", value=1, kind=KIND_PERSON),
        ],
    )
    d = v.to_dict()
    assert d["badge"] == BADGE_YELLOW
    assert d["field_count_by_kind"] == {KIND_PERSON: 1}
    assert d["verifier_id"] == "claude-3-5"
    assert len(d["low_confidence_fields"]) == 1


def test_verification_round_trip_via_dict() -> None:
    original = Verification(
        verified=True,
        verifier_id="gpt-4o",
        low_confidence_fields=[
            LowConfidenceField(
                path="$.slides[0].title",
                value="The Q1 Outlook",
                kind=KIND_PERSON,
                reason="Title ambiguous about whose Q1.",
            ),
        ],
        notes="title cross-checked",
    )
    restored = Verification.from_dict(original.to_dict())
    assert restored == original


# ── render_verification_badge ─────────────────────────────────────────


def test_render_badge_green_shows_zero_flags() -> None:
    s = render_verification_badge(Verification.green(verifier_id="claude"))
    assert s == "[VERIFIED:claude] 0 flags"


def test_render_badge_yellow_summarizes_kinds_in_sorted_order() -> None:
    """Sorted output stops snapshot tests from flapping on dict ordering."""
    v = Verification(
        verified=False,
        verifier_id="gpt-4o",
        low_confidence_fields=[
            LowConfidenceField(path="$.a", value=1, kind=KIND_NUMBER),
            LowConfidenceField(path="$.b", value=2, kind=KIND_NUMBER),
            LowConfidenceField(path="$.c", value=3, kind=KIND_NUMBER),
            LowConfidenceField(path="$.d", value="2024", kind=KIND_DATE),
        ],
    )
    s = render_verification_badge(v)
    assert s == "[NEEDS_REVIEW:gpt-4o] 1 date, 3 numbers"


def test_render_badge_red_explains_why() -> None:
    s = render_verification_badge(Verification.red())
    assert s == "[UNVERIFIED] no verifier ran"


def test_render_badge_accepts_dict_input() -> None:
    """Round-trip parity: callers may pass either object or wire dict."""
    v = Verification.green(verifier_id="claude")
    assert render_verification_badge(v) == render_verification_badge(v.to_dict())


# ── merge_verifications ───────────────────────────────────────────────


def test_merge_empty_returns_default_red() -> None:
    """Empty input must produce the same envelope as ``Verification()``
    so callers can skip an ``if not parts`` guard."""
    assert merge_verifications([]) == Verification()


def test_merge_concatenates_flagged_fields_in_order() -> None:
    p1 = Verification(
        verified=True,
        verifier_id="claude",
        low_confidence_fields=[
            LowConfidenceField(path="$.s1.x", value=1, kind=KIND_NUMBER),
        ],
    )
    p2 = Verification(
        verified=True,
        verifier_id="gpt-4o",
        low_confidence_fields=[
            LowConfidenceField(path="$.s2.y", value="2024", kind=KIND_DATE),
        ],
    )
    merged = merge_verifications([p1, p2])
    assert [f.path for f in merged.low_confidence_fields] == [
        "$.s1.x",
        "$.s2.y",
    ]
    assert merged.verifier_id == "claude+gpt-4o"
    assert merged.verified is True
    # any flagged field still drags the badge to yellow
    assert merged.badge == BADGE_YELLOW


def test_merge_one_unverified_taints_whole_deck() -> None:
    """A single ``verified=False`` part must flip ``verified_all`` to
    False — otherwise a section with a silent verifier failure could
    appear "all green" at the deck level."""
    p1 = Verification.green(verifier_id="claude")
    p2 = Verification(verified=False, verifier_id="claude")
    merged = merge_verifications([p1, p2])
    assert merged.verified is False
    assert merged.verifier_id == "claude"  # deduped


def test_merge_joins_notes_with_newline() -> None:
    parts = [
        Verification.green(verifier_id="claude", notes="slide 1 ok"),
        Verification.green(verifier_id="claude", notes=""),
        Verification.green(verifier_id="claude", notes="slide 3 ok"),
    ]
    merged = merge_verifications(parts)
    assert merged.notes == "slide 1 ok\nslide 3 ok"
