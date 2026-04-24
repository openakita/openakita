# ruff: noqa: N999
"""8-step pipeline orchestrator + 4 mode-specific sub-pipelines.

The pipeline has the same skeleton as avatar-studio / subtitle-craft so
operators only have to learn the contract once:

    setup_environment → validate_input → prepare_assets →
    dispatch_by_mode → emit_progress → finalize → handoff_prepare →
    handle_exception

``dispatch_by_mode`` short-circuits to the matching sub-pipeline:
:func:`run_source_review_pipeline`, :func:`run_silence_cut_pipeline`,
:func:`run_auto_color_pipeline`, :func:`run_cut_qc_pipeline`. Every
sub-pipeline is a sync function so we can unit-test them without an
event loop; the plugin layer wraps the dispatch in
``loop.run_in_executor`` to keep the FastAPI route non-blocking.

The :class:`PipelineContext` dataclass is the **only** state carried
between steps. It lands directly in the ``tasks`` row when the pipeline
finishes (success or failure) so the Tasks tab can render a complete
audit trail.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footage_gate_ffmpeg import FFmpegError, ffprobe_json, is_hdr_source
from footage_gate_grade import (
    apply_grade,
    auto_grade_for_clip,
    prepare_filter_chain,
)
from footage_gate_models import ERROR_HINTS, MODES, RISK_THRESHOLDS, TONEMAP_CHAIN
from footage_gate_qc import run_qc_with_remux
from footage_gate_review import detect_media_type, review_source_media
from footage_gate_silence import (
    apply_silence_cut,
    compute_non_silent_intervals,
    has_audio_track,
)

logger = logging.getLogger(__name__)


EmitFn = Callable[[str, dict[str, Any]], None]


# ── Pipeline context ─────────────────────────────────────────────────────


@dataclass
class PipelineContext:
    """Runtime state for a single pipeline invocation.

    Mirrors the tasks-row shape (see ``footage_gate_task_manager.SCHEMA_SQL``)
    so the orchestrator can flush the dataclass into the row in one
    ``update_task_safe`` call.
    """

    task_id: str
    mode: str
    input_path: Path
    work_dir: Path
    params: dict[str, Any] = field(default_factory=dict)
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None

    input_kind: str | None = None
    is_hdr_source: bool = False
    duration_input_sec: float = 0.0

    output_path: Path | None = None
    report_path: Path | None = None
    thumbs: list[str] = field(default_factory=list)

    removed_seconds: float = 0.0
    qc_attempts: int = 0
    qc_issues_count: int = 0

    error_kind: str | None = None
    error_message: str | None = None
    error_hints: list[str] = field(default_factory=list)

    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    def to_task_update(self) -> dict[str, Any]:
        """Subset of the dataclass that maps onto ``update_task_safe``."""
        return {
            "input_kind": self.input_kind,
            "is_hdr_source": self.is_hdr_source,
            "params": self.params,
            "output_path": str(self.output_path) if self.output_path else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "thumbs": self.thumbs,
            "duration_input_sec": self.duration_input_sec,
            "removed_seconds": self.removed_seconds,
            "qc_attempts": self.qc_attempts,
            "qc_issues_count": self.qc_issues_count,
            "error_kind": self.error_kind,
            "error_message": self.error_message,
            "error_hints": self.error_hints,
            "completed_at": self.completed_at,
        }


# ── 8-step orchestrator ──────────────────────────────────────────────────


def _no_emit(_event: str, _payload: dict[str, Any]) -> None:
    pass


def setup_environment(ctx: PipelineContext) -> None:
    """Step 1 — make sure the per-task work directory exists."""
    ctx.work_dir.mkdir(parents=True, exist_ok=True)


def validate_input(ctx: PipelineContext) -> None:
    """Step 2 — Pydantic-style validation done by the route + ffprobe probe.

    We only run the ffprobe-side checks here so the route can keep its
    422 path lightweight. Raises :class:`FileNotFoundError` /
    :class:`FFmpegError` so the orchestrator's exception handler can map
    to the proper ``error_kind``.
    """
    if not ctx.input_path.is_file():
        raise FileNotFoundError(f"input not found: {ctx.input_path}")

    media_type = detect_media_type(ctx.input_path)
    if media_type is None:
        raise ValueError(f"unsupported media type for {ctx.input_path.name}")
    ctx.input_kind = media_type

    if media_type in ("video", "audio"):
        probe = ffprobe_json(ctx.input_path, ffprobe_path=ctx.ffprobe_path)
        ctx.duration_input_sec = float(probe.get("format", {}).get("duration", 0) or 0)
        if media_type == "video":
            ctx.is_hdr_source = is_hdr_source(ctx.input_path, ffprobe_path=ctx.ffprobe_path)


def prepare_assets(ctx: PipelineContext) -> None:
    """Step 3 — placeholder. Inputs are already on disk for v1.0."""
    return None


def dispatch_by_mode(ctx: PipelineContext, *, emit: EmitFn = _no_emit) -> None:
    """Step 4 — route to the matching sub-pipeline."""
    mode = ctx.mode
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if mode == "source_review":
        run_source_review_pipeline(ctx, emit=emit)
    elif mode == "silence_cut":
        run_silence_cut_pipeline(ctx, emit=emit)
    elif mode == "auto_color":
        run_auto_color_pipeline(ctx, emit=emit)
    elif mode == "cut_qc":
        run_cut_qc_pipeline(ctx, emit=emit)


def finalize(ctx: PipelineContext) -> None:
    """Step 6 — write the metadata.json sidecar."""
    ctx.completed_at = time.time()
    metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "input_path": str(ctx.input_path),
        "output_path": str(ctx.output_path) if ctx.output_path else None,
        "report_path": str(ctx.report_path) if ctx.report_path else None,
        "is_hdr_source": ctx.is_hdr_source,
        "duration_input_sec": ctx.duration_input_sec,
        "started_at": ctx.started_at,
        "completed_at": ctx.completed_at,
        "params": ctx.params,
        "extra": ctx.extra,
    }
    sidecar = ctx.work_dir / "metadata.json"
    sidecar.write_text(_json_dump(metadata), encoding="utf-8")


def handoff_prepare(ctx: PipelineContext) -> None:
    """Step 7 — v2.0 cross-plugin handoff field staging.

    v1.0 red line: we DO NOT write to the ``assets_bus`` table. We only
    populate ``ctx.extra['handoff']`` so a v2.0 migration can stamp the
    row without a code change.
    """
    if not ctx.output_path:
        return
    ctx.extra["handoff"] = {
        "asset_kind": "video" if ctx.mode in ("silence_cut", "auto_color", "cut_qc") else "json",
        "asset_uri": str(ctx.output_path or ctx.report_path),
        "source_plugin_id": "footage-gate",
        "source_task_id": ctx.task_id,
    }


def handle_exception(ctx: PipelineContext, exc: BaseException) -> None:
    """Step 8 — map any exception to one of the 9 error kinds."""
    kind = _classify_error(exc)
    hints = ERROR_HINTS.get(kind, ERROR_HINTS["unknown"])
    ctx.error_kind = kind
    ctx.error_message = f"{type(exc).__name__}: {exc}"
    if isinstance(hints, Mapping):
        ctx.error_hints = list(hints.get("zh") or hints.get("en") or [])
    else:
        ctx.error_hints = list(hints)
    ctx.completed_at = time.time()
    logger.error("footage-gate task %s failed: %s\n%s", ctx.task_id, exc, traceback.format_exc())


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, TimeoutError):
        return "timeout"
    msg = str(exc).lower()
    if isinstance(exc, FFmpegError):
        if "timeout" in msg:
            return "timeout"
        if "no such file" in msg or "no such" in msg:
            return "not_found"
        if "ffmpeg" in msg and ("not found" in msg or "winerror 2" in msg):
            return "dependency"
        return "dependency"
    if "rate limit" in msg or "429" in msg:
        return "rate_limit"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "auth"
    if "moderation" in msg or "sensitive" in msg:
        return "moderation"
    if "quota" in msg or "balance" in msg:
        return "quota"
    if "network" in msg or "connection" in msg:
        return "network"
    return "unknown"


def run_pipeline(
    ctx: PipelineContext,
    *,
    emit: EmitFn = _no_emit,
) -> PipelineContext:
    """Top-level entry: runs the 8 steps in order, populating ``ctx``.

    Always returns the context (success or failure). Callers should
    inspect ``ctx.error_kind`` to decide whether to surface a 4xx /
    500-class response.
    """
    try:
        emit("step", {"name": "setup_environment"})
        setup_environment(ctx)
        emit("step", {"name": "validate_input"})
        validate_input(ctx)
        emit("step", {"name": "prepare_assets"})
        prepare_assets(ctx)
        emit("step", {"name": "dispatch_by_mode", "mode": ctx.mode})
        dispatch_by_mode(ctx, emit=emit)
        emit("step", {"name": "finalize"})
        finalize(ctx)
        emit("step", {"name": "handoff_prepare"})
        handoff_prepare(ctx)
    except BaseException as exc:  # noqa: BLE001 — we MUST catch everything
        handle_exception(ctx, exc)
    return ctx


# ── Sub-pipeline 1 — source_review ───────────────────────────────────────


def run_source_review_pipeline(
    ctx: PipelineContext,
    *,
    emit: EmitFn = _no_emit,
    transcribe: Callable[[Path, str], str | None] | None = None,
) -> None:
    """Probe the input + emit the OpenMontage-shaped review JSON."""
    emit("progress", {"pct": 10, "message": "probing media"})
    review = review_source_media(
        [ctx.input_path],
        transcribe=transcribe,
        ffprobe_path=ctx.ffprobe_path,
    )

    if ctx.is_hdr_source and review.get("files"):
        review["files"][0].setdefault("quality_risks", []).append(
            "hdr_source — output will need tone-mapping for SDR display"
        )

    emit("progress", {"pct": 60, "message": "writing report"})
    report_path = ctx.work_dir / "review.json"
    report_path.write_text(_json_dump(review), encoding="utf-8")
    ctx.report_path = report_path
    ctx.extra["review_summary"] = review.get("summary")
    emit("progress", {"pct": 100, "message": "review complete"})


# ── Sub-pipeline 2 — silence_cut ─────────────────────────────────────────


def run_silence_cut_pipeline(
    ctx: PipelineContext,
    *,
    emit: EmitFn = _no_emit,
) -> None:
    """Decode → detect → concat. Pure-numpy, no aubio."""
    if not has_audio_track(ctx.input_path, ffprobe_path=ctx.ffprobe_path):
        raise ValueError("input has no audio stream — silence_cut needs audio")

    threshold = float(ctx.params.get("threshold_db", -45.0))
    min_silence_len = float(ctx.params.get("min_silence_len", 0.15))
    min_sound_len = float(ctx.params.get("min_sound_len", 0.05))
    pad = float(ctx.params.get("pad", 0.05))

    emit("progress", {"pct": 20, "message": "detecting non-silent intervals"})
    intervals = compute_non_silent_intervals(
        ctx.input_path,
        threshold_db=threshold,
        min_silence_len=min_silence_len,
        min_sound_len=min_sound_len,
        pad=pad,
        ffmpeg_path=ctx.ffmpeg_path,
    )

    emit(
        "progress",
        {
            "pct": 60,
            "message": f"cutting {len(intervals)} segments",
        },
    )
    output = ctx.work_dir / "output.mp4"
    report = apply_silence_cut(
        ctx.input_path,
        output,
        intervals,
        work_dir=ctx.work_dir / "_silence_segs",
        ffmpeg_path=ctx.ffmpeg_path,
    )

    intervals_path = ctx.work_dir / "intervals.json"
    intervals_path.write_text(
        _json_dump({"intervals": intervals, "report": report}),
        encoding="utf-8",
    )

    ctx.output_path = output
    ctx.report_path = intervals_path
    ctx.removed_seconds = float(report.get("removed_seconds", 0.0))
    ctx.extra["silence_segments"] = report.get("segments", 0)
    emit("progress", {"pct": 100, "message": "silence cut complete"})


# ── Sub-pipeline 3 — auto_color ──────────────────────────────────────────


def run_auto_color_pipeline(
    ctx: PipelineContext,
    *,
    emit: EmitFn = _no_emit,
) -> None:
    """Sample → derive → render. HDR sources get TONEMAP_CHAIN prepended."""
    emit("progress", {"pct": 10, "message": "sampling frames for grade analysis"})
    filter_string, stats = auto_grade_for_clip(
        ctx.input_path,
        ffmpeg_path=ctx.ffmpeg_path,
        ffprobe_path=ctx.ffprobe_path,
    )

    chain = prepare_filter_chain(filter_string, hdr_source=ctx.is_hdr_source)
    no_change_needed = (not chain) and (not ctx.is_hdr_source)

    output = ctx.work_dir / "output.mp4"
    if no_change_needed:
        # Hardlink/copy the original to a stable output path so the UI
        # always has *something* to download.
        try:
            output.write_bytes(ctx.input_path.read_bytes())
        except OSError as exc:
            logger.warning("auto_color hardlink fallback failed: %s", exc)
        ctx.extra["no_change_needed"] = True
    else:
        emit("progress", {"pct": 60, "message": "rendering graded output"})
        apply_grade(
            ctx.input_path,
            output,
            filter_string,
            hdr_source=ctx.is_hdr_source,
            ffmpeg_path=ctx.ffmpeg_path,
            ffprobe_path=ctx.ffprobe_path,
        )

    grade_payload = {
        "stats": stats,
        "filter_string": filter_string,
        "filter_chain_used": chain,
        "hdr_source": ctx.is_hdr_source,
        "tonemap_chain": TONEMAP_CHAIN if ctx.is_hdr_source else "",
    }
    grade_json = ctx.work_dir / "grade.json"
    grade_json.write_text(_json_dump(grade_payload), encoding="utf-8")

    ctx.output_path = output
    ctx.report_path = grade_json
    ctx.extra["grade"] = grade_payload
    emit("progress", {"pct": 100, "message": "auto color complete"})


# ── Sub-pipeline 4 — cut_qc ──────────────────────────────────────────────


def run_cut_qc_pipeline(
    ctx: PipelineContext,
    *,
    emit: EmitFn = _no_emit,
) -> None:
    """Run the 4 checkers, optionally remux up to N times."""
    edl_payload = ctx.params.get("edl") or ctx.params.get("edl_payload")
    if edl_payload is None:
        raise ValueError("cut_qc requires an EDL JSON payload in params['edl']")

    auto_remux = bool(ctx.params.get("auto_remux", False))
    max_attempts = int(ctx.params.get("max_attempts", 3))

    emit(
        "progress",
        {
            "pct": 10,
            "message": ("running QC checks" if not auto_remux else "running QC + auto-remux loop"),
        },
    )

    result = run_qc_with_remux(
        ctx.input_path,
        edl_payload,
        work_dir=ctx.work_dir,
        auto_remux=auto_remux,
        max_attempts=max_attempts,
        ffmpeg_path=ctx.ffmpeg_path,
        ffprobe_path=ctx.ffprobe_path,
    )

    report = {
        "issues": [i.to_dict() for i in result.issues],
        "attempts": result.attempts,
        "naming_normalized": result.naming_normalized,
        "final_video": str(result.final_video),
        "grid_path": str(result.grid_path) if result.grid_path else None,
    }
    report_path = ctx.work_dir / "qc_report.json"
    report_path.write_text(_json_dump(report), encoding="utf-8")

    ctx.output_path = result.final_video
    ctx.report_path = report_path
    if result.grid_path:
        ctx.thumbs = [str(result.grid_path)]
    ctx.qc_attempts = result.attempts
    ctx.qc_issues_count = sum(1 for i in result.issues if i.severity in ("warning", "error"))
    ctx.extra["qc"] = {
        "auto_remux": auto_remux,
        "max_attempts": max_attempts,
    }
    emit("progress", {"pct": 100, "message": "cut_qc complete"})


# ── helpers ──────────────────────────────────────────────────────────────


def _json_dump(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# Imported here only so static checkers can confirm we surface the
# RISK_THRESHOLDS dict for downstream consumers (the routes layer reads
# it when emitting the SourceReviewResponse).
_RISK_THRESHOLDS_REFERENCE = RISK_THRESHOLDS  # noqa: N816 — intentionally re-exported


__all__ = [
    "EmitFn",
    "PipelineContext",
    "dispatch_by_mode",
    "finalize",
    "handle_exception",
    "handoff_prepare",
    "prepare_assets",
    "run_auto_color_pipeline",
    "run_cut_qc_pipeline",
    "run_pipeline",
    "run_silence_cut_pipeline",
    "run_source_review_pipeline",
    "setup_environment",
    "validate_input",
]
