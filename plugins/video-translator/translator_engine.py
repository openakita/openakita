"""video-translator — pipeline glue: ASR + LLM translate + TTS + mux.

Reuses (no duplication):
- ``subtitle-maker.subtitle_engine`` for ASR (whisper.cpp) and SRT writing.
- ``tts-studio.studio_engine`` for TTS provider selection.

Pure functions in this module (no I/O for ``translate_chunks_offline``,
``build_extract_audio_cmd``, ``build_mux_cmd``) so they're trivially testable.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from openakita_plugin_sdk.contrib import parse_llm_json_array


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


_sm = _load_sibling("subtitle-maker", "subtitle_engine", "_oa_sm_engine")
to_srt = _sm.to_srt
to_vtt = _sm.to_vtt

_hc = _load_sibling("highlight-cutter", "highlight_engine", "_oa_hc_engine")
TranscriptChunk = _hc.TranscriptChunk
whisper_cpp_transcribe = _hc.whisper_cpp_transcribe

_ts = _load_sibling("tts-studio", "studio_engine", "_oa_ts_engine")
select_tts_provider = _ts.select_tts_provider

logger = logging.getLogger(__name__)

__all__ = [
    "TranscriptChunk", "whisper_cpp_transcribe", "to_srt", "to_vtt",
    "select_tts_provider", "translate_chunks", "translate_chunks_offline",
    "build_extract_audio_cmd", "build_mux_cmd", "concat_audio_chunks_cmd",
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
