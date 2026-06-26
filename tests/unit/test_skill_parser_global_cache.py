"""P7.6a — SkillParser global parse cache regression tests.

Ensures that the process-wide cache in
``openakita.skills.parser`` reuses ParsedSkill objects across
SkillParser instances (so per-Agent skill loading does not re-read
the same SKILL.md from disk repeatedly), and that
``notify_skills_changed`` flushes the cache so install / uninstall
events do not serve stale parsed payloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.skills.events import SkillEvent, notify_skills_changed
from openakita.skills.parser import (
    _GLOBAL_PARSE_CACHE,
    SkillParser,
    invalidate_global_parse_cache,
)

SKILL_MD = """---
name: cache-probe
description: probe skill used by the global cache regression tests
---

This is the skill body.
""".strip()


@pytest.fixture(autouse=True)
def _clear_global_cache():
    """Each test starts with an empty global parse cache."""
    invalidate_global_parse_cache()
    yield
    invalidate_global_parse_cache()


def _make_skill(tmp_path: Path, name: str = "cache-probe") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    return skill_dir


def test_first_parse_populates_global_cache(tmp_path: Path):
    skill_dir = _make_skill(tmp_path)
    parser = SkillParser()

    assert not _GLOBAL_PARSE_CACHE, "cache should be empty pre-parse"
    parser.parse_directory(skill_dir)
    assert _GLOBAL_PARSE_CACHE, "first parse must populate the global cache"


def test_second_parser_reuses_global_cache_without_reading_disk(tmp_path: Path, monkeypatch):
    """A fresh SkillParser instance must hit the global cache instead
    of touching the filesystem again — that is the whole point of the
    process-wide cache."""
    skill_dir = _make_skill(tmp_path)

    parser_a = SkillParser()
    parser_a.parse_directory(skill_dir)
    assert _GLOBAL_PARSE_CACHE

    parser_b = SkillParser()
    skill_md = skill_dir / "SKILL.md"

    calls: list[Path] = []
    orig_read_text = Path.read_text

    def _spy_read_text(self, *args, **kwargs):
        if self == skill_md:
            calls.append(self)
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy_read_text)

    skill = parser_b.parse_directory(skill_dir)

    assert calls == [], (
        "second parse must serve from the global cache without reading SKILL.md from disk again"
    )
    assert skill.metadata.name == "cache-probe"


def test_cache_hit_returns_isolated_copy_so_mutation_does_not_leak(tmp_path: Path):
    """``SkillLoader.load_skill`` post-processes ``metadata.category``.
    The cache must hand each caller its own copy so this mutation does
    not pollute subsequent cache hits."""
    skill_dir = _make_skill(tmp_path)

    parser = SkillParser()
    first = parser.parse_directory(skill_dir)
    first.metadata.category = "Misc"
    first.metadata.allowed_tools.append("contaminated")

    parser_b = SkillParser()
    second = parser_b.parse_directory(skill_dir)
    assert second.metadata.category != "Misc"
    assert "contaminated" not in second.metadata.allowed_tools


def test_invalidate_clears_all_entries(tmp_path: Path):
    skill_dir = _make_skill(tmp_path)
    parser = SkillParser()
    parser.parse_directory(skill_dir)
    assert _GLOBAL_PARSE_CACHE

    dropped = invalidate_global_parse_cache()
    assert dropped >= 1
    assert not _GLOBAL_PARSE_CACHE


def test_invalidate_with_path_only_drops_matching_entries(tmp_path: Path):
    skill_a = _make_skill(tmp_path / "a", name="probe-a")
    skill_b = _make_skill(tmp_path / "b", name="probe-b")

    parser = SkillParser()
    parser.parse_directory(skill_a)
    parser.parse_directory(skill_b)
    assert len(_GLOBAL_PARSE_CACHE) >= 2

    # Drop only the "a" subtree.
    dropped = invalidate_global_parse_cache(tmp_path / "a")
    assert dropped == 1
    # The b skill must still be cached.
    remaining_paths = [k[0] for k in _GLOBAL_PARSE_CACHE]
    assert any("probe-b" in p for p in remaining_paths)
    assert not any("probe-a" in p for p in remaining_paths)


def test_notify_skills_changed_clears_parse_cache(tmp_path: Path):
    """install / reload / etc. must flush the cache so callers see fresh
    SKILL.md content even when its mtime did not change."""
    skill_dir = _make_skill(tmp_path)
    SkillParser().parse_directory(skill_dir)
    assert _GLOBAL_PARSE_CACHE

    notify_skills_changed(SkillEvent.INSTALL)
    assert not _GLOBAL_PARSE_CACHE, (
        "notify_skills_changed must wipe the parse cache so post-install "
        "SKILL.md changes are visible without an mtime bump"
    )


def test_mtime_change_bypasses_cache_naturally(tmp_path: Path):
    """When the on-disk SKILL.md mtime changes, the (path, mtime) key
    no longer matches and the parser re-reads the file."""
    import time

    skill_dir = _make_skill(tmp_path)
    parser = SkillParser()
    parser.parse_directory(skill_dir)
    first_keys = set(_GLOBAL_PARSE_CACHE.keys())

    # Rewrite the file with new content, advancing mtime.
    time.sleep(0.01)
    new_body = SKILL_MD.replace(
        "This is the skill body.",
        "This is the UPDATED skill body.",
    )
    (skill_dir / "SKILL.md").write_text(new_body, encoding="utf-8")
    # Force-bump mtime to defeat sub-second granularity on Windows.
    import os

    new_t = (skill_dir / "SKILL.md").stat().st_mtime + 1
    os.utime(skill_dir / "SKILL.md", (new_t, new_t))

    updated = parser.parse_directory(skill_dir)
    assert "UPDATED" in updated.body
    # New (path, mtime) key was added — old key may linger until
    # invalidate is called explicitly; that is fine.
    second_keys = set(_GLOBAL_PARSE_CACHE.keys())
    assert second_keys - first_keys, "new mtime should appear as a fresh cache key"
