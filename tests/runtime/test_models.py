"""Tests for ``openakita.runtime.models``.

Phase 1 / commit 1 (foundation). Asserts:
* dataclass round-trip through JSON-able dicts is lossless;
* enum vocabulary matches ADR-0002 / ADR-0007 expectations;
* deprecated legacy keys (``max_task_seconds``) are dropped on load,
  not silently accepted, fulfilling ADR-0010's promise that
  deprecated knobs do not re-enter v2 through the data path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from openakita.runtime.models import (
    DefaultsSpec,
    EdgeKind,
    EdgeV2,
    NodeRuntimeOverrides,
    NodeStatus,
    NodeType,
    NodeV2,
    OrgStatus,
    OrgV2,
    TaskLifecycleState,
    WorkbenchBinding,
    new_command_id,
    new_edge_id,
    new_node_id,
    new_org_id,
)

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------


def test_id_minting_prefixes() -> None:
    assert new_org_id().startswith("org_")
    assert new_node_id().startswith("node_")
    assert new_edge_id().startswith("edge_")
    assert new_command_id().startswith("cmd_")
    # uniqueness across rapid calls
    assert new_org_id() != new_org_id()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_node_status_includes_suspect() -> None:
    """SUSPECT is the new middle state from ADR-0007 / ADR-0004."""
    assert NodeStatus.SUSPECT.value == "suspect"
    # legacy WAITING / FROZEN are gone in v2
    assert "waiting" not in [s.value for s in NodeStatus]
    assert "frozen" not in [s.value for s in NodeStatus]


def test_org_status_drops_legacy_dormant_archived() -> None:
    """Dormant/archived are replaced by explicit pause + ADR-0010 archives."""
    values = {s.value for s in OrgStatus}
    assert values == {"created", "active", "running", "paused", "stopped"}


def test_task_lifecycle_state_covers_supervisor_path() -> None:
    """Supervisor ADR-0004 references all of these labels."""
    expected = {
        "received",
        "planning",
        "waiting_deps",
        "executing",
        "stalled",
        "replanning",
        "verifying",
        "done",
        "failed",
        "cancelled",
    }
    assert {s.value for s in TaskLifecycleState} == expected


def test_node_type_matches_adr_0007() -> None:
    assert {t.value for t in NodeType} == {
        "llm",
        "workbench",
        "tool",
        "condition",
        "human_review",
    }


def test_edge_kind_unchanged_from_legacy() -> None:
    assert {k.value for k in EdgeKind} == {
        "hierarchy",
        "collaborate",
        "escalate",
        "consult",
    }


# ---------------------------------------------------------------------------
# WorkbenchBinding round-trip
# ---------------------------------------------------------------------------


def test_workbench_binding_round_trip_with_capabilities() -> None:
    wb = WorkbenchBinding(
        plugin_id="happyhorse-video",
        mode="art_director",
        capabilities=("storyboard", "review"),
    )
    payload = wb.to_jsonable()
    assert payload == {
        "plugin_id": "happyhorse-video",
        "mode": "art_director",
        "capabilities": ["storyboard", "review"],
    }
    rebuilt = WorkbenchBinding.from_jsonable(payload)
    assert rebuilt == wb


def test_workbench_binding_round_trip_default_capabilities() -> None:
    wb = WorkbenchBinding(plugin_id="happyhorse-video", mode="image_artist")
    rebuilt = WorkbenchBinding.from_jsonable(wb.to_jsonable())
    assert rebuilt.capabilities is None


# ---------------------------------------------------------------------------
# NodeRuntimeOverrides — deprecated keys are dropped on load
# ---------------------------------------------------------------------------


def test_runtime_overrides_drops_unknown_legacy_keys() -> None:
    legacy = {
        "max_iterations": 12,
        "max_task_seconds": 300,  # deprecated; ADR-0010 says drop on import
        "command_stuck_warn_secs": 900,  # deprecated
        "max_turns": 25,
        "max_stalls": 4,
        "suspect_secs": 90,
    }
    overrides = NodeRuntimeOverrides.from_jsonable(legacy)
    assert overrides.max_iterations == 12
    assert overrides.max_turns == 25
    assert overrides.max_stalls == 4
    # round-trip out: deprecated keys never re-appear
    out = overrides.to_jsonable()
    assert "max_task_seconds" not in out
    assert "command_stuck_warn_secs" not in out


def test_runtime_overrides_empty_input() -> None:
    assert NodeRuntimeOverrides.from_jsonable(None) == NodeRuntimeOverrides()
    assert NodeRuntimeOverrides.from_jsonable({}) == NodeRuntimeOverrides()


# ---------------------------------------------------------------------------
# DefaultsSpec
# ---------------------------------------------------------------------------


def test_defaults_spec_default_channels_match_adr_0006() -> None:
    """ADR-0006 defines the default channel set; track drift here."""
    d = DefaultsSpec()
    assert d.max_turns == 30
    assert d.max_stalls == 3
    assert d.suspect_secs == 90
    expected_channels = {
        "values",
        "updates",
        "tasks",
        "checkpoints",
        "messages",
        "progress_ledger",
        "lifecycle",
    }
    assert set(d.stream_channels) == expected_channels


def test_defaults_spec_round_trip() -> None:
    d = DefaultsSpec(max_turns=42, max_stalls=5, suspect_secs=120,
                     stream_channels=("values", "updates"))
    rebuilt = DefaultsSpec.from_jsonable(d.to_jsonable())
    assert rebuilt == d


# ---------------------------------------------------------------------------
# Full org round-trip
# ---------------------------------------------------------------------------


def _sample_org() -> OrgV2:
    org_id = "org_aigc_demo_x1"
    director = NodeV2(
        id="node_director",
        org_id=org_id,
        type=NodeType.WORKBENCH,
        role="art_director",
        label="Art Director",
        persona_prompt=None,
        tool_subset=("hh_storyboard_decompose", "hh_review", "org_delegate_task"),
        workbench=WorkbenchBinding(
            plugin_id="happyhorse-video",
            mode="art_director",
        ),
        runtime_overrides=NodeRuntimeOverrides(max_iterations=8),
    )
    image_artist = NodeV2(
        id="node_image",
        org_id=org_id,
        type=NodeType.WORKBENCH,
        role="image_artist",
        label="Image Artist",
        workbench=WorkbenchBinding(plugin_id="happyhorse-video", mode="image_artist"),
        parent_id=director.id,
    )
    edge = EdgeV2(
        id="edge_director_image",
        org_id=org_id,
        src=director.id,
        dst=image_artist.id,
        kind=EdgeKind.HIERARCHY,
    )
    return OrgV2(
        id=org_id,
        name="AIGC Video Studio Demo",
        template_id="aigc_video_studio",
        description="Used for round-trip tests.",
        nodes=[director, image_artist],
        edges=[edge],
        defaults=DefaultsSpec(),
        status=OrgStatus.ACTIVE,
        created_at=datetime(2026, 5, 18, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 18, 10, 0, tzinfo=UTC),
    )


def test_org_round_trip_through_json_string() -> None:
    """Full org dataclass tree must survive `json.dumps`/`json.loads`."""
    org = _sample_org()
    encoded = json.dumps(org.to_jsonable())
    decoded = json.loads(encoded)
    rebuilt = OrgV2.from_jsonable(decoded)
    assert rebuilt.to_jsonable() == org.to_jsonable()


def test_org_accessors() -> None:
    org = _sample_org()
    assert org.get_node("node_director") is not None
    assert org.get_node("node_missing") is None
    roots = org.root_nodes()
    assert [n.id for n in roots] == ["node_director"]
    children = org.children_of("node_director")
    assert [n.id for n in children] == ["node_image"]


def test_org_immutable_status_machine_via_assignment() -> None:
    """OrgV2 is mutable; status changes via direct assignment.

    This is by design (ADR-0002 separates spec types from live types).
    The supervisor is the only writer in normal flow.
    """
    org = _sample_org()
    org.status = OrgStatus.RUNNING
    assert org.status == OrgStatus.RUNNING


# ---------------------------------------------------------------------------
# Failure / defensive paths
# ---------------------------------------------------------------------------


def test_node_v2_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        NodeV2.from_jsonable(
            {
                "id": "node_x",
                "org_id": "org_y",
                "type": "totally_made_up_type",
                "role": "r",
                "label": "L",
                "runtime_overrides": {},
                "status": NodeStatus.IDLE.value,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )


def test_edge_kind_defaults_when_absent() -> None:
    edge = EdgeV2.from_jsonable(
        {"id": "edge_x", "org_id": "org_y", "src": "a", "dst": "b"}
    )
    assert edge.kind == EdgeKind.HIERARCHY
