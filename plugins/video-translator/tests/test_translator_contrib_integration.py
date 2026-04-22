"""Phase 2-06 contrib.* integration tests for video-translator.

Verifies the engine no longer pulls in sibling plugins via the
historical ``_load_sibling`` shim and instead routes ASR through
``contrib.asr.select_provider`` and TTS through
``contrib.tts.select_provider``.

Also locks in the local ``TranscriptChunk`` ownership and the SRT/VTT
renderers (so the plugin stays self-contained even if subtitle-maker
is uninstalled).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import translator_engine as eng  # noqa: E402
from translator_engine import (  # noqa: E402
    TranscriptChunk,
    configure_credentials,
    select_tts_provider,
    to_srt,
    to_vtt,
    transcribe_with_contrib_asr,
)


def test_engine_no_longer_imports_load_sibling() -> None:
    """Phase 2-06: the engine MUST be self-contained — no sibling
    plugin imports, no ``_load_sibling`` shim left over."""
    src = (_HERE / "translator_engine.py").read_text(encoding="utf-8")
    assert "def _load_sibling" not in src, (
        "_load_sibling helper should be removed in Phase 2-06"
    )
    assert "_load_sibling(" not in src, (
        "no live calls to _load_sibling should remain"
    )
    # No live imports of sibling plugin modules (we tolerate
    # docstring mentions like "subtitle-maker" so the assertion only
    # checks the patterns _load_sibling used to construct):
    assert 'plugin_dir_name="subtitle-maker"' not in src
    assert 'plugin_dir_name="highlight-cutter"' not in src
    assert 'plugin_dir_name="tts-studio"' not in src
    assert "subtitle-maker/subtitle_engine" not in src
    assert "highlight-cutter/highlight_engine" not in src
    assert "tts-studio/studio_engine" not in src


def test_transcript_chunk_owned_locally() -> None:
    """``TranscriptChunk`` must come from translator_engine itself, not
    from a sibling plugin's namespace."""
    assert TranscriptChunk.__module__ == "translator_engine"


def test_to_srt_renders_two_lines() -> None:
    chunks = [
        TranscriptChunk(start=0.0, end=1.5, text="hello"),
        TranscriptChunk(start=1.5, end=3.0, text="world"),
    ]
    out = to_srt(chunks)
    assert "1\n00:00:00,000 --> 00:00:01,500\nhello" in out
    assert "2\n00:00:01,500 --> 00:00:03,000\nworld" in out


def test_to_vtt_starts_with_webvtt_header() -> None:
    out = to_vtt([TranscriptChunk(start=0.0, end=1.0, text="hi")])
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.000" in out


# ── ASR routing ────────────────────────────────────────────────────────


def test_transcribe_with_contrib_asr_uses_select_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeChunk:
        def __init__(self, text, start, end):
            self.text, self.start, self.end = text, start, end
            self.confidence = 0.9

    class _FakeResult:
        def __init__(self):
            self.chunks = [
                _FakeChunk("hello", 0.0, 1.0),
                _FakeChunk("world", 1.0, 2.0),
            ]

    class _FakeProv:
        provider_id = "fake"

        async def transcribe(self, source, *, language):
            captured["source"] = source
            captured["language"] = language
            return _FakeResult()

    def _fake_select(provider_id, *, configs, region, allow_stub):
        captured["provider_id"] = provider_id
        captured["configs"] = configs
        captured["region"] = region
        captured["allow_stub"] = allow_stub
        return _FakeProv()

    monkeypatch.setattr(eng, "_sdk_select_asr", _fake_select)
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")

    out = asyncio.run(transcribe_with_contrib_asr(
        audio, provider_id="auto", region="cn", language="zh",
        model="base", binary="whisper-cli",
    ))

    assert captured["provider_id"] == "auto"
    assert captured["region"] == "cn"
    assert captured["allow_stub"] is False
    assert captured["language"] == "zh"
    assert "whisper_local" in captured["configs"]
    assert captured["configs"]["whisper_local"]["binary"] == "whisper-cli"
    assert [c.text for c in out] == ["hello", "world"]
    assert isinstance(out[0], TranscriptChunk)


def test_transcribe_with_contrib_asr_returns_empty_on_select_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from openakita_plugin_sdk.contrib.asr import ASRError

    def _boom(*a, **kw):
        raise ASRError("no providers")

    monkeypatch.setattr(eng, "_sdk_select_asr", _boom)
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")

    out = asyncio.run(transcribe_with_contrib_asr(audio))
    assert out == []  # caller fallbacks (offline translate / VendorError)


def test_transcribe_with_contrib_asr_returns_empty_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class _Boom:
        provider_id = "boom"

        async def transcribe(self, source, *, language):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        eng, "_sdk_select_asr",
        lambda *a, **kw: _Boom(),
    )
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    assert asyncio.run(transcribe_with_contrib_asr(audio)) == []


# ── TTS routing ────────────────────────────────────────────────────────


def test_configure_credentials_flows_into_tts_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_select_tts(preferred, *, configs):
        captured["preferred"] = preferred
        captured["configs"] = configs
        return object()

    monkeypatch.setattr(eng, "_sdk_select_tts", _fake_select_tts)

    configure_credentials(dashscope_api_key="sk-d", openai_api_key="sk-o")
    select_tts_provider("auto")

    cfgs = captured["configs"]
    assert cfgs["qwen3_tts_flash"]["api_key"] == "sk-d"
    assert cfgs["cosyvoice"]["api_key"] == "sk-d"
    assert cfgs["openai_tts"]["api_key"] == "sk-o"

    # Clear back so other tests in the suite get a clean slate.
    configure_credentials(dashscope_api_key="", openai_api_key="")
    select_tts_provider("auto")
    cfgs = captured["configs"]
    assert cfgs["qwen3_tts_flash"] == {}
    assert cfgs["openai_tts"] == {}


def test_configure_credentials_partial_update_preserves_other_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        eng, "_sdk_select_tts",
        lambda preferred, *, configs: captured.setdefault("configs", configs),
    )

    configure_credentials(dashscope_api_key="sk-d", openai_api_key="sk-o")
    configure_credentials(dashscope_api_key="sk-d2")  # only update dashscope
    select_tts_provider("auto")

    assert captured["configs"]["qwen3_tts_flash"]["api_key"] == "sk-d2"
    assert captured["configs"]["openai_tts"]["api_key"] == "sk-o"

    configure_credentials(dashscope_api_key="", openai_api_key="")
