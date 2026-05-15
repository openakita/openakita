"""Workbench-orchestration regressions for seedance-video.

1. ``_task_to_tool_payload`` must surface ``video_url`` / ``video_path``
   / ``last_frame_url`` / ``local_paths`` / ``asset_ids`` so the
   OrgRuntime hook can register the generated video (and optional
   last-frame image) as task attachments.
2. ``_expand_from_asset_ids`` must turn an array of upstream asset_ids
   into Ark-style ``image_url`` content items with the correct role
   per mode — this is how a workbench node consumes assets produced
   by an upstream image workbench (e.g. tongyi-image).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from plugin import Plugin as SeedanceVideoPlugin  # noqa: E402


def test_task_payload_includes_workbench_fields():
    task = {
        "id": "tk_v1",
        "status": "succeeded",
        "mode": "i2v",
        "video_url": "https://example.com/v.mp4",
        "local_video_path": "/tmp/v.mp4",
        "last_frame_url": "https://example.com/lf.png",
        "last_frame_local_path": "/tmp/lf.png",
        "asset_ids": ["a1", "a2"],
        "prompt": "an autumn forest",
    }
    payload = SeedanceVideoPlugin._task_to_tool_payload(task)
    assert payload["ok"] is True
    assert payload["task_id"] == "tk_v1"
    assert payload["video_url"] == "https://example.com/v.mp4"
    assert payload["video_path"] == "/tmp/v.mp4"
    assert payload["last_frame_url"] == "https://example.com/lf.png"
    assert payload["last_frame_path"] == "/tmp/lf.png"
    # local_paths union (video + optional last-frame) — runtime hook
    # iterates this list directly
    assert "/tmp/v.mp4" in payload["local_paths"]
    assert "/tmp/lf.png" in payload["local_paths"]
    assert payload["asset_ids"] == ["a1", "a2"]


def test_task_payload_failed_sets_ok_false():
    task = {
        "id": "tk", "status": "failed", "mode": "t2v",
        "error_message": "Ark quota exceeded",
        "video_url": "", "local_video_path": None,
        "last_frame_url": "", "last_frame_local_path": None,
        "asset_ids": [],
    }
    payload = SeedanceVideoPlugin._task_to_tool_payload(task)
    assert payload["ok"] is False
    assert payload["error_message"] == "Ark quota exceeded"


def test_task_payload_json_round_trip():
    task = {
        "id": "tk", "status": "succeeded", "mode": "t2v",
        "video_url": "u", "local_video_path": "/p",
        "last_frame_url": "", "last_frame_local_path": None,
        "asset_ids": ["x"],
    }
    payload = SeedanceVideoPlugin._task_to_tool_payload(task)
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


# ── from_asset_ids expansion ─────────────────────────────────────────


def _make_plugin_for_expand(asset_lookup: dict[str, dict]):
    """Stub a SeedanceVideoPlugin with just enough wiring for
    ``_expand_from_asset_ids`` to run: an _api whose consume_asset
    returns whatever the lookup dict says."""

    async def _consume(aid: str) -> dict | None:
        return asset_lookup.get(aid)

    plugin = SeedanceVideoPlugin.__new__(SeedanceVideoPlugin)
    plugin._api = SimpleNamespace(consume_asset=AsyncMock(side_effect=_consume))
    return plugin


@pytest.mark.asyncio
async def test_expand_from_asset_ids_i2v_assigns_first_then_reference():
    plugin = _make_plugin_for_expand({
        "a1": {"preview_url": "https://oss/x.png"},
        "a2": {"preview_url": "https://oss/y.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v")
    assert out == [
        {"type": "image_url", "image_url": {"url": "https://oss/x.png"}, "role": "first_frame"},
        {"type": "image_url", "image_url": {"url": "https://oss/y.png"}, "role": "reference_image"},
    ]


@pytest.mark.asyncio
async def test_expand_from_asset_ids_i2v_end_uses_last_frame_for_subsequent_ids():
    plugin = _make_plugin_for_expand({
        "a1": {"preview_url": "https://oss/first.png"},
        "a2": {"preview_url": "https://oss/last.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2"], mode="i2v_end")
    assert [item["role"] for item in out] == ["first_frame", "last_frame"]


@pytest.mark.asyncio
async def test_expand_from_asset_ids_multimodal_uses_reference_for_subsequent():
    plugin = _make_plugin_for_expand({
        "a1": {"preview_url": "https://oss/1.png"},
        "a2": {"preview_url": "https://oss/2.png"},
        "a3": {"preview_url": "https://oss/3.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2", "a3"], mode="multimodal")
    assert [item["role"] for item in out] == [
        "first_frame", "reference_image", "reference_image",
    ]


@pytest.mark.asyncio
async def test_expand_from_asset_ids_skips_missing_and_invalid_assets():
    plugin = _make_plugin_for_expand({
        "a1": {"preview_url": "https://oss/x.png"},
        # a2: lookup returns None → skipped, but does not abort
        # a3: has neither preview_url nor source_path → skipped
        "a3": {"extra": "bogus"},
    })
    out = await plugin._expand_from_asset_ids(["a1", "a2", "a3"], mode="i2v")
    # only a1 survives; role assignment uses ORIGINAL index 0 → first_frame
    assert out == [
        {"type": "image_url", "image_url": {"url": "https://oss/x.png"}, "role": "first_frame"},
    ]


@pytest.mark.asyncio
async def test_expand_from_asset_ids_prefers_preview_over_source_path():
    plugin = _make_plugin_for_expand({
        "a1": {
            "preview_url": "https://oss/preview.png",
            "source_path": "/local/should/not/win.png",
        },
    })
    out = await plugin._expand_from_asset_ids(["a1"], mode="i2v")
    assert out[0]["image_url"]["url"] == "https://oss/preview.png"


@pytest.mark.asyncio
async def test_expand_from_asset_ids_falls_back_to_source_path():
    plugin = _make_plugin_for_expand({
        "a1": {"source_path": "/local/img.png"},
    })
    out = await plugin._expand_from_asset_ids(["a1"], mode="i2v")
    assert out[0]["image_url"]["url"] == "/local/img.png"


@pytest.mark.asyncio
async def test_expand_from_asset_ids_empty_returns_empty():
    plugin = _make_plugin_for_expand({})
    assert await plugin._expand_from_asset_ids([], mode="i2v") == []
