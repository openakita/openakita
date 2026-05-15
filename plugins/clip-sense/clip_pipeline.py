"""7-step pipeline for clip-sense editing modes.

Steps: setup → check_deps → transcribe → analyze → execute → subtitle → finalize
Each mode may skip certain steps (e.g. silence_clean skips transcribe+analyze).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clip_models import (
    MAX_VIDEO_DURATION_SEC,
    MODES_BY_ID,
    estimate_cost,
    get_error_hints,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Any]


@dataclass
class ClipPipelineContext:
    task_id: str
    mode: str
    params: dict[str, Any]
    task_dir: Path
    source_video_path: Path
    source_url: str = ""
    output_dir: Path | None = None
    source_duration_sec: float | None = None
    transcript_id: str | None = None
    transcript_sentences: list[dict[str, Any]] | None = None
    transcript_text: str | None = None
    segments: list[dict[str, Any]] | None = None
    silence_segments: list[dict[str, Any]] | None = None
    output_path: Path | None = None
    output_duration_sec: float | None = None
    subtitle_path: Path | None = None
    cost_items: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str | None = None
    error_message: str | None = None
    error_hints: list[str] | None = None
    cancelled: bool = False


class PipelineError(Exception):
    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


async def run_pipeline(
    ctx: ClipPipelineContext,
    tm: Any,
    asr: Any,
    ffmpeg: Any,
    emit: EmitFn,
) -> None:
    """Execute the 7-step editing pipeline with mode-specific short-circuiting."""
    mode_def = MODES_BY_ID.get(ctx.mode)
    if mode_def is None:
        raise PipelineError(f"Unknown mode: {ctx.mode}", kind="unknown")

    skip = mode_def.skip_steps

    steps: list[tuple[str, Any]] = [
        ("setup", _step_setup),
        ("check_deps", _step_check_deps),
        ("transcribe", _step_transcribe),
        ("analyze", _step_analyze),
        ("execute", _step_execute),
        ("subtitle", _step_subtitle),
        ("finalize", _step_finalize),
    ]

    for step_name, step_fn in steps:
        if ctx.cancelled:
            await _set_cancelled(ctx, tm)
            emit("task_update", {"task_id": ctx.task_id, "status": "cancelled"})
            return

        if step_name in skip:
            continue

        emit("task_update", {
            "task_id": ctx.task_id,
            "step": step_name,
            "status": "running",
        })
        await tm.update_task(ctx.task_id, pipeline_step=step_name, status="running")

        try:
            await step_fn(ctx, tm, asr, ffmpeg, emit)
        except PipelineError as e:
            await _set_error(ctx, tm, e.kind, str(e))
            emit("task_update", {
                "task_id": ctx.task_id,
                "status": "failed",
                "error_kind": e.kind,
                "error_message": str(e),
            })
            return
        except Exception as e:
            kind = _classify_error(e)
            await _set_error(ctx, tm, kind, str(e))
            emit("task_update", {
                "task_id": ctx.task_id,
                "status": "failed",
                "error_kind": kind,
                "error_message": str(e),
            })
            return

    await tm.update_task(ctx.task_id, status="succeeded", pipeline_step="done")
    emit("task_update", {"task_id": ctx.task_id, "status": "succeeded"})


# ======================================================================
# Step implementations
# ======================================================================

async def _step_setup(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    ctx.task_dir.mkdir(parents=True, exist_ok=True)

    if not ctx.source_video_path.exists():
        raise PipelineError(
            f"Source video not found: {ctx.source_video_path}", kind="format"
        )

    ctx.source_duration_sec = await ffmpeg.get_duration(ctx.source_video_path)
    if ctx.source_duration_sec <= 0:
        raise PipelineError("Cannot determine video duration", kind="format")

    if ctx.source_duration_sec > MAX_VIDEO_DURATION_SEC:
        raise PipelineError(
            f"Video duration {ctx.source_duration_sec:.0f}s exceeds "
            f"limit {MAX_VIDEO_DURATION_SEC:.0f}s",
            kind="duration",
        )

    await tm.update_task(ctx.task_id, source_duration_sec=ctx.source_duration_sec)

    cost = estimate_cost(ctx.mode, ctx.source_duration_sec)
    ctx.cost_items = cost.items
    await tm.update_task(ctx.task_id, cost={"total_cny": cost.total_cny, "items": cost.items})


async def _step_check_deps(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    if not ffmpeg.available:
        raise PipelineError("FFmpeg not found. Install ffmpeg >= 4.0.", kind="dependency")

    mode_def = MODES_BY_ID.get(ctx.mode)
    if mode_def and mode_def.requires_api_key and asr is None:
        raise PipelineError(
            "DashScope API Key not configured. Go to Settings.", kind="auth"
        )


async def _step_transcribe(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    """Transcribe video via Paraformer, with SHA256 cache check."""
    source_hash = await _compute_file_hash(ctx.source_video_path)

    cached = await tm.get_transcript_by_hash(source_hash)
    if cached and cached.get("status") == "succeeded" and cached.get("sentences"):
        ctx.transcript_id = cached["id"]
        ctx.transcript_sentences = cached["sentences"]
        ctx.transcript_text = cached.get("full_text", "")
        await tm.update_task(ctx.task_id, transcript_id=cached["id"])
        logger.info("Transcript cache hit for %s", source_hash[:8])
        return

    transcript_rec = await tm.get_or_create_transcript_for_hash(
        source_hash=source_hash,
        source_path=str(ctx.source_video_path),
        source_name=ctx.source_video_path.name,
        duration_sec=ctx.source_duration_sec,
    )
    ctx.transcript_id = transcript_rec["id"]
    await tm.update_task(ctx.task_id, transcript_id=transcript_rec["id"])

    from clip_asr_client import AsrError
    from clip_dashscope_upload import DashScopeUploadError, paraformer_file_url_is_public

    effective_url = (ctx.source_url or "").strip()
    if not effective_url or not paraformer_file_url_is_public(effective_url):
        if asr is None:
            raise PipelineError(
                "DashScope API Key not configured. Go to Settings.", kind="auth"
            )
        emit("task_update", {
            "task_id": ctx.task_id,
            "step": "transcribe",
            "status": "running",
            "detail": "dashscope_upload",
        })
        try:
            effective_url = await asr.upload_local_source(ctx.source_video_path)
        except DashScopeUploadError as e:
            await tm.update_transcript(
                transcript_rec["id"], status="failed", error_message=str(e),
            )
            raise PipelineError(
                f"DashScope temp upload failed: {e}",
                kind=getattr(e, "kind", "config"),
            ) from e

    emit("task_update", {
        "task_id": ctx.task_id,
        "step": "transcribe",
        "status": "running",
        "detail": "paraformer",
    })
    try:
        result = await asr.transcribe(effective_url)
    except AsrError as e:
        await tm.update_transcript(transcript_rec["id"], status="failed", error_message=str(e))
        raise PipelineError(str(e), kind=e.kind) from e

    sentences_dicts = [
        {"start": s.start, "end": s.end, "text": s.text, "confidence": s.confidence}
        for s in result.sentences
    ]
    ctx.transcript_sentences = sentences_dicts
    ctx.transcript_text = result.full_text

    await tm.update_transcript(
        transcript_rec["id"],
        status="succeeded",
        sentences=sentences_dicts,
        full_text=result.full_text,
        language=result.language,
        api_task_id=result.api_task_id,
    )


async def _step_analyze(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    """AI analysis step — mode-specific Qwen calls."""
    if not ctx.transcript_sentences or not ctx.transcript_text:
        raise PipelineError("No transcript available for analysis", kind="unknown")

    from clip_asr_client import AsrError

    try:
        if ctx.mode == "highlight_extract":
            ctx.segments = await asr.analyze_highlights(
                ctx.transcript_text,
                ctx.transcript_sentences,
                flavor=ctx.params.get("flavor", ""),
                target_count=ctx.params.get("target_count", 5),
                target_duration=ctx.params.get("target_duration", 30),
                total_duration_sec=ctx.source_duration_sec or 0,
            )
        elif ctx.mode == "topic_split":
            ctx.segments = await asr.analyze_topics(
                ctx.transcript_text,
                ctx.transcript_sentences,
                target_segment_duration=ctx.params.get("target_segment_duration", 180),
            )
        elif ctx.mode == "talking_polish":
            ctx.segments = await asr.analyze_filler(
                ctx.transcript_text,
                ctx.transcript_sentences,
            )
    except AsrError as e:
        raise PipelineError(str(e), kind=e.kind) from e

    if ctx.segments:
        await tm.update_task(ctx.task_id, segments=ctx.segments)


async def _step_execute(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    """Execute ffmpeg operations based on mode."""
    from clip_ffmpeg_ops import FFmpegError

    output_dir = ctx.output_dir or (ctx.task_dir / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = ctx.params.get("output_format", "mp4")
    if ext not in ("mp4", "mkv"):
        ext = "mp4"

    try:
        if ctx.mode == "silence_clean":
            audio_path = ctx.task_dir / "audio.wav"
            await ffmpeg.extract_audio(ctx.source_video_path, audio_path)

            silence = await ffmpeg.detect_silence(
                audio_path,
                threshold_db=ctx.params.get("threshold_db", -40.0),
                min_silence_sec=ctx.params.get("min_silence_sec", 0.5),
                padding_sec=ctx.params.get("padding_sec", 0.1),
            )
            ctx.silence_segments = silence
            logger.info(
                "silence_clean [%s]: detected %d silence segments (threshold_db=%.1f)",
                ctx.task_id, len(silence),
                ctx.params.get("threshold_db", -40.0),
            )

            if not silence:
                out_file = output_dir / f"cleaned_{ctx.task_id}.{ext}"
                await asyncio.to_thread(
                    shutil.copy2, str(ctx.source_video_path), str(out_file)
                )
                ctx.output_path = out_file
                ctx.output_duration_sec = ctx.source_duration_sec
                ctx.params["_no_silence_detected"] = True
                return

            total_silence_sec = sum(s.get("duration", 0) for s in silence)
            logger.info(
                "silence_clean [%s]: total silence %.1fs, removing...",
                ctx.task_id, total_silence_sec,
            )

            out_file = output_dir / f"cleaned_{ctx.task_id}.{ext}"
            await ffmpeg.remove_segments(
                ctx.source_video_path, silence, out_file,
                total_duration=ctx.source_duration_sec,
            )
            ctx.output_path = out_file
            ctx.output_duration_sec = await ffmpeg.get_duration(out_file)

        elif ctx.mode == "highlight_extract":
            if not ctx.segments:
                raise PipelineError("No highlight segments from AI analysis", kind="unknown")
            out_file = output_dir / f"highlights_{ctx.task_id}.{ext}"
            await ffmpeg.cut_segments(ctx.source_video_path, ctx.segments, out_file)
            ctx.output_path = out_file

        elif ctx.mode == "topic_split":
            if not ctx.segments:
                raise PipelineError("No topic segments from AI analysis", kind="unknown")
            outputs: list[Path] = []
            for i, seg in enumerate(ctx.segments):
                seg_file = output_dir / f"topic_{i:02d}_{ctx.task_id}.{ext}"
                await ffmpeg.cut_segments(
                    ctx.source_video_path,
                    [{"start": seg["start_sec"], "end": seg["end_sec"]}],
                    seg_file,
                )
                outputs.append(seg_file)
            if outputs:
                ctx.output_path = outputs[0]
            if len(outputs) > 1:
                import zipfile
                zip_path = output_dir / f"topics_{ctx.task_id}.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                    for p in outputs:
                        zf.write(p, p.name)
                ctx.params["_topics_zip"] = str(zip_path)
                ctx.params["_topic_files"] = [str(p) for p in outputs]

        elif ctx.mode == "talking_polish":
            remove_list: list[dict[str, Any]] = []
            if ctx.segments:
                # Map UI / API toggle names to analyze_filler "type" values.
                # Accept both snake_case (Pydantic alias) and camelCase
                # (raw frontend payload via extra="allow") for resilience.
                p = ctx.params
                allow_filler = p.get("remove_filler",
                                     p.get("removeFiller", True))
                allow_stutter = p.get("remove_stutter",
                                      p.get("removeStutter", True))
                allow_repetition = p.get("remove_repetition",
                                         p.get("removeRepetition", True))
                allowed_types: set[str] = set()
                if allow_filler:
                    allowed_types.add("filler")
                if allow_stutter:
                    allowed_types.add("stutter")
                if allow_repetition:
                    allowed_types.add("repeat")
                    allowed_types.add("repetition")
                for seg in ctx.segments:
                    seg_type = (seg.get("type") or "filler").lower()
                    if seg_type not in allowed_types:
                        continue
                    remove_list.append({
                        "start": seg.get("start_sec", 0),
                        "end": seg.get("end_sec", 0),
                    })

            audio_path = ctx.task_dir / "audio.wav"
            await ffmpeg.extract_audio(ctx.source_video_path, audio_path)
            silence = await ffmpeg.detect_silence(
                audio_path,
                threshold_db=ctx.params.get("threshold_db", -40.0),
                min_silence_sec=ctx.params.get("min_silence_sec", 0.3),
                padding_sec=ctx.params.get("padding_sec", 0.05),
            )
            remove_list.extend(silence)

            remove_list.sort(key=lambda s: s["start"])
            merged = _merge_overlapping(remove_list)

            if not merged:
                ctx.output_path = ctx.source_video_path
                ctx.output_duration_sec = ctx.source_duration_sec
                return

            out_file = output_dir / f"polished_{ctx.task_id}.{ext}"
            await ffmpeg.remove_segments(
                ctx.source_video_path, merged, out_file,
                total_duration=ctx.source_duration_sec,
            )
            ctx.output_path = out_file

    except FFmpegError as e:
        raise PipelineError(str(e), kind=e.kind) from e

    if ctx.output_path and ctx.output_path.exists():
        ctx.output_duration_sec = await ffmpeg.get_duration(ctx.output_path)


async def _step_subtitle(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    """Generate SRT and optionally burn subtitles."""
    if not ctx.transcript_sentences:
        return

    from clip_ffmpeg_ops import FFmpegOps

    normalized_segments = None
    if ctx.segments and ctx.mode != "talking_polish":
        normalized_segments = []
        for seg in ctx.segments:
            normalized_segments.append({
                "start": seg.get("start", seg.get("start_sec", 0)),
                "end": seg.get("end", seg.get("end_sec", 0)),
            })

    srt_content = FFmpegOps.generate_srt(
        ctx.transcript_sentences,
        segments=normalized_segments,
    )
    srt_path = ctx.task_dir / f"subtitle_{ctx.task_id}.srt"
    srt_path.write_text(srt_content, encoding="utf-8")
    ctx.subtitle_path = srt_path

    # talking_polish removes variable-length segments, compressing the
    # timeline in ways the original-timestamp SRT cannot track. Burning
    # would produce wildly mis-synced subtitles, so we skip it.
    if ctx.mode == "talking_polish":
        return

    if ctx.params.get("burn_subtitle") and ctx.output_path and ctx.output_path.exists():
        from clip_ffmpeg_ops import FFmpegError
        sub_ext = ctx.params.get("output_format", "mp4")
        if sub_ext not in ("mp4", "mkv"):
            sub_ext = "mp4"
        output_dir = ctx.output_dir or (ctx.task_dir / "output")
        output_dir.mkdir(parents=True, exist_ok=True)
        burned = output_dir / f"subtitled_{ctx.task_id}.{sub_ext}"
        try:
            await ffmpeg.burn_subtitles(ctx.output_path, srt_path, burned)
            ctx.output_path = burned
        except FFmpegError as e:
            logger.warning("Subtitle burn failed (non-fatal): %s", e)


async def _step_finalize(
    ctx: ClipPipelineContext, tm: Any, asr: Any, ffmpeg: Any, emit: EmitFn
) -> None:
    """Final metadata update."""
    updates: dict[str, Any] = {}
    if ctx.output_path:
        updates["output_path"] = str(ctx.output_path)
    if ctx.output_duration_sec:
        updates["output_duration_sec"] = ctx.output_duration_sec
    if ctx.subtitle_path:
        updates["subtitle_path"] = str(ctx.subtitle_path)
    if ctx.cost_items:
        cost = estimate_cost(ctx.mode, ctx.source_duration_sec or 0)
        updates["cost"] = {"total_cny": cost.total_cny, "items": cost.items}
    if (ctx.params.get("_topics_zip") or ctx.params.get("_topic_files")
            or ctx.params.get("_no_silence_detected")):
        updates["params"] = ctx.params

    if updates:
        await tm.update_task(ctx.task_id, **updates)


# ======================================================================
# Helpers
# ======================================================================

async def _compute_file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """SHA256 of the whole file to avoid transcript cache collisions."""
    def _hash_sync() -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while data := f.read(chunk_size):
                h.update(data)
        return h.hexdigest()
    return await asyncio.to_thread(_hash_sync)


async def _set_error(
    ctx: ClipPipelineContext, tm: Any, kind: str, message: str
) -> None:
    hints = get_error_hints(kind)
    ctx.error_kind = kind
    ctx.error_message = message
    ctx.error_hints = hints.get("hints_zh", [])
    await tm.update_task(
        ctx.task_id,
        status="failed",
        error_kind=kind,
        error_message=message,
        error_hints=ctx.error_hints,
    )


async def _set_cancelled(ctx: ClipPipelineContext, tm: Any) -> None:
    ctx.error_kind = "cancelled"
    ctx.error_message = "Task was cancelled"
    ctx.error_hints = []
    await tm.update_task(
        ctx.task_id,
        status="cancelled",
        error_kind="cancelled",
        error_message="Task was cancelled",
        error_hints=[],
    )


def _classify_error(exc: Exception) -> str:
    """Map exception types to error_kind categories."""
    msg = str(exc).lower()
    if "timeout" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg:
        return "network"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "auth"
    if "ffmpeg" in msg or "dependency" in msg:
        return "dependency"
    if "moderation" in msg or "sensitive" in msg:
        return "moderation"
    return "unknown"


def _merge_overlapping(intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge overlapping/adjacent intervals."""
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x["start"])
    merged: list[dict[str, Any]] = [dict(sorted_ivs[0])]
    for iv in sorted_ivs[1:]:
        if iv["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], iv["end"])
        else:
            merged.append(dict(iv))
    return merged
