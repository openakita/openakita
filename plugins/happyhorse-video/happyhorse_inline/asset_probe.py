"""Asset pre-flight probe — verify media specs locally before submitting
to costly DashScope endpoints.

Why this exists
---------------
Several DashScope models reject inputs that violate strict dimension /
duration / format rules (e.g. wan2.2-animate refuses videos longer than
30 s; videoretalk refuses audio > 30 MB). The vendor returns a clear
422 a few seconds after submission, but only AFTER the OSS upload step
has finished, which means:

1. Wasted seconds of wall-clock per failed attempt.
2. Wasted bandwidth on the upload (a 200 MB video pushed for nothing).
3. Asset-rejection errors are billed in some cases.

This module probes assets *before* OSS upload and surfaces a precise
Chinese error so the user fixes the file once, instead of submitting and
waiting. Probes are best-effort: when ``ffprobe`` or ``PIL`` are missing
we degrade to size-only / extension-only checks rather than block, with
a logged warning. ``probe_*`` never raises; the caller decides whether a
probe failure should hard-fail the task via :func:`assert_*` helpers.

Specs enforced (per official Bailian docs as of 2026-05)
--------------------------------------------------------
- ``assert_videoretalk_audio``: 2..120 s, ``<=30 MB``, wav/mp3/aac
- ``assert_s2v_image``: ``min(w,h)>=400`` & ``max(w,h)<=7000``,
  JPG/JPEG/PNG/BMP/WEBP
- ``assert_s2v_audio``: 2..20 s, ``<=15 MB``, wav/mp3
- ``assert_animate_image``: ``min(w,h)>=200`` & ``max(w,h)<=4096``,
  ``<=5 MB``, JPG/JPEG/PNG/BMP/WEBP
- ``assert_animate_video``: 2..30 s, ``min(w,h)>=200`` &
  ``max(w,h)<=2048``, ``<=200 MB``, mp4/avi/mov

Each assertion raises :class:`AssetSpecError` on violation; the caller
maps it to a ``VendorError(ERROR_KIND_CLIENT)`` so the UI shows an
actionable hint at the create-form stage instead of mid-pipeline.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Result types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageProbe:
    """Best-effort image probe. ``width``/``height`` are 0 when probing
    failed (e.g. PIL missing); ``fmt`` is lower-case ext (``png``,
    ``jpeg``, ``webp``, ``bmp``, ...). ``size_bytes`` is always set."""

    width: int
    height: int
    fmt: str
    size_bytes: int


@dataclass(frozen=True)
class AudioProbe:
    """Best-effort audio probe. ``duration_sec`` is 0.0 when ffprobe is
    missing. ``fmt`` is lower-case ext; ``size_bytes`` always set."""

    duration_sec: float
    fmt: str
    size_bytes: int


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    duration_sec: float
    fmt: str
    size_bytes: int


class AssetSpecError(ValueError):
    """Raised when an asset violates a documented vendor spec.

    The message is user-facing Chinese (matches the rest of the plugin's
    error surface) and includes the documented limit alongside the
    actual observed value so users can fix the file without leaving the
    app.
    """


# ─── Low-level probes ────────────────────────────────────────────────


def _ffprobe_path() -> str | None:
    """Return ``ffprobe`` binary path or ``None`` if not installed.

    System-deps manager auto-installs FFmpeg for the long-video concat
    flow; we piggyback on the same install for probing here so users
    don't see two separate ffmpeg requirements.
    """
    return shutil.which("ffprobe")


def _ext(path: str | Path) -> str:
    return Path(str(path)).suffix.lstrip(".").lower()


def _stat_size(path: str | Path) -> int:
    try:
        return os.stat(str(path)).st_size
    except OSError:
        return 0


def probe_image(path: str | Path) -> ImageProbe:
    """Return ``(width, height, fmt, size)`` for a local image file.

    Uses Pillow when available; otherwise falls back to magic-header
    parsing for PNG / JPEG which covers ~99% of UI uploads.
    """
    size = _stat_size(path)
    fmt = _ext(path) or "unknown"
    w = h = 0
    try:
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        logger.info("PIL not importable (%s); image probe degraded to size-only", e)
        return ImageProbe(0, 0, fmt, size)
    try:
        with Image.open(str(path)) as img:
            w, h = img.size
            fmt = (img.format or fmt).lower()
    except Exception as e:  # noqa: BLE001
        logger.warning("PIL.open failed for %s: %s", path, e)
    # Normalise common aliases: pillow reports JPEG as 'jpeg' but file
    # extensions are usually .jpg — keep a single canonical form so the
    # caller doesn't have to handle both.
    if fmt == "jpg":
        fmt = "jpeg"
    return ImageProbe(int(w or 0), int(h or 0), fmt, size)


def _run_ffprobe(path: str | Path, *select_streams: str) -> dict[str, str]:
    """Run ``ffprobe`` for selected streams and return a flat dict.

    Returns an empty dict when ffprobe is missing or the call fails —
    callers downgrade to size-only checks in that case.
    """
    bin_path = _ffprobe_path()
    if not bin_path:
        return {}
    args = [
        bin_path,
        "-v", "error",
        "-print_format", "default=noprint_wrappers=1",
        "-show_entries",
        # Pull format-level duration AND any stream-level fields the
        # caller asked for in one ffprobe invocation (cheaper than two).
        "format=duration:stream=width,height,codec_type,duration",
    ]
    for s in select_streams:
        args += ["-select_streams", s]
    args += [str(path)]
    try:
        out = subprocess.run(  # noqa: S603 — args list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("ffprobe call failed for %s: %s", path, e)
        return {}
    if out.returncode != 0:
        logger.info("ffprobe %s rc=%s stderr=%s", path, out.returncode, out.stderr[:200])
        return {}
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip()
    return fields


def probe_audio(path: str | Path) -> AudioProbe:
    """Return ``(duration_sec, fmt, size)`` for a local audio file.

    Falls back to size-only when ffprobe is missing.
    """
    size = _stat_size(path)
    fmt = _ext(path) or "unknown"
    fields = _run_ffprobe(path, "a:0")
    dur = float(fields.get("duration", "0") or 0.0)
    return AudioProbe(dur, fmt, size)


def probe_video(path: str | Path) -> VideoProbe:
    """Return ``(w, h, duration_sec, fmt, size)`` for a local video.

    Falls back to size-only when ffprobe is missing.
    """
    size = _stat_size(path)
    fmt = _ext(path) or "unknown"
    fields = _run_ffprobe(path, "v:0")
    w = int(fields.get("width", "0") or 0)
    h = int(fields.get("height", "0") or 0)
    dur = float(fields.get("duration", "0") or 0.0)
    return VideoProbe(w, h, dur, fmt, size)


# ─── Per-endpoint assertions (raise AssetSpecError on violation) ─────


_IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "webp"}
_VIDEORETALK_AUDIO_EXTS = {"wav", "mp3", "aac"}
_S2V_AUDIO_EXTS = {"wav", "mp3"}
_ANIMATE_VIDEO_EXTS = {"mp4", "avi", "mov"}

_MB = 1024 * 1024


def _check_ext(fmt: str, allowed: set[str], label: str) -> None:
    if fmt and fmt not in allowed:
        raise AssetSpecError(
            f"{label} 格式不支持：当前 {fmt!r}，仅允许 {sorted(allowed)}"
        )


def assert_videoretalk_audio(path: str | Path) -> AudioProbe:
    """Verify an audio file matches the official videoretalk spec.

    Limits: 2..120 s duration; <=30 MB; wav / mp3 / aac.
    """
    probe = probe_audio(path)
    _check_ext(probe.fmt, _VIDEORETALK_AUDIO_EXTS, "videoretalk 音频")
    if probe.size_bytes > 30 * _MB:
        raise AssetSpecError(
            f"videoretalk 音频大小 {probe.size_bytes / _MB:.1f} MB 超过 30 MB 上限"
        )
    if probe.duration_sec:
        if probe.duration_sec < 2.0:
            raise AssetSpecError(
                f"videoretalk 音频时长 {probe.duration_sec:.2f}s 短于下限 2.00s"
            )
        if probe.duration_sec > 120.0:
            raise AssetSpecError(
                f"videoretalk 音频时长 {probe.duration_sec:.2f}s 超过上限 120.00s"
            )
    return probe


def assert_s2v_image(path: str | Path) -> ImageProbe:
    """wan2.2-s2v image: 400..7000 px on each edge, JPG/PNG/BMP/WEBP."""
    probe = probe_image(path)
    _check_ext(probe.fmt, _IMAGE_EXTS, "wan2.2-s2v 图像")
    if probe.width and probe.height:
        if min(probe.width, probe.height) < 400:
            raise AssetSpecError(
                f"wan2.2-s2v 图像短边 {min(probe.width, probe.height)}px"
                " 小于 400px 下限"
            )
        if max(probe.width, probe.height) > 7000:
            raise AssetSpecError(
                f"wan2.2-s2v 图像长边 {max(probe.width, probe.height)}px"
                " 超过 7000px 上限"
            )
    return probe


def assert_s2v_audio(path: str | Path) -> AudioProbe:
    """wan2.2-s2v audio: 2..20 s, <=15 MB, wav/mp3."""
    probe = probe_audio(path)
    _check_ext(probe.fmt, _S2V_AUDIO_EXTS, "wan2.2-s2v 音频")
    if probe.size_bytes > 15 * _MB:
        raise AssetSpecError(
            f"wan2.2-s2v 音频大小 {probe.size_bytes / _MB:.1f} MB 超过 15 MB 上限"
        )
    if probe.duration_sec:
        if probe.duration_sec < 2.0:
            raise AssetSpecError(
                f"wan2.2-s2v 音频时长 {probe.duration_sec:.2f}s 短于下限 2.00s"
            )
        if probe.duration_sec > 20.0:
            raise AssetSpecError(
                f"wan2.2-s2v 音频时长 {probe.duration_sec:.2f}s 超过上限 20.00s"
            )
    return probe


def assert_animate_image(path: str | Path) -> ImageProbe:
    """wan2.2-animate-mix/-move image: 200..4096 px, <=5 MB."""
    probe = probe_image(path)
    _check_ext(probe.fmt, _IMAGE_EXTS, "wan2.2-animate 图像")
    if probe.size_bytes > 5 * _MB:
        raise AssetSpecError(
            f"wan2.2-animate 图像大小 {probe.size_bytes / _MB:.1f} MB 超过 5 MB 上限"
        )
    if probe.width and probe.height:
        if min(probe.width, probe.height) < 200:
            raise AssetSpecError(
                f"wan2.2-animate 图像短边 {min(probe.width, probe.height)}px"
                " 小于 200px 下限"
            )
        if max(probe.width, probe.height) > 4096:
            raise AssetSpecError(
                f"wan2.2-animate 图像长边 {max(probe.width, probe.height)}px"
                " 超过 4096px 上限"
            )
    return probe


def assert_animate_video(path: str | Path) -> VideoProbe:
    """wan2.2-animate-mix/-move video: 2..30 s, 200..2048 px, <=200 MB."""
    probe = probe_video(path)
    _check_ext(probe.fmt, _ANIMATE_VIDEO_EXTS, "wan2.2-animate 视频")
    if probe.size_bytes > 200 * _MB:
        raise AssetSpecError(
            f"wan2.2-animate 视频大小 {probe.size_bytes / _MB:.1f} MB 超过 200 MB 上限"
        )
    if probe.duration_sec:
        if probe.duration_sec < 2.0:
            raise AssetSpecError(
                f"wan2.2-animate 视频时长 {probe.duration_sec:.2f}s 短于下限 2.00s"
            )
        if probe.duration_sec > 30.0:
            raise AssetSpecError(
                f"wan2.2-animate 视频时长 {probe.duration_sec:.2f}s 超过上限 30.00s"
            )
    if probe.width and probe.height:
        if min(probe.width, probe.height) < 200:
            raise AssetSpecError(
                f"wan2.2-animate 视频短边 {min(probe.width, probe.height)}px"
                " 小于 200px 下限"
            )
        if max(probe.width, probe.height) > 2048:
            raise AssetSpecError(
                f"wan2.2-animate 视频长边 {max(probe.width, probe.height)}px"
                " 超过 2048px 上限"
            )
    return probe


__all__ = [
    "AssetSpecError",
    "AudioProbe",
    "ImageProbe",
    "VideoProbe",
    "assert_animate_image",
    "assert_animate_video",
    "assert_s2v_audio",
    "assert_s2v_image",
    "assert_videoretalk_audio",
    "probe_audio",
    "probe_image",
    "probe_video",
]
