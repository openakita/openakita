"""video-color-grade — pure-logic core.

This module is *intentionally* tiny: every algorithmic decision (sampling
brightness/contrast/saturation, computing per-axis adjustments, the
\u00b18% clamp) lives in :mod:`openakita_plugin_sdk.contrib.ffmpeg`.  The
plugin exists to wire that SDK helper into a task-managed pipeline plus
expose a couple of UI-friendly conveniences:

* ``analyze_clip``  — sample → emit ``GradeStats`` + filter string in one call,
* ``build_grade_command`` — convert (input, filter, output) into a fully
  validated ffmpeg argv list (so :func:`run_ffmpeg` can execute it),
* ``apply_grade`` — orchestrate the analyze + render flow with explicit
  timeouts so a long source can never hang the worker,
* ``GradeJobResult`` / ``to_verification`` — small dataclass + D2.10
  envelope hooked into the task record.

Keeping this layer thin means the same logic can be exercised inside
``shorts-batch`` (D3, future Sprint 17) by importing
:func:`build_grade_command` directly without dragging in the plugin
shell.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openakita_plugin_sdk.contrib import (
    DEFAULT_GRADE_CLAMP_PCT,
    KIND_NUMBER,
    KIND_OTHER,
    AUTO_GRADE_PRESETS,
    GradeStats,
    LowConfidenceField,
    Verification,
    auto_color_grade_filter,
    ffprobe_json_sync,
    get_grade_preset,
    list_grade_presets,
    resolve_binary,
    run_ffmpeg_sync,
    sample_signalstats_sync,
)

__all__ = [
    "DEFAULT_GRADE_CLAMP_PCT",
    "DEFAULT_PROBE_DURATION_SEC",
    "DEFAULT_RENDER_TIMEOUT_SEC",
    "DEFAULT_SAMPLE_FRAMES",
    "MODE_AUTO",
    "MODE_PRESET",
    "GradeJobResult",
    "GradePlan",
    "analyze_clip",
    "apply_grade",
    "build_grade_command",
    "ffmpeg_available",
    "list_modes",
    "plan_grade",
    "probe_video_duration_sec",
    "to_verification",
]


MODE_AUTO = "auto"
MODE_PRESET = "preset"

# Defaults are tuned for "good enough on a 1-hour 1080p clip without
# overrunning a 15-minute web request".  All can be overridden per-job.
DEFAULT_PROBE_DURATION_SEC = 10.0
DEFAULT_SAMPLE_FRAMES = 10
DEFAULT_RENDER_TIMEOUT_SEC = 1800.0  # 30 min ceiling — beyond this, fail fast


def list_modes() -> list[str]:
    """Modes the plugin understands (``auto`` + every named preset)."""
    return [MODE_AUTO] + [
        f"{MODE_PRESET}:{name}" for name in list_grade_presets()
    ]


# ── ffprobe helpers ────────────────────────────────────────────────────────


def ffmpeg_available() -> bool:
    """Cheap check used by ``/healthz`` and the QualityGates probe."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_video_duration_sec(
    path: str | Path, *, ffprobe: str = "ffprobe", timeout_sec: float = 15.0,
) -> float:
    """Return the source duration in seconds (rounded to 3 decimals).

    Returns ``0.0`` when ffprobe is missing or the source has no
    ``format.duration`` field — callers should treat that as "unknown
    duration" and either skip or fall back to a fixed sample window.
    """
    try:
        meta = ffprobe_json_sync(path, ffprobe=ffprobe, timeout_sec=timeout_sec)
    except Exception:  # noqa: BLE001 — caller will surface via ErrorCoach
        return 0.0
    fmt = meta.get("format") or {}
    try:
        return round(float(fmt.get("duration", 0.0)), 3)
    except (ValueError, TypeError):
        return 0.0


# ── plan + render ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GradePlan:
    """Resolved plan for one grade job.

    ``filter_string`` may be empty (``""``) which means "no eq adjustment
    required — caller may stream-copy".  We still re-encode in that case
    because most plugin consumers want a uniform mp4 (predictable codec
    + faststart) downstream.
    """

    input_path: str
    output_path: str
    mode: str
    filter_string: str
    stats: GradeStats
    clamp_pct: float
    sample_window_sec: float
    sample_frames: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": self.input_path,
            "output_path": self.output_path,
            "mode": self.mode,
            "filter_string": self.filter_string,
            "clamp_pct": self.clamp_pct,
            "sample_window_sec": self.sample_window_sec,
            "sample_frames": self.sample_frames,
            "stats": self.stats.to_dict(),
        }


@dataclass(frozen=True)
class GradeJobResult:
    """What ``apply_grade`` returns once ffmpeg has produced the file."""

    plan: GradePlan
    duration_sec: float           # source duration (0 if unknown)
    elapsed_sec: float            # wall-clock for the render step
    output_size_bytes: int
    ffmpeg_cmd: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "duration_sec": self.duration_sec,
            "elapsed_sec": self.elapsed_sec,
            "output_size_bytes": self.output_size_bytes,
            "ffmpeg_cmd": list(self.ffmpeg_cmd),
        }


def analyze_clip(
    video: str | Path,
    *,
    duration: float | None = None,
    n_samples: int = DEFAULT_SAMPLE_FRAMES,
    clamp_pct: float = DEFAULT_GRADE_CLAMP_PCT,
    timeout_sec: float = 60.0,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> tuple[GradeStats, str]:
    """Sample the source and produce ``(stats, filter_string)`` in one call.

    ``duration`` defaults to ``min(probe_duration, DEFAULT_PROBE_DURATION_SEC)``
    so even a 2-hour movie is sampled cheaply.  When ffmpeg is missing
    we return neutral stats + the ``"subtle"`` preset so callers still
    get a clean baseline filter to apply (or skip).
    """
    if duration is None:
        probed = probe_video_duration_sec(video, ffprobe=ffprobe)
        # Sample at most DEFAULT_PROBE_DURATION_SEC; on tiny clips, cap to
        # the actual length to avoid ffmpeg complaining.
        duration = min(DEFAULT_PROBE_DURATION_SEC, probed) if probed else DEFAULT_PROBE_DURATION_SEC
        if duration <= 0:
            duration = DEFAULT_PROBE_DURATION_SEC
    stats = sample_signalstats_sync(
        video,
        start=0.0,
        duration=duration,
        n_samples=n_samples,
        timeout_sec=timeout_sec,
        ffmpeg=ffmpeg,
    )
    filter_str = auto_color_grade_filter(stats, clamp_pct=clamp_pct)
    return stats, filter_str


def plan_grade(
    *,
    input_path: str | Path,
    output_path: str | Path,
    mode: str = MODE_AUTO,
    clamp_pct: float = DEFAULT_GRADE_CLAMP_PCT,
    sample_window_sec: float = DEFAULT_PROBE_DURATION_SEC,
    sample_frames: int = DEFAULT_SAMPLE_FRAMES,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> GradePlan:
    """Resolve ``(mode, params)`` into a concrete :class:`GradePlan`.

    ``mode`` accepts:
    * ``"auto"`` — sample the source, derive an eq filter (default).
    * ``"preset:<name>"`` — use a fixed preset, no sampling.

    Raises:
        ValueError: If ``mode`` is malformed or names a missing preset.
    """
    in_str = str(input_path)
    out_str = str(output_path)

    if mode == MODE_AUTO:
        stats, filter_str = analyze_clip(
            in_str,
            duration=sample_window_sec,
            n_samples=sample_frames,
            clamp_pct=clamp_pct,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        return GradePlan(
            input_path=in_str,
            output_path=out_str,
            mode=MODE_AUTO,
            filter_string=filter_str,
            stats=stats,
            clamp_pct=clamp_pct,
            sample_window_sec=sample_window_sec,
            sample_frames=sample_frames,
        )

    if mode.startswith(f"{MODE_PRESET}:"):
        preset_name = mode.split(":", 1)[1]
        # Raises KeyError → ValueError below for a uniform error type.
        try:
            filter_str = get_grade_preset(preset_name)
        except KeyError as e:
            raise ValueError(str(e)) from e
        return GradePlan(
            input_path=in_str,
            output_path=out_str,
            mode=mode,
            filter_string=filter_str,
            stats=GradeStats(  # neutral — no probing for presets
                y_mean=0.5, y_range=0.72, sat_mean=0.25, samples=0,
            ),
            clamp_pct=clamp_pct,
            sample_window_sec=0.0,
            sample_frames=0,
        )

    raise ValueError(
        f"unsupported mode {mode!r}. Use 'auto' or 'preset:<name>' "
        f"(presets: {', '.join(list_grade_presets())})",
    )


def build_grade_command(
    plan: GradePlan,
    *,
    ffmpeg: str = "ffmpeg",
    crf: int = 18,
    preset: str = "fast",
) -> list[str]:
    """Translate a :class:`GradePlan` into an ffmpeg argv list.

    When ``filter_string`` is empty we still re-encode (not stream-copy)
    so downstream consumers always get an mp4 with consistent codec /
    faststart flags.  This adds a few seconds for short clips but
    eliminates an entire category of "container surprised me" bugs that
    the original ``video-use/grade.py`` workflow exhibited.
    """
    bin_path = resolve_binary(ffmpeg)
    cmd: list[str] = [
        bin_path, "-y", "-hide_banner", "-i", plan.input_path,
    ]
    if plan.filter_string:
        cmd.extend(["-vf", plan.filter_string])
    cmd.extend([
        "-c:v", "libx264", "-preset", preset, "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        plan.output_path,
    ])
    return cmd


def apply_grade(
    plan: GradePlan,
    *,
    timeout_sec: float = DEFAULT_RENDER_TIMEOUT_SEC,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    crf: int = 18,
    preset: str = "fast",
) -> GradeJobResult:
    """Render the graded output and return its metadata.

    The function is synchronous and CPU/IO-heavy; the plugin's worker
    runs it via ``asyncio.to_thread`` so the host loop stays free.
    Mirrors the ``mix_tracks`` pattern in ``bgm-mixer/mixer_engine.py``.
    """
    Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = build_grade_command(plan, ffmpeg=ffmpeg, crf=crf, preset=preset)
    duration_src = probe_video_duration_sec(plan.input_path, ffprobe=ffprobe)
    result = run_ffmpeg_sync(cmd, timeout_sec=timeout_sec, check=True, capture=True)
    out = Path(plan.output_path)
    size = out.stat().st_size if out.exists() else 0
    return GradeJobResult(
        plan=plan,
        duration_sec=duration_src,
        elapsed_sec=result.duration_sec,
        output_size_bytes=size,
        ffmpeg_cmd=list(cmd),
    )


# ── D2.10 verification envelope ────────────────────────────────────────────


def to_verification(result: GradeJobResult) -> Verification:
    """Surface what the user should double-check.

    Heuristics (kept consistent with the ``mixer_engine.to_verification``
    style — same Verification envelope contract):

    * ``stats.is_empty`` — ffmpeg sampling failed; we fell back to the
      ``"subtle"`` preset.  Yellow flag so the UI nudges the user to
      confirm the result *visually* before exporting.
    * Empty ``filter_string`` — no measurable adjustment was needed.
      That is itself worth surfacing so the user knows the plugin
      didn't silently no-op.
    * ``duration_sec == 0`` — ffprobe could not read the source duration.
      Most often a corrupt MOOV atom; we still produced an output but
      flag it for review.
    """
    fields: list[LowConfidenceField] = []

    if result.plan.stats.is_empty and result.plan.mode == MODE_AUTO:
        fields.append(LowConfidenceField(
            path="$.plan.stats.samples",
            value=0,
            kind=KIND_NUMBER,
            reason="auto-grade fell back to the 'subtle' preset because "
                   "signalstats produced no usable samples",
        ))
    if not result.plan.filter_string and result.plan.mode == MODE_AUTO:
        fields.append(LowConfidenceField(
            path="$.plan.filter_string",
            value="",
            kind=KIND_OTHER,
            reason="every per-axis adjustment fell below the 0.5% drop "
                   "threshold — output is essentially a re-encode",
        ))
    if result.duration_sec <= 0:
        fields.append(LowConfidenceField(
            path="$.duration_sec",
            value=0,
            kind=KIND_NUMBER,
            reason="ffprobe could not read the source duration; render "
                   "succeeded but please verify visually",
        ))

    return Verification(
        verified=not fields,
        verifier_id="video_color_grade_self_check",
        low_confidence_fields=fields,
    )
