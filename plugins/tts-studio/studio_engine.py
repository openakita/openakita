"""tts-studio — script → multi-segment audio.

Reuses ``plugins/avatar-speaker/providers.py`` for TTS so we don't
duplicate the multi-vendor logic.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


def _load_sibling(plugin_dir_name: str, module_name: str, alias: str):
    src = Path(__file__).resolve().parent.parent / plugin_dir_name / f"{module_name}.py"
    if alias in sys.modules: return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


_avp = _load_sibling("avatar-speaker", "providers", "_oa_avatar_providers")
PRESET_VOICES_ZH = _avp.PRESET_VOICES_ZH
TTSResult = _avp.TTSResult
select_tts_provider = _avp.select_tts_provider

__all__ = [
    "Segment", "Script", "PRESET_VOICES_ZH", "TTSResult", "select_tts_provider",
    "parse_dialogue_script", "concat_audio_command",
]


@dataclass
class Segment:
    """One spoken line in a multi-segment script."""

    index: int
    text: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    speaker: str = ""   # display label only ("旁白", "A", "B", ...)


@dataclass
class Script:
    title: str
    segments: list[Segment] = field(default_factory=list)


# ── parser: dialogue script ────────────────────────────────────────────


_LINE_RE = re.compile(r"^\s*([^:：]{1,16})[:：]\s*(.+)$")


def parse_dialogue_script(
    raw: str, *, default_voice: str, voice_map: dict[str, str] | None = None,
    title: str = "未命名",
) -> Script:
    """Parse a multi-line dialogue script.

    Each non-empty line of the form ``Speaker: text`` becomes a Segment.
    Lines without a speaker marker are merged into the previous segment.
    Speakers are mapped to voices via ``voice_map``; unknown speakers fall
    back to ``default_voice``.
    """
    voice_map = voice_map or {}
    segs: list[Segment] = []
    idx = 0
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if m:
            speaker, text = m.group(1).strip(), m.group(2).strip()
            voice = voice_map.get(speaker, default_voice)
            idx += 1
            segs.append(Segment(index=idx, text=text, voice=voice, speaker=speaker))
        else:
            if segs:
                segs[-1].text += " " + line.strip()
            else:
                idx += 1
                segs.append(Segment(index=idx, text=line.strip(),
                                     voice=default_voice, speaker="旁白"))
    return Script(title=title, segments=segs)


# ── concat audio via ffmpeg ────────────────────────────────────────────


def concat_audio_command(
    *, parts: Iterable[Path], list_file: Path, output: Path, ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Build the ffmpeg concat-demuxer command for a sequence of audio parts."""
    bin_path = ffmpeg if Path(ffmpeg).is_absolute() else (shutil.which(ffmpeg) or ffmpeg)
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text(
        "\n".join(f"file '{Path(p).as_posix()}'" for p in parts) + "\n",
        encoding="utf-8",
    )
    return [
        bin_path, "-y", "-hide_banner",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output),
    ]
