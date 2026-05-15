"""Tests for templates.py — builtin org templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.models import Organization
from openakita.orgs.templates import (
    AIGC_VIDEO_STUDIO,
    ALL_TEMPLATES,
    SOFTWARE_TEAM,
    STARTUP_COMPANY,
    TEMPLATE_POLICY_MAP,
    ensure_builtin_templates,
)


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


class TestAigcVideoStudioTemplate:
    """Workbench-specific invariants for the AIGC video studio template.

    The template ships workbench leaf nodes that depend on the
    `tongyi-image` and `seedance-video` plugins. The runtime / manager will
    refuse to run if a workbench node has hierarchy children, so the
    template's structural shape must hold even before any plugin is loaded.
    """

    def test_workbench_nodes_carry_plugin_origin(self):
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        wb_nodes = [n for n in org.nodes if n.plugin_origin]
        plugin_ids = {n.plugin_origin["plugin_id"] for n in wb_nodes}
        assert plugin_ids == {"tongyi-image", "seedance-video"}
        for n in wb_nodes:
            assert n.plugin_origin.get("template_id", "").startswith("workbench:")
            assert n.can_delegate is False
            assert n.enable_file_tools is False

    def test_workbench_nodes_are_leaves(self):
        """No hierarchy edge may originate from a workbench node."""
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        wb_ids = {n.id for n in org.nodes if n.plugin_origin}
        offenders = [
            (n.id, [c.id for c in org.get_children(n.id)])
            for n in org.nodes
            if n.id in wb_ids and org.get_children(n.id)
        ]
        assert offenders == [], (
            f"Workbench nodes must be leaves (no hierarchy children), "
            f"offenders={offenders}"
        )

    def test_workbench_external_tools_match_plugin_tool_names(self):
        """`external_tools` on workbench nodes must list the exact tool names
        the plugins register (``tongyi_image_*``, ``seedance_*``)."""
        tpl = AIGC_VIDEO_STUDIO
        expected = {
            "tongyi-image": {"tongyi_image_create", "tongyi_image_status", "tongyi_image_list"},
            "seedance-video": {
                "seedance_create",
                "seedance_edit",
                "seedance_extend",
                "seedance_transition",
                "seedance_status",
                "seedance_list",
            },
        }
        for node in tpl["nodes"]:
            po = node.get("plugin_origin")
            if not po:
                continue
            pid = po["plugin_id"]
            assert pid in expected
            assert set(node["external_tools"]) == expected[pid]

    def test_template_round_trips_plugin_origin(self):
        """Plugin origin must survive ``from_dict``/``to_dict`` (used during
        save/load and create_from_template)."""
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        data = org.to_dict()
        keyed = {n["id"]: n for n in data["nodes"]}
        assert keyed["wb-tongyi-image"]["plugin_origin"]["plugin_id"] == "tongyi-image"
        assert keyed["wb-seedance-video"]["plugin_origin"]["plugin_id"] == "seedance-video"


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

