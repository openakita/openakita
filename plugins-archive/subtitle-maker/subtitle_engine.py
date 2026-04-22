"""subtitle-maker — produce SRT / VTT from ASR chunks; burn into video.

Phase 2-04 of the overhaul playbook removes the cross-plugin shim that
physically reached into a sibling plugin's ``providers`` /
``highlight_engine`` modules. ASR now flows through
:mod:`openakita_plugin_sdk.contrib.asr`, so this plugin no longer
depends on highlight-cutter's load order or source layout.
"""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _shared.asr import (
    ASRError,
    select_provider as _sdk_select_asr,
)

logger = logging.getLogger(__name__)


# ── transcript dataclass (back-compat with the old import shape) ──────


@dataclass
class TranscriptChunk:
    """Sentence-ish granularity transcript segment.

    Field names match the contrib.asr ``ASRChunk`` shape so callers can
    read either type without renaming attributes.
    """

    start: float
    end: float
    text: str
    confidence: float = 1.0


# ── credentials registry ──────────────────────────────────────────────


_CREDENTIALS: dict[str, str | None] = {
    "dashscope_api_key": None,
}


def configure_credentials(
    *,
    dashscope_api_key: str | None = None,
) -> None:
    """Hot-update credentials used by subsequent ASR provider builds."""
    if dashscope_api_key is not None:
        _CREDENTIALS["dashscope_api_key"] = dashscope_api_key or None


def _build_asr_configs(*, model: str, binary: str) -> dict[str, dict[str, Any]]:
    dk = _CREDENTIALS.get("dashscope_api_key")
    return {
        "dashscope_paraformer": {"api_key": dk} if dk else {},
        "whisper_local": {"binary": binary, "model": model},
        "stub": {},
    }


async def transcribe_with_contrib_asr(
    source: Path,
    *,
    provider_id: str = "auto",
    region: str = "cn",
    language: str = "auto",
    model: str = "base",
    binary: str = "whisper-cli",
    allow_stub: bool = False,
) -> list[TranscriptChunk]:
    """Transcribe ``source`` using a ``contrib.asr`` provider.

    Returns an empty list if no provider is available — the caller
    surfaces a coached error so the user can install whisper.cpp or
    configure DashScope.
    """
    configs = _build_asr_configs(model=model, binary=binary)
    try:
        provider = _sdk_select_asr(
            provider_id, configs=configs, region=region, allow_stub=allow_stub,
        )
    except ASRError as exc:
        logger.warning("contrib.asr select failed: %s", exc)
        return []
    try:
        result = await provider.transcribe(source, language=language)
    except ASRError as exc:
        logger.warning("contrib.asr transcribe failed: %s", exc)
        return []
    return [
        TranscriptChunk(start=c.start, end=c.end, text=c.text, confidence=c.confidence)
        for c in result.chunks
    ]


__all__ = [
    "TranscriptChunk",
    "burn_subtitles_command",
    "configure_credentials",
    "to_srt",
    "to_vtt",
    "transcribe_with_contrib_asr",
]


# ── format conversion ──────────────────────────────────────────────────


def _format_ts_srt(seconds: float) -> str:
    """SRT timestamps look like ``00:00:01,234``."""
    s = max(0.0, seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    whole = int(sec)
    millis = int(round((sec - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    return f"{h:02d}:{m:02d}:{whole:02d},{millis:03d}"


def _format_ts_vtt(seconds: float) -> str:
    """WebVTT timestamps use ``.`` instead of ``,``."""
    return _format_ts_srt(seconds).replace(",", ".")


def to_srt(chunks: Iterable[TranscriptChunk]) -> str:
    """Render a list of transcript chunks as a single SRT text."""
    out: list[str] = []
    for i, c in enumerate(chunks, 1):
        text = (c.text or "").strip()
        if not text:
            continue
        out.append(str(i))
        out.append(f"{_format_ts_srt(c.start)} --> {_format_ts_srt(c.end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_vtt(chunks: Iterable[TranscriptChunk]) -> str:
    out: list[str] = ["WEBVTT", ""]
    for i, c in enumerate(chunks, 1):
        text = (c.text or "").strip()
        if not text:
            continue
        out.append(f"{i}")
        out.append(f"{_format_ts_vtt(c.start)} --> {_format_ts_vtt(c.end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── burn-in via ffmpeg ─────────────────────────────────────────────────


def burn_subtitles_command(
    *,
    source_video: Path,
    srt_file: Path,
    output: Path,
    fps: int = 24,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that burns ``srt_file`` into ``source_video``."""
    bin_path = ffmpeg if Path(ffmpeg).is_absolute() else (shutil.which(ffmpeg) or ffmpeg)
    srt_arg = str(srt_file.as_posix()).replace(":", r"\\:")
    return [
        bin_path, "-y", "-hide_banner",
        "-i", str(source_video),
        "-vf", f"subtitles='{srt_arg}'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-r", str(fps), "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
