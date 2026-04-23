"""7-step pipeline for subtitle-craft (4 modes + step 4.5 conditional trigger).

Step layout per ``docs/subtitle-craft-plan.md`` §3.4 (post-patch P-1):

| step | name                  | auto_subtitle | translate | repair | burn |
|------|-----------------------|:-------------:|:---------:|:------:|:----:|
| 1    | setup_environment     |      ✓        |    ✓      |   ✓    |  ✓   |
| 2    | estimate_cost         |      ✓        |    ✓      |   ✓    |  ✓   |
| 3    | prepare_assets        |      ✓        |    -      |   -    |  ✓   |
| 4    | asr_or_load           |      ✓        |    ✓      |   ✓    |  ✓   |
| 4.5  | identify_characters   |      ◐        |    -      |   -    |  -   |
| 5    | translate_or_repair   |      -        |    ✓      |   ✓    |  -   |
| 6    | render_output         |      ✓        |    ✓      |   ✓    |  ✓   |
| 7    | burn_or_finalize      |      ✓        |    ✓      |   ✓    |  ✓   |

Legend: ✓ runs, - skipped, ◐ runs only when ALL of these are true:
``mode == 'auto_subtitle'`` AND ``params['diarization_enabled']`` AND
``params['character_identify_enabled']`` AND ``len(speaker_ids) > 0``.
Failure of step 4.5 is **non-fatal** (P1-12): we keep the original
``SPEAKER_xx`` labels and continue.

Architectural rules (red-line guardrails baked in):

- ``error_kind`` written to ``tasks.error_kind`` is **always** one of the 9
  canonical kinds in ``ERROR_HINTS`` (P-2). Vendor errors come pre-mapped
  from ``SubtitleAsrClient`` via ``map_vendor_kind_to_error_kind``; native
  exceptions go through ``_classify_error`` which only returns canonical
  kinds.
- **No cross-plugin dispatch references** anywhere in this module.
  ``assets_bus`` stays read-only in v1.0; ``tasks.origin_*`` columns stay
  NULL. Phase 0 grep guard verifies the v2.0-only feature names do not
  leak into v1.0 code paths.
- Cancel is **cooperative**: the pipeline checks
  ``tm.is_canceled(task_id)`` before and after each step, and the AsrClient
  poll loop is given a ``cancel_check`` callback so DashScope tasks can be
  cut short mid-poll.
- SSE event name is fixed to ``task_update`` (red line #21). Payload always
  includes ``{task_id, status, mode, pipeline_step}`` and conditionally
  ``{error_kind, error_message, error_hints, progress}``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

from subtitle_models import (
    ALLOWED_ERROR_KINDS,
    MAX_AUDIO_DURATION_SEC,
    MODES_BY_ID,
    estimate_cost,
    get_error_hints,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Any]
"""Type for the SSE emit callback. Always called with event_name=='task_update'."""


# ---------------------------------------------------------------------------
# Public Pipeline Context (24-field dataclass per Pixelle A2)
# ---------------------------------------------------------------------------


@dataclass
class SubtitlePipelineContext:
    """Mutable per-task context threaded through every pipeline step.

    Lifetime: created by the route handler, populated step-by-step, persisted
    via ``tm.update_task`` after meaningful state changes. Never serialized
    to JSON in full; the ``metadata.json`` write at the end of step 7 picks
    fields explicitly per §8.4 contract.
    """

    # --- identity (set on construction) ---
    task_id: str
    mode: str
    params: dict[str, Any]
    task_dir: Path

    # --- inputs (filled in step 1 / step 3) ---
    source_kind: str = ""  # "video" | "audio" | "srt"
    source_path: Path | None = None
    source_url: str = ""  # public preview URL (auto_subtitle/burn only)
    source_duration_sec: float | None = None
    source_lang: str = ""
    target_lang: str = ""

    # --- step 4 outputs ---
    transcript_id: str | None = None
    transcript_words: list[dict[str, Any]] | None = None
    transcript_full_text: str | None = None
    transcript_language: str = ""
    speaker_ids: set[str] = field(default_factory=set)
    api_task_id: str = ""

    # --- step 4.5 outputs ---
    speaker_map: dict[str, str] = field(default_factory=dict)
    speaker_map_failed: bool = False

    # --- step 5/6 outputs ---
    cues: list[dict[str, Any]] | None = None  # serialized SRTCue dicts
    repair_stats: dict[str, int] | None = None
    output_srt_path: Path | None = None
    output_vtt_path: Path | None = None

    # --- step 7 outputs ---
    output_video_path: Path | None = None

    # --- accounting ---
    cost_items: list[dict[str, Any]] = field(default_factory=list)

    # --- error state (set by _set_error) ---
    error_kind: str | None = None
    error_message: str | None = None
    error_hints: list[str] | None = None


class PipelineError(Exception):
    """Raised by step functions to abort with a canonical error_kind.

    ``kind`` MUST be one of ``ALLOWED_ERROR_KINDS`` — enforced by the
    constructor below so an out-of-band kind is surfaced immediately
    rather than silently rewritten by ``_set_error``.
    """

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        if kind not in ALLOWED_ERROR_KINDS:
            logger.warning("PipelineError: non-canonical kind %r; coercing to 'unknown'", kind)
            kind = "unknown"
        self.kind = kind


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    *,
    emit: EmitFn,
    ffmpeg_path: str | None = None,
) -> None:
    """Execute the 7-step subtitle-craft pipeline.

    Args:
        ctx: Per-task context (filled in by the caller for fields known at
            request time; the rest is populated by step functions).
        tm: ``SubtitleTaskManager`` instance.
        asr: ``SubtitleAsrClient`` instance (may be ``None`` for offline
            modes ``repair``/``burn`` — checked in step 4 only when needed).
        emit: SSE emit callback. Always invoked with ``event="task_update"``.
        ffmpeg_path: Optional explicit ffmpeg binary path (else autodiscover).

    Raises:
        Nothing — every error path is captured and recorded via ``_set_error``.
    """
    mode_def = MODES_BY_ID.get(ctx.mode)
    if mode_def is None:
        await _set_error(ctx, tm, "format", f"Unknown mode: {ctx.mode}")
        await _emit_terminal(emit, ctx, status="failed")
        return

    skip = mode_def.skip_steps

    steps: list[tuple[str, Callable[..., Awaitable[None]]]] = [
        ("setup_environment", _step_setup_environment),
        ("estimate_cost", _step_estimate_cost),
        ("prepare_assets", _step_prepare_assets),
        ("asr_or_load", _step_asr_or_load),
        ("identify_characters", _step_identify_characters),
        ("translate_or_repair", _step_translate_or_repair),
        ("render_output", _step_render_output),
        ("burn_or_finalize", _step_burn_or_finalize),
    ]

    for step_name, step_fn in steps:
        # Cooperative cancel check (between every step).
        if tm.is_canceled(ctx.task_id):
            await _set_error(ctx, tm, "unknown", "Task was canceled by user")
            await _emit_terminal(emit, ctx, status="canceled")
            return

        # Mode-driven skip (auto_subtitle skip_steps for translate/repair etc).
        if step_name in skip:
            continue

        # Step 4.5 conditional trigger (3-way AND — see module docstring).
        if step_name == "identify_characters" and not _should_identify_characters(ctx):
            continue

        emit(
            "task_update",
            {
                "task_id": ctx.task_id,
                "mode": ctx.mode,
                "pipeline_step": step_name,
                "status": "running",
            },
        )
        await tm.update_task(ctx.task_id, pipeline_step=step_name, status="running")

        try:
            await step_fn(ctx, tm, asr, emit, ffmpeg_path=ffmpeg_path)
        except PipelineError as e:
            # P1-12: step 4.5 failure is non-fatal — degrade and continue.
            if step_name == "identify_characters":
                logger.info(
                    "identify_characters failed for %s (kind=%s, msg=%s); "
                    "keeping original SPEAKER_xx labels",
                    ctx.task_id,
                    e.kind,
                    e,
                )
                ctx.speaker_map_failed = True
                continue
            await _set_error(ctx, tm, e.kind, str(e))
            await _emit_terminal(emit, ctx, status="failed")
            return
        except Exception as e:  # noqa: BLE001 — last-resort catch-all
            if step_name == "identify_characters":
                logger.warning(
                    "identify_characters unexpected failure for %s: %s",
                    ctx.task_id,
                    e,
                )
                ctx.speaker_map_failed = True
                continue
            kind = _classify_error(e)
            await _set_error(ctx, tm, kind, str(e))
            await _emit_terminal(emit, ctx, status="failed")
            return

    await tm.update_task(ctx.task_id, status="succeeded", pipeline_step="done")
    await _emit_terminal(emit, ctx, status="succeeded")


def _should_identify_characters(ctx: SubtitlePipelineContext) -> bool:
    """Step 4.5 trigger: 3 conditions ALL true (per plan §3.1 mode 1)."""
    if ctx.mode != "auto_subtitle":
        return False
    if not bool(ctx.params.get("diarization_enabled")):
        return False
    if not bool(ctx.params.get("character_identify_enabled")):
        return False
    if not ctx.speaker_ids:
        # Step 4 produced no speakers; nothing to identify.
        return False
    return True


async def _emit_terminal(emit: EmitFn, ctx: SubtitlePipelineContext, *, status: str) -> None:
    payload: dict[str, Any] = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "status": status,
        "pipeline_step": "done" if status == "succeeded" else "error",
    }
    if ctx.error_kind:
        payload["error_kind"] = ctx.error_kind
        payload["error_message"] = ctx.error_message
        payload["error_hints"] = ctx.error_hints
    emit("task_update", payload)


# ---------------------------------------------------------------------------
# Step 1 · setup_environment
# ---------------------------------------------------------------------------


async def _step_setup_environment(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    ctx.task_dir.mkdir(parents=True, exist_ok=True)

    # Validate input file exists for modes that need a local source.
    if ctx.source_path is not None and not ctx.source_path.exists():
        raise PipelineError(f"Source file not found: {ctx.source_path}", kind="format")

    # Duration probe — only meaningful for video/audio modes.
    if (
        ctx.mode in {"auto_subtitle", "burn"}
        and ctx.source_path is not None
        and ctx.source_duration_sec is None
    ):
        try:
            ctx.source_duration_sec = await _probe_duration(
                ctx.source_path, ffmpeg_path=ffmpeg_path
            )
        except subprocess.CalledProcessError as e:
            # ffprobe may be missing — degrade gracefully (duration optional
            # for cost preview). Pipeline continues.
            logger.info("ffprobe unavailable / failed: %s", e)

    if ctx.source_duration_sec is not None and ctx.source_duration_sec > MAX_AUDIO_DURATION_SEC:
        raise PipelineError(
            f"Source duration {ctx.source_duration_sec:.0f}s exceeds the "
            f"Paraformer-v2 limit ({MAX_AUDIO_DURATION_SEC:.0f}s); please "
            f"trim the file with ffmpeg first.",
            kind="duration",
        )

    if ctx.source_duration_sec is not None:
        await tm.update_task(ctx.task_id, source_duration_sec=ctx.source_duration_sec)


# ---------------------------------------------------------------------------
# Step 2 · estimate_cost
# ---------------------------------------------------------------------------


async def _step_estimate_cost(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    char_count = 0
    if ctx.mode == "translate":
        # If we already loaded SRT in step 4, use its char count; otherwise
        # the route handler should have stuffed an estimate into params.
        char_count = int(ctx.params.get("estimated_char_count", 0))

    speaker_count = int(ctx.params.get("estimated_speaker_count", 0))
    if ctx.speaker_ids:
        speaker_count = len(ctx.speaker_ids)

    preview = estimate_cost(
        ctx.mode,
        duration_sec=ctx.source_duration_sec or 0.0,
        char_count=char_count,
        translation_model=ctx.params.get("translation_model", "qwen-mt-flash"),
        character_identify=bool(ctx.params.get("character_identify_enabled")),
        speaker_count=speaker_count,
    )
    ctx.cost_items = preview.items
    await tm.update_task(
        ctx.task_id,
        cost={"total_cny": preview.total_cny, "items": preview.items},
    )


# ---------------------------------------------------------------------------
# Step 3 · prepare_assets
# ---------------------------------------------------------------------------


async def _step_prepare_assets(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    """Audio-only prep for auto_subtitle; pass-through for burn.

    For ``auto_subtitle``: extract 16 kHz mono WAV from video → store
    alongside the source so the upload preview route can serve it via the
    public preview URL set by the caller.

    For ``burn``: nothing to do — caller supplies the SRT and video paths.
    """
    if ctx.mode != "auto_subtitle":
        return
    if ctx.source_path is None or ctx.source_kind != "video":
        # source_kind == "audio" → nothing to extract.
        return

    out_wav = ctx.task_dir / "audio_16k_mono.wav"
    if out_wav.exists():
        return

    ffmpeg = _find_ffmpeg(ffmpeg_path)
    args = [
        ffmpeg,
        "-y",
        "-i",
        str(ctx.source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]

    def _run() -> int:
        proc = subprocess.run(  # noqa: S603 — args is list, no shell
            args, capture_output=True, timeout=600
        )
        return proc.returncode

    rc = await asyncio.to_thread(_run)
    if rc != 0 or not out_wav.exists():
        raise PipelineError(
            f"ffmpeg audio extraction failed (rc={rc}); "
            f"check that ffmpeg is installed and the source is a valid video.",
            kind="dependency",
        )


# ---------------------------------------------------------------------------
# Step 4 · asr_or_load
# ---------------------------------------------------------------------------


async def _step_asr_or_load(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    """Run Paraformer (auto_subtitle) or load existing SRT (translate/repair/burn).

    For ``auto_subtitle``: SHA256 of first 64KB → cache check; on miss,
    POST Paraformer task, poll, normalize, persist transcripts row.
    """
    if ctx.mode == "auto_subtitle":
        await _asr_paraformer(ctx, tm, asr)
    else:
        await _load_srt_input(ctx, tm)


async def _asr_paraformer(ctx: SubtitlePipelineContext, tm: Any, asr: Any) -> None:
    if asr is None:
        raise PipelineError(
            "DashScope API Key not configured. Go to Settings → API Key.",
            kind="auth",
        )
    if not ctx.source_url:
        raise PipelineError(
            "source_url is required (Paraformer-v2 needs a public URL)",
            kind="format",
        )
    if ctx.source_path is None:
        raise PipelineError("source_path is required to compute the cache key", kind="format")

    source_hash = await _compute_file_hash(ctx.source_path)

    cached = await tm.get_transcript_by_hash(source_hash)
    if cached and cached.get("status") == "succeeded" and cached.get("words"):
        ctx.transcript_id = cached["id"]
        ctx.transcript_words = cached["words"]
        ctx.transcript_full_text = cached.get("full_text", "")
        ctx.transcript_language = cached.get("language", "")
        ctx.speaker_ids = {
            w["speaker_id"] for w in (cached.get("words") or []) if w.get("speaker_id")
        }
        await tm.update_task(ctx.task_id, transcript_id=cached["id"])
        logger.info("Transcript cache hit for %s", source_hash[:8])
        return

    transcript_rec = await tm.create_transcript(
        source_hash=source_hash,
        source_path=str(ctx.source_path),
        source_name=ctx.source_path.name,
        duration_sec=ctx.source_duration_sec,
    )
    ctx.transcript_id = transcript_rec["id"]
    await tm.update_task(ctx.task_id, transcript_id=transcript_rec["id"])

    from subtitle_asr_client import AsrError

    cancel_check = _make_cancel_check(tm, ctx.task_id)
    try:
        result = await asr.transcribe(
            ctx.source_url,
            language_hints=ctx.params.get("language_hints") or None,
            diarization_enabled=bool(ctx.params.get("diarization_enabled")),
            channel_id=ctx.params.get("channel_id") or [0],
            cancel_check=cancel_check,
        )
    except AsrError as e:
        await tm.update_transcript(transcript_rec["id"], status="failed")
        raise PipelineError(str(e), kind=e.kind) from e

    words_serialized: list[dict[str, Any]] = []
    speaker_ids: set[str] = set()
    for w in result.all_words():
        if w.speaker_id:
            speaker_ids.add(w.speaker_id)
        words_serialized.append(
            {
                "text": w.text,
                "start_ms": w.start_ms,
                "end_ms": w.end_ms,
                "punctuation": w.punctuation,
                "speaker_id": w.speaker_id,
            }
        )

    ctx.transcript_words = words_serialized
    ctx.transcript_full_text = result.full_text
    ctx.transcript_language = result.language
    ctx.speaker_ids = speaker_ids
    ctx.api_task_id = result.api_task_id

    await tm.update_transcript(
        transcript_rec["id"],
        status="succeeded",
        duration_sec=result.duration_sec,
        words=words_serialized,
        full_text=result.full_text,
        language=result.language,
        speaker_count=result.speaker_count,
        channel_count=result.channel_count,
        raw_payload=result.raw_payload,
    )
    await tm.update_task(
        ctx.task_id,
        paraformer_task_id=result.api_task_id or "",
    )


async def _load_srt_input(ctx: SubtitlePipelineContext, tm: Any) -> None:
    """For translate/repair/burn modes: load the user-supplied SRT."""
    from subtitle_renderer import parse_srt

    srt_path_raw = ctx.params.get("srt_path")
    if not srt_path_raw:
        raise PipelineError("srt_path is required for translate/repair/burn modes", kind="format")
    srt_path = Path(srt_path_raw)
    if not srt_path.exists():
        raise PipelineError(f"SRT file not found: {srt_path}", kind="format")
    try:
        text = srt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise PipelineError(f"SRT must be UTF-8 encoded: {e}", kind="format") from e

    cues = parse_srt(text)
    if not cues:
        raise PipelineError(f"No valid cues parsed from SRT: {srt_path}", kind="format")
    ctx.cues = [
        {
            "index": c.index,
            "start": c.start,
            "end": c.end,
            "text": c.text,
            "speaker_id": c.speaker_id,
        }
        for c in cues
    ]
    ctx.transcript_full_text = "\n".join(c.text for c in cues)


# ---------------------------------------------------------------------------
# Step 4.5 · identify_characters (conditional, non-fatal on error)
# ---------------------------------------------------------------------------


async def _step_identify_characters(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    if asr is None:
        raise PipelineError("API key not configured for character identification", kind="auth")
    if not ctx.transcript_words:
        return

    # Build per-speaker sample text (first ~200 chars of their utterances).
    samples: dict[str, str] = {}
    for w in ctx.transcript_words:
        sid = w.get("speaker_id")
        if not sid:
            continue
        text = w.get("text", "")
        if w.get("punctuation"):
            text += w["punctuation"]
        existing = samples.get(sid, "")
        if len(existing) < 250:
            samples[sid] = (existing + text)[:250]

    if not samples:
        return

    from subtitle_asr_client import AsrError

    try:
        speaker_map = await asr.identify_characters(
            samples,
            context_hint=str(ctx.params.get("context_hint", "")),
        )
    except AsrError as e:
        # Re-raise as PipelineError; outer loop demotes step 4.5 errors to non-fatal.
        raise PipelineError(str(e), kind=e.kind) from e

    if not speaker_map:
        ctx.speaker_map_failed = True
        return

    ctx.speaker_map = speaker_map
    if ctx.transcript_id:
        await tm.update_transcript(ctx.transcript_id, speaker_map=speaker_map)


# ---------------------------------------------------------------------------
# Step 5 · translate_or_repair
# ---------------------------------------------------------------------------


async def _step_translate_or_repair(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    if ctx.mode == "translate":
        await _do_translate(ctx, asr)
    elif ctx.mode == "repair":
        await _do_repair(ctx)
    # auto_subtitle / burn: no-op


async def _do_translate(ctx: SubtitlePipelineContext, asr: Any) -> None:
    if asr is None:
        raise PipelineError("DashScope API Key not configured for translation", kind="auth")
    if not ctx.cues:
        raise PipelineError("No cues to translate", kind="format")

    source_lang = ctx.source_lang or ctx.params.get("source_lang") or "auto"
    target_lang = ctx.target_lang or ctx.params.get("target_lang") or ""
    if not target_lang:
        raise PipelineError("target_lang is required for translate mode", kind="format")

    # Send cue texts as one chunk per cue (preserves 1:1 alignment, P1-6).
    chunks = [c["text"] for c in ctx.cues]

    from subtitle_asr_client import AsrError

    try:
        translated = await asr.translate_batch(
            chunks,
            source_lang=source_lang,
            target_lang=target_lang,
            model=ctx.params.get("translation_model", "qwen-mt-flash"),
        )
    except AsrError as e:
        raise PipelineError(str(e), kind=e.kind) from e

    bilingual = bool(ctx.params.get("bilingual"))
    new_cues: list[dict[str, Any]] = []
    for cue, t in zip(ctx.cues, translated, strict=False):
        text = f"{cue['text']}\n{t}".strip() if bilingual and t else (t or cue["text"])
        new_cues.append({**cue, "text": text})
    ctx.cues = new_cues


async def _do_repair(ctx: SubtitlePipelineContext) -> None:
    if not ctx.cues:
        raise PipelineError("No cues to repair", kind="format")

    from subtitle_renderer import SRTCue, repair_srt_cues

    cue_objs = [
        SRTCue(
            index=c["index"],
            start=c["start"],
            end=c["end"],
            text=c["text"],
            speaker_id=c.get("speaker_id"),
        )
        for c in ctx.cues
    ]
    repaired, stats = repair_srt_cues(cue_objs)
    ctx.cues = [
        {
            "index": c.index,
            "start": c.start,
            "end": c.end,
            "text": c.text,
            "speaker_id": c.speaker_id,
        }
        for c in repaired
    ]
    ctx.repair_stats = stats


# ---------------------------------------------------------------------------
# Step 6 · render_output (SRT/VTT serialization)
# ---------------------------------------------------------------------------


async def _step_render_output(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    from subtitle_renderer import (
        SRTCue,
        cues_to_srt,
        cues_to_vtt,
        words_to_srt_cues,
    )

    # If we came from auto_subtitle, ctx.cues is None — pack words to cues now.
    if ctx.cues is None:
        if not ctx.transcript_words:
            raise PipelineError(
                "Nothing to render: no transcript words and no cues",
                kind="unknown",
            )
        from subtitle_asr_client import AsrWord

        word_objs = [
            AsrWord(
                text=w["text"],
                start_ms=int(w["start_ms"]),
                end_ms=int(w["end_ms"]),
                punctuation=w.get("punctuation", ""),
                speaker_id=w.get("speaker_id"),
            )
            for w in ctx.transcript_words
        ]
        cue_objs = words_to_srt_cues(word_objs)
        # Apply speaker_map if step 4.5 produced one (P1-12 success path).
        if ctx.speaker_map:
            cue_objs = _apply_speaker_map(cue_objs, ctx.speaker_map)
        ctx.cues = [
            {
                "index": c.index,
                "start": c.start,
                "end": c.end,
                "text": c.text,
                "speaker_id": c.speaker_id,
            }
            for c in cue_objs
        ]
    else:
        cue_objs = [
            SRTCue(
                index=c["index"],
                start=c["start"],
                end=c["end"],
                text=c["text"],
                speaker_id=c.get("speaker_id"),
            )
            for c in ctx.cues
        ]

    out_dir = ctx.task_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / f"subtitle_{ctx.task_id}.srt"
    vtt_path = out_dir / f"subtitle_{ctx.task_id}.vtt"
    srt_path.write_text(cues_to_srt(cue_objs), encoding="utf-8")
    vtt_path.write_text(cues_to_vtt(cue_objs), encoding="utf-8")
    ctx.output_srt_path = srt_path
    ctx.output_vtt_path = vtt_path

    await tm.update_task(
        ctx.task_id,
        output_srt_path=str(srt_path),
        output_vtt_path=str(vtt_path),
    )


def _apply_speaker_map(cues: list[Any], mapping: dict[str, str]) -> list[Any]:
    """Prepend ``[NAME] `` to each cue's text using ``speaker_map`` (P1-12)."""
    from dataclasses import replace

    out = []
    for c in cues:
        if c.speaker_id and c.speaker_id in mapping:
            label = mapping[c.speaker_id]
            new_text = f"[{label}] {c.text}"
            out.append(replace(c, text=new_text))
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Step 7 · burn_or_finalize
# ---------------------------------------------------------------------------


async def _step_burn_or_finalize(
    ctx: SubtitlePipelineContext,
    tm: Any,
    asr: Any,
    emit: EmitFn,
    *,
    ffmpeg_path: str | None,
) -> None:
    if ctx.mode == "burn":
        await _do_burn(ctx, tm, ffmpeg_path=ffmpeg_path)

    # Always: write metadata.json + persist final paths to tasks row.
    await _write_metadata(ctx, tm)


async def _do_burn(
    ctx: SubtitlePipelineContext,
    tm: Any,
    *,
    ffmpeg_path: str | None,
) -> None:
    if ctx.source_path is None or not ctx.source_path.exists():
        raise PipelineError("Source video required for burn mode", kind="format")
    if ctx.output_srt_path is None or not ctx.output_srt_path.exists():
        raise PipelineError(
            "No SRT to burn (step 6 must have produced subtitle_*.srt)",
            kind="unknown",
        )

    style = ctx.params.get("subtitle_style", "default")
    burn_engine = ctx.params.get("burn_engine", "ass").lower()
    out_video = ctx.task_dir / f"burned_{ctx.task_id}.mp4"

    from subtitle_renderer import burn_subtitles_ass, burn_subtitles_html

    try:
        if burn_engine == "html":
            await burn_subtitles_html(
                ctx.source_path,
                ctx.output_srt_path,
                out_video,
                style=style,
                ffmpeg_path=ffmpeg_path,
                fallback_on_error=True,  # always degrade to ASS (P1-13)
            )
        else:
            await burn_subtitles_ass(
                ctx.source_path,
                ctx.output_srt_path,
                out_video,
                style=style,
                ffmpeg_path=ffmpeg_path,
            )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise PipelineError(f"ffmpeg burn failed: {stderr[:400]}", kind="dependency") from e
    except FileNotFoundError as e:
        raise PipelineError(str(e), kind="dependency") from e

    if not out_video.exists():
        raise PipelineError("ffmpeg returned 0 but output file is missing", kind="unknown")

    ctx.output_video_path = out_video
    await tm.update_task(ctx.task_id, output_video_path=str(out_video))


async def _write_metadata(ctx: SubtitlePipelineContext, tm: Any) -> None:
    """Persist a metadata.json next to the outputs (per §8.4 contract)."""
    from datetime import datetime

    cost_total = sum(item.get("cost_cny", 0.0) for item in ctx.cost_items)
    metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "params": ctx.params,
        "transcript_id": ctx.transcript_id,
        "outputs": {
            "srt": str(ctx.output_srt_path) if ctx.output_srt_path else None,
            "vtt": str(ctx.output_vtt_path) if ctx.output_vtt_path else None,
            "video": str(ctx.output_video_path) if ctx.output_video_path else None,
        },
        "cost": {"total_cny": round(cost_total, 4), "items": ctx.cost_items},
        "speaker_map": ctx.speaker_map or None,
        "speaker_map_failed": ctx.speaker_map_failed or None,
        "repair_stats": ctx.repair_stats or None,
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }
    meta_path = ctx.task_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _compute_file_hash(path: Path, *, chunk_size: int = 65536) -> str:
    """SHA256 of the first 64 KB — fast, stable fingerprint for cache lookup."""

    def _hash_sync() -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            data = f.read(chunk_size)
            h.update(data)
        return h.hexdigest()

    return await asyncio.to_thread(_hash_sync)


def _make_cancel_check(tm: Any, task_id: str) -> Callable[[], bool]:
    def _check() -> bool:
        return bool(tm.is_canceled(task_id))

    return _check


def _find_ffmpeg(explicit: str | None) -> str:
    if explicit and Path(explicit).exists():
        return explicit
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    raise PipelineError(
        "ffmpeg not found — set ffmpeg_path in Settings or install ffmpeg",
        kind="dependency",
    )


async def _probe_duration(path: Path, *, ffmpeg_path: str | None) -> float | None:
    """Best-effort duration probe via ffprobe (sibling of ffmpeg)."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    ffprobe = str(Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix))
    if not Path(ffprobe).exists():
        ffprobe_on_path = shutil.which("ffprobe")
        if not ffprobe_on_path:
            return None
        ffprobe = ffprobe_on_path

    args = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    def _run() -> tuple[int, bytes]:
        proc = subprocess.run(  # noqa: S603 — args is list, no shell
            args, capture_output=True, timeout=60
        )
        return proc.returncode, proc.stdout

    rc, stdout = await asyncio.to_thread(_run)
    if rc != 0:
        return None
    try:
        return float(stdout.decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return None


async def _set_error(ctx: SubtitlePipelineContext, tm: Any, kind: str, message: str) -> None:
    if kind not in ALLOWED_ERROR_KINDS:
        kind = "unknown"
    hints = get_error_hints(kind)
    ctx.error_kind = kind
    ctx.error_message = message
    ctx.error_hints = list(hints.get("hints_zh", []))
    await tm.update_task_safe(
        ctx.task_id,
        status="failed",
        error_kind=kind,
        error_message=message,
        error_hints=ctx.error_hints,
    )


def _classify_error(exc: Exception) -> str:
    """Best-effort classification of *unexpected* exceptions into 9 kinds.

    Vendor errors arrive pre-mapped via ``AsrError.kind`` (always canonical),
    so this only handles native Python / FastAPI / I/O exceptions that
    escape an except clause.
    """
    if isinstance(exc, FileNotFoundError):
        return "format"
    if isinstance(exc, PermissionError):
        return "format"
    if isinstance(exc, TimeoutError):
        return "timeout"
    msg = str(exc).lower()
    if "timeout" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg:
        return "network"
    if "401" in msg or "403" in msg or "unauthor" in msg:
        return "auth"
    if "ffmpeg" in msg or "playwright" in msg or "dependency" in msg:
        return "dependency"
    if "moderation" in msg or "sensitive" in msg:
        return "moderation"
    return "unknown"
