"""Tests for openakita_plugin_sdk.skill_loader (C0.4).

Pins the SKILL.md frontmatter contract — the host's "load all skills"
loop relies on these invariants:

* missing / mismatched delimiters → :class:`SkillManifestError`,
* required keys (``name``, ``description``) enforced,
* ``name`` must be slug-safe,
* list / map-list / scalar shapes parsed correctly,
* unknown keys preserved verbatim under ``extra``,
* body text returned alongside the manifest so the host need not
  re-read the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita_plugin_sdk.skill_loader import (
    SkillManifest,
    SkillManifestError,
    load_skill,
    parse_skill_frontmatter,
)


# ── happy path ────────────────────────────────────────────────────────


_FULL_FIXTURE = """---
name: highlight-cutter
description: Cut a 30-second highlight reel from a long video.
version: 0.4.0
category: video
owner: openakita
triggers:
  - "highlight reel"
  - "cut a clip"
inputs:
  - source_video: path
  - target_duration_sec: int
outputs:
  - reel_video: path
requires:
  - ffmpeg
  - yt-dlp
tags:
  - video
  - editing
custom_field: kept-verbatim
---

# Highlight Cutter

Body content goes here.  Multiple paragraphs.

Even fenced blocks::

    not parsed by us
"""


def test_full_fixture_round_trip() -> None:
    parsed = parse_skill_frontmatter(_FULL_FIXTURE)
    m = parsed.manifest
    assert m.name == "highlight-cutter"
    assert m.description.startswith("Cut a 30-second")
    assert m.version == "0.4.0"
    assert m.category == "video"
    assert m.owner == "openakita"
    assert m.triggers == ["highlight reel", "cut a clip"]
    assert m.inputs == [
        {"source_video": "path"},
        {"target_duration_sec": "int"},
    ]
    assert m.outputs == [{"reel_video": "path"}]
    assert m.requires == ["ffmpeg", "yt-dlp"]
    assert m.tags == ["video", "editing"]
    assert m.extra == {"custom_field": "kept-verbatim"}
    assert "# Highlight Cutter" in parsed.body


def test_minimal_fixture_only_required_keys() -> None:
    text = "---\nname: bgm-mixer\ndescription: Mix BGM into a video.\n---\n"
    parsed = parse_skill_frontmatter(text)
    assert parsed.manifest.name == "bgm-mixer"
    assert parsed.manifest.description == "Mix BGM into a video."
    assert parsed.manifest.triggers == []
    assert parsed.body == ""


def test_unknown_keys_land_in_extra() -> None:
    """Forward-compat: a skill written for a future SDK version may use
    keys we don't recognise — they must survive the round-trip."""
    text = (
        "---\n"
        "name: sample\n"
        "description: d\n"
        "future_knob: 42\n"
        "another_one: hello\n"
        "---\n"
    )
    parsed = parse_skill_frontmatter(text)
    assert parsed.manifest.extra == {"future_knob": "42", "another_one": "hello"}


def test_to_dict_round_trip_preserves_lists() -> None:
    parsed = parse_skill_frontmatter(_FULL_FIXTURE)
    d = parsed.manifest.to_dict()
    assert d["triggers"] == ["highlight reel", "cut a clip"]
    assert d["inputs"][0] == {"source_video": "path"}
    assert d["extra"]["custom_field"] == "kept-verbatim"


# ── error paths ───────────────────────────────────────────────────────


def test_missing_leading_delimiter_raises() -> None:
    with pytest.raises(SkillManifestError, match="leading"):
        parse_skill_frontmatter("name: x\n---\n")


def test_missing_closing_delimiter_raises() -> None:
    with pytest.raises(SkillManifestError, match="closing"):
        parse_skill_frontmatter("---\nname: x\ndescription: y\n")


def test_missing_required_keys_raises_with_list() -> None:
    """Error message must list *which* keys are missing so the plugin
    author does not have to play whack-a-mole."""
    text = "---\nversion: 1.0\n---\nbody\n"
    with pytest.raises(SkillManifestError, match="description.*name|name.*description"):
        parse_skill_frontmatter(text)


def test_invalid_name_slug_raises() -> None:
    """Names appear in tool-call dispatch — must stay slug-safe."""
    text = "---\nname: Bad Name!\ndescription: x\n---\n"
    with pytest.raises(SkillManifestError, match="must match"):
        parse_skill_frontmatter(text)


def test_indented_first_line_raises() -> None:
    text = "---\n  name: x\n  description: y\n---\n"
    with pytest.raises(SkillManifestError, match="indented"):
        parse_skill_frontmatter(text)


def test_missing_colon_in_scalar_line_raises() -> None:
    text = "---\nname x\ndescription: y\n---\n"
    with pytest.raises(SkillManifestError, match="missing ':'"):
        parse_skill_frontmatter(text)


def test_mixed_scalar_and_dict_block_raises() -> None:
    """A list block must be all-strings or all-dicts, never mixed —
    otherwise downstream code can't pick a single iterator type."""
    text = (
        "---\n"
        "name: skill\n"
        "description: d\n"
        "triggers:\n"
        "  - bare-string\n"
        "  - keyed: value\n"
        "---\n"
    )
    with pytest.raises(SkillManifestError, match="mixes scalars and key:value"):
        parse_skill_frontmatter(text)


def test_missing_bullet_in_block_raises() -> None:
    text = (
        "---\n"
        "name: skill\n"
        "description: d\n"
        "triggers:\n"
        "  no_bullet_here\n"
        "---\n"
    )
    with pytest.raises(SkillManifestError, match="'- ' bullets"):
        parse_skill_frontmatter(text)


# ── load_skill (file-based) ──────────────────────────────────────────


def test_load_skill_reads_file_and_attaches_path(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_text(_FULL_FIXTURE, encoding="utf-8")
    parsed = load_skill(p)
    assert parsed.manifest.name == "highlight-cutter"


def test_load_skill_error_includes_path(tmp_path: Path) -> None:
    p = tmp_path / "broken.md"
    p.write_text("name: x\n", encoding="utf-8")
    with pytest.raises(SkillManifestError) as excinfo:
        load_skill(p)
    assert str(p) in str(excinfo.value)
    assert excinfo.value.path == p


def test_skill_manifest_is_frozen() -> None:
    m = SkillManifest(name="x", description="d")
    with pytest.raises(Exception):
        m.name = "y"  # type: ignore[misc]


# ── comments / blank lines ───────────────────────────────────────────


def test_comments_and_blank_lines_are_skipped() -> None:
    text = (
        "---\n"
        "# This is a comment\n"
        "\n"
        "name: skill\n"
        "# another comment\n"
        "description: d\n"
        "---\n"
        "body\n"
    )
    parsed = parse_skill_frontmatter(text)
    assert parsed.manifest.name == "skill"
