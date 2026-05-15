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
    plugin = _make_plugin({
        "a1": {"preview_url": "https://oss/x.png"},
        "a2": {"preview_url": "https://oss/y.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v")
    assert out == {
        "first_frame_url": "https://oss/x.png",
        "reference_urls": ["https://oss/y.png"],
    }


@pytest.mark.asyncio
async def test_expand_i2v_end_uses_first_and_last():
    plugin = _make_plugin({
        "a1": {"preview_url": "https://oss/first.png"},
        "a2": {"preview_url": "https://oss/last.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v_end")
    assert out == {
        "first_frame_url": "https://oss/first.png",
        "last_frame_url": "https://oss/last.png",
    }


@pytest.mark.asyncio
async def test_expand_r2v_uses_reference_urls_for_all():
    plugin = _make_plugin({
        "a1": {"preview_url": "https://oss/1.png"},
        "a2": {"preview_url": "https://oss/2.png"},
        "a3": {"preview_url": "https://oss/3.png"},
    })
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
    plugin = _make_plugin({
        "v1": {"preview_url": "https://oss/v.mp4", "asset_kind": "video"},
    })
    out = await plugin._expand_from_asset_ids(["v1"], mode="video_extend")
    assert out["source_video_url"] == "https://oss/v.mp4"


@pytest.mark.asyncio
async def test_expand_video_edit_carries_extra_references():
    plugin = _make_plugin({
        "v1": {"preview_url": "https://oss/v.mp4"},
        "i1": {"preview_url": "https://oss/i.png"},
    })
    out = await plugin._expand_from_asset_ids(["v1", "i1"], mode="video_edit")
    assert out["source_video_url"] == "https://oss/v.mp4"
    assert out["reference_urls"] == ["https://oss/i.png"]


@pytest.mark.asyncio
async def test_expand_photo_speak_uses_image_url_and_image_urls():
    plugin = _make_plugin({
        "p1": {"preview_url": "https://oss/face.png"},
        "p2": {"preview_url": "https://oss/scene.png"},
    })
    out = await plugin._expand_from_asset_ids(["p1", "p2"], mode="photo_speak")
    assert out == {
        "image_url": "https://oss/face.png",
        "image_urls": ["https://oss/scene.png"],
    }


@pytest.mark.asyncio
async def test_expand_avatar_compose_same_as_photo_speak():
    plugin = _make_plugin({
        "p1": {"preview_url": "https://oss/1.png"},
        "p2": {"preview_url": "https://oss/2.png"},
        "p3": {"preview_url": "https://oss/3.png"},
    })
    out = await plugin._expand_from_asset_ids(
        ["p1", "p2", "p3"], mode="avatar_compose"
    )
    assert out["image_url"] == "https://oss/1.png"
    assert out["image_urls"] == ["https://oss/2.png", "https://oss/3.png"]


@pytest.mark.asyncio
async def test_expand_skips_missing_assets():
    plugin = _make_plugin({
        "a1": {"preview_url": "https://oss/x.png"},
        # a2: lookup returns None — must be skipped
        "a3": {},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2", "a3"], mode="r2v")
    assert out == {"reference_urls": ["https://oss/x.png"]}


@pytest.mark.asyncio
async def test_expand_empty_returns_empty_dict():
    plugin = _make_plugin({})
    assert await plugin._expand_from_asset_ids([], mode="i2v") == {}
