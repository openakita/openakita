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

    Starting with v1.1.0 the default template splits a single
    ``happyhorse-video`` plugin into four per-category workbench leaves
    (image / video / digital-human / long-video post) plus three
    coordination roles (producer / screenwriter / art-director). The
    runtime / manager refuses to run if a workbench node has hierarchy
    children, so the template's structural shape must hold even before
    any plugin is loaded.
    """

    HAPPYHORSE_WORKBENCH_NODES: dict[str, set[str]] = {
        "wb-hh-image": {
            "hh_image_create",
            "hh_image_edit",
            "hh_image_style_repaint",
            "hh_image_background",
            "hh_image_outpaint",
            "hh_image_sketch",
            "hh_image_ecommerce",
            "hh_status",
            "hh_cost_preview",
        },
        "wb-hh-video": {
            "hh_t2v",
            "hh_i2v",
            "hh_r2v",
            "hh_video_edit",
            "hh_status",
            "hh_cost_preview",
        },
        "wb-hh-human": {
            "hh_photo_speak",
            "hh_video_relip",
            "hh_video_reface",
            "hh_pose_drive",
            "hh_avatar_compose",
            "hh_status",
            "hh_cost_preview",
        },
        "wb-hh-long": {
            "hh_long_video_create",
            "hh_video_concat",
            "hh_status",
            "hh_list",
            "hh_cost_preview",
        },
    }

    def test_workbench_nodes_carry_plugin_origin(self):
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        wb_nodes = [n for n in org.nodes if n.plugin_origin]
        plugin_ids = {n.plugin_origin["plugin_id"] for n in wb_nodes}
        assert plugin_ids == {"happyhorse-video"}
        assert len(wb_nodes) == len(self.HAPPYHORSE_WORKBENCH_NODES)
        for n in wb_nodes:
            assert n.plugin_origin.get("template_id", "") == "workbench:happyhorse-video"
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
        """Each workbench leaf must whitelist exactly the ``hh_*`` tool
        names ``happyhorse-video`` registers for that category."""
        tpl = AIGC_VIDEO_STUDIO
        keyed = {node["id"]: node for node in tpl["nodes"]}
        for node_id, expected_tools in self.HAPPYHORSE_WORKBENCH_NODES.items():
            assert node_id in keyed, f"missing workbench node: {node_id}"
            node = keyed[node_id]
            assert node["plugin_origin"]["plugin_id"] == "happyhorse-video"
            assert set(node["external_tools"]) == expected_tools, (
                f"node {node_id} external_tools drift: "
                f"expected={sorted(expected_tools)} "
                f"actual={sorted(node['external_tools'])}"
            )

    def test_screenwriter_can_decompose_storyboard(self):
        """The screenwriter role must be able to call
        ``hh_storyboard_decompose`` so it can produce the segments JSON
        consumed by the long-video workbench."""
        tpl = AIGC_VIDEO_STUDIO
        keyed = {node["id"]: node for node in tpl["nodes"]}
        screenwriter = keyed["screenwriter"]
        assert "hh_storyboard_decompose" in screenwriter["external_tools"]

    def test_art_director_owns_all_workbench_nodes(self):
        """The art-director must be the *single* hierarchy parent of all
        four happyhorse workbench leaves.

        ``OrgGraph.get_parent`` returns the first matching hierarchy edge
        and ``org_delegate_task`` only accepts targets in
        ``get_children``. If we let producer also keep hierarchy edges to
        the workbenches (double-parent), ``org_submit_deliverable``'s
        ``get_parent`` fallback would silently route deliverables to
        producer instead of the art-director that actually delegated the
        task — so make the single-parent invariant explicit.
        """
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        wb_ids = set(self.HAPPYHORSE_WORKBENCH_NODES.keys())
        art_children = {c.id for c in org.get_children("art-director")}
        assert wb_ids.issubset(art_children), (
            f"art-director hierarchy children missing workbench nodes: "
            f"expected={sorted(wb_ids)} actual={sorted(art_children)}"
        )
        for wb_id in wb_ids:
            parent = org.get_parent(wb_id)
            assert parent is not None, f"workbench {wb_id} has no hierarchy parent"
            assert parent.id == "art-director", (
                f"workbench {wb_id} parent must be art-director, "
                f"got {parent.id}"
            )

    def test_template_round_trips_plugin_origin(self):
        """Plugin origin must survive ``from_dict``/``to_dict`` (used
        during save/load and create_from_template)."""
        org = Organization.from_dict(AIGC_VIDEO_STUDIO)
        data = org.to_dict()
        keyed = {n["id"]: n for n in data["nodes"]}
        for node_id in self.HAPPYHORSE_WORKBENCH_NODES:
            assert keyed[node_id]["plugin_origin"]["plugin_id"] == "happyhorse-video"


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

