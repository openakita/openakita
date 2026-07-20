"""happyhorse_pipeline — sanity checks (full flow tested in integration)."""

from __future__ import annotations

from pathlib import Path

import happyhorse_pipeline as pipeline_mod
import pytest
from happyhorse_inline.asset_probe import MediaValidationError
from happyhorse_pipeline import (
    _DIGITAL_HUMAN_MODES,
    _TTS_CAPABLE_MODES,
    _VIDEO_SYNTH_MODES,
    DEFAULT_POLL,
    HappyhorsePipelineContext,
    _approval_wait_hints,
    _step_finalize,
    _step_prepare_assets,
)


def test_pipeline_context_defaults():
    ctx = HappyhorsePipelineContext(task_id="t", mode="t2v", params={})
    assert ctx.task_id == "t"
    assert ctx.mode == "t2v"
    assert ctx.asset_ids == []
    assert ctx.cost_approved is False


def test_video_synth_modes_cover_all_seven_pure_video_modes():
    assert {
        "t2v",
        "i2v",
        "i2v_end",
        "video_extend",
        "r2v",
        "video_edit",
        "long_video",
    } == _VIDEO_SYNTH_MODES


def test_digital_human_modes_cover_all_five():
    assert {
        "photo_speak",
        "video_relip",
        "video_reface",
        "pose_drive",
        "avatar_compose",
    } == _DIGITAL_HUMAN_MODES


def test_tts_capable_modes_exclude_native_audio_sync_modes():
    """HappyHorse 1.0 video modes do their own audio — skip TTS."""
    assert _TTS_CAPABLE_MODES.isdisjoint(_VIDEO_SYNTH_MODES - {"long_video"})


def test_poll_schedule_three_tier_backoff():
    poll = DEFAULT_POLL
    assert poll.interval_for(0) == poll.fast_interval_sec
    assert poll.interval_for(60) == poll.medium_interval_sec
    assert poll.interval_for(300) == poll.slow_interval_sec
    assert poll.total_timeout_sec >= 600


def test_pipeline_context_carries_cost_approval_flag():
    ctx = HappyhorsePipelineContext(task_id="t", mode="t2v", params={})
    ctx.cost_approved = True
    assert ctx.cost_approved is True


def test_cost_approval_uses_generic_blocked_wait_contract():
    hints = _approval_wait_hints(
        {"total": 8.0, "threshold": 5.0, "currency": "CNY"},
        message="approval needed",
    )

    assert hints["wait_state"] == "blocked"
    assert hints["blocker"]["kind"] == "approval_required"
    assert hints["blocker"]["action"] == "approve_cost"
    assert hints["blocker"]["resume_patch"] == {"cost_approved": True}


class _FakeTaskManager:
    def __init__(self):
        self.updates = []

    async def update_task_safe(self, *_args, **_kwargs):
        self.updates.append((_args, _kwargs))
        return True


class _FakeClient:
    async def face_detect(self, _url):
        return {"ok": True}


async def _noop_emit(_event, _payload):
    return None


@pytest.mark.asyncio
async def test_pipeline_persists_blocked_wait_contract_for_cost_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    async def setup(*_args, **_kwargs):
        return None

    async def require_approval(*_args, **_kwargs):
        raise pipeline_mod.ApprovalRequired({"total": 8.0, "threshold": 5.0, "currency": "CNY"})

    monkeypatch.setattr(pipeline_mod, "_step_setup_environment", setup)
    monkeypatch.setattr(pipeline_mod, "_step_estimate_cost", require_approval)
    tm = _FakeTaskManager()
    ctx = HappyhorsePipelineContext(task_id="hh_cost", mode="i2v", params={})

    result = await pipeline_mod.run_pipeline(
        ctx,
        tm=tm,
        client=object(),
        emit=_noop_emit,
        base_data_dir=tmp_path,
    )

    assert result.error_kind == "approval_required"
    assert result.error_hints["wait_state"] == "blocked"
    persisted = tm.updates[-1][1]
    assert persisted["status"] == "pending"
    assert persisted["error_hints_json"]["blocker"]["action"] == "approve_cost"


@pytest.mark.asyncio
async def test_prepare_assets_normalizes_top_level_video_fields():
    ctx = HappyhorsePipelineContext(
        task_id="t",
        mode="i2v",
        params={
            "first_frame_url": "https://oss.example/first.png",
            "reference_urls": ["https://oss.example/ref.png"],
        },
    )

    await _step_prepare_assets(
        ctx,
        "happyhorse-video",
        _FakeClient(),
        _FakeTaskManager(),
        _noop_emit,
    )

    assert ctx.asset_urls["first_frame_url"] == "https://oss.example/first.png"
    assert ctx.asset_urls["reference_urls"] == ["https://oss.example/ref.png"]


@pytest.mark.asyncio
async def test_prepare_assets_aliases_source_video_for_digital_humans():
    ctx = HappyhorsePipelineContext(
        task_id="t",
        mode="video_reface",
        params={
            "image_url": "https://oss.example/face.png",
            "source_video_url": "https://oss.example/source.mp4",
        },
    )

    await _step_prepare_assets(
        ctx,
        "happyhorse-video",
        _FakeClient(),
        _FakeTaskManager(),
        _noop_emit,
    )

    assert ctx.asset_urls["source_video_url"] == "https://oss.example/source.mp4"
    assert ctx.asset_urls["video_url"] == "https://oss.example/source.mp4"
    assert ctx.asset_urls["ref_images_url"] == ["https://oss.example/face.png"]


# ─── Bug 3 regression — _publish_asset receives 4 args + metadata ────


@pytest.mark.asyncio
async def test_finalize_calls_publish_with_metadata(tmp_path: Path):
    """``_step_finalize`` must pass the per-task metadata dict as the
    4th positional argument to ``_publish_asset`` so Asset Bus rows
    inherit task lineage (task_id / mode / model_id / cost). Pre-fix
    the call was 3-arg and Asset Bus saw an empty metadata blob, which
    broke downstream consumers that filter by mode / task_id.
    """
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"00")
    fake_frame = tmp_path / "frame.png"
    fake_frame.write_bytes(b"00")

    captured: list[tuple] = []

    async def fake_publish(path, kind, preview_url, metadata):
        captured.append((path, kind, preview_url, metadata))
        return f"aid-{len(captured)}"

    ctx = HappyhorsePipelineContext(
        task_id="task-xyz",
        mode="t2v",
        model_id="happyhorse-1.0-t2v",
        params={"_publish_asset": fake_publish},
    )
    ctx.task_dir = tmp_path / "task_dir"
    ctx.task_dir.mkdir()
    ctx.video_path = fake_video
    ctx.last_frame_path = fake_frame
    ctx.video_url = "https://cdn.example/video.mp4"
    ctx.last_frame_url = "https://cdn.example/frame.png"
    ctx.cost_breakdown = {"total": 1.23, "currency": "CNY"}
    ctx.dashscope_id = "ds-001"
    ctx.dashscope_endpoint = "happyhorse-1.0-t2v"

    await _step_finalize(
        ctx,
        "happyhorse-video",
        _FakeTaskManager(),
        _noop_emit,
        base_data_dir=tmp_path,
    )

    assert len(captured) == 2, "video + last_frame should both publish"
    video_call, frame_call = captured
    assert video_call[1] == "video"
    assert frame_call[1] == "image"
    for _path, _kind, _url, meta in captured:
        assert isinstance(meta, dict) and meta, "metadata must be present"
        assert meta.get("task_id") == "task-xyz"
        assert meta.get("mode") == "t2v"
        assert meta.get("model_id") == "happyhorse-1.0-t2v"
        assert meta.get("dashscope_id") == "ds-001"
        assert meta.get("cost_breakdown") == {"total": 1.23, "currency": "CNY"}
    assert frame_call[3].get("role") == "last_frame"
    assert ctx.asset_ids == ["aid-1", "aid-2"]


@pytest.mark.asyncio
async def test_finalize_rejects_bad_video_dimensions_before_asset_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"not-a-real-video")
    published: list[str] = []

    async def fake_publish(path, *_args):
        published.append(path)
        return "aid-should-not-exist"

    def reject_dimensions(*_args, **_kwargs):
        raise MediaValidationError(
            {
                "passed": False,
                "code": "media_dimensions_mismatch",
                "message": "期望 1280x720，实际 960x960；必须重新生成",
                "expected": {"aspect_ratio": "16:9", "width": 1280, "height": 720},
                "actual": {"width": 960, "height": 960},
            }
        )

    monkeypatch.setattr(pipeline_mod, "assert_media_dimensions", reject_dimensions)
    ctx = HappyhorsePipelineContext(
        task_id="task-bad-ratio",
        mode="i2v",
        params={
            "_publish_asset": fake_publish,
            "expected_media": {"aspect_ratio": "16:9", "width": 1280, "height": 720},
        },
    )
    ctx.task_dir = tmp_path
    ctx.video_path = fake_video

    with pytest.raises(MediaValidationError, match="必须重新生成"):
        await _step_finalize(
            ctx,
            "happyhorse-video",
            _FakeTaskManager(),
            _noop_emit,
            base_data_dir=tmp_path,
        )

    assert published == []
