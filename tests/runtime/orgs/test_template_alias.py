"""F-4 §A-3: backward-compat alias map tests for get_template."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager


@pytest.fixture
def manager_with_canonical_template(tmp_path: Path) -> tuple[OrgManager, Path]:
    """Manager + templates dir, seeded with one ASCII-id template file."""
    mgr = OrgManager(tmp_path)
    templates_dir = tmp_path / "org_templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    canonical = templates_dir / "content-ops-zh.json"
    canonical.write_text(
        json.dumps(
            {
                "name": "内容运营团队",
                "description": "alias target",
                "nodes": [{"id": "n1"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return mgr, templates_dir


def test_get_template_direct_hit_still_works(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """Direct ASCII id lookup unchanged from pre-§A-3 behavior."""
    mgr, _ = manager_with_canonical_template
    tpl = mgr.get_template("content-ops-zh")
    assert tpl is not None
    assert tpl["name"] == "内容运营团队"


def test_get_template_alias_resolves_legacy_cjk_id(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """Legacy CJK id resolves via ``_aliases.json`` to the canonical ASCII id.

    This is the regression case: a frontend (or external caller) that
    bookmarked the pre-A-2 URL ``/api/v2/orgs/templates/内容运营团队``
    keeps working after the user runs the §A-4 migration.
    """
    _, templates_dir = manager_with_canonical_template
    (templates_dir / "_aliases.json").write_text(
        json.dumps({"内容运营团队": "content-ops-zh"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr, _ = manager_with_canonical_template
    tpl = mgr.get_template("内容运营团队")
    assert tpl is not None
    assert tpl["name"] == "内容运营团队"
    assert tpl["description"] == "alias target"


def test_get_template_missing_alias_returns_none(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """A miss with no alias entry still returns None (no fabrication)."""
    _, templates_dir = manager_with_canonical_template
    (templates_dir / "_aliases.json").write_text(
        json.dumps({"some-other-name": "content-ops-zh"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr, _ = manager_with_canonical_template
    assert mgr.get_template("内容运营团队") is None


def test_get_template_no_alias_file_is_noop(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """Absent ``_aliases.json`` -> direct-file behavior, no surprise lookups."""
    mgr, _ = manager_with_canonical_template
    assert mgr.get_template("does-not-exist") is None


def test_get_template_alias_points_to_missing_file_returns_none(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """An alias that points to a missing target returns None, not crashes."""
    _, templates_dir = manager_with_canonical_template
    (templates_dir / "_aliases.json").write_text(
        json.dumps({"some-cjk": "no-such-template"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr, _ = manager_with_canonical_template
    assert mgr.get_template("some-cjk") is None


def test_get_template_alias_self_reference_does_not_recurse(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """Alias map entry pointing at itself collapses to None (cycle-safe)."""
    _, templates_dir = manager_with_canonical_template
    # Self-alias to a non-existent canonical id.
    (templates_dir / "_aliases.json").write_text(
        json.dumps({"phantom": "phantom"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr, _ = manager_with_canonical_template
    # Direct file lookup misses; alias resolves to "phantom" which equals
    # the original id, so the `aliased != template_id` guard fires and
    # returns None without re-querying the filesystem.
    assert mgr.get_template("phantom") is None


def test_get_template_malformed_alias_file_logs_and_returns_none(
    manager_with_canonical_template: tuple[OrgManager, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed alias file (root is list) is logged and treated as absent."""
    import logging

    _, templates_dir = manager_with_canonical_template
    (templates_dir / "_aliases.json").write_text("[1,2,3]", encoding="utf-8")
    mgr, _ = manager_with_canonical_template
    with caplog.at_level(logging.WARNING):
        result = mgr.get_template("does-not-exist")
    assert result is None
    assert any("_aliases.json root must be an object" in r.message for r in caplog.records)


def test_create_from_template_uses_alias_resolution(
    manager_with_canonical_template: tuple[OrgManager, Path],
) -> None:
    """create_from_template inherits alias resolution via get_template."""
    _, templates_dir = manager_with_canonical_template
    (templates_dir / "_aliases.json").write_text(
        json.dumps({"内容运营团队": "content-ops-zh"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr, _ = manager_with_canonical_template
    # Should NOT raise FileNotFoundError; alias resolves.
    org = mgr.create_from_template("内容运营团队", overrides={"name": "instance-via-alias"})
    assert org.name == "instance-via-alias"
