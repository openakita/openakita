from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs._runtime_artifact_flow import (
    ArtifactBindingError,
    CommandArtifactLedger,
    bind_tool_input,
    record_tool_result,
    structured_upstream_records,
)
from openakita.orgs._runtime_delivery_manifest import (
    DeliveryManifest,
    validate_manifest_media_delivery,
)
from openakita.orgs._runtime_node_tools import execute_node_tool


def _edge(source: str, target: str, **binding: object) -> dict:
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        "edge_type": "artifact",
        "binding": binding,
    }


def _record(
    ledger: CommandArtifactLedger,
    *,
    command: str,
    node: str,
    tool: str,
    result: dict,
    segment_id: str = "",
) -> None:
    record_tool_result(
        org_id="org",
        command_id=command,
        source_node_id=node,
        tool_name=tool,
        tool_input={"segment_id": segment_id},
        result=result,
        ledger=ledger,
    )


def test_binds_image_asset_by_segment_id() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        command="cmd",
        node="image",
        tool="hh_image_create",
        result={"ok": True, "asset_ids": ["asset-a"]},
        segment_id="shot-1",
    )
    _record(
        ledger,
        command="cmd",
        node="image",
        tool="hh_image_create",
        result={"ok": True, "asset_ids": ["asset-b"]},
        segment_id="shot-2",
    )
    edge = _edge(
        "image",
        "video",
        target_tools=["hh_i2v"],
        target_param="from_asset_ids",
        value_field="asset_ids",
        accepts=["image"],
        join_key="segment_id",
        required=True,
        cardinality="one",
    )

    bound, applied = bind_tool_input(
        org_id="org",
        command_id="cmd",
        target_node_id="video",
        tool_name="hh_i2v",
        tool_input={"segment_id": "shot-2", "prompt": "move"},
        edges=[edge],
        ledger=ledger,
    )

    assert bound["from_asset_ids"] == ["asset-b"]
    assert applied[0]["value_count"] == 1


def test_required_one_binding_rejects_ambiguous_records() -> None:
    ledger = CommandArtifactLedger()
    for asset_id in ("asset-a", "asset-b"):
        _record(
            ledger,
            command="cmd",
            node="image",
            tool="hh_image_create",
            result={"ok": True, "asset_ids": [asset_id]},
        )
    edge = _edge(
        "image",
        "video",
        target_tools=["hh_i2v"],
        target_param="from_asset_ids",
        value_field="asset_ids",
        required=True,
        cardinality="one",
        join_key="segment_id",
    )

    with pytest.raises(ArtifactBindingError, match="多个上游产物") as exc_info:
        bind_tool_input(
            org_id="org",
            command_id="cmd",
            target_node_id="video",
            tool_name="hh_i2v",
            tool_input={},
            edges=[edge],
            ledger=ledger,
        )
    assert exc_info.value.reason == "artifact_binding_ambiguous"


def test_required_binding_is_isolated_by_command() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        command="old-command",
        node="image",
        tool="hh_image_create",
        result={"ok": True, "asset_ids": ["stale"]},
        segment_id="shot-1",
    )
    edge = _edge(
        "image",
        "video",
        target_tools=["hh_i2v"],
        target_param="from_asset_ids",
        value_field="asset_ids",
        required=True,
        cardinality="one",
        join_key="segment_id",
    )

    with pytest.raises(ArtifactBindingError) as exc_info:
        bind_tool_input(
            org_id="org",
            command_id="new-command",
            target_node_id="video",
            tool_name="hh_i2v",
            tool_input={"segment_id": "shot-1"},
            edges=[edge],
            ledger=ledger,
        )
    assert exc_info.value.reason == "artifact_binding_missing"


def test_many_binding_merges_multiple_sources_and_explicit_values() -> None:
    ledger = CommandArtifactLedger()
    _record(
        ledger,
        command="cmd",
        node="video",
        tool="hh_i2v",
        result={"ok": True, "task_id": "video-task", "asset_ids": ["video"]},
    )
    _record(
        ledger,
        command="cmd",
        node="human",
        tool="hh_photo_speak",
        result={"ok": True, "task_id": "human-task", "asset_ids": ["human"]},
    )
    edges = [
        _edge(
            "video",
            "long",
            target_tools=["hh_video_concat"],
            target_param="task_ids",
            value_field="task_ids",
            accepts=["video"],
            required=True,
            cardinality="many",
        ),
        _edge(
            "human",
            "long",
            target_tools=["hh_video_concat"],
            target_param="task_ids",
            value_field="task_ids",
            accepts=["video"],
            required=False,
            cardinality="many",
        ),
    ]

    bound, _ = bind_tool_input(
        org_id="org",
        command_id="cmd",
        target_node_id="long",
        tool_name="hh_video_concat",
        tool_input={"task_ids": ["external-task"]},
        edges=edges,
        ledger=ledger,
    )

    assert bound["task_ids"] == ["external-task", "video-task", "human-task"]


def test_records_storyboard_segments_for_downstream_binding() -> None:
    ledger = CommandArtifactLedger()
    segments = [{"segment_id": "shot-1", "prompt": "opening"}]
    record = record_tool_result(
        org_id="org",
        command_id="cmd",
        source_node_id="writer",
        tool_name="hh_storyboard_decompose",
        tool_input={},
        result={"ok": True, "segments": segments},
        ledger=ledger,
    )

    assert record is not None
    assert list(record.segments) == segments

    context = structured_upstream_records(
        org_id="org",
        command_id="cmd",
        source_node_ids=("writer",),
        ledger=ledger,
    )
    assert context["version"] == 1
    assert context["records"][0]["segments"] == segments
    assert context["records"][0]["source_node_id"] == "writer"


def test_video_result_is_materialized_and_validated_as_command_delivery(
    tmp_path: Path,
) -> None:
    ledger = CommandArtifactLedger()
    source = tmp_path / "plugin" / "毛绒玩具跳舞.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video-bytes")
    delivery_dir = tmp_path / "command" / "artifacts" / "deliverables" / "plugin_assets"

    record = record_tool_result(
        org_id="org",
        command_id="cmd",
        source_node_id="video",
        tool_name="hh_t2v",
        tool_input={"segment_id": "shot-1"},
        result={
            "ok": True,
            "task_id": "task-1",
            "asset_ids": ["asset-1"],
            "video_path": str(source),
            "media_validation": {"passed": True},
        },
        ledger=ledger,
        delivery_dir=delivery_dir,
    )

    assert record is not None
    assert record.media_validation_passed is True
    assert len(record.registered_paths) == 1
    assert record.registered_video_paths == record.registered_paths
    registered = Path(record.registered_paths[0])
    assert registered.is_file()
    assert registered.name == "毛绒玩具跳舞.mp4"
    assert registered.read_bytes() == b"video-bytes"
    manifest = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": True,
            "summary": "ready",
            "artifacts": [
                {
                    "kind": "video",
                    "status": "ready",
                    "asset_ids": ["asset-1"],
                    "task_ids": ["task-1"],
                }
            ],
        },
        org_id="org",
        command_id="cmd",
        node_id="producer",
    )
    assert (
        validate_manifest_media_delivery(
            manifest,
            artifact_records=ledger.get("org", "cmd"),
        )
        == []
    )


def test_final_video_claim_requires_registered_validated_file(tmp_path: Path) -> None:
    ledger = CommandArtifactLedger()
    source = tmp_path / "last-frame.png"
    source.write_bytes(b"image-bytes")
    record_tool_result(
        org_id="org",
        command_id="cmd",
        source_node_id="video",
        tool_name="hh_t2v",
        tool_input={},
        result={
            "ok": True,
            "task_id": "task-1",
            "asset_ids": ["asset-1"],
            "last_frame_path": str(source),
            "media_validation": {"passed": True},
        },
        ledger=ledger,
        delivery_dir=tmp_path / "deliveries",
    )

    manifest = DeliveryManifest.from_mapping(
        {
            "state": "complete",
            "final": True,
            "summary": "ready",
            "artifacts": [
                {
                    "kind": "video",
                    "status": "ready",
                    "asset_ids": ["asset-1"],
                    "task_ids": ["task-1"],
                }
            ],
        },
        org_id="org",
        command_id="cmd",
        node_id="producer",
    )
    failures = validate_manifest_media_delivery(
        manifest,
        artifact_records=ledger.get("org", "cmd"),
    )

    assert failures[0]["code"] == "media_delivery_validation_missing"


def test_delivery_paths_in_prose_do_not_trigger_media_validation() -> None:
    assert validate_manifest_media_delivery(None, artifact_records=()) == []


@pytest.mark.asyncio
async def test_execute_node_tool_injects_binding_and_emits_lineage_events() -> None:
    from openakita.orgs._runtime_artifact_flow import (
        artifact_ledger,
        current_artifact_edges_var,
    )

    artifact_ledger.clear()
    record_tool_result(
        org_id="org",
        command_id="cmd",
        source_node_id="image",
        tool_name="hh_image_create",
        tool_input={"segment_id": "shot-1"},
        result={"ok": True, "asset_ids": ["asset-1"]},
    )
    edge = _edge(
        "image",
        "video",
        target_tools=["hh_i2v"],
        target_param="from_asset_ids",
        value_field="asset_ids",
        accepts=["image"],
        join_key="segment_id",
        required=True,
        cardinality="one",
    )
    calls: list[dict] = []
    events: list[str] = []

    class _Host:
        async def execute_tool(self, tool_name, tool_input, *, node_id, command_id):
            calls.append(tool_input)
            return '{"ok": true, "task_id": "video-task", "asset_ids": ["video-1"]}'

    async def _emit(event_type: str, _payload: dict) -> None:
        events.append(event_type)

    token = current_artifact_edges_var.set((edge,))
    try:
        text, is_error = await execute_node_tool(
            tool_name="hh_i2v",
            tool_input={"segment_id": "shot-1", "prompt": "move"},
            org_id="org",
            node_id="video",
            command_id="cmd",
            emit=_emit,
            tool_host=_Host(),
        )
    finally:
        current_artifact_edges_var.reset(token)
        artifact_ledger.clear()

    assert is_error is False
    assert "video-task" in text
    assert calls[0]["from_asset_ids"] == ["asset-1"]
    assert "artifact_binding_applied" in events
    assert "artifact_recorded" in events


@pytest.mark.asyncio
async def test_execute_node_tool_registers_verified_plugin_video_attachment(
    tmp_path: Path,
) -> None:
    from openakita.orgs._runtime_artifact_flow import (
        artifact_ledger,
        current_artifact_delivery_dir_var,
    )

    source = tmp_path / "plugin-task" / "result.mp4"
    source.parent.mkdir()
    source.write_bytes(b"verified-video")
    events: list[tuple[str, dict]] = []

    class _Host:
        async def execute_tool(self, tool_name, tool_input, *, node_id, command_id):
            return json.dumps(
                {
                    "ok": True,
                    "task_id": "video-task",
                    "asset_ids": ["video-asset"],
                    "video_path": str(source),
                    "media_validation": {"passed": True},
                }
            )

    async def _emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    artifact_ledger.clear()
    delivery_dir = tmp_path / "command" / "artifacts" / "deliverables" / "plugin_assets"
    token = current_artifact_delivery_dir_var.set(delivery_dir)
    try:
        _, is_error = await execute_node_tool(
            tool_name="hh_t2v",
            tool_input={"segment_id": "shot-1", "prompt": "dance"},
            org_id="org",
            node_id="video",
            command_id="cmd",
            emit=_emit,
            tool_host=_Host(),
        )
    finally:
        current_artifact_delivery_dir_var.reset(token)
        artifact_ledger.clear()

    assert is_error is False
    registered = [payload for name, payload in events if name == "file_output_registered"]
    assert len(registered) == 1
    assert registered[0]["verified_plugin_asset"] is True
    assert Path(registered[0]["path"]).read_bytes() == b"verified-video"
