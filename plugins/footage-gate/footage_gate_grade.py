# ruff: noqa: N999
"""Auto color-grade port of ``video-use/helpers/grade.py`` (L78–291).

Re-implements the C1 ``AutoColorGrade`` atom 1:1 with two deliberate
upgrades over the upstream:

1. **HDR safety** — every ``apply_grade`` call inspects the source via
   :func:`footage_gate_ffmpeg.is_hdr_source`; when the source uses an
   HDR transfer (``smpte2084`` / ``arib-std-b67``) the
   :data:`footage_gate_models.TONEMAP_CHAIN` is prepended to the ``eq=``
   chain. This is the regression video-use PR #6 fixed and we keep the
   defence here.
2. **Always emit a non-empty filter** in auto-grade mode — even when the
   per-clip stats land in the "clip is fine" zone we still emit the
   ``subtle`` baseline so downstream consumers (cut_qc, parallel_executor)
   can pin re-encode parameters once. Upstream returned ``""`` which
   silently switched ``apply_grade`` to a stream-copy, breaking pipelines
   that expected a uniform output codec.

The math (clamps, decision rules, signalstats parsing) is byte-identical
to upstream — see the inline comments referencing the original line
ranges. The new pieces live behind clearly named helpers
(:func:`build_grade_filter` / :func:`prepare_filter_chain`) so the
upstream-aligned ``auto_grade_for_clip`` body stays untouched.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from footage_gate_ffmpeg import (
    FFmpegError,
    is_hdr_source,
    run_ffmpeg,
    run_ffprobe,
)
from footage_gate_models import GRADE_CLAMPS, TONEMAP_CHAIN

logger = logging.getLogger(__name__)


# ── Presets (verbatim from video-use/helpers/grade.py L38-63) ────────────


PRESETS: dict[str, str] = {
    "subtle": "eq=contrast=1.03:saturation=0.98",
    "neutral_punch": (
        "eq=contrast=1.06:brightness=0.0:saturation=1.0,"
        "curves=master='0/0 0.25/0.23 0.75/0.77 1/1'"
    ),
    "warm_cinematic": (
        "eq=contrast=1.12:brightness=-0.02:saturation=0.88,"
        "colorbalance="
        "rs=0.02:gs=0.0:bs=-0.03:"
        "rm=0.04:gm=0.01:bm=-0.02:"
        "rh=0.08:gh=0.02:bh=-0.05,"
        "curves=master='0/0 0.25/0.22 0.75/0.78 1/1'"
    ),
    "none": "",
}


def get_preset(name: str) -> str:
    """Return the ffmpeg filter string for a preset name."""
    if name not in PRESETS:
        raise KeyError(
            f"unknown preset '{name}'. Available: {', '.join(sorted(PRESETS))}"
        )
    return PRESETS[name]


# ── signalstats sampler (port of video-use L78–175) ──────────────────────


def _sample_frame_stats(
    video: Path,
    start: float,
    duration: float,
    n_samples: int = 10,
    *,
    ffmpeg_path: str | None = None,
    timeout_sec: float = 60.0,
) -> dict[str, float]:
    """Sample N frames and compute brightness/contrast/saturation stats.

    Mirrors video-use grade.py L78-175. Bit-depth normalisation is the
    important non-obvious bit: signalstats reports values in the NATIVE
    bit depth (8-bit → 0-255, 10-bit → 0-1023), so we read YBITDEPTH
    and divide by ``2^depth - 1``. Without this, a 10-bit clip's
    ``y_mean`` would land at 0.04 and trigger the "way too dark" path.
    """
    fps = max(0.5, min(n_samples / max(duration, 0.1), 10.0))

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".txt", delete=False
    ) as tf:
        metadata_path = tf.name

    try:
        args: list[str] = [
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video),
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"fps={fps:.2f},signalstats,metadata=print:file={metadata_path}",
            "-f",
            "null",
            "-",
        ]
        try:
            run_ffmpeg(args, timeout_sec=timeout_sec, ffmpeg_path=ffmpeg_path)
        except FFmpegError as exc:
            logger.warning("signalstats sampling failed: %s", exc)
            return {"y_mean": 0.5, "y_std": 0.18, "sat_mean": 0.25}

        y_avgs: list[float] = []
        y_mins: list[float] = []
        y_maxs: list[float] = []
        sat_avgs: list[float] = []
        bit_depth: int = 8

        def _parse_value(line: str) -> float | None:
            try:
                return float(line.rsplit("=", 1)[1])
            except (ValueError, IndexError):
                return None

        with open(metadata_path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if "lavfi.signalstats.YBITDEPTH" in line:
                    v = _parse_value(line)
                    if v is not None:
                        bit_depth = int(v)
                elif "lavfi.signalstats.YAVG" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_avgs.append(v)
                elif "lavfi.signalstats.YMIN" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_mins.append(v)
                elif "lavfi.signalstats.YMAX" in line:
                    v = _parse_value(line)
                    if v is not None:
                        y_maxs.append(v)
                elif "lavfi.signalstats.SATAVG" in line:
                    v = _parse_value(line)
                    if v is not None:
                        sat_avgs.append(v)

        if not y_avgs:
            return {"y_mean": 0.5, "y_std": 0.18, "sat_mean": 0.25}

        max_val = (2**bit_depth) - 1
        y_mean = (sum(y_avgs) / len(y_avgs)) / max_val
        y_range = (
            ((sum(y_maxs) / len(y_maxs)) - (sum(y_mins) / len(y_mins))) / max_val
            if y_maxs and y_mins
            else 0.7
        )
        sat_mean = (
            ((sum(sat_avgs) / len(sat_avgs)) / max_val) if sat_avgs else 0.25
        )

        return {
            "y_mean": y_mean,
            "y_std": y_range / 4.0,
            "sat_mean": sat_mean,
        }
    finally:
        try:
            Path(metadata_path).unlink(missing_ok=True)
        except OSError:
            pass


# ── Decision rules (port of video-use L213–269) ──────────────────────────


def derive_adjustments(stats: dict[str, float]) -> dict[str, float]:
    """Map sampled stats → (contrast, gamma, saturation) corrections.

    All adjustments hard-clamped against :data:`GRADE_CLAMPS` so a single
    pass cannot push the clip into clipping or hyper-saturation.
    """
    y_mean = stats["y_mean"]
    y_range = stats["y_std"] * 4.0
    sat_mean = stats["sat_mean"]

    # Contrast: target y_range ≈ 0.72; boost gently if flat, never reduce.
    contrast_adj = 1.0
    if y_range < 0.65:
        t = max(0.0, min(1.0, (y_range - 0.50) / 0.15))
        contrast_adj = 1.08 - 0.05 * t
    else:
        contrast_adj = 1.03

    # Gamma: target y_mean ≈ 0.48; lift gently if dark, slight pullback if hot.
    gamma_adj = 1.0
    if y_mean < 0.42:
        t = max(0.0, min(1.0, (y_mean - 0.30) / 0.12))
        gamma_adj = 1.10 - 0.08 * t
    elif y_mean > 0.60:
        gamma_adj = 0.97

    # Saturation: target sat_mean ≈ 0.25; tiny pullback by default.
    sat_adj = 0.98
    if sat_mean < 0.18:
        sat_adj = 1.04
    elif sat_mean > 0.38:
        sat_adj = 0.96

    cl_c = GRADE_CLAMPS["contrast"]
    cl_g = GRADE_CLAMPS["gamma"]
    cl_s = GRADE_CLAMPS["saturation"]
    return {
        "contrast": max(cl_c[0], min(cl_c[1], contrast_adj)),
        "gamma": max(cl_g[0], min(cl_g[1], gamma_adj)),
        "saturation": max(cl_s[0], min(cl_s[1], sat_adj)),
    }


def build_grade_filter(adj: dict[str, float]) -> str:
    """Compose an ``eq=`` filter string from the adjustment dict.

    Skips any axis whose adjustment rounds to identity (within 0.005).
    Returns the ``subtle`` baseline preset when every axis is identity —
    this is the deliberate divergence from upstream noted in the module
    docstring.
    """
    eq_parts: list[str] = []
    if abs(adj["contrast"] - 1.0) > 0.005:
        eq_parts.append(f"contrast={adj['contrast']:.3f}")
    if abs(adj["gamma"] - 1.0) > 0.005:
        eq_parts.append(f"gamma={adj['gamma']:.3f}")
    if abs(adj["saturation"] - 1.0) > 0.005:
        eq_parts.append(f"saturation={adj['saturation']:.3f}")
    if not eq_parts:
        return PRESETS["subtle"]
    return "eq=" + ":".join(eq_parts)


# ── Auto-grade entry point (port of video-use L178–271) ──────────────────


def auto_grade_for_clip(
    video: Path,
    start: float = 0.0,
    duration: float | None = None,
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
) -> tuple[str, dict[str, float]]:
    """Analyse a clip range and emit a subtle per-clip correction filter.

    Returns ``(filter_string, stats)``. ``filter_string`` is a bare ``eq=``
    chain (or :data:`PRESETS['subtle']` baseline). It is NOT prepended
    with the HDR tonemap chain — call :func:`prepare_filter_chain` for
    that, or pass straight through :func:`apply_grade` which takes care
    of HDR detection itself.
    """
    if duration is None:
        try:
            blob = run_ffprobe(
                (
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video),
                ),
                ffprobe_path=ffprobe_path,
            )
            duration = float(blob.strip())
        except (FFmpegError, ValueError):
            duration = 10.0

    stats = _sample_frame_stats(video, start, duration, ffmpeg_path=ffmpeg_path)
    adj = derive_adjustments(stats)
    return build_grade_filter(adj), stats


# ── HDR-safe filter chain assembly ───────────────────────────────────────


def prepare_filter_chain(filter_string: str, *, hdr_source: bool) -> str:
    """Prepend :data:`TONEMAP_CHAIN` when the source is HDR.

    Returns the unchanged ``filter_string`` for SDR sources. For HDR
    sources the result is ``"<TONEMAP_CHAIN>,<filter_string>"`` (a single
    comma between, even when ``filter_string`` is empty — empty string
    is treated as identity, so we still emit ``TONEMAP_CHAIN`` so the
    output lands in BT.709 SDR for downstream display).
    """
    if not hdr_source:
        return filter_string
    if not filter_string:
        return TONEMAP_CHAIN
    return f"{TONEMAP_CHAIN},{filter_string}"


# ── Render ────────────────────────────────────────────────────────────────


def apply_grade(
    input_path: Path,
    output_path: Path,
    filter_string: str,
    *,
    hdr_source: bool | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    timeout_sec: float = 600.0,
) -> None:
    """Re-encode ``input_path`` with the given grade filter.

    When ``filter_string`` is empty AND the source is SDR we stream-copy
    (matches upstream behaviour). Any other case re-encodes with libx264
    CRF 18 / yuv420p / faststart — pinned to keep the output ABR-friendly
    and playable on every modern browser without a probe round-trip.

    ``hdr_source`` autodetects when ``None``; pass an explicit ``False``
    to skip the probe (saves one ffprobe call when the caller already
    knows the answer).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if hdr_source is None:
        hdr_source = is_hdr_source(input_path, ffprobe_path=ffprobe_path)
    chain = prepare_filter_chain(filter_string, hdr_source=hdr_source)

    if not chain:
        args: list[str] = [
            "-y",
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
    else:
        args = [
            "-y",
            "-i",
            str(input_path),
            "-vf",
            chain,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    run_ffmpeg(args, timeout_sec=timeout_sec, ffmpeg_path=ffmpeg_path)


__all__ = [
    "PRESETS",
    "apply_grade",
    "auto_grade_for_clip",
    "build_grade_filter",
    "derive_adjustments",
    "get_preset",
    "prepare_filter_chain",
]
