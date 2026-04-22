"""video-translator engine tests (offline, no ffmpeg/whisper required)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from translator_engine import (  # noqa: E402
    SUPPORTED_LANGS,
    TranscriptChunk,
    _safe_json_array,
    build_extract_audio_cmd,
    build_mux_cmd,
    concat_audio_chunks_cmd,
    translate_chunks,
    translate_chunks_offline,
)


def _chunks() -> list[TranscriptChunk]:
    return [
        TranscriptChunk(start=0.0, end=2.0, text="Hello world"),
        TranscriptChunk(start=2.0, end=4.0, text="How are you"),
    ]


def test_supported_langs_contains_basic_set() -> None:
    assert {"zh", "en", "ja"}.issubset(SUPPORTED_LANGS)


def test_translate_offline_prefixes_text_preserves_timing() -> None:
    out = translate_chunks_offline(_chunks(), prefix="[TR] ")
    assert len(out) == 2
    assert out[0].text == "[TR] Hello world"
    assert out[0].start == 0.0 and out[0].end == 2.0


def test_safe_json_array_handles_clean_array() -> None:
    assert _safe_json_array('[{"i":0,"t":"a"}]') == [{"i": 0, "t": "a"}]


def test_safe_json_array_handles_fenced_block() -> None:
    raw = '```json\n[{"i":0,"t":"a"}]\n```'
    assert _safe_json_array(raw) == [{"i": 0, "t": "a"}]


def test_safe_json_array_handles_garbage_returns_empty_list() -> None:
    assert _safe_json_array("not json at all") == []
    assert _safe_json_array("") == []


def test_translate_chunks_uses_llm_response() -> None:
    async def fake_llm(prompt: str, max_tokens: int = 2000, **_):
        return '[{"i":0,"t":"你好"},{"i":1,"t":"近况如何"}]'
    out = asyncio.run(translate_chunks(_chunks(), target_lang="zh", llm_call=fake_llm))
    assert [c.text for c in out] == ["你好", "近况如何"]
    assert out[0].start == 0.0


def test_translate_chunks_falls_back_when_llm_returns_garbage() -> None:
    async def bad_llm(prompt: str, max_tokens: int = 2000, **_):
        return "this is not json"
    out = asyncio.run(translate_chunks(_chunks(), target_lang="zh", llm_call=bad_llm))
    # Falls back to original text rather than raising
    assert [c.text for c in out] == ["Hello world", "How are you"]


def test_translate_chunks_handles_partial_response() -> None:
    async def partial_llm(prompt: str, max_tokens: int = 2000, **_):
        return '[{"i":0,"t":"你好"}]'  # missing index 1
    out = asyncio.run(translate_chunks(_chunks(), target_lang="zh", llm_call=partial_llm))
    assert out[0].text == "你好"
    assert out[1].text == "How are you"  # falls back


def test_translate_chunks_empty_input() -> None:
    async def llm(*a, **k): return "[]"
    out = asyncio.run(translate_chunks([], target_lang="en", llm_call=llm))
    assert out == []


def test_build_extract_audio_cmd_uses_16khz_mono() -> None:
    cmd = build_extract_audio_cmd(source=Path("a.mp4"), output_audio=Path("a.wav"),
                                   ffmpeg="ffmpeg")
    j = " ".join(cmd)
    assert "-ar 16000" in j and "-ac 1" in j and "-vn" in j
    assert cmd[-1] == "a.wav"


def test_concat_audio_chunks_cmd_writes_list_file(tmp_path) -> None:
    parts = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    for p in parts:
        p.write_bytes(b"x")
    list_file = tmp_path / "list.txt"
    cmd = concat_audio_chunks_cmd(parts=parts, list_file=list_file,
                                   output_audio=tmp_path / "out.m4a", ffmpeg="ffmpeg")
    assert list_file.exists()
    txt = list_file.read_text(encoding="utf-8")
    assert "a.mp3" in txt and "b.mp3" in txt
    assert "concat" in " ".join(cmd) and "-c:a aac" in " ".join(cmd)


def test_build_mux_cmd_softsub_default() -> None:
    cmd = build_mux_cmd(
        source_video=Path("v.mp4"), dubbed_audio=Path("a.m4a"),
        srt_file=Path("s.srt"), output_video=Path("out.mp4"),
        ffmpeg="ffmpeg", burn_subtitles=False, keep_original_audio_volume=0.0,
    )
    j = " ".join(cmd)
    assert "-c:v copy" in j
    assert "-c:s mov_text" in j
    assert "subtitles=" not in j  # not burned in


def test_build_mux_cmd_burn_subtitles_reencodes() -> None:
    cmd = build_mux_cmd(
        source_video=Path("v.mp4"), dubbed_audio=Path("a.m4a"),
        srt_file=Path("s.srt"), output_video=Path("out.mp4"),
        ffmpeg="ffmpeg", burn_subtitles=True, keep_original_audio_volume=0.0,
    )
    j = " ".join(cmd)
    assert "subtitles=" in j and "libx264" in j


def test_build_mux_cmd_mix_original_audio() -> None:
    cmd = build_mux_cmd(
        source_video=Path("v.mp4"), dubbed_audio=Path("a.m4a"),
        srt_file=None, output_video=Path("out.mp4"),
        ffmpeg="ffmpeg", burn_subtitles=False, keep_original_audio_volume=0.2,
    )
    j = " ".join(cmd)
    assert "amix" in j and "volume=0.2" in j
