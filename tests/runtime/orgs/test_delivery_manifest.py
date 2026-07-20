from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openakita.orgs._runtime_delivery_manifest import (
    DeliveryManifest,
    DeliveryManifestError,
    aggregate_completed_child_manifests,
    delivery_manifest_ledger,
    validate_manifest_runtime_evidence,
)
from openakita.orgs._runtime_node_tools import execute_node_tool, resolve_node_tools
from openakita.runtime.execution_context import ExecutionPhase, current_execution_phase_var


def test_org_submit_deliverable_is_available_to_every_node() -> None:
    tools = resolve_node_tools(external_tools=[], enable_file_tools=False)
    assert [tool["name"] for tool in tools] == ["org_submit_deliverable"]


@pytest.mark.asyncio
async def test_org_submit_deliverable_records_structured_manifest(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    async def emit(name: str, payload: dict) -> None:
        events.append((name, payload))

    delivery_manifest_ledger.clear()
    storyboard = tmp_path / "分镜.json"
    storyboard.write_text('{"segments": []}', encoding="utf-8")
    text, is_error = await execute_node_tool(
        tool_name="org_submit_deliverable",
        tool_input={
            "state": "complete",
            "final": False,
            "summary": "分镜已完成",
            "artifacts": [
                {
                    "kind": "storyboard",
                    "status": "ready",
                    "task_ids": ["story-1"],
                    "paths": [str(storyboard)],
                }
            ],
        },
        org_id="org",
        node_id="writer",
        command_id="cmd",
        emit=emit,
    )

    assert is_error is False
    assert json.loads(text)["ok"] is True
    manifest = delivery_manifest_ledger.latest("org", "cmd", "writer")
    assert manifest is not None
    assert manifest.state == "complete"
    assert manifest.artifacts[0].kind == "storyboard"
    assert any(name == "delivery_manifest_recorded" for name, _ in events)


def test_runtime_evidence_rejects_missing_declared_file(tmp_path: Path) -> None:
    manifest = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": False,
            "summary": "document ready",
            "artifacts": [
                {
                    "kind": "document",
                    "status": "ready",
                    "paths": [str(tmp_path / "missing.md")],
                }
            ],
        },
        org_id="org",
        command_id="cmd",
        node_id="writer",
    )

    failures = validate_manifest_runtime_evidence(manifest, artifact_records=())

    assert failures[0]["code"] == "delivery_file_missing"


def test_runtime_evidence_accepts_existing_declared_file(tmp_path: Path) -> None:
    document = tmp_path / "report.md"
    document.write_text("complete report", encoding="utf-8")
    manifest = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": False,
            "summary": "document ready",
            "artifacts": [
                {"kind": "document", "status": "ready", "paths": [str(document)]}
            ],
        },
        org_id="org",
        command_id="cmd",
        node_id="writer",
    )

    assert validate_manifest_runtime_evidence(manifest, artifact_records=()) == []


def test_complete_manifest_rejects_pending_artifact() -> None:
    with pytest.raises(DeliveryManifestError, match="complete manifest"):
        DeliveryManifest.from_mapping(
            {
                "state": "complete",
                "final": True,
                "summary": "not actually ready",
                "artifacts": [{"kind": "video", "status": "pending"}],
            },
            org_id="org",
            command_id="cmd",
            node_id="root",
        )


def test_manifest_artifact_role_is_runtime_owned() -> None:
    planning_token = current_execution_phase_var.set(ExecutionPhase.PLANNING)
    try:
        kickoff = DeliveryManifest.from_mapping(
            {"state": "in_progress", "final": False, "summary": "plan", "artifacts": []},
            org_id="org",
            command_id="cmd",
            node_id="root",
        )
        with pytest.raises(DeliveryManifestError, match="planning activation"):
            DeliveryManifest.from_mapping(
                {"state": "complete", "final": True, "summary": "done", "artifacts": []},
                org_id="org",
                command_id="cmd",
                node_id="root",
            )
    finally:
        current_execution_phase_var.reset(planning_token)

    finalization_token = current_execution_phase_var.set(ExecutionPhase.FINALIZATION)
    try:
        final = DeliveryManifest.from_mapping(
            {"state": "complete", "final": True, "summary": "done", "artifacts": []},
            org_id="org",
            command_id="cmd",
            node_id="root",
        )
    finally:
        current_execution_phase_var.reset(finalization_token)

    assert kickoff.artifact_role == "kickoff"
    assert final.artifact_role == "final"


def test_manifest_ledger_lists_only_current_run_child_deliveries() -> None:
    delivery_manifest_ledger.clear()
    old_child = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": False,
            "summary": "old",
            "artifacts": [{"kind": "document", "status": "ready"}],
        },
        org_id="org",
        command_id="cmd",
        node_id="old-child",
    )
    delivery_manifest_ledger.record(old_child)
    time.sleep(0.002)
    cutoff = time.time()
    parent = DeliveryManifest.from_mapping(
        {"state": "in_progress", "final": False, "summary": "routing", "artifacts": []},
        org_id="org",
        command_id="cmd",
        node_id="root",
    )
    child = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": False,
            "summary": "storyboard ready",
            "artifacts": [{"kind": "storyboard", "status": "ready"}],
        },
        org_id="org",
        command_id="cmd",
        node_id="writer",
    )
    delivery_manifest_ledger.record(parent)
    delivery_manifest_ledger.record(child)

    current = delivery_manifest_ledger.list_since(
        "org",
        "cmd",
        since=cutoff,
        exclude_node_id="root",
    )

    assert [manifest.node_id for manifest in current] == ["writer"]
    delivery_manifest_ledger.clear()


def test_completed_child_assignment_synthesizes_missing_coordinator_manifest() -> None:
    delivery_manifest_ledger.clear()
    child = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": False,
            "summary": "validated video ready",
            "artifacts": [
                {
                    "kind": "video",
                    "status": "ready",
                    "asset_ids": ["asset-video"],
                    "task_ids": ["task-video"],
                }
            ],
        },
        org_id="org",
        command_id="cmd",
        node_id="video",
        assignment_id="child-video",
        output_slot="shot-1",
    )
    delivery_manifest_ledger.record(child)

    promoted = aggregate_completed_child_manifests(
        org_id="org",
        command_id="cmd",
        node_id="director",
        assignment_id="parent-director",
        output_slot="final-video",
        children=(("video", "child-video"),),
    )

    assert promoted is not None
    assert promoted.state == "complete"
    assert promoted.final is False
    assert promoted.output_slot == "final-video"
    assert promoted.artifacts[0].asset_ids == ("asset-video",)
    assert "validated video ready" in promoted.summary
    delivery_manifest_ledger.clear()


def test_completed_child_assignments_promote_parent_and_preserve_multiple_outputs() -> None:
    delivery_manifest_ledger.clear()
    parent = DeliveryManifest.from_mapping(
        {
            "state": "in_progress",
            "final": False,
            "summary": "waiting for two requested variants",
            "artifacts": [{"kind": "video", "status": "pending"}],
        },
        org_id="org",
        command_id="cmd",
        node_id="director",
        assignment_id="parent-assignment",
    )
    delivery_manifest_ledger.record(parent)
    for slot in ("variant-1", "variant-2"):
        child = DeliveryManifest.from_mapping(
            {
                "state": "complete",
                "final": False,
                "summary": f"{slot} ready",
                "artifacts": [
                    {
                        "kind": "video",
                        "status": "ready",
                        "asset_ids": [f"asset-{slot}"],
                        "segment_id": "shot-1",
                    }
                ],
            },
            org_id="org",
            command_id="cmd",
            node_id="video",
            assignment_id=f"child-{slot}",
            output_slot=slot,
        )
        delivery_manifest_ledger.record(child)

    promoted = aggregate_completed_child_manifests(
        org_id="org",
        command_id="cmd",
        node_id="director",
        assignment_id="parent-assignment",
        children=(("video", "child-variant-1"), ("video", "child-variant-2")),
    )

    assert promoted is not None
    assert promoted.state == "complete"
    assert [artifact.asset_ids[0] for artifact in promoted.artifacts] == [
        "asset-variant-1",
        "asset-variant-2",
    ]
    assert len(delivery_manifest_ledger.list_since("org", "cmd")) == 3
    delivery_manifest_ledger.clear()
