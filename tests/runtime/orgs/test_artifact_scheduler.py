from __future__ import annotations

from openakita.orgs._runtime_artifact_flow import (
    CommandArtifactLedger,
    record_tool_result,
)
from openakita.orgs._runtime_artifact_scheduler import (
    ArtifactActivationLedger,
    ArtifactEdgeScheduler,
)
from openakita.runtime.supervisor import DelegationResult


def _record(
    ledger: CommandArtifactLedger,
    *,
    node: str,
    tool: str,
    result: dict,
    segment_id: str = "",
) -> None:
    record_tool_result(
        org_id="org",
        command_id="cmd",
        source_node_id=node,
        tool_name=tool,
        tool_input={"segment_id": segment_id},
        result=result,
        ledger=ledger,
    )


def _edge(**binding: object) -> dict:
    return {
        "id": "image-video",
        "source": "image",
        "target": "video",
        "edge_type": "artifact",
        "binding": {
            "target_tools": ["render_video"],
            "target_param": "source_ids",
            "value_field": "asset_ids",
            "join_key": "segment_id",
            "required": True,
            "cardinality": "one",
            **binding,
        },
    }


def test_manual_artifact_edge_never_activates_target() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        node="image",
        tool="render_image",
        result={"ok": True, "asset_id": "image-1"},
        segment_id="s1",
    )

    scheduler = ArtifactEdgeScheduler(
        org_id="org",
        command_id="cmd",
        edges=[_edge()],
        ledger=ledger,
        activation_ledger=ArtifactActivationLedger(),
    )

    assert scheduler.next_action() is None


def test_join_all_waits_for_declared_scope_then_claims_once() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        node="writer",
        tool="build_plan",
        result={
            "ok": True,
            "segments": [
                {"segment_id": "s1", "prompt": "first"},
                {"segment_id": "s2", "prompt": "second"},
            ],
        },
    )
    edge = _edge(
        activation="when_ready",
        dispatch_mode="join_all",
        join_scope={
            "source": "writer",
            "value_field": "segments",
            "key_field": "segment_id",
        },
    )
    scheduler = ArtifactEdgeScheduler(
        org_id="org",
        command_id="cmd",
        edges=[edge],
        ledger=ledger,
        activation_ledger=ArtifactActivationLedger(),
    )

    _record(
        ledger,
        node="image",
        tool="render_image",
        result={"ok": True, "asset_id": "image-1"},
        segment_id="s1",
    )
    assert scheduler.next_action() is None

    _record(
        ledger,
        node="image",
        tool="render_image",
        result={"ok": True, "asset_id": "image-2"},
        segment_id="s2",
    )
    action = scheduler.next_action()

    assert action is not None
    assert action.speaker == "video"
    assert action.metadata["join_keys"] == ["s1", "s2"]
    assert "image-1" in action.instruction
    assert '"prompt": "second"' in action.instruction
    assert scheduler.next_action() is None

    scheduler.record_result(
        action,
        DelegationResult(success=False, speaker="video", message="failed"),
    )
    assert scheduler.next_action() is None


def test_join_all_with_scope_does_not_activate_without_a_plan() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        node="image",
        tool="render_image",
        result={"ok": True, "asset_id": "poster"},
        segment_id="s1",
    )
    scheduler = ArtifactEdgeScheduler(
        org_id="org",
        command_id="cmd",
        edges=[
            _edge(
                activation="when_ready",
                dispatch_mode="join_all",
                join_scope={
                    "source": "writer",
                    "value_field": "segments",
                    "key_field": "segment_id",
                },
            )
        ],
        ledger=ledger,
        activation_ledger=ArtifactActivationLedger(),
    )

    assert scheduler.next_action() is None


def test_per_join_key_skips_segments_already_produced_by_target() -> None:
    ledger = CommandArtifactLedger()
    for segment_id in ("s1", "s2"):
        _record(
            ledger,
            node="image",
            tool="render_image",
            result={"ok": True, "asset_id": f"image-{segment_id}"},
            segment_id=segment_id,
        )
    _record(
        ledger,
        node="video",
        tool="render_video",
        result={"ok": True, "task_id": "video-s1"},
        segment_id="s1",
    )
    scheduler = ArtifactEdgeScheduler(
        org_id="org",
        command_id="cmd",
        edges=[_edge(activation="when_ready", dispatch_mode="per_join_key")],
        ledger=ledger,
        activation_ledger=ArtifactActivationLedger(),
    )

    action = scheduler.next_action()

    assert action is not None
    assert action.action_id == "image-video:s2"
