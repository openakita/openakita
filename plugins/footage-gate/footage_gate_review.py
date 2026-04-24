# ruff: noqa: N999
"""Source media review — port of OpenMontage `lib/source_media_review.py`.

Re-implements the C6 ``SourceMediaReview`` atom with two deliberate
deviations from upstream:

1. **No ``tool_registry``** — upstream calls
   ``tool_registry.get_tool("audio_probe").execute(...)`` and falls
   through to ``ffprobe`` only when the registry returns ``None``.
   That registry was retired in OpenMontage PR #46 (the API became
   ``registry.get(...)`` and the dataclass-driven ``ToolDescriptor`` was
   removed); plugins that still call ``.get_tool`` crash on import. We
   skip the registry entirely and call ffprobe / PIL directly.
2. **Always emit ``usable_for``** — upstream Issue #44 reported the
   field was missing for image inputs (``_probe_image`` returned a
   stripped dict). We surface the field for ALL three media kinds so the
   v2.0 cross-plugin handoff has a uniform shape on the receiving end
   (``footage_gate_inline.upload_preview`` consumers in subtitle-craft
   v2.0 read ``usable_for`` to gate their own pipeline).

The optional Paraformer transcription is *not* baked into this module —
the plugin layer (Phase 4) injects it via a callback so we can keep this
file ffprobe-only and unit-test it without an API key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from footage_gate_ffmpeg import (
    FFmpegError,
    ffprobe_json,
    first_audio_stream,
    first_video_stream,
    parse_fps,
)
from footage_gate_models import RISK_THRESHOLDS

logger = logging.getLogger(__name__)


_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"})
_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus"})
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"})


# ── Type detection ───────────────────────────────────────────────────────


def detect_media_type(path: Path) -> str | None:
    """Classify a file as ``video`` / ``audio`` / ``image`` by extension."""
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    return None


# ── Per-kind probes ──────────────────────────────────────────────────────


def _probe_video(path: Path, *, ffprobe_path: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "technical_probe": {},
        "quality_risks": [],
    }
    try:
        probe = ffprobe_json(path, ffprobe_path=ffprobe_path)
    except FFmpegError as exc:
        result["quality_risks"].append(f"Could not probe file: {exc}")
        return result

    fmt = probe.get("format", {})
    vstream = first_video_stream(probe)
    astream = first_audio_stream(probe)
    width = int(vstream.get("width", 0) or 0)
    height = int(vstream.get("height", 0) or 0)
    duration_sec = float(fmt.get("duration", 0) or 0)
    channels = int(astream.get("channels", 0) or 0) if astream else 0

    result["technical_probe"] = {
        "duration_seconds": duration_sec,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else "",
        "fps": parse_fps(vstream.get("r_frame_rate", "0/1")),
        "codec": vstream.get("codec_name", "unknown"),
        "color_transfer": (vstream.get("color_transfer") or "").lower(),
        "audio_codec": astream.get("codec_name", "") if astream else "",
        "sample_rate": int(astream.get("sample_rate", 0) or 0) if astream else 0,
        "channels": channels,
        "file_size_bytes": int(fmt.get("size", 0) or 0),
        "bitrate_kbps": round(int(fmt.get("bit_rate", 0) or 0) / 1000, 1),
    }

    risks: list[str] = result["quality_risks"]
    min_w = int(RISK_THRESHOLDS["video_min_width"])
    min_h = int(RISK_THRESHOLDS["video_min_height"])
    if width and (width < min_w or height < min_h):
        risks.append(f"Low resolution ({width}x{height}) — may appear pixelated in final output")
    if 0 < duration_sec < float(RISK_THRESHOLDS["video_min_duration_sec"]):
        risks.append(f"Very short clip ({duration_sec:.1f}s) — limited usability")
    if astream and channels == 1:
        risks.append("Mono audio — consider if stereo output is expected")

    return result


def _probe_audio(path: Path, *, ffprobe_path: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"technical_probe": {}, "quality_risks": []}
    try:
        probe = ffprobe_json(path, ffprobe_path=ffprobe_path)
    except FFmpegError as exc:
        result["quality_risks"].append(f"Could not probe audio: {exc}")
        return result

    fmt = probe.get("format", {})
    stream = first_audio_stream(probe)
    duration_sec = float(fmt.get("duration", 0) or 0)
    channels = int(stream.get("channels", 0) or 0) if stream else 0

    result["technical_probe"] = {
        "duration_seconds": duration_sec,
        "audio_codec": stream.get("codec_name", "unknown") if stream else "unknown",
        "sample_rate": int(stream.get("sample_rate", 0) or 0) if stream else 0,
        "channels": channels,
        "file_size_bytes": int(fmt.get("size", 0) or 0),
        "bitrate_kbps": round(int(fmt.get("bit_rate", 0) or 0) / 1000, 1),
    }

    risks: list[str] = result["quality_risks"]
    if 0 < duration_sec < float(RISK_THRESHOLDS["audio_min_duration_sec"]):
        risks.append(f"Very short audio ({duration_sec:.2f}s) — limited usability")
    if channels == 0:
        risks.append("No audio stream detected")
    return result


def _probe_image(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"technical_probe": {}, "quality_risks": []}
    width = height = 0
    fmt_name = ""
    file_size = 0
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        result["quality_risks"].append(f"Could not stat image: {exc}")

    try:
        # PIL is the only optional dep we touch from this module; falls
        # back to "file size only" when missing so the route still works.
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(path) as img:
            width, height = img.size
            fmt_name = img.format or ""
    except ImportError:
        logger.debug("Pillow not installed — using file-size only for %s", path)
    except Exception as exc:  # noqa: BLE001 — broad PIL surface
        result["quality_risks"].append(f"Could not probe image: {exc}")

    result["technical_probe"] = {
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else "",
        "codec": fmt_name or "unknown",
        "file_size_bytes": file_size,
    }

    min_w = int(RISK_THRESHOLDS["image_min_width"])
    min_h = int(RISK_THRESHOLDS["image_min_height"])
    if width and (width < min_w or height < min_h):
        result["quality_risks"].append(f"Low resolution ({width}x{height}) — may need upscaling")
    if file_size and file_size < int(RISK_THRESHOLDS["image_min_filesize_kb"]) * 1024:
        result["quality_risks"].append(
            f"Tiny file ({file_size} bytes) — likely corrupt or thumbnail-only"
        )
    return result


# ── Usability inference (vs OpenMontage Issue #44) ───────────────────────


def _infer_video_usability(probe: dict[str, Any], transcript: str | None) -> list[str]:
    uses: list[str] = []
    dur = float(probe.get("duration_seconds", 0) or 0)
    if dur > 10:
        uses.append("hero footage")
    if dur > 3:
        uses.append("b-roll")
    if transcript:
        uses.append("source dialogue")
    if probe.get("audio_codec"):
        uses.append("source audio")
    return uses or ["short clip"]


def _infer_audio_usability(probe: dict[str, Any], transcript: str | None) -> list[str]:
    uses: list[str] = []
    dur = float(probe.get("duration_seconds", 0) or 0)
    if transcript:
        uses.append("narration source")
    if dur > 30:
        uses.append("background music candidate")
    if dur > 5:
        uses.append("sound effect or ambient")
    return uses or ["audio clip"]


def _infer_image_usability(probe: dict[str, Any]) -> list[str]:
    """Always non-empty (vs OpenMontage Issue #44)."""
    width = int(probe.get("width", 0) or 0)
    height = int(probe.get("height", 0) or 0)
    if width and height and width >= 1024 and height >= 1024:
        return ["hero still", "visual asset", "reference image"]
    return ["visual asset", "reference image"]


# ── Top-level entry ──────────────────────────────────────────────────────


def review_source_media(
    files: list[Path],
    *,
    transcribe: Callable[[Path, str], str | None] | None = None,
    ffprobe_path: str | None = None,
) -> dict[str, Any]:
    """Inspect each file and return a normalised review artifact.

    Args:
        files: Paths to user-supplied media files.
        transcribe: Optional callback ``(path, media_type) -> transcript_text``
            invoked for video / audio inputs. Plugin layer wires the
            DashScope Paraformer client in here when the user toggles
            "transcribe with Paraformer". Returning ``None`` (or raising
            inside) silently skips transcription — the review is still
            produced. Pass ``None`` to disable transcription entirely.
        ffprobe_path: Optional override; falls back to ``shutil.which``.

    The artifact shape matches the OpenMontage v1.0 contract so any
    consumer that already speaks ``source_media_review`` can read our
    output without changes. Every entry contains ``usable_for`` (vs Issue
    #44) including image inputs.
    """
    reviewed_files: list[dict[str, Any]] = []
    summaries: list[str] = []
    implications: list[str] = []

    for file_path in files:
        media_type = detect_media_type(file_path)
        if media_type is None:
            logger.warning("Skipping unrecognized file type: %s", file_path)
            continue
        if not file_path.exists():
            logger.warning("File does not exist: %s", file_path)
            continue

        entry: dict[str, Any] = {
            "path": str(file_path),
            "media_type": media_type,
            "reviewed": True,
        }

        if media_type == "video":
            probe_data = _probe_video(file_path, ffprobe_path=ffprobe_path)
        elif media_type == "audio":
            probe_data = _probe_audio(file_path, ffprobe_path=ffprobe_path)
        else:
            probe_data = _probe_image(file_path)

        entry["technical_probe"] = probe_data.get("technical_probe", {})
        entry["quality_risks"] = probe_data.get("quality_risks", [])
        entry["representative_frames"] = []

        transcript: str | None = None
        if transcribe and media_type in ("video", "audio"):
            try:
                transcript = transcribe(file_path, media_type)
            except Exception as exc:  # noqa: BLE001 — caller-supplied callback
                logger.warning("Transcription callback raised: %s", exc)
                transcript = None
        if transcript:
            entry["transcript_summary"] = transcript

        probe = entry["technical_probe"]
        if media_type == "video":
            dur = probe.get("duration_seconds", 0)
            res = probe.get("resolution", "unknown") or "unknown"
            has_audio = bool(probe.get("audio_codec"))
            entry["content_summary"] = (
                f"Video file: {dur:.1f}s at {res}, {'with' if has_audio else 'without'} audio"
            )
            entry["usable_for"] = _infer_video_usability(probe, transcript)
        elif media_type == "audio":
            dur = probe.get("duration_seconds", 0)
            entry["content_summary"] = (
                f"Audio file: {dur:.1f}s, {probe.get('audio_codec', 'unknown')}"
            )
            entry["usable_for"] = _infer_audio_usability(probe, transcript)
        else:
            res = probe.get("resolution", "unknown") or "unknown"
            entry["content_summary"] = f"Image file: {res}"
            entry["usable_for"] = _infer_image_usability(probe)

        summaries.append(f"{file_path.name}: {entry['content_summary']}")
        reviewed_files.append(entry)
        for risk in entry["quality_risks"]:
            implications.append(f"Quality risk in {file_path.name}: {risk}")

    if not reviewed_files:
        summary = "No user-supplied media files could be reviewed."
        implications.append("No source media available — production is fully generated.")
    else:
        summary = "; ".join(summaries)

    has_video = any(f["media_type"] == "video" for f in reviewed_files)
    has_audio = any(f["media_type"] == "audio" for f in reviewed_files)
    has_images = any(f["media_type"] == "image" for f in reviewed_files)
    if has_video:
        implications.append(
            "Source video available — consider source-led or hybrid production approach"
        )
    if has_audio and not has_video:
        implications.append("Audio-only source — production needs visual assets to accompany audio")
    if has_images and not has_video:
        implications.append(
            "Image-only source — motion must come from animation or video generation"
        )
    if not implications:
        implications.append("No specific constraints identified from source media.")

    return {
        "version": "1.0",
        "files": reviewed_files,
        "summary": summary,
        "planning_implications": implications,
    }


__all__ = [
    "detect_media_type",
    "review_source_media",
]
