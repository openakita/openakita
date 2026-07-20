from __future__ import annotations

import copy

import pytest

from openakita.orgs._runtime_agent_pipeline_executor import _direct_dispatch_children
from openakita.orgs._runtime_templates import (
    AIGC_VIDEO_STUDIO,
    _upgrade_aigc_artifact_edges,
    _upgrade_aigc_runtime_budget,
)
from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import EdgeType, OrgEdge
from openakita.runtime.models import EdgeKind, EdgeV2
from openakita.runtime.templates.schema import (
    EdgeSpec,
    TemplateValidationError,
)


def _binding() -> dict:
    return {
        "target_tools": ["hh_i2v"],
        "target_param": "from_asset_ids",
        "value_field": "asset_ids",
        "accepts": ["image"],
        "join_key": "segment_id",
        "required": True,
        "cardinality": "one",
    }


def test_org_edge_artifact_binding_round_trip() -> None:
    edge = OrgEdge(
        id="edge",
        source="image",
        target="video",
        edge_type=EdgeType.ARTIFACT,
        binding=_binding(),
    )

    restored = OrgEdge.from_dict(edge.to_dict())

    assert restored.edge_type == EdgeType.ARTIFACT
    assert restored.binding == _binding()


def test_edge_v2_artifact_binding_round_trip() -> None:
    edge = EdgeV2(
        id="edge",
        org_id="org",
        src="image",
        dst="video",
        kind=EdgeKind.ARTIFACT,
        binding=_binding(),
    )

    assert EdgeV2.from_jsonable(edge.to_jsonable()) == edge


def test_edge_spec_rejects_malformed_artifact_binding() -> None:
    edge = EdgeSpec(
        src="image",
        dst="video",
        kind=EdgeKind.ARTIFACT,
        binding={"target_tools": ["hh_i2v"]},
    )

    with pytest.raises(TemplateValidationError, match="target_param"):
        edge.validate(valid_node_ids=frozenset({"image", "video"}))


def test_manager_rejects_dangling_artifact_edge(tmp_path) -> None:
    manager = OrgManager(tmp_path)
    with pytest.raises(ValueError, match="unknown node"):
        manager.create(
            {
                "name": "artifact validation",
                "nodes": [{"id": "image", "role_title": "Image"}],
                "edges": [
                    {
                        "source": "image",
                        "target": "missing",
                        "edge_type": "artifact",
                        "binding": _binding(),
                    }
                ],
            }
        )


def test_manager_projection_preserves_artifact_binding(tmp_path) -> None:
    manager = OrgManager(tmp_path)
    org = manager.create(
        {
            "name": "artifact projection",
            "nodes": [
                {"id": "image", "role_title": "Image"},
                {"id": "video", "role_title": "Video"},
            ],
            "edges": [
                {
                    "id": "edge",
                    "source": "image",
                    "target": "video",
                    "edge_type": "artifact",
                    "binding": _binding(),
                }
            ],
        }
    )

    projected = manager.as_orgv2(org.id)
    assert projected.edges[0].kind == EdgeKind.ARTIFACT
    assert projected.edges[0].binding == _binding()


def test_manager_persists_per_org_supervisor_budget(tmp_path) -> None:
    manager = OrgManager(tmp_path)
    expected = {
        "supervisor_hard_ceiling_s": 1800,
        "supervisor_soft_ceiling_ratio": 0.8,
        "supervisor_soft_watchdog_grace_ratio": 0.5,
    }
    org = manager.create(
        {
            "name": "budgeted org",
            "nodes": [{"id": "root", "role_title": "Root"}],
            "runtime_overrides": expected,
        }
    )

    assert manager.get_org(org.id).runtime_overrides == expected


def test_legacy_aigc_template_uses_artifact_edges_for_asset_handoffs() -> None:
    edges = {edge["id"]: edge for edge in AIGC_VIDEO_STUDIO["edges"]}
    producer = next(node for node in AIGC_VIDEO_STUDIO["nodes"] if node["id"] == "producer")

    assert edges["e-writer-art"]["edge_type"] == "collaborate"
    for edge_id in (
        "e-writer-long",
        "e-image-video",
        "e-image-human",
        "e-video-long",
        "e-human-long",
    ):
        assert edges[edge_id]["edge_type"] == "artifact"
        assert edges[edge_id]["binding"]["target_tools"]
    assert edges["e-image-video"]["binding"]["activation"] == "when_ready"
    assert edges["e-video-long"]["binding"]["dispatch_mode"] == "join_all"
    assert "step_id=storyboard" in producer["custom_prompt"]
    assert "depends_on=[storyboard]" in producer["custom_prompt"]
    assert AIGC_VIDEO_STUDIO["runtime_overrides"] == {
        "supervisor_hard_ceiling_s": 1800,
        "supervisor_soft_ceiling_ratio": 0.8,
        "supervisor_soft_watchdog_grace_ratio": 0.5,
    }


def test_artifact_edge_does_not_grant_delegation_authority() -> None:
    org = type(
        "Org",
        (),
        {
            "edges": [
                OrgEdge(source="parent", target="child", edge_type=EdgeType.HIERARCHY),
                OrgEdge(source="parent", target="asset_peer", edge_type=EdgeType.ARTIFACT),
            ]
        },
    )()

    assert _direct_dispatch_children(org, "parent") == {"child"}


def test_existing_happyhorse_template_upgrades_only_asset_handoffs() -> None:
    template = copy.deepcopy(AIGC_VIDEO_STUDIO)
    artifact_ids = {edge["id"] for edge in template["edges"] if edge["edge_type"] == "artifact"}
    for edge in template["edges"]:
        if edge["id"] in artifact_ids:
            edge["edge_type"] = "collaborate"
            edge.pop("binding", None)

    assert _upgrade_aigc_artifact_edges(template) is True
    upgraded = {edge["id"]: edge for edge in template["edges"]}
    assert all(upgraded[edge_id]["edge_type"] == "artifact" for edge_id in artifact_ids)
    assert upgraded["e-writer-art"]["edge_type"] == "collaborate"
    assert _upgrade_aigc_artifact_edges(template) is False


def test_existing_artifact_edges_gain_activation_without_replacing_custom_binding() -> None:
    template = copy.deepcopy(AIGC_VIDEO_STUDIO)
    edges = {edge["id"]: edge for edge in template["edges"]}
    image_video = edges["e-image-video"]["binding"]
    image_video["selection"] = "user-choice"
    for edge in edges.values():
        binding = edge.get("binding", {})
        binding.pop("activation", None)
        binding.pop("dispatch_mode", None)
        binding.pop("join_scope", None)
        binding.pop("max_attempts", None)

    assert _upgrade_aigc_artifact_edges(template) is True
    assert image_video["activation"] == "when_ready"
    assert image_video["dispatch_mode"] == "join_all"
    assert image_video["selection"] == "user-choice"


def test_manager_upgrades_asset_edges_on_existing_aigc_org_load(tmp_path) -> None:
    legacy = copy.deepcopy(AIGC_VIDEO_STUDIO)
    legacy["name"] = "existing AIGC org"
    artifact_ids = {edge["id"] for edge in legacy["edges"] if edge["edge_type"] == "artifact"}
    for edge in legacy["edges"]:
        if edge["id"] in artifact_ids:
            edge["edge_type"] = "collaborate"
            edge.pop("binding", None)

    created = OrgManager(tmp_path).create(legacy)
    loaded = OrgManager(tmp_path).get(created.id)

    assert loaded is not None
    loaded_edges = {edge.id: edge for edge in loaded.edges}
    assert all(loaded_edges[edge_id].edge_type == EdgeType.ARTIFACT for edge_id in artifact_ids)
    persisted = OrgManager(tmp_path).get_org(created.id)
    assert persisted is not None
    assert all(
        {edge.id: edge for edge in persisted.edges}[edge_id].binding for edge_id in artifact_ids
    )


def test_existing_happyhorse_template_adds_missing_runtime_budget_without_overwrite() -> None:
    template = copy.deepcopy(AIGC_VIDEO_STUDIO)
    template.pop("runtime_overrides")

    assert _upgrade_aigc_runtime_budget(template) is True
    assert template["runtime_overrides"]["supervisor_hard_ceiling_s"] == 1800

    template["runtime_overrides"]["supervisor_hard_ceiling_s"] = 2400
    assert _upgrade_aigc_runtime_budget(template) is False
    assert template["runtime_overrides"]["supervisor_hard_ceiling_s"] == 2400
