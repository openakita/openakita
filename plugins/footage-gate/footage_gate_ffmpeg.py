# ruff: noqa: N999
"""FFmpeg / FFprobe shim layer used by every pipeline mode.

This is the **only** file in the plugin that spawns ffmpeg / ffprobe
subprocesses. Higher layers (grade / silence / review / qc) import the
helpers below; never reach for ``subprocess`` directly. Centralising the
calls keeps three things easy:

1. **Audit** — one place to enforce the "always pass ``-hide_banner``,
   ``-nostats``, ``-nostdin``" hardening that prevents ffmpeg from
   blocking on a phantom prompt.
2. **Timeout** — every helper accepts a ``timeout_sec`` so the pipeline's
   ``ffmpeg_timeout_sec`` setting can apply uniformly. A timed-out call
   raises :class:`FFmpegTimeoutError` so callers can fold it into the
   ``timeout`` ``error_kind``.
3. **Defensive parsing** — ffprobe JSON output is mostly stable but the
   ``r_frame_rate`` rational and ``size``/``bit_rate`` strings have
   bitten plenty of plugins (NaN / divide-by-zero / empty string). The
   parsing helpers here normalise every weird shape upstream.

Public surface:

- :class:`FFmpegError` / :class:`FFmpegTimeoutError`
- :func:`ffprobe_json` — full ffprobe ``-show_format -show_streams`` dump
- :func:`is_hdr_source` — vs video-use PR #6 (HDR colour transfer probe)
- :func:`extract_frames` — fast-seek frame dump to PNG
- :func:`compute_envelope` — RMS envelope ndarray for waveform / spike
- :func:`extract_pcm_mono` — float32 mono PCM ndarray for silence_cut
- :func:`run_ffmpeg` / :func:`run_ffprobe` — low-level wrappers (used by
  the helpers above; exposed so test code can monkey-patch one place)
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────


class FFmpegError(RuntimeError):
    """Non-zero exit from ffmpeg / ffprobe."""

    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr or ""
        # Trim stderr in the message — full text is on the attribute for tests.
        tail = self.stderr.splitlines()[-3:] if self.stderr else []
        super().__init__(f"ffmpeg exit {returncode}: {' / '.join(tail) or '(no stderr)'}")


class FFmpegTimeoutError(FFmpegError):
    """ffmpeg / ffprobe call exceeded the configured ``timeout_sec``."""

    def __init__(self, cmd: Sequence[str], timeout_sec: float) -> None:
        super().__init__(cmd, returncode=-1, stderr=f"timeout after {timeout_sec}s")
        self.timeout_sec = timeout_sec


# ── Hardening defaults applied to every spawn ────────────────────────────


_FFMPEG_HARDEN: tuple[str, ...] = ("-hide_banner", "-nostats", "-nostdin")
_FFPROBE_HARDEN: tuple[str, ...] = ("-hide_banner", "-loglevel", "error")


def _resolve_binary(name: str, override: str | None) -> str:
    """Pick the binary to spawn.

    ``override`` (typically the absolute path returned by
    ``SystemDepsManager.detect``) wins; otherwise we fall back to ``which``
    so unit tests can monkey-patch ``shutil.which``.
    """
    if override:
        return override
    found = shutil.which(name)
    return found or name


def run_ffmpeg(
    args: Sequence[str],
    *,
    timeout_sec: float = 600.0,
    ffmpeg_path: str | None = None,
    capture_stdout: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Spawn ffmpeg with the standard hardening flags pre-prepended.

    ``args`` should be the ffmpeg arguments AFTER the hardening flags
    (typically ``("-y", "-i", input, ..., output)``). Raises
    :class:`FFmpegTimeoutError` on timeout, :class:`FFmpegError` on a
    non-zero exit; otherwise returns the ``CompletedProcess`` so callers
    can inspect ``stdout`` (when ``capture_stdout=True``) or the empty
    object (when piping to a file via ``-``).
    """
    bin_path = _resolve_binary("ffmpeg", ffmpeg_path)
    cmd: tuple[str, ...] = (bin_path, *_FFMPEG_HARDEN, *args)
    try:
        # NOTE: ``capture_output=True`` is mutually exclusive with explicit
        # stdout/stderr kwargs in ``subprocess.run``. Use the explicit kwargs
        # so we can route stdout to ``DEVNULL`` (when piping to a file via
        # ``-``) without capturing it twice.
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(cmd, timeout_sec) from exc
    except FileNotFoundError as exc:
        raise FFmpegError(cmd, returncode=-1, stderr=str(exc)) from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        raise FFmpegError(cmd, proc.returncode, stderr)
    return proc


def run_ffprobe(
    args: Sequence[str],
    *,
    timeout_sec: float = 30.0,
    ffprobe_path: str | None = None,
) -> str:
    """Spawn ffprobe and return its stdout as text. See :func:`run_ffmpeg`
    for the error contract."""
    bin_path = _resolve_binary("ffprobe", ffprobe_path)
    cmd: tuple[str, ...] = (bin_path, *_FFPROBE_HARDEN, *args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(cmd, timeout_sec) from exc
    except FileNotFoundError as exc:
        raise FFmpegError(cmd, returncode=-1, stderr=str(exc)) from exc

    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr or "")
    return proc.stdout or ""


# ── ffprobe JSON helpers ─────────────────────────────────────────────────


def ffprobe_json(
    path: Path | str,
    *,
    timeout_sec: float = 30.0,
    ffprobe_path: str | None = None,
) -> dict[str, Any]:
    """Run ``ffprobe -show_format -show_streams`` and return the parsed JSON.

    Raises :class:`FFmpegError` if the file is unreadable or ffprobe fails;
    raises :class:`FFmpegTimeoutError` on timeout.
    """
    blob = run_ffprobe(
        (
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ),
        timeout_sec=timeout_sec,
        ffprobe_path=ffprobe_path,
    )
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        raise FFmpegError(
            ("ffprobe", "json", str(path)),
            returncode=0,
            stderr=f"ffprobe stdout was not valid JSON: {exc}",
        ) from exc


def parse_fps(fps_str: str) -> float:
    """Parse ffprobe ``r_frame_rate`` like ``"30/1"`` or ``"24000/1001"``.

    Returns ``0.0`` on every parse / div-zero failure (callers downstream
    treat zero as "unknown" so we never propagate NaN).
    """
    if not fps_str:
        return 0.0
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/", 1)
            den_i = int(den)
            if den_i <= 0:
                return 0.0
            return round(int(num) / den_i, 4)
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


def first_video_stream(probe: dict[str, Any]) -> dict[str, Any]:
    """Return the first ``codec_type == 'video'`` stream, or an empty dict."""
    for s in probe.get("streams", []) or []:
        if s.get("codec_type") == "video":
            return s
    return {}


def first_audio_stream(probe: dict[str, Any]) -> dict[str, Any]:
    for s in probe.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            return s
    return {}


# ── HDR detection — vs video-use PR #6 ───────────────────────────────────


def is_hdr_source(
    path: Path | str,
    *,
    ffprobe_path: str | None = None,
) -> bool:
    """Return ``True`` if the first video stream uses an HDR transfer.

    Detection is purely metadata-based: ffprobe's ``color_transfer`` field
    (one of ``smpte2084`` / ``arib-std-b67``) is the same signal the
    upstream ``video-use`` autograde missed in PR #6 — that regression
    fed PQ samples directly into ``eq=gamma=`` and produced black frames.
    Callers should prepend ``footage_gate_models.TONEMAP_CHAIN`` to the
    ``eq=`` chain when this returns ``True``.

    Probe failure → ``False`` (fail-open). The auto_color pipeline already
    bounds every adjustment to ±8 % so a missed detection at worst
    produces a slightly-off SDR result; a false positive would prepend
    a heavy tonemap chain to an SDR clip and visibly clip highlights.
    """
    try:
        probe = ffprobe_json(path, ffprobe_path=ffprobe_path)
    except FFmpegError:
        return False
    vstream = first_video_stream(probe)
    transfer = (vstream.get("color_transfer") or "").lower()
    # Imported here to avoid an import cycle at module top.
    from footage_gate_models import HDR_TRANSFERS

    return transfer in HDR_TRANSFERS


# ── Frame extraction ─────────────────────────────────────────────────────


def extract_frames(
    input_path: Path | str,
    *,
    timestamps: Sequence[float],
    dest_dir: Path,
    width: int = 0,
    timeout_sec: float = 60.0,
    ffmpeg_path: str | None = None,
) -> list[Path]:
    """Extract one PNG per timestamp under ``dest_dir``.

    Uses **fast seek** (``-ss`` BEFORE ``-i``) per call so even a 30-min
    source is sub-second per frame. Returns the list of PNG paths in the
    same order as ``timestamps`` — a frame that fails to decode is
    omitted (caller can compare lengths to detect partial failure).

    ``width`` > 0 emits a downscaled thumbnail (height auto via ``-1``)
    so callers like ``cut_qc`` can build a ``qc_grid.png`` without
    burning gigabytes on full-res PNGs.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    vf = f"scale={int(width)}:-1" if width and width > 0 else ""
    for idx, ts in enumerate(timestamps):
        target = dest_dir / f"frame_{idx:04d}.png"
        args: list[str] = [
            "-y",
            "-ss",
            f"{max(0.0, float(ts)):.3f}",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
        ]
        if vf:
            args.extend(["-vf", vf])
        args.append(str(target))
        try:
            run_ffmpeg(
                args,
                timeout_sec=timeout_sec,
                ffmpeg_path=ffmpeg_path,
            )
        except FFmpegError as exc:
            logger.warning("extract_frames failed at t=%s: %s", ts, exc)
            continue
        if target.is_file():
            out.append(target)
    return out


# ── Audio envelope / PCM extraction ──────────────────────────────────────


def extract_pcm_mono(
    input_path: Path | str,
    *,
    sample_rate: int = 16000,
    timeout_sec: float = 120.0,
    ffmpeg_path: str | None = None,
) -> np.ndarray:
    """Decode the input to mono float32 PCM and return as a numpy array.

    Uses ``-f f32le -ac 1 -ar <sr>`` so the output is portable between
    platforms (no WAV header parsing). Empty result (silent / unreadable)
    returns a zero-length array; callers should handle that explicitly.
    """
    args: tuple[str, ...] = (
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(int(sample_rate)),
        "-f",
        "f32le",
        "-",
    )
    proc = run_ffmpeg(
        args,
        timeout_sec=timeout_sec,
        ffmpeg_path=ffmpeg_path,
        capture_stdout=True,
    )
    raw = proc.stdout or b""
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32).copy()


def compute_envelope(
    input_path: Path | str,
    *,
    samples: int = 400,
    sample_rate: int = 16000,
    timeout_sec: float = 120.0,
    ffmpeg_path: str | None = None,
) -> np.ndarray:
    """Compute an RMS envelope (length ``samples``) of the audio track.

    Used by :func:`waveform_spike_check` and the optional waveform
    sparkline in the QC report. Each output value is the RMS amplitude of
    one bucket; the whole array is normalised to ``[0, 1]`` so callers
    do not need to know the source bit-depth.

    For inputs with no audio track the function returns a zero-length
    array — callers should fall back to "no audio" branches gracefully.
    """
    pcm = extract_pcm_mono(
        input_path,
        sample_rate=sample_rate,
        timeout_sec=timeout_sec,
        ffmpeg_path=ffmpeg_path,
    )
    if pcm.size == 0:
        return np.zeros(0, dtype=np.float32)
    n = max(1, int(samples))
    bucket = max(1, math.ceil(pcm.size / n))
    # Pad so reshape divides evenly.
    pad = (bucket * n) - pcm.size
    if pad > 0:
        pcm = np.concatenate([pcm, np.zeros(pad, dtype=np.float32)])
    rms = np.sqrt((pcm.reshape(n, bucket) ** 2).mean(axis=1))
    peak = float(rms.max()) if rms.size else 0.0
    if peak > 0:
        rms = rms / peak
    return rms.astype(np.float32, copy=False)


__all__ = [
    "FFmpegError",
    "FFmpegTimeoutError",
    "compute_envelope",
    "extract_frames",
    "extract_pcm_mono",
    "ffprobe_json",
    "first_audio_stream",
    "first_video_stream",
    "is_hdr_source",
    "parse_fps",
    "run_ffmpeg",
    "run_ffprobe",
]
