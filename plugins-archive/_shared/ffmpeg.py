"""ffmpeg / ffprobe execution helpers with mandatory timeouts.

This module is the **execution** counterpart to
:mod:`openakita_plugin_sdk.contrib.render_pipeline` (which only **builds**
command lists).  Plugins should always call ``run_ffmpeg`` instead of
``subprocess.run([...ffmpeg...])`` directly so that:

- A timeout is **always** present (no more hung renders on huge inputs — the
  exact failure mode reported in ``video-use`` ``transcribe.py:75-82`` and
  ``timeline_view.py:267-268``).
- Output is captured uniformly so :class:`FFmpegError` carries enough context
  for ``ErrorCoach`` to render a helpful message.
- The binary is resolved via :func:`resolve_binary` which raises a
  user-friendly ``RuntimeError`` if ffmpeg is not on ``PATH`` (so the
  ``dep_gate.js`` UI can prompt installation).

Design rules (audit3):

- **No mandatory timeout default** — the parameter is required and validated
  to be positive, mirroring CutClaw ``audio/madmom_api.py`` discipline.
- **Async by default** (uses ``asyncio.to_thread``) so a long render does
  not block the event loop.  A sync helper :func:`run_ffmpeg_sync` is kept
  for non-async callers.
- **Zero extra deps**: stdlib only (``subprocess``, ``shutil``, ``json``,
  ``asyncio``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AUTO_GRADE_PRESETS",
    "DEFAULT_GRADE_CLAMP_PCT",
    "FFmpegError",
    "FFmpegResult",
    "GradeStats",
    "auto_color_grade_filter",
    "ffprobe_json",
    "ffprobe_json_sync",
    "get_grade_preset",
    "list_grade_presets",
    "resolve_binary",
    "run_ffmpeg",
    "run_ffmpeg_sync",
    "sample_signalstats",
    "sample_signalstats_sync",
]


class FFmpegError(RuntimeError):
    """Raised on ffmpeg / ffprobe failure or timeout.

    Attributes:
        cmd: The argv list that was run.
        returncode: Process return code (or ``None`` on timeout).
        stderr_tail: Last ~2KB of stderr (helpful for ErrorCoach matching).
        timed_out: ``True`` if the call hit the timeout.
    """

    def __init__(
        self,
        message: str,
        *,
        cmd: list[str],
        returncode: int | None,
        stderr_tail: str = "",
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.timed_out = timed_out


@dataclass(frozen=True)
class FFmpegResult:
    """Successful ffmpeg run."""

    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float


def resolve_binary(name: str) -> str:
    """Find a binary on ``PATH`` and return its absolute path.

    Args:
        name: Binary name (``"ffmpeg"``, ``"ffprobe"``) or absolute path.

    Returns:
        Absolute path string suitable for ``subprocess`` argv[0].

    Raises:
        RuntimeError: If the binary is not absolute and not found on PATH.
            The error message is actionable so plugins can surface it via
            ``ErrorCoach`` directly.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"binary name must be a non-empty string, got {name!r}")
    p = Path(name)
    if p.is_absolute():
        if not p.exists():
            raise RuntimeError(
                f"{name} does not exist — verify the path is correct.",
            )
        return name
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"{name} not found in PATH — install it via the dependency gate "
            "(see docs/dependency-gate.md) or add it to PATH manually.",
        )
    return found


def _validate_timeout(timeout_sec: float) -> float:
    if timeout_sec is None or not isinstance(timeout_sec, (int, float)):
        raise ValueError("timeout_sec is required (must be a positive number)")
    if timeout_sec <= 0:
        raise ValueError(f"timeout_sec must be > 0, got {timeout_sec}")
    return float(timeout_sec)


def _tail(text: str | bytes | None, limit: int = 2048) -> str:
    if text is None:
        return ""
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    return s[-limit:] if len(s) > limit else s


def run_ffmpeg_sync(
    cmd: list[str],
    *,
    timeout_sec: float,
    check: bool = True,
    capture: bool = True,
    input_bytes: bytes | None = None,
) -> FFmpegResult:
    """Synchronous ffmpeg/ffprobe runner with mandatory timeout.

    Args:
        cmd: Full argv list (argv[0] should already be resolved via
            :func:`resolve_binary` or just ``"ffmpeg"`` for ``shutil.which``
            to handle).
        timeout_sec: **Required.**  Hard wall-clock timeout in seconds.
            Raises ``ValueError`` if missing or non-positive.
        check: When True (default), non-zero exit raises :class:`FFmpegError`.
        capture: Capture stdout/stderr (default True).  Set False for
            interactive use (rare for ffmpeg in a server context).
        input_bytes: Optional bytes piped to stdin.

    Returns:
        :class:`FFmpegResult` on success.

    Raises:
        ValueError: If ``timeout_sec`` is missing or invalid.
        FFmpegError: On timeout or, if ``check=True``, non-zero exit.
    """
    timeout = _validate_timeout(timeout_sec)
    if not cmd:
        raise ValueError("cmd must not be empty")

    import time
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=capture,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(
            f"{cmd[0]} timed out after {timeout:.1f}s",
            cmd=cmd,
            returncode=None,
            stderr_tail=_tail(e.stderr),
            timed_out=True,
        ) from e
    except FileNotFoundError as e:
        raise FFmpegError(
            f"binary not found: {cmd[0]} — {e}",
            cmd=cmd,
            returncode=None,
        ) from e

    elapsed = time.monotonic() - started
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")

    if check and proc.returncode != 0:
        raise FFmpegError(
            f"{cmd[0]} exited with {proc.returncode}",
            cmd=cmd,
            returncode=proc.returncode,
            stderr_tail=_tail(stderr),
        )
    return FFmpegResult(
        cmd=list(cmd),
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_sec=elapsed,
    )


async def run_ffmpeg(
    cmd: list[str],
    *,
    timeout_sec: float,
    check: bool = True,
    capture: bool = True,
    input_bytes: bytes | None = None,
) -> FFmpegResult:
    """Async wrapper around :func:`run_ffmpeg_sync` (uses ``asyncio.to_thread``).

    Identical contract to :func:`run_ffmpeg_sync` but does not block the
    event loop.  Plugins should prefer this in async handlers.
    """
    return await asyncio.to_thread(
        run_ffmpeg_sync,
        cmd,
        timeout_sec=timeout_sec,
        check=check,
        capture=capture,
        input_bytes=input_bytes,
    )


def _ffprobe_argv(media_path: str | Path, ffprobe: str) -> list[str]:
    bin_path = resolve_binary(ffprobe)
    return [
        bin_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]


def ffprobe_json_sync(
    media_path: str | Path,
    *,
    timeout_sec: float = 15.0,
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Synchronous ffprobe → parsed JSON dict.

    Args:
        media_path: Path to the media file.
        timeout_sec: Defaults to 15s (probing is fast); still capped.
        ffprobe: Binary name or absolute path.

    Returns:
        Parsed ffprobe JSON (``{"format": {...}, "streams": [...]}``).
        Returns an empty dict if ffprobe succeeds but produces no JSON.

    Raises:
        FFmpegError: On non-zero exit, timeout, or missing binary.
    """
    cmd = _ffprobe_argv(media_path, ffprobe)
    result = run_ffmpeg_sync(cmd, timeout_sec=timeout_sec, check=True, capture=True)
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as e:
        raise FFmpegError(
            f"ffprobe returned non-JSON output: {e}",
            cmd=cmd,
            returncode=result.returncode,
            stderr_tail=_tail(result.stderr),
        ) from e


async def ffprobe_json(
    media_path: str | Path,
    *,
    timeout_sec: float = 15.0,
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Async wrapper around :func:`ffprobe_json_sync`."""
    return await asyncio.to_thread(
        ffprobe_json_sync,
        media_path,
        timeout_sec=timeout_sec,
        ffprobe=ffprobe,
    )


# ── auto color grade (signalstats sampling + ±8% clamp) ────────────────────
#
# Ported from ``video-use/helpers/grade.py:78-271`` with two hardenings:
#
#   1. ``timeout_sec`` is **mandatory** for every ffmpeg sub-call (the
#      upstream uses ``subprocess.run(..., check=True)`` with no timeout —
#      a known footgun on huge inputs, see the file's module docstring).
#   2. The clamp percentage is a single configurable constant
#      (:data:`DEFAULT_GRADE_CLAMP_PCT`) instead of being scattered as
#      magic numbers across the decision tree.  All bounds derive from it.
#
# The goal of "auto grade" is **not** to make a creative look; it is to
# make a clip *look clean without looking graded*.  No teal/orange splits,
# no LUTs, no curves.  Subtle eq corrections only, capped at ±8%.
#
# For creative looks, callers should pick a named preset from
# :data:`AUTO_GRADE_PRESETS` (``"warm_cinematic"``, ``"neutral_punch"``).


DEFAULT_GRADE_CLAMP_PCT: float = 0.08
"""Hard cap on every per-axis adjustment (contrast/gamma/saturation).

Mirrors video-use's ``grade.py:213`` rule "All caps bounded to ±8%."
Single source of truth so plugins do not re-define the constant.
"""


AUTO_GRADE_PRESETS: dict[str, str] = {
    # Subtle baseline — barely perceptible cleanup.  No color shift.
    "subtle": "eq=contrast=1.03:saturation=0.98",
    # Light contrast + subtle S-curve, no color shifts.
    "neutral_punch": (
        "eq=contrast=1.06:brightness=0.0:saturation=1.0,"
        "curves=master='0/0 0.25/0.23 0.75/0.77 1/1'"
    ),
    # OPT-IN creative preset for retro/cinematic looks.  Not a default.
    "warm_cinematic": (
        "eq=contrast=1.12:brightness=-0.02:saturation=0.88,"
        "colorbalance="
        "rs=0.02:gs=0.0:bs=-0.03:"
        "rm=0.04:gm=0.01:bm=-0.02:"
        "rh=0.08:gh=0.02:bh=-0.05,"
        "curves=master='0/0 0.25/0.22 0.75/0.78 1/1'"
    ),
    # Sentinel for "skip grading this source" — caller can detect ``""``
    # and do a stream-copy instead of re-encoding.
    "none": "",
}


def list_grade_presets() -> list[str]:
    """Return the available preset names (sorted)."""
    return sorted(AUTO_GRADE_PRESETS.keys())


def get_grade_preset(name: str) -> str:
    """Return the ffmpeg filter string for a preset name.

    Raises:
        KeyError: If ``name`` is not in :data:`AUTO_GRADE_PRESETS`.  The
            error message lists the available names so an ``ErrorCoach``
            can render a friendly suggestion.
    """
    if name not in AUTO_GRADE_PRESETS:
        raise KeyError(
            f"unknown grade preset {name!r}. "
            f"Available: {', '.join(list_grade_presets())}",
        )
    return AUTO_GRADE_PRESETS[name]


@dataclass(frozen=True)
class GradeStats:
    """Aggregated luma / saturation statistics from ``signalstats``.

    All values are normalized to ``0..1`` regardless of the source bit
    depth (8-bit / 10-bit / 12-bit).  ``samples`` is the number of frames
    that contributed to the averages — when ``samples == 0`` the source
    failed to probe and downstream code should treat the stats as
    "neutral defaults" (no correction).

    Fields:
        y_mean: Mean luma (0..1).  Target ≈ 0.48.
        y_range: Luma dynamic range (max-min averaged across samples).
            Target ≈ 0.72.  Smaller = flatter = needs contrast boost.
        sat_mean: Mean saturation (0..1).  Target ≈ 0.25.
        bit_depth: Native bit depth detected from signalstats metadata
            (8 / 10 / 12).  Defaults to 8 when unreported.
        samples: Number of frames that contributed.  ``0`` means the
            probe produced no usable metadata — callers should treat
            this as "skip grading" or fall back to a preset.
    """

    y_mean: float
    y_range: float
    sat_mean: float
    bit_depth: int = 8
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "y_mean": round(self.y_mean, 4),
            "y_range": round(self.y_range, 4),
            "sat_mean": round(self.sat_mean, 4),
            "bit_depth": self.bit_depth,
            "samples": self.samples,
        }

    @property
    def is_empty(self) -> bool:
        """True when no signalstats samples were parsed (probe failure)."""
        return self.samples == 0


_NEUTRAL_STATS = GradeStats(
    y_mean=0.5, y_range=0.72, sat_mean=0.25, bit_depth=8, samples=0,
)


def _signalstats_argv(
    video: str | Path,
    *,
    start: float,
    duration: float,
    n_samples: int,
    metadata_path: str | Path,
    ffmpeg: str,
) -> list[str]:
    bin_path = resolve_binary(ffmpeg)
    fps = max(0.5, min(n_samples / max(duration, 0.1), 10.0))
    return [
        bin_path,
        "-y", "-hide_banner", "-nostats",
        "-ss", f"{max(0.0, float(start)):.3f}",
        "-i", str(video),
        "-t", f"{max(0.1, float(duration)):.3f}",
        "-vf", f"fps={fps:.2f},signalstats,metadata=print:file={metadata_path}",
        "-f", "null", "-",
    ]


def _parse_signalstats_metadata(text: str) -> GradeStats:
    """Parse ffmpeg ``metadata=print`` output into a :class:`GradeStats`.

    The format is line-oriented:

    ``lavfi.signalstats.YBITDEPTH=8``
    ``lavfi.signalstats.YAVG=120.5``
    ``lavfi.signalstats.YMIN=4``
    ``lavfi.signalstats.YMAX=235``
    ``lavfi.signalstats.SATAVG=42.7``

    We average each value across all samples and normalize by
    ``2 ** bit_depth - 1`` so downstream math is in 0..1 regardless of
    the source bit depth.  When no ``YAVG`` line is present we return a
    neutral stats object with ``samples == 0`` so callers can fall back
    to a preset.
    """
    y_avgs: list[float] = []
    y_mins: list[float] = []
    y_maxs: list[float] = []
    sat_avgs: list[float] = []
    bit_depth = 8

    def _value(line: str) -> float | None:
        try:
            return float(line.rsplit("=", 1)[1])
        except (ValueError, IndexError):
            return None

    for raw in text.splitlines():
        line = raw.strip()
        if "lavfi.signalstats.YBITDEPTH" in line:
            v = _value(line)
            if v is not None:
                bit_depth = int(v)
        elif "lavfi.signalstats.YAVG" in line:
            v = _value(line)
            if v is not None:
                y_avgs.append(v)
        elif "lavfi.signalstats.YMIN" in line:
            v = _value(line)
            if v is not None:
                y_mins.append(v)
        elif "lavfi.signalstats.YMAX" in line:
            v = _value(line)
            if v is not None:
                y_maxs.append(v)
        elif "lavfi.signalstats.SATAVG" in line:
            v = _value(line)
            if v is not None:
                sat_avgs.append(v)

    if not y_avgs:
        return _NEUTRAL_STATS

    max_val = float((1 << bit_depth) - 1)  # 8-bit → 255, 10-bit → 1023, ...
    y_mean = (sum(y_avgs) / len(y_avgs)) / max_val
    if y_maxs and y_mins:
        y_range = ((sum(y_maxs) / len(y_maxs)) - (sum(y_mins) / len(y_mins))) / max_val
    else:
        y_range = 0.7
    sat_mean = ((sum(sat_avgs) / len(sat_avgs)) / max_val) if sat_avgs else 0.25

    return GradeStats(
        y_mean=max(0.0, min(1.0, y_mean)),
        y_range=max(0.0, min(1.0, y_range)),
        sat_mean=max(0.0, min(1.0, sat_mean)),
        bit_depth=bit_depth,
        samples=len(y_avgs),
    )


def sample_signalstats_sync(
    video: str | Path,
    *,
    start: float = 0.0,
    duration: float = 10.0,
    n_samples: int = 10,
    timeout_sec: float = 30.0,
    ffmpeg: str = "ffmpeg",
) -> GradeStats:
    """Sample ``n_samples`` frames and return aggregated signalstats.

    Args:
        video: Path to the source video.
        start: Seconds offset to start sampling at (default 0).
        duration: Length of the sampling window (default 10s).
        n_samples: Approximate target number of frames (clamped to a
            ``fps`` between 0.5 and 10).  Default 10.
        timeout_sec: Hard wall-clock limit for the ffmpeg sub-call
            (mandatory; defaults to 30s which is plenty for sampling
            10 frames out of any reasonable clip).
        ffmpeg: Binary name or absolute path.

    Returns:
        :class:`GradeStats`.  When the probe fails (binary missing, the
        clip is unreadable, ffmpeg returns no metadata) the stats are
        the neutral defaults (``samples == 0``) so callers can either
        skip grading or fall back to :func:`get_grade_preset`.

    Notes:
        We write the metadata to a temp file because ``-vf
        ...metadata=print`` writes to stderr by default which would mix
        with ffmpeg's own logs and force fragile string parsing.  The
        temp file is always cleaned up.
    """
    import tempfile

    if duration <= 0:
        raise ValueError(f"duration must be > 0, got {duration}")
    if n_samples < 1:
        raise ValueError(f"n_samples must be >= 1, got {n_samples}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_signalstats.txt", delete=False,
    ) as fh:
        meta_path = fh.name
    try:
        cmd = _signalstats_argv(
            video,
            start=start,
            duration=duration,
            n_samples=n_samples,
            metadata_path=meta_path,
            ffmpeg=ffmpeg,
        )
        try:
            run_ffmpeg_sync(cmd, timeout_sec=timeout_sec, check=True, capture=True)
        except FFmpegError as exc:
            logger.warning("signalstats sampling failed: %s", exc)
            return _NEUTRAL_STATS
        try:
            with open(meta_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            logger.warning("could not read signalstats metadata file: %s", exc)
            return _NEUTRAL_STATS
        return _parse_signalstats_metadata(text)
    finally:
        try:
            Path(meta_path).unlink(missing_ok=True)
        except OSError:
            pass


async def sample_signalstats(
    video: str | Path,
    *,
    start: float = 0.0,
    duration: float = 10.0,
    n_samples: int = 10,
    timeout_sec: float = 30.0,
    ffmpeg: str = "ffmpeg",
) -> GradeStats:
    """Async wrapper around :func:`sample_signalstats_sync`."""
    return await asyncio.to_thread(
        sample_signalstats_sync,
        video,
        start=start,
        duration=duration,
        n_samples=n_samples,
        timeout_sec=timeout_sec,
        ffmpeg=ffmpeg,
    )


def auto_color_grade_filter(
    stats: GradeStats,
    *,
    clamp_pct: float = DEFAULT_GRADE_CLAMP_PCT,
) -> str:
    """Translate :class:`GradeStats` into an ffmpeg ``eq=...`` filter.

    The decision rules mirror video-use's ``auto_grade_for_clip``
    (``grade.py:213-263``):

    * Contrast: target ``y_range ≈ 0.72``.  Boost gently when flat
      (range < 0.65), never reduce.
    * Gamma: target ``y_mean ≈ 0.48``.  Lift when too dark (y_mean
      < 0.42); slight pullback when too bright (y_mean > 0.60).
    * Saturation: target ``sat_mean ≈ 0.25``.  Modest boost when very
      flat (< 0.18); slight pullback when already punchy (> 0.38).

    All three adjustments are clamped to ``[1 - clamp_pct, 1 + clamp_pct]``
    (default ``±8%``).  Adjustments smaller than 0.5% are dropped from
    the filter string (no point re-encoding for an imperceptible delta).

    When ``stats.is_empty`` (probe failure) we return the ``"subtle"``
    preset so the caller still gets a clean, safe baseline.

    Args:
        stats: Output of :func:`sample_signalstats`.
        clamp_pct: Per-axis cap (0 < clamp_pct ≤ 0.5).  Default 0.08.

    Returns:
        An ffmpeg filter string like ``"eq=contrast=1.060:gamma=1.040"``,
        or ``""`` if no adjustment passes the 0.5% drop threshold.

    Raises:
        ValueError: If ``clamp_pct`` is out of range.
    """
    if not (0.0 < clamp_pct <= 0.5):
        raise ValueError(
            f"clamp_pct must be in (0, 0.5], got {clamp_pct}",
        )
    if stats.is_empty:
        return AUTO_GRADE_PRESETS["subtle"]

    lo = 1.0 - clamp_pct
    hi = 1.0 + clamp_pct

    # Contrast — gentle linear interp over [0.50..0.65] → [hi..1.03].
    if stats.y_range < 0.65:
        t = max(0.0, min(1.0, (stats.y_range - 0.50) / 0.15))
        contrast = hi - (hi - 1.03) * t
    else:
        contrast = 1.03

    # Gamma — lift when dark, slight pullback when bright.
    if stats.y_mean < 0.42:
        t = max(0.0, min(1.0, (stats.y_mean - 0.30) / 0.12))
        # max lift = +10%, mapped to 1.02 floor at the bright end of the
        # under-exposure band.  Capped further by ``hi`` below so a
        # caller passing clamp_pct=0.05 still gets a 5% ceiling.
        gamma = 1.10 - 0.08 * t
    elif stats.y_mean > 0.60:
        gamma = 0.97
    else:
        gamma = 1.0

    # Saturation — boost only when very flat; mild pullback otherwise.
    if stats.sat_mean < 0.18:
        sat = 1.04
    elif stats.sat_mean > 0.38:
        sat = 0.96
    else:
        sat = 0.98

    contrast = max(lo, min(hi, contrast))
    gamma = max(lo, min(hi, gamma))
    sat = max(lo, min(hi, sat))

    parts: list[str] = []
    if abs(contrast - 1.0) > 0.005:
        parts.append(f"contrast={contrast:.3f}")
    if abs(gamma - 1.0) > 0.005:
        parts.append(f"gamma={gamma:.3f}")
    if abs(sat - 1.0) > 0.005:
        parts.append(f"saturation={sat:.3f}")

    return ("eq=" + ":".join(parts)) if parts else ""
