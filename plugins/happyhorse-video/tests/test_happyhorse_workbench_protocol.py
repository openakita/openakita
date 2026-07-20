"""Workbench-orchestration regression for happyhorse-video.

Mirrors ``plugins/seedance-video/tests/test_seedance_workbench_protocol.py``
but adapted for happyhorse-video's wider mode catalog (12 modes).

The two contracts that must remain stable across versions:

1. :meth:`Plugin._task_to_tool_payload` projects a task row into the
   canonical workbench JSON (``ok / task_id / status / mode / model_id /
   video_url / video_path / last_frame_url / last_frame_path /
   local_paths / asset_ids``). This is what
   :func:`OrgRuntime._record_plugin_asset_output` reads to register
   produced media as task attachments.
2. :meth:`Plugin._expand_from_asset_ids` turns an array of upstream
   Asset Bus ids into the right per-mode input field — this is how a
   workbench node consumes assets produced by an upstream image
   workbench (e.g. tongyi-image).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _plugin_loader import load_happyhorse_plugin

_HH = load_happyhorse_plugin()
HappyhorsePlugin = _HH.Plugin


def test_image_and_video_tool_schemas_expose_segment_id():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    definitions = {tool["name"]: tool for tool in plugin._tool_definitions()}

    for tool_name in ("hh_image_create", "hh_i2v", "hh_r2v"):
        properties = definitions[tool_name]["input_schema"]["properties"]
        assert properties["segment_id"]["type"] == "string"
        assert properties["client_request_id"]["type"] == "string"
        assert definitions[tool_name]["x-openakita-execution"]["kind"] == "external_task"


def test_org_readiness_reports_local_prerequisites(monkeypatch: pytest.MonkeyPatch):
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._client = SimpleNamespace(has_api_key=lambda: False)
    plugin._oss = SimpleNamespace(is_configured=lambda: False)
    monkeypatch.setattr(_HH, "ffmpeg_available", lambda: False)

    assert plugin.check_org_readiness() == {
        "ready": False,
        "missing_requirements": ["dashscope_api_key", "oss", "ffmpeg"],
    }


def test_org_readiness_passes_when_local_prerequisites_exist(
    monkeypatch: pytest.MonkeyPatch,
):
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._client = SimpleNamespace(has_api_key=lambda: True)
    plugin._oss = SimpleNamespace(is_configured=lambda: True)
    monkeypatch.setattr(_HH, "ffmpeg_available", lambda: True)

    assert plugin.check_org_readiness() == {
        "ready": True,
        "missing_requirements": [],
    }


# ── _task_to_tool_payload ─────────────────────────────────────────────


def test_task_payload_includes_workbench_fields():
    task = {
        "id": "hh_v1",
        "status": "succeeded",
        "mode": "i2v",
        "model_id": "happyhorse-1.0-i2v",
        "video_url": "https://example.com/v.mp4",
        "video_path": "/tmp/v.mp4",
        "last_frame_url": "https://example.com/lf.png",
        "last_frame_path": "/tmp/lf.png",
        "asset_ids": ["a1", "a2"],
        "prompt": "an autumn forest",
    }
    payload = HappyhorsePlugin._task_to_tool_payload(task)
    assert payload["ok"] is True
    assert payload["task_id"] == "hh_v1"
    assert payload["mode"] == "i2v"
    assert payload["model_id"] == "happyhorse-1.0-i2v"
    assert payload["video_url"] == "https://example.com/v.mp4"
    assert payload["video_path"] == "/tmp/v.mp4"
    assert payload["last_frame_url"] == "https://example.com/lf.png"
    assert payload["last_frame_path"] == "/tmp/lf.png"
    assert "/tmp/v.mp4" in payload["local_paths"]
    assert "/tmp/lf.png" in payload["local_paths"]
    assert payload["asset_ids"] == ["a1", "a2"]
    assert payload["asset_kinds"] == ["video", "image"]


def test_task_payload_failed_sets_ok_false_with_terminal():
    task = {
        "id": "hh_x",
        "status": "failed",
        "mode": "t2v",
        "error_kind": "quota",
        "error_message": "no balance",
        "asset_ids": [],
    }
    payload = HappyhorsePlugin._task_to_tool_payload(task)
    assert payload["ok"] is False
    assert payload["terminal"] is True
    assert payload["error_kind"] == "quota"
    assert payload["error_message"] == "no balance"


def test_task_payload_exposes_blocked_wait_contract():
    task = {
        "id": "hh_approval",
        "status": "pending",
        "mode": "i2v",
        "error_hints": {
            "wait_state": "blocked",
            "blocker": {
                "kind": "approval_required",
                "action": "approve_cost",
                "message": "预计费用超过阈值",
            },
        },
        "asset_ids": [],
    }

    payload = HappyhorsePlugin._task_to_tool_payload(task)

    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["wait_state"] == "blocked"
    assert payload["blocker"]["action"] == "approve_cost"


def test_task_payload_synthesizes_blocked_contract_from_legacy_approval_row():
    task = {
        "id": "hh_legacy_approval",
        "status": "pending",
        "mode": "i2v",
        "error_kind": "approval_required",
        "error_message": "Cost exceeds threshold; user confirmation required",
        "error_hints": None,
        "asset_ids": [],
    }

    payload = HappyhorsePlugin._task_to_tool_payload(task)

    assert payload["ok"] is False
    assert payload["wait_state"] == "blocked"
    assert payload["blocker"]["action"] == "approve_cost"
    assert payload["blocker"]["resume_patch"] == {"cost_approved": True}


@pytest.mark.asyncio
async def test_wait_for_task_returns_blocked_state_immediately():
    row = {
        "id": "hh_approval",
        "status": "pending",
        "error_hints": {
            "wait_state": "blocked",
            "blocker": {"kind": "approval_required", "action": "approve_cost"},
        },
    }
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._tm = SimpleNamespace(get_task=AsyncMock(return_value=row))

    result = await plugin._wait_for_task("hh_approval", interval=0.001)

    assert result["wait_state"] == "blocked"
    assert plugin._tm.get_task.await_count == 1


@pytest.mark.asyncio
async def test_wait_for_task_returns_legacy_approval_row_immediately():
    row = {
        "id": "hh_legacy_approval",
        "status": "pending",
        "error_kind": "approval_required",
        "error_message": "approval required",
        "error_hints": None,
    }
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._tm = SimpleNamespace(get_task=AsyncMock(return_value=row))

    result = await plugin._wait_for_task("hh_legacy_approval", interval=0.001)

    assert result["wait_state"] == "blocked"
    assert result["blocker"]["kind"] == "approval_required"
    assert plugin._tm.get_task.await_count == 1


@pytest.mark.asyncio
async def test_wait_for_task_keeps_polling_ordinary_pending_state():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._tm = SimpleNamespace(
        get_task=AsyncMock(
            side_effect=[
                {"id": "hh_active", "status": "pending", "error_hints": None},
                {"id": "hh_active", "status": "succeeded", "error_hints": None},
            ]
        )
    )

    result = await plugin._wait_for_task("hh_active", interval=0.001)

    assert result["status"] == "succeeded"
    assert result["wait_state"] == "terminal"
    assert plugin._tm.get_task.await_count == 2


@pytest.mark.asyncio
async def test_retry_resumes_blocked_task_with_same_task_id():
    row = {
        "id": "hh_approval",
        "status": "pending",
        "mode": "i2v",
        "model_id": "happyhorse-1.0-i2v",
        "prompt": "dance",
        "params": {"mode": "i2v", "prompt": "dance", "cost_approved": False},
        "error_hints": {
            "wait_state": "blocked",
            "blocker": {
                "kind": "approval_required",
                "action": "approve_cost",
                "resume_patch": {"cost_approved": True},
            },
        },
    }
    resumed = dict(row, status="pending", error_hints=None)
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._tm = SimpleNamespace(
        update_task_safe=AsyncMock(return_value=True),
        get_task=AsyncMock(return_value=resumed),
    )
    spawned: list[tuple[str, object, dict]] = []
    plugin._spawn_pipeline = lambda task_id, body, params: spawned.append((task_id, body, params))
    plugin._broadcast = lambda *_args, **_kwargs: None

    result = await plugin._resume_blocked_task("hh_approval", row)

    assert result is resumed
    assert spawned[0][0] == "hh_approval"
    assert spawned[0][1].cost_approved is True
    assert spawned[0][2]["cost_approved"] is True


def test_task_payload_exposes_reworkable_media_validation_failure():
    failure = {
        "passed": False,
        "code": "media_dimensions_mismatch",
        "message": "期望 1280x720，实际 960x960",
        "expected": {"aspect_ratio": "16:9", "width": 1280, "height": 720},
        "actual": {"width": 960, "height": 960},
    }
    task = {
        "id": "hh_bad_ratio",
        "status": "failed",
        "mode": "i2v",
        "error_kind": "media_validation_failed",
        "error_message": failure["message"],
        "error_hints": failure,
        "params": {
            "segment_id": "segment-1",
            "expected_media": failure["expected"],
        },
        "asset_ids": [],
    }

    payload = HappyhorsePlugin._task_to_tool_payload(task)

    assert payload["ok"] is False
    assert payload["reworkable"] is True
    assert payload["segment_id"] == "segment-1"
    assert payload["quality_failure"]["actual"] == {"width": 960, "height": 960}


def test_concat_requires_one_shared_target_pixel_spec():
    target = {"aspect_ratio": "16:9", "width": 1280, "height": 720}
    rows = [
        (1, 0, {"params": {"expected_media": target}}),
        (2, 1, {"params": {"expected_media": dict(target)}}),
    ]
    assert HappyhorsePlugin._shared_expected_media(rows) == target


def test_concat_rejects_mixed_target_pixel_specs():
    rows = [
        (1, 0, {"params": {"expected_media": {"width": 1280, "height": 720}}}),
        (2, 1, {"params": {"expected_media": {"width": 960, "height": 960}}}),
    ]
    with pytest.raises(_HH.MediaValidationError, match="不一致"):
        HappyhorsePlugin._shared_expected_media(rows)


def test_task_payload_json_round_trip():
    task = {
        "id": "hh_y",
        "status": "succeeded",
        "mode": "t2v",
        "model_id": "happyhorse-1.0-t2v",
        "video_url": "u",
        "video_path": "/p",
        "last_frame_url": "",
        "last_frame_path": "",
        "asset_ids": ["x"],
    }
    payload = HappyhorsePlugin._task_to_tool_payload(task)
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_task_payload_asset_ids_decoded_from_json_string():
    """asset_ids may arrive from sqlite as a JSON-encoded string."""
    task = {
        "id": "hh_z",
        "status": "succeeded",
        "mode": "t2v",
        "asset_ids": '["a", "b"]',
        "video_url": "u",
        "video_path": "",
    }
    payload = HappyhorsePlugin._task_to_tool_payload(task)
    assert payload["asset_ids"] == ["a", "b"]


def test_task_payload_succeeded_without_local_emits_download_warning():
    task = {
        "id": "hh_w",
        "status": "succeeded",
        "mode": "t2v",
        "video_url": "https://oss/x.mp4",
        "video_path": "",
        "last_frame_url": "",
        "last_frame_path": "",
        "asset_ids": [],
    }
    payload = HappyhorsePlugin._task_to_tool_payload(task)
    assert payload["ok"] is True
    assert "download_warning" in payload


# ── _expand_from_asset_ids ────────────────────────────────────────────


def _make_plugin(asset_lookup: dict[str, dict | None]):
    async def _consume(aid: str) -> dict | None:
        return asset_lookup.get(aid)

    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    plugin._api = SimpleNamespace(consume_asset=AsyncMock(side_effect=_consume))
    return plugin


@pytest.mark.asyncio
async def test_expand_i2v_assigns_first_then_reference():
    plugin = _make_plugin(
        {
            "a1": {"preview_url": "https://oss/x.png"},
            "a2": {"preview_url": "https://oss/y.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v")
    assert out == {
        "first_frame_url": "https://oss/x.png",
        "reference_urls": ["https://oss/y.png"],
    }


@pytest.mark.asyncio
async def test_expand_i2v_end_uses_first_and_last():
    plugin = _make_plugin(
        {
            "a1": {"preview_url": "https://oss/first.png"},
            "a2": {"preview_url": "https://oss/last.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v_end")
    assert out == {
        "first_frame_url": "https://oss/first.png",
        "last_frame_url": "https://oss/last.png",
    }


@pytest.mark.asyncio
async def test_expand_r2v_uses_reference_urls_for_all():
    plugin = _make_plugin(
        {
            "a1": {"preview_url": "https://oss/1.png"},
            "a2": {"preview_url": "https://oss/2.png"},
            "a3": {"preview_url": "https://oss/3.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["a1", "a2", "a3"], mode="r2v")
    assert out == {
        "reference_urls": [
            "https://oss/1.png",
            "https://oss/2.png",
            "https://oss/3.png",
        ],
    }


@pytest.mark.asyncio
async def test_expand_video_extend_uses_source_video_url():
    plugin = _make_plugin(
        {
            "v1": {"preview_url": "https://oss/v.mp4", "asset_kind": "video"},
        }
    )
    out = await plugin._expand_from_asset_ids(["v1"], mode="video_extend")
    assert out["source_video_url"] == "https://oss/v.mp4"


@pytest.mark.asyncio
async def test_expand_video_edit_carries_extra_references():
    plugin = _make_plugin(
        {
            "v1": {"preview_url": "https://oss/v.mp4"},
            "i1": {"preview_url": "https://oss/i.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["v1", "i1"], mode="video_edit")
    assert out["source_video_url"] == "https://oss/v.mp4"
    assert out["reference_urls"] == ["https://oss/i.png"]


@pytest.mark.asyncio
async def test_expand_photo_speak_uses_image_url_and_image_urls():
    plugin = _make_plugin(
        {
            "p1": {"preview_url": "https://oss/face.png"},
            "p2": {"preview_url": "https://oss/scene.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["p1", "p2"], mode="photo_speak")
    assert out == {
        "image_url": "https://oss/face.png",
        "image_urls": ["https://oss/scene.png"],
    }


@pytest.mark.asyncio
async def test_expand_avatar_compose_same_as_photo_speak():
    plugin = _make_plugin(
        {
            "p1": {"preview_url": "https://oss/1.png"},
            "p2": {"preview_url": "https://oss/2.png"},
            "p3": {"preview_url": "https://oss/3.png"},
        }
    )
    out = await plugin._expand_from_asset_ids(["p1", "p2", "p3"], mode="avatar_compose")
    assert out["image_url"] == "https://oss/1.png"
    assert out["image_urls"] == ["https://oss/2.png", "https://oss/3.png"]


@pytest.mark.asyncio
async def test_expand_skips_missing_assets():
    plugin = _make_plugin(
        {
            "a1": {"preview_url": "https://oss/x.png"},
            # a2: lookup returns None — must be skipped
            "a3": {},
        }
    )
    out = await plugin._expand_from_asset_ids(["a1", "a2", "a3"], mode="r2v")
    assert out == {"reference_urls": ["https://oss/x.png"]}


@pytest.mark.asyncio
async def test_expand_empty_returns_empty_dict():
    plugin = _make_plugin({})
    assert await plugin._expand_from_asset_ids([], mode="i2v") == {}
