"""Tests for templates.py — builtin org templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.templates import (
    ALL_TEMPLATES,
    CONTENT_OPS,
    SOFTWARE_TEAM,
    STARTUP_COMPANY,
    TEMPLATE_POLICY_MAP,
    ensure_builtin_templates,
)
from openakita.orgs.models import Organization, OrgNode, OrgEdge


class TestTemplateData:
    @pytest.mark.parametrize("tpl_id", ALL_TEMPLATES.keys())
    def test_template_parseable(self, tpl_id: str):
        tpl = ALL_TEMPLATES[tpl_id]
        org = Organization.from_dict(tpl)
        assert org.name
        assert len(org.nodes) > 0
        assert len(org.edges) > 0

    @pytest.mark.parametrize("tpl_id", ALL_TEMPLATES.keys())
    def test_edges_reference_valid_nodes(self, tpl_id: str):
        tpl = ALL_TEMPLATES[tpl_id]
        node_ids = {n["id"] for n in tpl["nodes"]}
        for e in tpl["edges"]:
            assert e["source"] in node_ids, f"Edge source {e['source']} not in nodes"
            assert e["target"] in node_ids, f"Edge target {e['target']} not in nodes"

    def test_startup_has_ceo(self):
        org = Organization.from_dict(STARTUP_COMPANY)
        roots = org.get_root_nodes()
        assert any("CEO" in n.role_title for n in roots)

    def test_software_team_has_departments(self):
        org = Organization.from_dict(SOFTWARE_TEAM)
        depts = org.get_departments()
        assert "前端组" in depts
        assert "后端组" in depts

    def test_policy_map_covers_all_templates(self):
        for tid in ALL_TEMPLATES:
            assert tid in TEMPLATE_POLICY_MAP


class TestEnsureBuiltinTemplates:
    def test_installs_all(self, tmp_path: Path):
        tpl_dir = tmp_path / "templates"
        ensure_builtin_templates(tpl_dir)

        files = list(tpl_dir.glob("*.json"))
        assert len(files) == len(ALL_TEMPLATES)

        for tid in ALL_TEMPLATES:
            p = tpl_dir / f"{tid}.json"
            assert p.is_file()
            data = json.loads(p.read_text(encoding="utf-8"))
            assert "policy_template" in data
            assert data["name"]

    def test_idempotent(self, tmp_path: Path):
        tpl_dir = tmp_path / "templates"
        ensure_builtin_templates(tpl_dir)
        ensure_builtin_templates(tpl_dir)
        files = list(tpl_dir.glob("*.json"))
        assert len(files) == len(ALL_TEMPLATES)

    def test_does_not_overwrite(self, tmp_path: Path):
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        custom = tpl_dir / "startup-company.json"
        custom.write_text('{"custom": true}', encoding="utf-8")

        ensure_builtin_templates(tpl_dir)
        data = json.loads(custom.read_text(encoding="utf-8"))
        assert data.get("custom") is True

