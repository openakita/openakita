"""Unit tests for F-4 §A-2: save_as_template auto-slug + list_templates display_name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager


@pytest.fixture
def tmp_manager(tmp_path: Path) -> OrgManager:
    """Fresh OrgManager rooted in an empty temp dir."""
    return OrgManager(tmp_path)


def test_save_as_template_with_cjk_name_yields_ascii_id(tmp_manager: OrgManager) -> None:
    """The regression case from smoke F-4: org.name pure-CJK -> auto slug.

    Before §A-2 the fallback `org.name.lower().replace(" ", "-")` would
    pass CJK through verbatim, producing pure-CJK template ids that
    broke non-JS HTTP clients. After §A-2 the slugify fallback yields a
    deterministic ``tpl-<md5_8>`` ASCII id.
    """
    org = tmp_manager.create({"name": "内容运营团队"})
    tid = tmp_manager.save_as_template(org.id)
    assert tid.isascii(), f"non-ascii auto-generated template id: {tid!r}"
    assert tid.startswith("tpl-"), tid
    # Determinism: the slug is a deterministic function of the org name.
    # We verify directly via the helper rather than create two orgs with
    # the same name (the manager enforces a name-uniqueness invariant).
    from openakita.orgs._slug import slugify_template_id

    assert slugify_template_id("内容运营团队") == tid


def test_save_as_template_with_ascii_name_yields_kebab_slug(tmp_manager: OrgManager) -> None:
    """ASCII org names still slug to kebab-case (no surprise behavior change)."""
    org = tmp_manager.create({"name": "Content Ops Team"})
    tid = tmp_manager.save_as_template(org.id)
    assert tid == "content-ops-team"


def test_save_as_template_explicit_id_unchanged(tmp_manager: OrgManager) -> None:
    """When the caller passes an explicit template_id, it is used verbatim.

    The slugify pass only kicks in for the auto-derive-from-org-name
    branch -- callers who already know what id they want are trusted.
    """
    org = tmp_manager.create({"name": "ignored"})
    tid = tmp_manager.save_as_template(org.id, template_id="MyCustom-ID")
    assert tid == "MyCustom-ID"


def test_list_templates_exposes_display_name(tmp_manager: OrgManager) -> None:
    """Every template entry must carry both ``id`` (slug) and ``display_name``."""
    # Seed: one ASCII-named template, one CJK-named template (saved
    # under an ASCII slug via §A-2's slugify fallback).
    org_ascii = tmp_manager.create({"name": "Content Ops Team"})
    tid_ascii = tmp_manager.save_as_template(org_ascii.id)
    org_cjk = tmp_manager.create({"name": "内容运营团队"})
    tid_cjk = tmp_manager.save_as_template(org_cjk.id)

    entries = tmp_manager.list_templates()
    by_id = {e["id"]: e for e in entries}

    assert tid_ascii in by_id, by_id
    assert tid_cjk in by_id, by_id
    assert by_id[tid_ascii]["display_name"] == "Content Ops Team"
    # CJK template: id is ASCII slug, display_name keeps the readable label.
    assert by_id[tid_cjk]["id"].isascii()
    assert by_id[tid_cjk]["display_name"] == "内容运营团队"


def test_list_templates_legacy_pre_a2_cjk_file_keeps_cjk_id(
    tmp_manager: OrgManager,
    tmp_path: Path,
) -> None:
    """Pre-A-2 user files whose stem is itself CJK still appear in list.

    The migration script (§A-4) is dry-run by default and never touches
    user data files. Legacy CJK-stemmed files therefore remain readable
    until the maintainer chooses to apply the migration. Verify
    list_templates exposes them with a CJK id AND a CJK display_name
    (so the UI does not show a stem-as-name fallback for the rare
    case where the JSON `name` is missing).
    """
    # Manually plant a pre-A-2 file with CJK stem (no JSON `name` field).
    templates_dir = tmp_path / "org_templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = templates_dir / "测试旧模板.json"
    legacy_file.write_text(
        json.dumps({"description": "legacy", "nodes": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    entries = tmp_manager.list_templates()
    by_id = {e["id"]: e for e in entries}
    assert "测试旧模板" in by_id, by_id
    # When JSON `name` is absent, display_name falls back to stem.
    assert by_id["测试旧模板"]["display_name"] == "测试旧模板"


def test_list_templates_prefers_json_name_over_stem_for_display_name(
    tmp_manager: OrgManager,
    tmp_path: Path,
) -> None:
    """If the JSON file has a `name` field, display_name uses it."""
    templates_dir = tmp_path / "org_templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    f = templates_dir / "tpl-abcd1234.json"
    f.write_text(
        json.dumps({"name": "内容运营团队", "nodes": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    entries = tmp_manager.list_templates()
    by_id = {e["id"]: e for e in entries}
    assert by_id["tpl-abcd1234"]["id"] == "tpl-abcd1234"
    assert by_id["tpl-abcd1234"]["display_name"] == "内容运营团队"
    # `name` (legacy field) is also display_name for backward compat.
    assert by_id["tpl-abcd1234"]["name"] == "内容运营团队"
