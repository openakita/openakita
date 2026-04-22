"""tts-studio — script → multi-segment audio.

Phase 2-02 of the overhaul playbook removes the cross-plugin shim that
physically reached into a sibling plugin's ``providers`` module. TTS
provider implementations now live in :mod:`openakita_plugin_sdk.contrib.tts`,
so this plugin no longer depends on another plugin's load order.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openakita_plugin_sdk.contrib.tts import (
    VOICE_CATALOG,
    BaseTTSProvider,
    CosyVoiceProvider,
    EdgeTTSProvider,
    OpenAITTSProvider,
    Qwen3TTSFlashProvider,
    TTSError,
)
from openakita_plugin_sdk.contrib.tts import (
    TTSResult as _SDKTTSResult,
)
from openakita_plugin_sdk.contrib.tts import (
    select_provider as _sdk_select_provider,
)

# ── back-compat dataclass ─────────────────────────────────────────────


@dataclass
class TTSResult:
    """Legacy TTSResult shape (preserved for callers that imported it
    from this module before the refactor)."""

    provider: str
    audio_path: Path
    duration_sec: float
    voice: str
    raw: dict[str, Any]


def _to_legacy_result(result: _SDKTTSResult) -> TTSResult:
    return TTSResult(
        provider=result.provider,
        audio_path=result.audio_path,
        duration_sec=result.duration_sec,
        voice=result.voice,
        raw=result.raw,
    )


# ── back-compat voice catalog ─────────────────────────────────────────


PRESET_VOICES_ZH: list[dict[str, str]] = [
    {"id": v.id, "label": v.label, "provider": v.provider}
    for v in VOICE_CATALOG
    if v.language.startswith("zh") or v.provider == "openai"
]


# ── per-plugin credentials registry ──────────────────────────────────

_CREDENTIALS: dict[str, str | None] = {
    "dashscope_api_key": None,
    "openai_api_key": None,
}

_LEGACY_TO_PROVIDER_ID: dict[str, str] = {
    "auto": "auto",
    "edge": "edge",
    "dashscope": "qwen3_tts_flash",
    "qwen3": "qwen3_tts_flash",
    "qwen3_tts_flash": "qwen3_tts_flash",
    "cosyvoice": "cosyvoice",
    "openai": "openai",
}


def configure_credentials(
    *,
    dashscope_api_key: str | None = None,
    openai_api_key: str | None = None,
) -> None:
    """Hot-update credentials used by subsequent provider builds."""
    if dashscope_api_key is not None:
        _CREDENTIALS["dashscope_api_key"] = dashscope_api_key or None
    if openai_api_key is not None:
        _CREDENTIALS["openai_api_key"] = openai_api_key or None


def _build_configs() -> dict[str, dict[str, Any]]:
    dk = _CREDENTIALS.get("dashscope_api_key")
    ok = _CREDENTIALS.get("openai_api_key")
    return {
        Qwen3TTSFlashProvider.provider_id: {"api_key": dk} if dk else {},
        CosyVoiceProvider.provider_id: {"api_key": dk} if dk else {},
        OpenAITTSProvider.provider_id: {"api_key": ok} if ok else {},
        EdgeTTSProvider.provider_id: {},
    }


class _SDKProviderShim:
    """Adapt SDK provider's TTSResult into this module's legacy shape."""

    def __init__(self, provider: BaseTTSProvider) -> None:
        self._inner = provider
        self.name = provider.provider_id
        self.provider_id = provider.provider_id

    async def synthesize(
        self,
        *,
        text: str,
        voice: str = "",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        output_dir: Path,
        **kwargs: Any,
    ) -> TTSResult:
        result = await self._inner.synthesize(
            text=text, voice=voice, output_dir=output_dir,
            rate=rate, pitch=pitch, **kwargs,
        )
        return _to_legacy_result(result)


def select_tts_provider(preferred: str = "auto") -> Any:
    """Pick a TTS provider, preserving the legacy entry-point signature."""
    pid = _LEGACY_TO_PROVIDER_ID.get(preferred, preferred)
    sdk_provider = _sdk_select_provider(
        pid, configs=_build_configs(), region="cn",
    )
    return _SDKProviderShim(sdk_provider)


__all__ = [
    "PRESET_VOICES_ZH",
    "Script",
    "Segment",
    "TTSError",
    "TTSResult",
    "concat_audio_command",
    "configure_credentials",
    "parse_dialogue_script",
    "select_tts_provider",
]


# ── script parsing & ffmpeg concat (unchanged) ────────────────────────


@dataclass
class Segment:
    """One spoken line in a multi-segment script."""

    index: int
    text: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    speaker: str = ""


@dataclass
class Script:
    title: str
    segments: list[Segment] = field(default_factory=list)


_LINE_RE = re.compile(r"^\s*([^:：]{1,16})[:：]\s*(.+)$")


def parse_dialogue_script(
    raw: str,
    *,
    default_voice: str,
    voice_map: dict[str, str] | None = None,
    title: str = "未命名",
) -> Script:
    """Parse a multi-line dialogue script into ordered Segments.

    Each ``Speaker: text`` line becomes a segment; lines without a
    speaker marker append to the previous segment so authors can soft-
    wrap long lines.
    """
    voice_map = voice_map or {}
    segs: list[Segment] = []
    idx = 0
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
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
                segs.append(
                    Segment(index=idx, text=line.strip(), voice=default_voice, speaker="旁白")
                )
    return Script(title=title, segments=segs)


def concat_audio_command(
    *,
    parts: Iterable[Path],
    list_file: Path,
    output: Path,
    ffmpeg: str = "ffmpeg",
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
