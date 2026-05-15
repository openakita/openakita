"""happyhorse_pipeline — sanity checks (full flow tested in integration)."""

from __future__ import annotations

from happyhorse_pipeline import (
    _DIGITAL_HUMAN_MODES,
    _TTS_CAPABLE_MODES,
    _VIDEO_SYNTH_MODES,
    DEFAULT_POLL,
    HappyhorsePipelineContext,
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
