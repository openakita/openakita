"""video-translator — pipeline glue: ASR + LLM translate + TTS + mux.

Phase 2-06 of the plugin overhaul: this module no longer reaches into
sibling plugins via the historical ``_load_sibling`` shim. ASR is now
served by :mod:`openakita_plugin_sdk.contrib.asr`, TTS by
:mod:`openakita_plugin_sdk.contrib.tts`, and the SRT/VTT renderers plus
``TranscriptChunk`` dataclass are owned locally so this engine can be
loaded in isolation (for tests, REPL exploration, or downstream agents
that don't have ``subtitle-maker`` installed).

Pure functions in this module (no I/O for ``translate_chunks_offline``,
``build_extract_audio_cmd``, ``build_mux_cmd``, ``to_srt``, ``to_vtt``)
remain trivially testable.
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

import json
import logging
import shutil
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _shared import parse_llm_json_array
from _shared.asr import (
    ASRError,
)
from _shared.asr import (
    select_provider as _sdk_select_asr,
)
from _shared.tts import (
    select_provider as _sdk_select_tts,
)

logger = logging.getLogger(__name__)


# ── locally-owned dataclass + renderers (no more sibling plugin import) ─


@dataclass(frozen=True)
class TranscriptChunk:
    """One transcribed line with timing in seconds.

    Owned here (not re-exported from ``subtitle-maker``) so the engine
    has zero hard dependency on sibling plugins. The shape is identical
    to the one ``subtitle-maker`` and ``highlight-cutter`` use, which
    keeps cross-plugin data flows mechanical.
    """

    start: float
    end: float
    text: str


def _ts_srt(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms >= 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_vtt(t: float) -> str:
    return _ts_srt(t).replace(",", ".")


def to_srt(chunks: list[TranscriptChunk]) -> str:
    out: list[str] = []
    for i, c in enumerate(chunks, start=1):
        out.append(str(i))
        out.append(f"{_ts_srt(c.start)} --> {_ts_srt(c.end)}")
        out.append(c.text)
        out.append("")
    return "\n".join(out)


def to_vtt(chunks: list[TranscriptChunk]) -> str:
    out: list[str] = ["WEBVTT", ""]
    for c in chunks:
        out.append(f"{_ts_vtt(c.start)} --> {_ts_vtt(c.end)}")
        out.append(c.text)
        out.append("")
    return "\n".join(out)


# ── credentials registry (set by Plugin.on_load via /settings) ──────────


_CREDENTIALS: dict[str, str | None] = {
    "dashscope_api_key": None,
    "openai_api_key": None,
}


def configure_credentials(
    *,
    dashscope_api_key: str | None = None,
    openai_api_key: str | None = None,
) -> None:
    """Plug API keys into the engine without touching ``os.environ``.

    Called from :meth:`plugin.Plugin._load_credentials` on load and on
    every ``POST /settings`` so users can rotate keys at runtime.
    """
    if dashscope_api_key is not None:
        _CREDENTIALS["dashscope_api_key"] = dashscope_api_key or None
    if openai_api_key is not None:
        _CREDENTIALS["openai_api_key"] = openai_api_key or None


def _build_asr_configs(*, model: str, binary: str) -> dict[str, dict[str, Any]]:
    return {
        "dashscope_paraformer": (
            {"api_key": _CREDENTIALS["dashscope_api_key"]}
            if _CREDENTIALS["dashscope_api_key"] else {}
        ),
        "whisper_local": {"binary": binary, "model": model},
        "stub": {},
    }


def _build_tts_configs() -> dict[str, dict[str, Any]]:
    return {
        "qwen3_tts_flash": (
            {"api_key": _CREDENTIALS["dashscope_api_key"]}
            if _CREDENTIALS["dashscope_api_key"] else {}
        ),
        "cosyvoice": (
            {"api_key": _CREDENTIALS["dashscope_api_key"]}
            if _CREDENTIALS["dashscope_api_key"] else {}
        ),
        "openai_tts": (
            {"api_key": _CREDENTIALS["openai_api_key"]}
            if _CREDENTIALS["openai_api_key"] else {}
        ),
        "edge": {},
    }


# ── ASR + TTS bridges around contrib.* ──────────────────────────────────


async def transcribe_with_contrib_asr(
    source: Path,
    *,
    provider_id: str = "auto",
    region: str = "cn",
    language: str = "auto",
    model: str = "base",
    binary: str = "whisper-cli",
) -> list[TranscriptChunk]:
    """Run ASR through the SDK's contrib.asr registry and adapt the
    result to the engine's chunk shape.

    Returns an empty list on any provider failure so the orchestrator
    can fall back to a deterministic offline path (matches the previous
    ``whisper_cpp_transcribe`` semantics).
    """
    try:
        prov = _sdk_select_asr(
            provider_id,
            configs=_build_asr_configs(model=model, binary=binary),
            region=region,
            allow_stub=False,
        )
    except ASRError as exc:
        logger.warning("contrib.asr select failed (%s): %s", provider_id, exc)
        return []

    try:
        result = await prov.transcribe(source, language=language)
    except Exception as exc:  # noqa: BLE001 — providers vary; degrade gracefully
        logger.warning("contrib.asr transcribe failed (%s): %s", provider_id, exc)
        return []

    out: list[TranscriptChunk] = []
    for ch in getattr(result, "chunks", []) or []:
        text = (getattr(ch, "text", "") or "").strip()
        if not text:
            continue
        start = max(0.0, float(getattr(ch, "start", 0.0)))
        end = max(start + 0.001, float(getattr(ch, "end", start)))
        out.append(TranscriptChunk(start=start, end=end, text=text))
    return out


def select_tts_provider(preferred: str = "auto") -> Any:
    """Pick a TTS provider through ``contrib.tts.select_provider``.

    Raises :class:`openakita_plugin_sdk.contrib.tts.TTSError` if no
    provider is available — caller maps that to ``ErrorCoach``.
    """
    return _sdk_select_tts(preferred, configs=_build_tts_configs())


# back-compat: legacy name used by older plugin code paths
async def whisper_cpp_transcribe(
    source: Path,
    *,
    model: str = "base",
    language: str = "auto",
    binary: str = "whisper-cli",
    timeout_sec: float = 600.0,  # noqa: ARG001 — kept for signature parity
) -> list[TranscriptChunk]:
    return await transcribe_with_contrib_asr(
        source,
        provider_id="whisper_local",
        language=language,
        model=model,
        binary=binary,
    )


__all__ = [
    "SUPPORTED_LANGS",
    "TranscriptChunk",
    "build_extract_audio_cmd",
    "build_mux_cmd",
    "concat_audio_chunks_cmd",
    "configure_credentials",
    "select_tts_provider",
    "to_srt",
    "to_vtt",
    "transcribe_with_contrib_asr",
    "translate_chunks",
    "translate_chunks_offline",
    "whisper_cpp_transcribe",
]


# ── translation ───────────────────────────────────────────────────────


LLMCall = Callable[..., Awaitable[str]]


SUPPORTED_LANGS = {
    "zh": "中文 (简体)", "en": "English", "ja": "日本語", "ko": "한국어",
    "es": "Español", "fr": "Français", "de": "Deutsch", "ru": "Русский",
}


PROMPT = """你是字幕翻译。把每条字幕翻译成 {target_lang_name}，保持语气自然，长度尽量接近原文（朗读时长接近）。

输入是 JSON 数组，每条 {{"i": index, "t": text}}。
输出严格的 JSON 数组（不要 markdown 包裹），格式 {{"i": index, "t": translated_text}}，i 顺序保持不变。

输入：
{payload}
"""


async def translate_chunks(
    chunks: list[TranscriptChunk], *, target_lang: str, llm_call: LLMCall,
    batch_size: int = 25,
) -> list[TranscriptChunk]:
    """LLM-translate transcript chunks; falls back to original text on any failure."""
    if not chunks:
        return chunks
    target_name = SUPPORTED_LANGS.get(target_lang, target_lang)

    out: list[TranscriptChunk] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        payload = [{"i": idx, "t": c.text} for idx, c in enumerate(batch)]
        prompt = PROMPT.format(target_lang_name=target_name,
                                payload=json.dumps(payload, ensure_ascii=False))
        translated_map: dict[int, str] = {}
        try:
            text = await llm_call(prompt=prompt, max_tokens=2000)
            data = _safe_json_array(text)
            for item in data:
                if isinstance(item, dict) and "i" in item and "t" in item:
                    translated_map[int(item["i"])] = str(item["t"])
        except Exception as e:  # noqa: BLE001
            logger.warning("translate batch %d failed: %s", i // batch_size, e)
        for idx, c in enumerate(batch):
            new_text = translated_map.get(idx, c.text)
            out.append(TranscriptChunk(start=c.start, end=c.end, text=new_text))
    return out


def translate_chunks_offline(
    chunks: list[TranscriptChunk], *, prefix: str = "[TR] ",
) -> list[TranscriptChunk]:
    """Deterministic offline 'translation' for tests / no-LLM fallback."""
    return [TranscriptChunk(start=c.start, end=c.end, text=prefix + c.text)
            for c in chunks]


def _safe_json_array(text: str) -> list:
    """Extract a JSON array from possibly fenced LLM output.

    Thin wrapper over ``openakita_plugin_sdk.contrib.parse_llm_json_array``
    (5-level fallback: direct → fence-strip → outer-span → balanced-span scan
    → ``[]``). Kept under this name for backward compatibility with the
    existing tests and any in-tree callers.
    """
    return parse_llm_json_array(text or "")


# ── ffmpeg command builders (pure) ────────────────────────────────────


def _resolve_ffmpeg(binary: str = "ffmpeg") -> str:
    return binary if Path(binary).is_absolute() else (shutil.which(binary) or binary)


def build_extract_audio_cmd(
    *, source: Path, output_audio: Path, ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Extract audio track from a video file as 16kHz mono WAV (whisper-friendly)."""
    bin_path = _resolve_ffmpeg(ffmpeg)
    return [
        bin_path, "-y", "-hide_banner", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(output_audio),
    ]


def concat_audio_chunks_cmd(
    *, parts: Iterable[Path], list_file: Path, output_audio: Path, ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Concat dubbed audio segments into a single track (concat demuxer)."""
    bin_path = _resolve_ffmpeg(ffmpeg)
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text(
        "\n".join(f"file '{Path(p).as_posix()}'" for p in parts) + "\n",
        encoding="utf-8",
    )
    return [
        bin_path, "-y", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:a", "aac", "-b:a", "192k",
        str(output_audio),
    ]


def build_mux_cmd(
    *, source_video: Path, dubbed_audio: Path, srt_file: Path | None,
    output_video: Path, ffmpeg: str = "ffmpeg",
    burn_subtitles: bool = False, keep_original_audio_volume: float = 0.0,
) -> list[str]:
    """Mux dubbed audio (and optional subs) onto the source video.

    - If ``burn_subtitles`` is True, subtitles are hard-burned via ``-vf subtitles=``.
    - Else if ``srt_file`` is given, it's added as a soft subtitle stream.
    - ``keep_original_audio_volume`` (0.0–1.0): if > 0, mix dubbed + original at this ratio.
    """
    bin_path = _resolve_ffmpeg(ffmpeg)
    cmd: list[str] = [bin_path, "-y", "-hide_banner",
                      "-i", str(source_video), "-i", str(dubbed_audio)]

    if keep_original_audio_volume > 0:
        # Mix: original (low) + dubbed (full)
        cmd += ["-filter_complex",
                f"[0:a]volume={keep_original_audio_volume}[a0];"
                f"[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=longest[aout]",
                "-map", "0:v:0", "-map", "[aout]"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]

    if burn_subtitles and srt_file is not None:
        # subtitles filter requires escaped path on some platforms; basic escape:
        srt_arg = str(srt_file).replace("\\", "/").replace(":", "\\:")
        # Replace the simple -map with a filter chain that includes subtitles
        if keep_original_audio_volume > 0:
            # already a filter_complex; appending is non-trivial, so we re-encode video with vf
            cmd += ["-vf", f"subtitles='{srt_arg}'"]
        else:
            cmd = [bin_path, "-y", "-hide_banner", "-i", str(source_video),
                   "-i", str(dubbed_audio),
                   "-vf", f"subtitles='{srt_arg}'",
                   "-map", "0:v:0", "-map", "1:a:0"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
        if srt_file is not None:
            # soft subs (mov_text for mp4)
            cmd += ["-i", str(srt_file), "-map", "2:s:0", "-c:s", "mov_text"]

    cmd.append(str(output_video))
    return cmd
