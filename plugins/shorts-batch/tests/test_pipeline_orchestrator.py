"""Phase 3 — pipeline_orchestrator unit tests.

Verifies the 6-stage video-pipeline orchestrator that ``shorts-batch``
exposes as the project's ``video-pipeline`` per the overhaul playbook.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pipeline_orchestrator import (
    STAGE_AUDIO,
    STAGE_IDS,
    STAGE_IMAGE,
    STAGE_MUX,
    STAGE_PLAN,
    STAGE_SUBTITLE,
    STAGE_VIDEO,
    AudioAsset,
    ClipAsset,
    FinalVideoAsset,
    ImageAsset,
    PipelineConfig,
    PipelineStageError,
    ShotPlan,
    SubtitleAsset,
    default_audio_stage,
    default_image_stage,
    default_mux_stage,
    default_plan_stage,
    default_subtitle_stage,
    default_video_stage,
    run_pipeline,
    to_verification,
)
from shorts_engine import ShortBrief

# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def brief() -> ShortBrief:
    return ShortBrief(topic="测试 短视频", duration_sec=9.0)


@pytest.fixture()
def cfg(tmp_path: Path) -> PipelineConfig:
    return PipelineConfig(out_dir=tmp_path / "pipe")


# ── default-stage smoke tests ───────────────────────────────────────────


def test_default_plan_stage_emits_one_shot_per_3s(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    assert len(plans) == 3
    assert all(isinstance(p, ShotPlan) for p in plans)
    assert sum(p.duration_sec for p in plans) == pytest.approx(brief.duration_sec)


def test_default_image_stage_writes_one_file_per_shot(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    images = default_image_stage(plans, cfg)
    assert len(images) == len(plans)
    for img in images:
        assert Path(img.image_path).exists()
        assert Path(img.image_path).read_bytes().startswith(b"\x89PNG")


def test_default_video_stage_passes_stills_through(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    images = default_image_stage(plans, cfg)
    clips = default_video_stage(images, plans, cfg)
    assert all(c.is_static for c in clips)
    assert {c.shot_index for c in clips} == {p.index for p in plans}


def test_default_audio_stage_writes_silence_wav(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    images = default_image_stage(plans, cfg)
    clips = default_video_stage(images, plans, cfg)
    audio = default_audio_stage(brief, clips, cfg)
    assert Path(audio.audio_path).exists()
    head = Path(audio.audio_path).read_bytes()[:12]
    assert head.startswith(b"RIFF") and b"WAVE" in head


def test_default_subtitle_stage_writes_minimal_srt(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    images = default_image_stage(plans, cfg)
    clips = default_video_stage(images, plans, cfg)
    audio = default_audio_stage(brief, clips, cfg)
    subs = default_subtitle_stage(audio, cfg)
    assert Path(subs.srt_path).exists()
    text = Path(subs.srt_path).read_text(encoding="utf-8")
    assert "-->" in text


def test_default_mux_stage_writes_placeholder(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    plans = default_plan_stage(brief, cfg)
    images = default_image_stage(plans, cfg)
    clips = default_video_stage(images, plans, cfg)
    audio = default_audio_stage(brief, clips, cfg)
    subs = default_subtitle_stage(audio, cfg)
    final = default_mux_stage(clips, audio, subs, cfg)
    assert Path(final.video_path).exists()


# ── full-pipeline tests ────────────────────────────────────────────────


def test_run_pipeline_with_all_defaults_succeeds(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    result = run_pipeline(brief, cfg)
    assert result.succeeded is True
    assert result.error == ""
    assert len(result.plan) >= 1
    assert len(result.images) == len(result.plan)
    assert len(result.clips) == len(result.plan)
    assert result.audio is not None
    assert result.subtitles is not None
    assert result.final_video is not None
    # one event per stage (video skipped → still gets an "ok" event)
    stages_seen = {e.stage for e in result.events if e.status == "ok"}
    assert stages_seen == set(STAGE_IDS)


def test_run_pipeline_skip_video_stage_synthesises_clips_from_stills(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    cfg.skip_video_stage = True
    sentinel: dict[str, bool] = {"called": False}

    def video_should_not_run(*_args, **_kwargs):  # pragma: no cover - guard only
        sentinel["called"] = True
        return []

    result = run_pipeline(brief, cfg, video_stage=video_should_not_run)
    assert result.succeeded
    assert sentinel["called"] is False
    assert all(c.is_static for c in result.clips)


def test_run_pipeline_skip_subtitle_stage(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    cfg.skip_subtitle_stage = True

    def subtitle_should_not_run(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("subtitle stage should be skipped")

    result = run_pipeline(brief, cfg, subtitle_stage=subtitle_should_not_run)
    assert result.succeeded
    assert result.subtitles is None


def test_run_pipeline_invokes_real_video_stage_when_enabled(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    cfg.skip_video_stage = False
    captured: dict[str, int] = {"calls": 0}

    def custom_video(images, plans, _cfg):
        captured["calls"] += 1
        return [
            ClipAsset(
                shot_index=img.shot_index,
                clip_path=img.image_path,
                duration_sec=plans[i].duration_sec,
                is_static=False,
            )
            for i, img in enumerate(images)
        ]

    result = run_pipeline(brief, cfg, video_stage=custom_video)
    assert result.succeeded
    assert captured["calls"] == 1
    assert all(not c.is_static for c in result.clips)


def test_run_pipeline_wraps_stage_failure_with_stage_id(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    def boom_image(*_a, **_kw):
        raise RuntimeError("dashscope down")

    result = run_pipeline(brief, cfg, image_stage=boom_image)
    assert result.succeeded is False
    assert "stage=image" in result.error
    assert "dashscope down" in result.error
    failed_events = [e for e in result.events if e.status == "failed"]
    assert len(failed_events) == 1
    assert failed_events[0].stage == STAGE_IMAGE


def test_run_pipeline_emits_stage_events_in_order(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    seen: list[tuple[str, str]] = []

    def listener(ev):
        seen.append((ev.stage, ev.status))

    result = run_pipeline(brief, cfg, on_event=listener)
    assert result.succeeded
    # every stage emits running before its terminal status
    stages_with_running = [s for s, st in seen if st == "running"]
    # video is skipped (cfg default), so it has only an "ok" event
    assert STAGE_PLAN in stages_with_running
    assert STAGE_IMAGE in stages_with_running
    assert STAGE_AUDIO in stages_with_running
    assert STAGE_SUBTITLE in stages_with_running
    assert STAGE_MUX in stages_with_running
    # ordering: plan precedes everything else
    plan_idx = next(i for i, (s, st) in enumerate(seen) if s == STAGE_PLAN and st == "running")
    mux_idx = next(i for i, (s, st) in enumerate(seen) if s == STAGE_MUX)
    assert plan_idx < mux_idx


def test_run_pipeline_planner_returning_empty_fails_with_plan_stage(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    result = run_pipeline(brief, cfg, plan_stage=lambda *_a, **_kw: [])
    assert result.succeeded is False
    assert "stage=plan" in result.error


def test_to_verification_green_when_all_stages_ok(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    result = run_pipeline(brief, cfg)
    v = to_verification(result)
    assert v.verifier_id == "shorts_batch_pipeline"
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert "total=" in v.notes


def test_to_verification_flags_low_confidence_on_failure(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    result = run_pipeline(
        brief, cfg, image_stage=lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("x")),
    )
    v = to_verification(result)
    assert v.verified is False
    assert len(v.low_confidence_fields) == 1
    assert v.low_confidence_fields[0].path == "$.pipeline"


def test_pipeline_stage_error_carries_stage_and_original() -> None:
    inner = ValueError("nope")
    err = PipelineStageError("image", inner)
    assert err.stage == "image"
    assert err.original is inner
    assert "image" in str(err) and "nope" in str(err)


def test_event_listener_exception_does_not_abort_pipeline(
    brief: ShortBrief, cfg: PipelineConfig,
) -> None:
    def evil(_ev):
        raise RuntimeError("listener exploded")

    result = run_pipeline(brief, cfg, on_event=evil)
    assert result.succeeded


def test_dataclass_to_dict_round_trip() -> None:
    plan = ShotPlan(index=0, visual="v", duration_sec=1.5, camera="cam", dialogue="d")
    img = ImageAsset(shot_index=0, image_path="/tmp/x.png", width=1080, height=1920)
    clip = ClipAsset(shot_index=0, clip_path="/tmp/x.mp4", duration_sec=1.5)
    audio = AudioAsset(audio_path="/tmp/a.wav", duration_sec=15.0, has_voiceover=True)
    subs = SubtitleAsset(srt_path="/tmp/s.srt", language="en")
    final = FinalVideoAsset(video_path="/tmp/o.mp4", duration_sec=15.0, aspect="9:16", bytes=999)
    for d in (plan.to_dict(), img.to_dict(), clip.to_dict(),
              audio.to_dict(), subs.to_dict(), final.to_dict()):
        assert isinstance(d, dict) and d  # non-empty


def test_stage_ids_constant_is_complete() -> None:
    assert STAGE_IDS == (
        STAGE_PLAN, STAGE_IMAGE, STAGE_VIDEO, STAGE_AUDIO, STAGE_SUBTITLE, STAGE_MUX,
    )
