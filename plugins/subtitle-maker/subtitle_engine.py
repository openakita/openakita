"""subtitle-maker — produce SRT / VTT / ASS from ASR chunks; burn into video.

Reuses ``plugins/highlight-cutter/highlight_engine.py`` for the ASR step
to avoid duplicating the whisper.cpp wrapper.  We import lazily (sys.path
hack) so the dependency is explicit but isolated.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Reuse highlight-cutter's ASR without polluting sys.path (avoids name
# collisions when several plugins each define their own ``providers.py``
# / ``highlight_engine.py``).  Loaded under a unique module name.
def _load_sibling(plugin_dir_name: str, module_name: str, alias: str):
    src = Path(__file__).resolve().parent.parent / plugin_dir_name / f"{module_name}.py"
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


_hc = _load_sibling("highlight-cutter", "highlight_engine", "_oa_hc_engine")
TranscriptChunk = _hc.TranscriptChunk           # noqa: F401
whisper_cpp_transcribe = _hc.whisper_cpp_transcribe  # noqa: F401

__all__ = [
    "TranscriptChunk", "whisper_cpp_transcribe",
    "to_srt", "to_vtt", "burn_subtitles_command",
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
    if millis == 1000:  # rounding overflow
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
    *, source_video: Path, srt_file: Path, output: Path,
    fps: int = 24, ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that burns ``srt_file`` into ``source_video``.

    Returns the command list — caller invokes ``subprocess.run(cmd, timeout=...)``.
    """
    bin_path = ffmpeg if Path(ffmpeg).is_absolute() else (shutil.which(ffmpeg) or ffmpeg)
    # ffmpeg subtitle filter wants forward slashes + escaped colons on Windows
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
