"""Tests for openakita_plugin_sdk.contrib.tool_result (C0.2).

These tests pin the canonical envelope every plugin tool handler must
return.  Field name ``duration_seconds`` (NOT ``runtime_sec``) is the
single most important invariant — every reviewer / log emitter in the
host depends on it.
"""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import ToolResult


# ── canonical field names ─────────────────────────────────────────────


def test_field_name_is_duration_seconds_not_runtime_sec() -> None:
    """C0.2 invariant — if anyone ever renames this field the reviewer
    pipeline breaks silently because every consumer reads
    ``r['duration_seconds']`` directly."""
    r = ToolResult.success(duration_seconds=1.25)
    d = r.to_dict()
    assert "duration_seconds" in d
    assert "runtime_sec" not in d
    assert d["duration_seconds"] == 1.25


def test_warnings_default_to_empty_list_not_none() -> None:
    """Reviewer iterates ``for w in r.warnings`` — None would crash.
    Frozen dataclass with ``default_factory=list`` keeps each instance
    independent."""
    a = ToolResult.success()
    b = ToolResult.success()
    assert a.warnings == []
    a.warnings.append("a-only")
    assert b.warnings == []  # mutating one must not leak into the other


# ── construction guard rails ─────────────────────────────────────────


def test_success_helper_rejects_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        ToolResult.success(duration_seconds=-0.5)


def test_failure_requires_non_empty_error() -> None:
    """A failed tool with no reason would surface "stage failed: " in
    the reviewer log — fail loudly at construction instead."""
    with pytest.raises(ValueError, match="non-empty error"):
        ToolResult.failure(error="")


def test_post_init_rejects_ok_true_with_error() -> None:
    """``ok=True`` + ``error="..."`` is contradictory — caller likely
    meant to set a warning instead."""
    with pytest.raises(ValueError, match="error must be empty when ok=True"):
        ToolResult(ok=True, error="something weird")


def test_post_init_rejects_ok_false_without_error() -> None:
    with pytest.raises(ValueError, match="error must be non-empty when ok=False"):
        ToolResult(ok=False, error="")


def test_failed_property_mirrors_parallel_result() -> None:
    """Symmetric API with ``ParallelResult.failed`` so callers can write
    ``if r.failed`` regardless of which envelope they're holding."""
    assert ToolResult.success().failed is False
    assert ToolResult.failure(error="boom").failed is True


# ── frozen-ness ──────────────────────────────────────────────────────


def test_tool_result_is_frozen() -> None:
    """OpenMontage once had a reviewer that flipped ``ok=True`` after the
    fact, hiding a downstream regression — ``frozen=True`` makes this
    impossible by construction."""
    r = ToolResult.success()
    with pytest.raises(Exception):
        r.ok = False  # type: ignore[misc]


# ── (de)serialization round-trip ─────────────────────────────────────


def test_dict_round_trip_preserves_all_fields() -> None:
    original = ToolResult.success(
        output={"frames": 1200, "url": "/preview/abc"},
        duration_seconds=12.75,
        warnings=["GPU at 92%", "transient 429 retried once"],
        metadata={"vendor": "ark", "task_id": "t-001"},
    )
    restored = ToolResult.from_dict(original.to_dict())
    assert restored == original


def test_from_dict_coerces_str_warnings_and_metadata_keys() -> None:
    """Defensive — the wire payload may have non-string warnings (e.g.
    int codes) or non-dict metadata — coerce gracefully."""
    r = ToolResult.from_dict({
        "ok": True,
        "warnings": [1, "two"],
        "metadata": {"a": 1},
    })
    assert r.warnings == ["1", "two"]
    assert r.metadata == {"a": 1}


def test_failure_to_dict_carries_error_string() -> None:
    r = ToolResult.failure(
        error="ffmpeg exited with code 1 (codec h265 unavailable)",
        duration_seconds=0.4,
    )
    d = r.to_dict()
    assert d["ok"] is False
    assert "ffmpeg" in d["error"]
    assert d["duration_seconds"] == 0.4
