"""shorts-batch — Phase 3 orchestrator: 6-step Brief → Final-MP4 pipeline.

This module turns ``shorts-batch`` from a "fan-out + risk-score + render
through one pluggable callback" tool into the full **video-pipeline**
orchestrator the overhaul playbook calls for, without spinning up a new
plugin id (the playbook explicitly prefers reusing this plugin for the
orchestrator role; the rationale is that ``shorts-batch`` already owns
the brief schema, the slideshow-risk gate, and the worker plumbing).

Pipeline stages — each one is a *pluggable callable* the host wires to
the right downstream plugin (storyboard / tongyi-image or local-sd-flux
/ seedance-video / bgm-suggester / subtitle-maker / ffmpeg-mux). The
defaults are deterministic stubs so the engine is testable in isolation
and end-to-end smoke tests don't require real GPUs / API quotas.

  1. **plan**       brief                → list[ShotPlan]   (storyboard)
  2. **image**      list[ShotPlan]       → list[ImageAsset] (tongyi-image / local-sd-flux)
  3. **video**      list[ImageAsset]     → list[ClipAsset]  (seedance-video, optional)
  4. **audio**      brief + list[ClipAsset] → AudioAsset    (bgm-suggester / bgm-mixer / tts-studio)
  5. **subtitle**   AudioAsset           → SubtitleAsset    (subtitle-maker / transcribe-archive)
  6. **mux**        clips + audio + subs → FinalVideoAsset  (ffmpeg / video-translator)

All stages accept and return frozen dataclasses defined in this module
so swapping a downstream plugin never leaks its internal types up the
chain. Each stage has a ``stage_id`` so the worker can emit per-stage
``task_updated`` events (the UI shows a "step 3/6 — generating image"
progress bar without knowing which plugin is doing the work).

The orchestrator is intentionally **synchronous-friendly**: each stage
is a plain callable returning the next dataclass; the worker thread
calls them sequentially, so an unfinished stage cleanly fails the whole
pipeline with a coached error rather than leaving half-rendered files.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openakita_plugin_sdk.contrib import (
    KIND_OTHER,
    LowConfidenceField,
    Verification,
)
from shorts_engine import ShortBrief

logger = logging.getLogger(__name__)


__all__ = [
    "AudioAsset",
    "ClipAsset",
    "FinalVideoAsset",
    "ImageAsset",
    "PipelineConfig",
    "PipelineError",
    "PipelineResult",
    "PipelineStageError",
    "STAGE_AUDIO",
    "STAGE_IDS",
    "STAGE_IMAGE",
    "STAGE_MUX",
    "STAGE_PLAN",
    "STAGE_SUBTITLE",
    "STAGE_VIDEO",
    "ShotPlan",
    "StageEvent",
    "SubtitleAsset",
    "default_audio_stage",
    "default_image_stage",
    "default_mux_stage",
    "default_plan_stage",
    "default_subtitle_stage",
    "default_video_stage",
    "run_pipeline",
    "to_verification",
]


STAGE_PLAN = "plan"
STAGE_IMAGE = "image"
STAGE_VIDEO = "video"
STAGE_AUDIO = "audio"
STAGE_SUBTITLE = "subtitle"
STAGE_MUX = "mux"
STAGE_IDS = (STAGE_PLAN, STAGE_IMAGE, STAGE_VIDEO, STAGE_AUDIO, STAGE_SUBTITLE, STAGE_MUX)


# ── Models ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShotPlan:
    """Single shot in a storyboard. Output of stage 1, input of stage 2."""

    index: int
    visual: str
    duration_sec: float
    camera: str = ""
    dialogue: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "visual": self.visual,
            "duration_sec": round(self.duration_sec, 3),
            "camera": self.camera,
            "dialogue": self.dialogue,
        }


@dataclass(frozen=True)
class ImageAsset:
    """One generated still per shot. Output of stage 2."""

    shot_index: int
    image_path: str
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_index": self.shot_index,
            "image_path": self.image_path,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class ClipAsset:
    """One short video clip per shot. Output of stage 3 (or pass-through
    when ``video_stage`` is the no-op default and we slideshow the stills)."""

    shot_index: int
    clip_path: str
    duration_sec: float
    is_static: bool = False  # True when synthesised from a still (Ken Burns / pan-zoom)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_index": self.shot_index,
            "clip_path": self.clip_path,
            "duration_sec": round(self.duration_sec, 3),
            "is_static": self.is_static,
        }


@dataclass(frozen=True)
class AudioAsset:
    """Single audio track for the whole short. Output of stage 4."""

    audio_path: str
    duration_sec: float
    has_voiceover: bool = False
    has_bgm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_path": self.audio_path,
            "duration_sec": round(self.duration_sec, 3),
            "has_voiceover": self.has_voiceover,
            "has_bgm": self.has_bgm,
        }


@dataclass(frozen=True)
class SubtitleAsset:
    """SRT/VTT subtitle file aligned to ``AudioAsset``. Output of stage 5."""

    srt_path: str
    vtt_path: str = ""
    language: str = "zh"

    def to_dict(self) -> dict[str, Any]:
        return {
            "srt_path": self.srt_path,
            "vtt_path": self.vtt_path,
            "language": self.language,
        }


@dataclass(frozen=True)
class FinalVideoAsset:
    """Final muxed mp4 with picture + audio + soft/hard subtitles. Output of stage 6."""

    video_path: str
    duration_sec: float
    aspect: str = ""
    bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "duration_sec": round(self.duration_sec, 3),
            "aspect": self.aspect,
            "bytes": self.bytes,
        }


@dataclass
class StageEvent:
    """One per-stage lifecycle event (``running`` then ``ok`` or ``failed``).

    The orchestrator emits these in order so the worker can forward them
    as ``task_updated`` events with a stable shape, regardless of which
    downstream plugin actually executed the stage.
    """

    stage: str
    status: str  # "running" | "ok" | "failed"
    elapsed_ms: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


@dataclass
class PipelineConfig:
    """Knobs for the whole pipeline. Kept on the brief / job level."""

    out_dir: Path
    skip_video_stage: bool = True   # default: slideshow stills (cheap, deterministic)
    skip_subtitle_stage: bool = False
    burn_subtitles: bool = False
    target_language: str = "zh"


# ── Stage signatures (as Callable types for documentation) ──────────────


PlanStage = Callable[[ShortBrief, PipelineConfig], list[ShotPlan]]
ImageStage = Callable[[list[ShotPlan], PipelineConfig], list[ImageAsset]]
VideoStage = Callable[[list[ImageAsset], list[ShotPlan], PipelineConfig], list[ClipAsset]]
AudioStage = Callable[[ShortBrief, list[ClipAsset], PipelineConfig], AudioAsset]
SubtitleStage = Callable[[AudioAsset, PipelineConfig], SubtitleAsset]
MuxStage = Callable[
    [list[ClipAsset], AudioAsset, SubtitleAsset | None, PipelineConfig],
    FinalVideoAsset,
]


# ── Result + errors ────────────────────────────────────────────────────


class PipelineError(Exception):
    """Top-level pipeline failure — wraps a stage error so the host can
    render a coached message that names which stage actually broke."""


class PipelineStageError(PipelineError):
    """One stage raised — the orchestrator caught it and wrapped it
    with the stage id so :class:`ErrorCoach` can render
    ``"图像生成失败 (stage=image): xxx"`` instead of a bare exception."""

    def __init__(self, stage: str, original: BaseException) -> None:
        super().__init__(f"{stage}: {original}")
        self.stage = stage
        self.original = original


@dataclass
class PipelineResult:
    """One full pipeline run."""

    brief: ShortBrief
    plan: list[ShotPlan] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    clips: list[ClipAsset] = field(default_factory=list)
    audio: AudioAsset | None = None
    subtitles: SubtitleAsset | None = None
    final_video: FinalVideoAsset | None = None
    events: list[StageEvent] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    succeeded: bool = False
    error: str = ""

    @property
    def total_ms(self) -> int:
        return int(round((self.finished_at - self.started_at) * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "error": self.error,
            "total_ms": self.total_ms,
            "plan": [p.to_dict() for p in self.plan],
            "images": [i.to_dict() for i in self.images],
            "clips": [c.to_dict() for c in self.clips],
            "audio": self.audio.to_dict() if self.audio else None,
            "subtitles": self.subtitles.to_dict() if self.subtitles else None,
            "final_video": self.final_video.to_dict() if self.final_video else None,
            "events": [e.to_dict() for e in self.events],
        }


# ── Default stages (deterministic stubs, useful out of the box) ─────────


def default_plan_stage(brief: ShortBrief, cfg: PipelineConfig) -> list[ShotPlan]:
    """Trivial fallback planner: 1 shot per ~3 seconds, evenly split."""
    n = max(1, int(round(brief.duration_sec / 3.0)))
    per = brief.duration_sec / n
    return [
        ShotPlan(
            index=i,
            visual=f"{brief.topic} — 镜头 {i+1}/{n}",
            duration_sec=per,
            camera="static medium",
        )
        for i in range(n)
    ]


def default_image_stage(plans: list[ShotPlan], cfg: PipelineConfig) -> list[ImageAsset]:
    """Stub image stage: writes a 1-byte placeholder per shot.

    Real integrations swap this for a callable that invokes the
    ``tongyi_image_create`` brain tool (or ``local_sd_flux_create``).
    """
    out = cfg.out_dir / "images"
    out.mkdir(parents=True, exist_ok=True)
    assets: list[ImageAsset] = []
    for p in plans:
        path = out / f"shot_{p.index:03d}.png"
        if not path.exists():
            path.write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny PNG signature
        assets.append(ImageAsset(shot_index=p.index, image_path=str(path)))
    return assets


def default_video_stage(
    images: list[ImageAsset], plans: list[ShotPlan], cfg: PipelineConfig,
) -> list[ClipAsset]:
    """Stub video stage: pass each still through as a "static clip".

    With ``skip_video_stage=True`` (the default), the orchestrator
    short-circuits this stage entirely — the slideshow path is the cheap
    Ken-Burns-able default. Set ``skip_video_stage=False`` to enable
    real video generation via ``seedance-video``.
    """
    by_shot = {img.shot_index: img for img in images}
    out: list[ClipAsset] = []
    for plan in plans:
        img = by_shot.get(plan.index)
        if img is None:
            continue
        out.append(ClipAsset(
            shot_index=plan.index,
            clip_path=img.image_path,  # stub: reuse the image path as the "clip"
            duration_sec=plan.duration_sec,
            is_static=True,
        ))
    return out


def default_audio_stage(
    brief: ShortBrief, clips: list[ClipAsset], cfg: PipelineConfig,
) -> AudioAsset:
    """Stub audio stage: zero-byte WAV header (silence) for the total length."""
    total = sum(c.duration_sec for c in clips) or brief.duration_sec
    path = cfg.out_dir / "audio" / "track.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Minimal RIFF/WAVE header pointing at zero PCM samples; ffmpeg
        # parses it as a valid silent track of length 0 (the mux step
        # treats this as a "no audio" sentinel).
        path.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
    return AudioAsset(
        audio_path=str(path),
        duration_sec=total,
        has_voiceover=False,
        has_bgm=False,
    )


def default_subtitle_stage(
    audio: AudioAsset, cfg: PipelineConfig,
) -> SubtitleAsset:
    """Stub subtitle stage: a one-line SRT covering the whole duration."""
    path = cfg.out_dir / "subtitles" / "track.srt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n[stub subtitle]\n",
            encoding="utf-8",
        )
    return SubtitleAsset(srt_path=str(path), language=cfg.target_language)


def default_mux_stage(
    clips: list[ClipAsset], audio: AudioAsset, subs: SubtitleAsset | None,
    cfg: PipelineConfig,
) -> FinalVideoAsset:
    """Stub mux stage: writes a zero-byte placeholder mp4.

    Real integrations swap this for the ffmpeg invocation in
    :mod:`video-translator.translator_engine.build_mux_cmd`.
    """
    path = cfg.out_dir / "final.mp4"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return FinalVideoAsset(
        video_path=str(path),
        duration_sec=audio.duration_sec,
        aspect="",
        bytes=path.stat().st_size if path.exists() else 0,
    )


# ── Orchestrator ───────────────────────────────────────────────────────


def run_pipeline(
    brief: ShortBrief,
    cfg: PipelineConfig,
    *,
    plan_stage: PlanStage | None = None,
    image_stage: ImageStage | None = None,
    video_stage: VideoStage | None = None,
    audio_stage: AudioStage | None = None,
    subtitle_stage: SubtitleStage | None = None,
    mux_stage: MuxStage | None = None,
    on_event: Callable[[StageEvent], None] | None = None,
) -> PipelineResult:
    """Run the 6-stage pipeline.

    Each stage is replaceable. Missing stages fall back to the
    deterministic stubs above so a fresh OpenAkita install can run a
    full smoke pipeline end-to-end without any external dependencies.

    Stage failures are caught and wrapped in :class:`PipelineStageError`
    so the host's :class:`ErrorCoach` always knows *which* stage broke
    (e.g. ``"image stage failed: dashscope rate limit"`` instead of a
    bare network traceback).
    """
    result = PipelineResult(brief=brief, started_at=time.time())
    cfg.out_dir.mkdir(parents=True, exist_ok=True)

    plan_fn = plan_stage or default_plan_stage
    image_fn = image_stage or default_image_stage
    video_fn = video_stage or default_video_stage
    audio_fn = audio_stage or default_audio_stage
    subtitle_fn = subtitle_stage or default_subtitle_stage
    mux_fn = mux_stage or default_mux_stage

    try:
        result.plan = _run_stage(STAGE_PLAN, lambda: plan_fn(brief, cfg), result, on_event)
        if not result.plan:
            raise PipelineStageError(STAGE_PLAN, RuntimeError("planner returned 0 shots"))

        result.images = _run_stage(
            STAGE_IMAGE, lambda: image_fn(result.plan, cfg), result, on_event,
        )

        if cfg.skip_video_stage:
            # synthesise stand-in ClipAssets directly from the images so the
            # downstream stages still see a uniform shape
            result.clips = default_video_stage(result.images, result.plan, cfg)
            _emit(on_event, result, StageEvent(
                stage=STAGE_VIDEO, status="ok", detail="skipped (slideshow mode)",
            ))
        else:
            result.clips = _run_stage(
                STAGE_VIDEO,
                lambda: video_fn(result.images, result.plan, cfg),
                result, on_event,
            )

        result.audio = _run_stage(
            STAGE_AUDIO, lambda: audio_fn(brief, result.clips, cfg), result, on_event,
        )

        if cfg.skip_subtitle_stage:
            result.subtitles = None
            _emit(on_event, result, StageEvent(
                stage=STAGE_SUBTITLE, status="ok", detail="skipped",
            ))
        else:
            result.subtitles = _run_stage(
                STAGE_SUBTITLE,
                lambda: subtitle_fn(result.audio, cfg),  # type: ignore[arg-type]
                result, on_event,
            )

        result.final_video = _run_stage(
            STAGE_MUX,
            lambda: mux_fn(result.clips, result.audio, result.subtitles, cfg),  # type: ignore[arg-type]
            result, on_event,
        )

        result.succeeded = True
    except PipelineStageError as e:
        result.error = f"stage={e.stage}: {e.original}"
        result.succeeded = False
    except Exception as e:  # noqa: BLE001 — unexpected non-stage failure
        result.error = f"unexpected: {e}"
        result.succeeded = False
    finally:
        result.finished_at = time.time()
    return result


def _run_stage(
    stage: str,
    fn: Callable[[], Any],
    result: PipelineResult,
    on_event: Callable[[StageEvent], None] | None,
) -> Any:
    _emit(on_event, result, StageEvent(stage=stage, status="running"))
    started = time.perf_counter()
    try:
        out = fn()
    except PipelineStageError:
        # already-wrapped — keep stage id as-is
        elapsed = int(round((time.perf_counter() - started) * 1000))
        _emit(on_event, result, StageEvent(stage=stage, status="failed", elapsed_ms=elapsed))
        raise
    except BaseException as e:
        elapsed = int(round((time.perf_counter() - started) * 1000))
        _emit(on_event, result, StageEvent(
            stage=stage, status="failed", elapsed_ms=elapsed, detail=str(e),
        ))
        raise PipelineStageError(stage, e) from e
    elapsed = int(round((time.perf_counter() - started) * 1000))
    _emit(on_event, result, StageEvent(stage=stage, status="ok", elapsed_ms=elapsed))
    return out


def _emit(
    on_event: Callable[[StageEvent], None] | None,
    result: PipelineResult,
    event: StageEvent,
) -> None:
    result.events.append(event)
    if on_event:
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 — listener errors must never break the pipeline
            logger.exception("pipeline event listener raised; continuing")


def to_verification(result: PipelineResult) -> Verification:
    """D2.10 envelope describing the pipeline run.

    Lists every stage's status + elapsed-ms so the host UI can render a
    truthful "what happened" timeline without inspecting raw logs.
    """
    failed_stages = [e.stage for e in result.events if e.status == "failed"]
    notes_parts = [f"{ev.stage}={ev.status}({ev.elapsed_ms}ms)" for ev in result.events]
    notes_parts.append(f"total={result.total_ms}ms")
    notes = "; ".join(notes_parts)
    low_conf: list[LowConfidenceField] = []
    if not result.succeeded:
        low_conf.append(LowConfidenceField(
            path="$.pipeline",
            value=result.error or "pipeline did not finish",
            reason=result.error or "pipeline did not finish",
            kind=KIND_OTHER,
        ))
    return Verification(
        verifier_id="shorts_batch_pipeline",
        verified=result.succeeded and not failed_stages,
        notes=notes,
        low_confidence_fields=low_conf,
    )
