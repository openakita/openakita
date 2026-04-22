"""Verification — output trust schema for "one creates, another validates".

Inspired by AnyGen's *double-check* UX (D2.10 in
``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``):

    "事实字段（数字/日期/人名）默认高亮黄色"
    "double AI verification: one creates, another validates"

Plugins that produce structured content (slides, scripts, summaries,
storyboards, schedules ...) attach a :class:`Verification` envelope to
their output so the host UI can:

* render a green/yellow/red trust badge,
* highlight low-confidence fields ("3 numbers, 1 date — please review"),
* surface the verifier's notes when the user clicks the badge.

This module is **schema-only** — running the second verifier is the
plugin's job (could be another LLM call, regex check, calculator, or
manual review).  The schema gives every plugin a uniform place to
report verification state.

Example::

    from openakita_plugin_sdk.contrib import (
        Verification, LowConfidenceField, render_verification_badge,
    )

    v = Verification(
        verified=True,
        verifier_id="claude-3-5-sonnet",
        low_confidence_fields=[
            LowConfidenceField(
                path="$.slides[2].stats.market_size",
                value="$4.2B",
                kind="number",
                reason="Source cites $3.8B–$4.5B range; chose midpoint.",
            ),
        ],
        notes="All people names cross-checked against source PDFs.",
    )
    output["verification"] = v.to_dict()
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# Field-kind taxonomy — used by the host UI to pick the right colour /
# tooltip / inline-edit affordance.  Keep the set small and stable so
# downstream renderers can hard-code icon/colour mappings.
KIND_NUMBER = "number"
KIND_DATE = "date"
KIND_PERSON = "person"
KIND_PLACE = "place"
KIND_QUOTE = "quote"
KIND_URL = "url"
KIND_OTHER = "other"

ALLOWED_KINDS: frozenset[str] = frozenset({
    KIND_NUMBER, KIND_DATE, KIND_PERSON,
    KIND_PLACE, KIND_QUOTE, KIND_URL, KIND_OTHER,
})


# Trust badge levels — keep thresholds in one place so SDK callers and
# the html.tpl renderer agree on what "green / yellow / red" means.
BADGE_GREEN = "verified"
BADGE_YELLOW = "needs_review"
BADGE_RED = "unverified"


@dataclass(frozen=True)
class LowConfidenceField:
    """One factual field the verifier could not fully confirm.

    ``path`` should be a JSONPath-ish breadcrumb so the UI can scroll/
    highlight the exact span (e.g. ``$.slides[2].stats.market_size``,
    ``$.script[12].tokens[3].text``).  The format is intentionally
    *unparsed* — both producer and consumer agree on the convention but
    the SDK never tries to evaluate the path.
    """

    path: str
    value: Any
    kind: str = KIND_OTHER
    reason: str = ""
    suggested_value: Any = None

    def __post_init__(self) -> None:
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(
                f"LowConfidenceField.kind must be one of {sorted(ALLOWED_KINDS)},"
                f" got {self.kind!r}"
            )
        if not self.path:
            raise ValueError("LowConfidenceField.path must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "value": self.value,
            "kind": self.kind,
            "reason": self.reason,
        }
        if self.suggested_value is not None:
            out["suggested_value"] = self.suggested_value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LowConfidenceField:
        return cls(
            path=str(data["path"]),
            value=data.get("value"),
            kind=str(data.get("kind", KIND_OTHER)),
            reason=str(data.get("reason", "")),
            suggested_value=data.get("suggested_value"),
        )


@dataclass(frozen=True)
class Verification:
    """Verification envelope attached to plugin output.

    Attributes:
        verified: True when the second verifier ran and produced *no*
            low-confidence fields.  When False, ``low_confidence_fields``
            usually has entries (but may be empty if the verifier itself
            failed — see ``notes``).
        verifier_id: Identifier of the second model / heuristic that ran
            the check (e.g. ``"gpt-4o"``, ``"regex:numeric"``,
            ``"manual"``).  Empty when no verifier ran.
        low_confidence_fields: Per-field flags.  Order is preserved so
            the UI can render them in document order.
        notes: Free-form note from the verifier ("3 numbers cross-checked
            against source", "verifier timed out, falling back to red").
    """

    verified: bool = False
    verifier_id: str = ""
    low_confidence_fields: list[LowConfidenceField] = field(default_factory=list)
    notes: str = ""

    @property
    def badge(self) -> str:
        """Compute the badge bucket from verified + low_confidence count.

        * ``verified=True`` and no flagged fields → green.
        * Any flagged field → yellow (regardless of ``verified``).
        * ``verified=False`` and no verifier_id → red (nothing ran).
        * ``verified=False`` and verifier_id present → yellow (verifier
          ran but disagreed).

        Two-AI check semantics: yellow means "one model said yes, the
        other flagged something — show the user before they ship."
        """
        if self.low_confidence_fields:
            return BADGE_YELLOW
        if self.verified:
            return BADGE_GREEN
        if self.verifier_id:
            return BADGE_YELLOW
        return BADGE_RED

    @property
    def field_count_by_kind(self) -> dict[str, int]:
        """Count flagged fields by ``kind`` — drives the badge tooltip
        ("3 numbers, 1 date — please review")."""
        counts: dict[str, int] = {}
        for f in self.low_confidence_fields:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "verifier_id": self.verifier_id,
            "badge": self.badge,
            "field_count_by_kind": self.field_count_by_kind,
            "low_confidence_fields": [
                f.to_dict() for f in self.low_confidence_fields
            ],
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verification:
        raw_fields = data.get("low_confidence_fields") or []
        fields_out = [LowConfidenceField.from_dict(f) for f in raw_fields]
        return cls(
            verified=bool(data.get("verified", False)),
            verifier_id=str(data.get("verifier_id", "")),
            low_confidence_fields=fields_out,
            notes=str(data.get("notes", "")),
        )

    @classmethod
    def green(cls, *, verifier_id: str, notes: str = "") -> Verification:
        """Convenience: build a clean ``verified=True`` envelope."""
        return cls(verified=True, verifier_id=verifier_id, notes=notes)

    @classmethod
    def red(cls, *, notes: str = "") -> Verification:
        """Convenience: build an ``unverified`` envelope (no verifier ran)."""
        return cls(verified=False, verifier_id="", notes=notes)


def render_verification_badge(v: Verification | dict[str, Any]) -> str:
    """Render a one-line, terminal-safe badge string for logs/CLI.

    UI surfaces should consume :meth:`Verification.to_dict` directly —
    this helper exists for log lines, ``--verbose`` CLI output, and
    test assertions where a comparable string is more convenient than
    a dict.

    Examples::

        "[VERIFIED:claude-3-5] 0 flags"
        "[NEEDS_REVIEW:gpt-4o] 3 numbers, 1 date"
        "[UNVERIFIED] no verifier ran"
    """
    obj = v if isinstance(v, Verification) else Verification.from_dict(v)
    badge = obj.badge

    if badge == BADGE_GREEN:
        flags_part = "0 flags"
    else:
        counts = obj.field_count_by_kind
        if counts:
            flags_part = ", ".join(
                f"{c} {k}{'s' if c > 1 and not k.endswith('s') else ''}"
                for k, c in sorted(counts.items())
            )
        elif badge == BADGE_RED:
            flags_part = "no verifier ran"
        else:
            flags_part = "verifier disagreed"

    if obj.verifier_id:
        return f"[{badge.upper()}:{obj.verifier_id}] {flags_part}"
    return f"[{badge.upper()}] {flags_part}"


def merge_verifications(parts: Iterable[Verification]) -> Verification:
    """Combine per-section verifications into a single document-level one.

    Used when a plugin verifies slides one-by-one then needs to surface
    a single envelope for the whole deck.  The merged result:

    * ``verified=True`` only if **every** input was verified.
    * ``low_confidence_fields`` concatenated, paths preserved verbatim.
    * ``verifier_id`` joined with ``+`` if multiple, deduped.
    * ``notes`` joined with ``\\n`` if non-empty.
    """
    parts = list(parts)
    if not parts:
        return Verification()
    verified_all = all(p.verified for p in parts)
    flagged: list[LowConfidenceField] = []
    for p in parts:
        flagged.extend(p.low_confidence_fields)
    seen_ids: list[str] = []
    for p in parts:
        if p.verifier_id and p.verifier_id not in seen_ids:
            seen_ids.append(p.verifier_id)
    notes = "\n".join(p.notes for p in parts if p.notes)
    return Verification(
        verified=verified_all,
        verifier_id="+".join(seen_ids),
        low_confidence_fields=flagged,
        notes=notes,
    )


__all__ = [
    "ALLOWED_KINDS",
    "BADGE_GREEN",
    "BADGE_RED",
    "BADGE_YELLOW",
    "KIND_DATE",
    "KIND_NUMBER",
    "KIND_OTHER",
    "KIND_PERSON",
    "KIND_PLACE",
    "KIND_QUOTE",
    "KIND_URL",
    "LowConfidenceField",
    "Verification",
    "merge_verifications",
    "render_verification_badge",
]
