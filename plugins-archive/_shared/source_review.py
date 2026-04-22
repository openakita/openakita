"""Source media review — pre-flight quality probe for video/image/audio inputs.

Inspired by OpenMontage ``source_media_review.py:104-115`` and CutClaw's
``video_screener``.  Surface common "garbage in → garbage out" failures
**before** the user spends API quota on a doomed task:

- A 240p smartphone screen recording fails the resolution gate for video
  generation
- A 1.5-second sound bite fails the minimum duration gate for transcription
- A monochannel/mono input fails the audio gate for stereo TTS dubbing
- A 320x240 thumbnail fails the resolution gate for poster generation

Design rules:

- **Pure metadata only**: no pixel/sample reading.  Uses
  :func:`~openakita_plugin_sdk.contrib.ffmpeg.ffprobe_json_sync` so the
  caller already declared the ``ffprobe`` dependency.
- **Tunable thresholds via dataclass overrides** — no hidden defaults.
- **Returns a structured report** so plugins can render it in the
  ``intent_verifier`` panel and in ``error-coach`` if a gate fails.
- Zero extra deps (stdlib + ``contrib.ffmpeg``).

Thresholds (defaults from OpenMontage / video-use findings):

- Video: ``>= 720x480``, ``>= 3.0s``, ``fps >= 15``
- Audio: ``>= 3.0s``, ``sample_rate >= 16000``, ``channels >= 1``
- Image: ``>= 640x480``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .ffmpeg import FFmpegError, ffprobe_json_sync

logger = logging.getLogger(__name__)

__all__ = [
    "ReviewIssue",
    "ReviewReport",
    "ReviewThresholds",
    "review_audio",
    "review_image",
    "review_source",
    "review_video",
]


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ReviewIssue:
    """A single problem found during review."""

    code: str          # stable machine code, e.g. ``"video.too_short"``
    severity: Severity
    message: str       # human-readable, plugin-localizable later
    metric: str        # which metric failed, e.g. ``"duration_sec"``
    actual: Any        # actual value
    expected: Any      # expected threshold value


@dataclass(frozen=True)
class ReviewReport:
    """Result of reviewing one source file."""

    source: str
    kind: Literal["video", "audio", "image", "unknown"]
    metadata: dict[str, Any] = field(default_factory=dict)
    issues: tuple[ReviewIssue, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """True when no ``error``-severity issue is present."""
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> tuple[ReviewIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ReviewIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "passed": self.passed,
            "metadata": dict(self.metadata),
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "message": i.message,
                    "metric": i.metric,
                    "actual": i.actual,
                    "expected": i.expected,
                }
                for i in self.issues
            ],
        }


@dataclass(frozen=True)
class ReviewThresholds:
    """Tunable thresholds.  Pass an instance to override defaults."""

    video_min_width: int = 720
    video_min_height: int = 480
    video_min_duration_sec: float = 3.0
    video_min_fps: float = 15.0
    audio_min_duration_sec: float = 3.0
    audio_min_sample_rate: int = 16000
    audio_min_channels: int = 1
    image_min_width: int = 640
    image_min_height: int = 480

    # Soft thresholds (warning, not error)
    video_warn_max_duration_sec: float = 60.0 * 60  # 1 hour — re-encode is expensive
    audio_warn_max_duration_sec: float = 60.0 * 30  # 30 min — long ASR


_DEFAULT_THRESHOLDS = ReviewThresholds()


_VIDEO_CODECS = {"h264", "hevc", "h265", "vp8", "vp9", "av1", "mpeg2video", "mpeg4", "prores"}
_IMAGE_CODECS = {"mjpeg", "png", "webp", "bmp", "gif", "tiff"}
_AUDIO_ONLY_FORMATS = {"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"}


def _ffprobe_safe(path: Path, *, ffprobe: str, timeout: float) -> dict[str, Any]:
    try:
        return ffprobe_json_sync(path, timeout_sec=timeout, ffprobe=ffprobe)
    except FFmpegError as e:
        logger.debug("ffprobe failed for %s: %s", path, e)
        return {}


def _parse_fps(rate_str: str) -> float:
    """Parse ``"30000/1001"`` style fraction → float (returns 0.0 on error)."""
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        try:
            num, den = rate_str.split("/", 1)
            d = float(den)
            return float(num) / d if d else 0.0
        except (ValueError, TypeError):
            return 0.0
    try:
        return float(rate_str)
    except (ValueError, TypeError):
        return 0.0


def _detect_kind(probe: dict[str, Any]) -> Literal["video", "audio", "image", "unknown"]:
    streams = probe.get("streams", []) or []
    has_video = False
    has_audio = False
    is_image = False
    n_frames = 0
    for s in streams:
        ctype = s.get("codec_type", "")
        if ctype == "video":
            has_video = True
            codec = (s.get("codec_name") or "").lower()
            # ffprobe reports "nb_frames" for static images as "1"
            try:
                n_frames = int(s.get("nb_frames", 0) or 0)
            except (ValueError, TypeError):
                n_frames = 0
            if codec in _IMAGE_CODECS and n_frames in (0, 1):
                is_image = True
        elif ctype == "audio":
            has_audio = True
    if is_image and not has_audio:
        return "image"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return "unknown"


def _video_stream(probe: dict[str, Any]) -> dict[str, Any] | None:
    for s in probe.get("streams", []) or []:
        if s.get("codec_type") == "video":
            return s
    return None


def _audio_stream(probe: dict[str, Any]) -> dict[str, Any] | None:
    for s in probe.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            return s
    return None


def _format_duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format", {}) or {}
    try:
        return float(fmt.get("duration", 0.0))
    except (ValueError, TypeError):
        return 0.0


# ── public API ──────────────────────────────────────────────────────────────


def review_video(
    source: str | Path,
    *,
    thresholds: ReviewThresholds = _DEFAULT_THRESHOLDS,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 15.0,
) -> ReviewReport:
    """Probe a video file and return a ``ReviewReport``.

    Issues are produced *before* any expensive API call.  See
    :class:`ReviewThresholds` for tunables.

    The function never raises for missing/unreadable files — instead it
    returns a report with an ``error`` issue (so callers can render it
    uniformly).
    """
    p = Path(source)
    if not p.exists():
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.missing", severity="error",
                message=f"\u6587\u4ef6\u4e0d\u5b58\u5728: {source}",
                metric="exists", actual=False, expected=True,
            ),),
        )

    probe = _ffprobe_safe(p, ffprobe=ffprobe, timeout=ffprobe_timeout_sec)
    if not probe:
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.probe_failed", severity="error",
                message="ffprobe \u65e0\u6cd5\u8bfb\u53d6\u6587\u4ef6\uff0c\u53ef\u80fd\u4e0d\u662f\u6709\u6548\u7684\u5a92\u4f53\u6587\u4ef6",
                metric="probe", actual=None, expected="valid media",
            ),),
        )

    issues: list[ReviewIssue] = []
    vstream = _video_stream(probe)
    metadata: dict[str, Any] = {}

    if vstream is None:
        issues.append(ReviewIssue(
            code="video.no_stream", severity="error",
            message="\u8be5\u6587\u4ef6\u4e0d\u5305\u542b\u89c6\u9891\u8f68",
            metric="streams.video", actual=0, expected=">=1",
        ))
        return ReviewReport(source=str(source), kind="unknown",
                            metadata=metadata, issues=tuple(issues))

    width = int(vstream.get("width", 0) or 0)
    height = int(vstream.get("height", 0) or 0)
    duration = _format_duration(probe)
    fps = _parse_fps(vstream.get("avg_frame_rate", "") or vstream.get("r_frame_rate", ""))
    codec = (vstream.get("codec_name") or "").lower()
    metadata = {
        "width": width, "height": height, "duration_sec": duration,
        "fps": round(fps, 2), "codec": codec,
    }

    if width < thresholds.video_min_width or height < thresholds.video_min_height:
        issues.append(ReviewIssue(
            code="video.resolution_too_low", severity="error",
            message=f"\u5206\u8fa8\u7387 {width}x{height} \u4f4e\u4e8e\u8981\u6c42 "
                    f"{thresholds.video_min_width}x{thresholds.video_min_height}",
            metric="resolution", actual=f"{width}x{height}",
            expected=f">={thresholds.video_min_width}x{thresholds.video_min_height}",
        ))
    if duration > 0 and duration < thresholds.video_min_duration_sec:
        issues.append(ReviewIssue(
            code="video.too_short", severity="error",
            message=f"\u89c6\u9891\u65f6\u957f {duration:.1f}s \u5c11\u4e8e\u6700\u5c0f\u8981\u6c42 "
                    f"{thresholds.video_min_duration_sec:.1f}s",
            metric="duration_sec", actual=round(duration, 2),
            expected=f">={thresholds.video_min_duration_sec}",
        ))
    if fps > 0 and fps < thresholds.video_min_fps:
        issues.append(ReviewIssue(
            code="video.fps_too_low", severity="warning",
            message=f"\u5e27\u7387 {fps:.1f} \u4f4e\u4e8e\u63a8\u8350\u503c {thresholds.video_min_fps}",
            metric="fps", actual=round(fps, 2), expected=f">={thresholds.video_min_fps}",
        ))
    if duration > thresholds.video_warn_max_duration_sec:
        issues.append(ReviewIssue(
            code="video.too_long", severity="warning",
            message=f"\u89c6\u9891\u65f6\u957f {duration/60:.1f} \u5206\u949f\u8f83\u957f\uff0c\u5904\u7406\u53ef\u80fd\u8d85\u8fc7\u9884\u4f30\u65f6\u95f4",
            metric="duration_sec", actual=round(duration, 2),
            expected=f"<={thresholds.video_warn_max_duration_sec}",
        ))

    return ReviewReport(source=str(source), kind="video",
                        metadata=metadata, issues=tuple(issues))


def review_audio(
    source: str | Path,
    *,
    thresholds: ReviewThresholds = _DEFAULT_THRESHOLDS,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 15.0,
) -> ReviewReport:
    """Probe an audio file (or audio track of a video) and return a report."""
    p = Path(source)
    if not p.exists():
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.missing", severity="error",
                message=f"\u6587\u4ef6\u4e0d\u5b58\u5728: {source}",
                metric="exists", actual=False, expected=True,
            ),),
        )

    probe = _ffprobe_safe(p, ffprobe=ffprobe, timeout=ffprobe_timeout_sec)
    if not probe:
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.probe_failed", severity="error",
                message="ffprobe \u65e0\u6cd5\u8bfb\u53d6\u6587\u4ef6",
                metric="probe", actual=None, expected="valid media",
            ),),
        )

    astream = _audio_stream(probe)
    issues: list[ReviewIssue] = []

    if astream is None:
        issues.append(ReviewIssue(
            code="audio.no_stream", severity="error",
            message="\u8be5\u6587\u4ef6\u4e0d\u5305\u542b\u97f3\u9891\u8f68",
            metric="streams.audio", actual=0, expected=">=1",
        ))
        return ReviewReport(source=str(source), kind="unknown",
                            metadata={}, issues=tuple(issues))

    duration = _format_duration(probe)
    sample_rate = int(astream.get("sample_rate", 0) or 0)
    channels = int(astream.get("channels", 0) or 0)
    codec = (astream.get("codec_name") or "").lower()
    metadata: dict[str, Any] = {
        "duration_sec": round(duration, 2),
        "sample_rate": sample_rate,
        "channels": channels,
        "codec": codec,
    }

    if duration > 0 and duration < thresholds.audio_min_duration_sec:
        issues.append(ReviewIssue(
            code="audio.too_short", severity="error",
            message=f"\u97f3\u9891\u65f6\u957f {duration:.1f}s \u5c11\u4e8e\u6700\u5c0f\u8981\u6c42 "
                    f"{thresholds.audio_min_duration_sec:.1f}s",
            metric="duration_sec", actual=round(duration, 2),
            expected=f">={thresholds.audio_min_duration_sec}",
        ))
    if sample_rate and sample_rate < thresholds.audio_min_sample_rate:
        issues.append(ReviewIssue(
            code="audio.sample_rate_too_low", severity="warning",
            message=f"\u91c7\u6837\u7387 {sample_rate}Hz \u4f4e\u4e8e\u63a8\u8350\u503c "
                    f"{thresholds.audio_min_sample_rate}Hz\uff0cASR \u51c6\u786e\u7387\u53ef\u80fd\u4e0b\u964d",
            metric="sample_rate", actual=sample_rate,
            expected=f">={thresholds.audio_min_sample_rate}",
        ))
    if channels < thresholds.audio_min_channels:
        issues.append(ReviewIssue(
            code="audio.channels_too_few", severity="error",
            message=f"\u58f0\u9053\u6570 {channels} \u5c11\u4e8e\u8981\u6c42 {thresholds.audio_min_channels}",
            metric="channels", actual=channels, expected=f">={thresholds.audio_min_channels}",
        ))
    if duration > thresholds.audio_warn_max_duration_sec:
        issues.append(ReviewIssue(
            code="audio.too_long", severity="warning",
            message=f"\u97f3\u9891\u65f6\u957f {duration/60:.1f} \u5206\u949f\u8f83\u957f",
            metric="duration_sec", actual=round(duration, 2),
            expected=f"<={thresholds.audio_warn_max_duration_sec}",
        ))

    return ReviewReport(source=str(source), kind="audio",
                        metadata=metadata, issues=tuple(issues))


def review_image(
    source: str | Path,
    *,
    thresholds: ReviewThresholds = _DEFAULT_THRESHOLDS,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 10.0,
) -> ReviewReport:
    """Probe a still image and return a report."""
    p = Path(source)
    if not p.exists():
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.missing", severity="error",
                message=f"\u6587\u4ef6\u4e0d\u5b58\u5728: {source}",
                metric="exists", actual=False, expected=True,
            ),),
        )

    probe = _ffprobe_safe(p, ffprobe=ffprobe, timeout=ffprobe_timeout_sec)
    if not probe:
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.probe_failed", severity="error",
                message="ffprobe \u65e0\u6cd5\u8bfb\u53d6\u6587\u4ef6",
                metric="probe", actual=None, expected="valid media",
            ),),
        )

    vstream = _video_stream(probe)
    if vstream is None:
        return ReviewReport(source=str(source), kind="unknown",
                            metadata={},
                            issues=(ReviewIssue(
                                code="image.no_stream", severity="error",
                                message="\u8be5\u6587\u4ef6\u4e0d\u662f\u6709\u6548\u56fe\u50cf",
                                metric="streams.video", actual=0, expected=">=1",
                            ),))

    width = int(vstream.get("width", 0) or 0)
    height = int(vstream.get("height", 0) or 0)
    codec = (vstream.get("codec_name") or "").lower()
    metadata = {"width": width, "height": height, "codec": codec}
    issues: list[ReviewIssue] = []

    if width < thresholds.image_min_width or height < thresholds.image_min_height:
        issues.append(ReviewIssue(
            code="image.resolution_too_low", severity="error",
            message=f"\u5206\u8fa8\u7387 {width}x{height} \u4f4e\u4e8e\u8981\u6c42 "
                    f"{thresholds.image_min_width}x{thresholds.image_min_height}",
            metric="resolution", actual=f"{width}x{height}",
            expected=f">={thresholds.image_min_width}x{thresholds.image_min_height}",
        ))

    return ReviewReport(source=str(source), kind="image",
                        metadata=metadata, issues=tuple(issues))


def review_source(
    source: str | Path,
    *,
    thresholds: ReviewThresholds = _DEFAULT_THRESHOLDS,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 15.0,
) -> ReviewReport:
    """Auto-detect kind (video/audio/image) and dispatch the appropriate review.

    Convenience wrapper for plugins that don't know the input kind in
    advance (e.g. drag-and-drop UI).
    """
    p = Path(source)
    if not p.exists():
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.missing", severity="error",
                message=f"\u6587\u4ef6\u4e0d\u5b58\u5728: {source}",
                metric="exists", actual=False, expected=True,
            ),),
        )
    probe = _ffprobe_safe(p, ffprobe=ffprobe, timeout=ffprobe_timeout_sec)
    if not probe:
        return ReviewReport(
            source=str(source), kind="unknown",
            issues=(ReviewIssue(
                code="source.probe_failed", severity="error",
                message="ffprobe \u65e0\u6cd5\u8bfb\u53d6\u6587\u4ef6",
                metric="probe", actual=None, expected="valid media",
            ),),
        )
    kind = _detect_kind(probe)
    if kind == "video":
        return review_video(source, thresholds=thresholds, ffprobe=ffprobe,
                            ffprobe_timeout_sec=ffprobe_timeout_sec)
    if kind == "audio":
        return review_audio(source, thresholds=thresholds, ffprobe=ffprobe,
                            ffprobe_timeout_sec=ffprobe_timeout_sec)
    if kind == "image":
        return review_image(source, thresholds=thresholds, ffprobe=ffprobe,
                            ffprobe_timeout_sec=ffprobe_timeout_sec)
    return ReviewReport(
        source=str(source), kind="unknown",
        metadata={"format": probe.get("format", {}).get("format_name", "")},
        issues=(ReviewIssue(
            code="source.unknown_kind", severity="error",
            message="\u65e0\u6cd5\u8bc6\u522b\u7684\u5a92\u4f53\u7c7b\u578b",
            metric="kind", actual="unknown", expected="video|audio|image",
        ),),
    )
