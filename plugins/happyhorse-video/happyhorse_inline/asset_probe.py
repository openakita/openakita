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


@dataclass(frozen=True)
class MediaTarget:
    """Deterministic dimensions expected from a generated media asset."""

    aspect_ratio: str
    width: int
    height: int

    def to_dict(self) -> dict[str, object]:
        return {
            "aspect_ratio": self.aspect_ratio,
            "width": self.width,
            "height": self.height,
        }


class AssetSpecError(ValueError):
    """Raised when an asset violates a documented vendor spec.

    The message is user-facing Chinese (matches the rest of the plugin's
    error surface) and includes the documented limit alongside the
    actual observed value so users can fix the file without leaving the
    app.
    """


class MediaValidationError(AssetSpecError):
    """Raised when generated media does not match its requested dimensions."""

    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        super().__init__(str(result.get("message") or "媒体规格校验失败"))


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
        "-v",
        "error",
        "-print_format",
        "default=noprint_wrappers=1",
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


# ─── Generated-output validation ─────────────────────────────────────


def _parse_aspect_ratio(aspect_ratio: str) -> tuple[float, float]:
    try:
        left, right = str(aspect_ratio or "").split(":", 1)
        width_ratio = float(left)
        height_ratio = float(right)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效画幅比例: {aspect_ratio!r}") from exc
    if width_ratio <= 0 or height_ratio <= 0:
        raise ValueError(f"无效画幅比例: {aspect_ratio!r}")
    return width_ratio, height_ratio


def _align_dimension(value: float, *, multiple: int = 16) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def image_target_for(aspect_ratio: str, size: str) -> MediaTarget:
    """Resolve an image quality tier and aspect ratio to explicit pixels.

    ``1K``/``2K``/``4K`` describe the long edge. Explicit ``W*H`` and
    ``WxH`` inputs remain exact, but must agree with the requested ratio.
    """

    ratio_w, ratio_h = _parse_aspect_ratio(aspect_ratio)
    normalized_size = str(size or "2K").strip().upper()
    explicit = normalized_size.replace("X", "*").split("*", 1)
    if len(explicit) == 2:
        try:
            width, height = (int(part) for part in explicit)
        except ValueError as exc:
            raise ValueError(f"无效图片像素规格: {size!r}") from exc
        if width <= 0 or height <= 0:
            raise ValueError(f"无效图片像素规格: {size!r}")
        ratio_error = abs((width / height) - (ratio_w / ratio_h)) / (ratio_w / ratio_h)
        if ratio_error > 0.01:
            raise ValueError(
                f"图片像素规格 {width}x{height} 与目标画幅 {aspect_ratio} 不一致"
            )
        return MediaTarget(aspect_ratio, width, height)

    long_edges = {"1K": 1024, "2K": 2048, "4K": 4096}
    long_edge = long_edges.get(normalized_size)
    if long_edge is None:
        raise ValueError(f"不支持的图片清晰度规格: {size!r}")
    if ratio_w >= ratio_h:
        width = long_edge
        height = _align_dimension(long_edge * ratio_h / ratio_w)
    else:
        width = _align_dimension(long_edge * ratio_w / ratio_h)
        height = long_edge
    return MediaTarget(aspect_ratio, width, height)


def video_target_for(aspect_ratio: str, resolution: str) -> MediaTarget:
    """Resolve a video resolution label to explicit encoded dimensions."""

    ratio_w, ratio_h = _parse_aspect_ratio(aspect_ratio)
    normalized = str(resolution or "720P").strip().upper()
    try:
        short_edge = int(normalized.removesuffix("P"))
    except ValueError as exc:
        raise ValueError(f"无效视频清晰度规格: {resolution!r}") from exc
    if short_edge <= 0:
        raise ValueError(f"无效视频清晰度规格: {resolution!r}")
    if ratio_w >= ratio_h:
        width = _align_dimension(short_edge * ratio_w / ratio_h)
        height = short_edge
    else:
        width = short_edge
        height = _align_dimension(short_edge * ratio_h / ratio_w)
    return MediaTarget(aspect_ratio, width, height)


def validate_media_dimensions(
    path: str | Path,
    *,
    kind: str,
    target: MediaTarget,
    tolerance: float = 0.01,
) -> dict[str, object]:
    """Probe generated media and return a fail-closed validation result."""

    if kind == "image":
        probe = probe_image(path)
        duration_sec = None
    elif kind == "video":
        probe = probe_video(path)
        duration_sec = round(probe.duration_sec, 3)
    else:
        raise ValueError(f"不支持的媒体类型: {kind!r}")

    actual: dict[str, object] = {
        "width": probe.width,
        "height": probe.height,
        "format": probe.fmt,
        "size_bytes": probe.size_bytes,
    }
    if duration_sec is not None:
        actual["duration_sec"] = duration_sec
    expected = target.to_dict()

    if not probe.width or not probe.height:
        return {
            "passed": False,
            "code": "media_probe_unavailable",
            "message": "无法读取生成媒体的实际宽高，交付已阻止；请确认 ffprobe/Pillow 可用",
            "expected": expected,
            "actual": actual,
        }

    width_limit = max(2, round(target.width * max(0.0, tolerance)))
    height_limit = max(2, round(target.height * max(0.0, tolerance)))
    passed = (
        abs(probe.width - target.width) <= width_limit
        and abs(probe.height - target.height) <= height_limit
    )
    if passed:
        message = (
            f"媒体尺寸校验通过：期望 {target.width}x{target.height}，"
            f"实际 {probe.width}x{probe.height}"
        )
        code = "media_dimensions_match"
    else:
        message = (
            f"媒体尺寸不符合目标：期望 {target.aspect_ratio} "
            f"({target.width}x{target.height})，实际 {probe.width}x{probe.height}；必须重新生成"
        )
        code = "media_dimensions_mismatch"
    return {
        "passed": passed,
        "code": code,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


def validate_media_aspect(
    path: str | Path,
    *,
    kind: str,
    aspect_ratio: str,
    tolerance: float = 0.01,
) -> dict[str, object]:
    """Validate only the aspect ratio, for source assets of any resolution."""

    ratio_w, ratio_h = _parse_aspect_ratio(aspect_ratio)
    probe = probe_image(path) if kind == "image" else probe_video(path)
    actual: dict[str, object] = {
        "width": probe.width,
        "height": probe.height,
        "format": probe.fmt,
        "size_bytes": probe.size_bytes,
    }
    expected: dict[str, object] = {"aspect_ratio": aspect_ratio}
    if not probe.width or not probe.height:
        return {
            "passed": False,
            "code": "media_probe_unavailable",
            "message": "无法读取输入媒体的实际宽高，已阻止付费生成",
            "expected": expected,
            "actual": actual,
        }
    expected_ratio = ratio_w / ratio_h
    actual_ratio = probe.width / probe.height
    passed = abs(actual_ratio - expected_ratio) / expected_ratio <= max(0.0, tolerance)
    return {
        "passed": passed,
        "code": "media_aspect_match" if passed else "media_aspect_mismatch",
        "message": (
            f"输入画幅校验通过：目标 {aspect_ratio}，实际 {probe.width}x{probe.height}"
            if passed
            else (
                f"输入画幅不符合目标：期望 {aspect_ratio}，实际 "
                f"{probe.width}x{probe.height}；已阻止付费视频生成"
            )
        ),
        "expected": expected,
        "actual": actual,
    }


def assert_media_aspect(
    path: str | Path,
    *,
    kind: str,
    aspect_ratio: str,
    tolerance: float = 0.01,
) -> dict[str, object]:
    result = validate_media_aspect(
        path,
        kind=kind,
        aspect_ratio=aspect_ratio,
        tolerance=tolerance,
    )
    if not result["passed"]:
        raise MediaValidationError(result)
    return result


def assert_media_dimensions(
    path: str | Path,
    *,
    kind: str,
    target: MediaTarget,
    tolerance: float = 0.01,
) -> dict[str, object]:
    result = validate_media_dimensions(path, kind=kind, target=target, tolerance=tolerance)
    if not result["passed"]:
        raise MediaValidationError(result)
    return result


# ─── Per-endpoint assertions (raise AssetSpecError on violation) ─────


_IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "webp"}
_VIDEORETALK_AUDIO_EXTS = {"wav", "mp3", "aac"}
_S2V_AUDIO_EXTS = {"wav", "mp3"}
_ANIMATE_VIDEO_EXTS = {"mp4", "avi", "mov"}

_MB = 1024 * 1024


def _check_ext(fmt: str, allowed: set[str], label: str) -> None:
    if fmt and fmt not in allowed:
        raise AssetSpecError(f"{label} 格式不支持：当前 {fmt!r}，仅允许 {sorted(allowed)}")


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
            raise AssetSpecError(f"videoretalk 音频时长 {probe.duration_sec:.2f}s 短于下限 2.00s")
        if probe.duration_sec > 120.0:
            raise AssetSpecError(f"videoretalk 音频时长 {probe.duration_sec:.2f}s 超过上限 120.00s")
    return probe


def assert_s2v_image(path: str | Path) -> ImageProbe:
    """wan2.2-s2v image: 400..7000 px on each edge, JPG/PNG/BMP/WEBP."""
    probe = probe_image(path)
    _check_ext(probe.fmt, _IMAGE_EXTS, "wan2.2-s2v 图像")
    if probe.width and probe.height:
        if min(probe.width, probe.height) < 400:
            raise AssetSpecError(
                f"wan2.2-s2v 图像短边 {min(probe.width, probe.height)}px 小于 400px 下限"
            )
        if max(probe.width, probe.height) > 7000:
            raise AssetSpecError(
                f"wan2.2-s2v 图像长边 {max(probe.width, probe.height)}px 超过 7000px 上限"
            )
    return probe


def assert_s2v_audio(path: str | Path) -> AudioProbe:
    """wan2.2-s2v audio: 2..20 s, <=15 MB, wav/mp3."""
    probe = probe_audio(path)
    _check_ext(probe.fmt, _S2V_AUDIO_EXTS, "wan2.2-s2v 音频")
    if probe.size_bytes > 15 * _MB:
        raise AssetSpecError(f"wan2.2-s2v 音频大小 {probe.size_bytes / _MB:.1f} MB 超过 15 MB 上限")
    if probe.duration_sec:
        if probe.duration_sec < 2.0:
            raise AssetSpecError(f"wan2.2-s2v 音频时长 {probe.duration_sec:.2f}s 短于下限 2.00s")
        if probe.duration_sec > 20.0:
            raise AssetSpecError(f"wan2.2-s2v 音频时长 {probe.duration_sec:.2f}s 超过上限 20.00s")
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
                f"wan2.2-animate 图像短边 {min(probe.width, probe.height)}px 小于 200px 下限"
            )
        if max(probe.width, probe.height) > 4096:
            raise AssetSpecError(
                f"wan2.2-animate 图像长边 {max(probe.width, probe.height)}px 超过 4096px 上限"
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
                f"wan2.2-animate 视频短边 {min(probe.width, probe.height)}px 小于 200px 下限"
            )
        if max(probe.width, probe.height) > 2048:
            raise AssetSpecError(
                f"wan2.2-animate 视频长边 {max(probe.width, probe.height)}px 超过 2048px 上限"
            )
    return probe


__all__ = [
    "AssetSpecError",
    "AudioProbe",
    "ImageProbe",
    "MediaTarget",
    "MediaValidationError",
    "VideoProbe",
    "assert_animate_image",
    "assert_animate_video",
    "assert_s2v_audio",
    "assert_s2v_image",
    "assert_videoretalk_audio",
    "assert_media_aspect",
    "assert_media_dimensions",
    "image_target_for",
    "probe_audio",
    "probe_image",
    "probe_video",
    "validate_media_dimensions",
    "validate_media_aspect",
    "video_target_for",
]
