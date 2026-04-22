"""Phase 2-04 regression tests: subtitle-maker on contrib.asr.

Confirms the cross-plugin shim is gone and the engine routes through
``openakita_plugin_sdk.contrib.asr`` for ASR.
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

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import subtitle_engine  # noqa: E402
from _shared.asr import (  # noqa: E402
    ASRChunk,
    ASRError,
    ASRResult,
    BaseASRProvider,
)


class _FakeProvider(BaseASRProvider):
    provider_id = "fake"
    requires_api_key = False

    def is_available(self) -> bool:  # type: ignore[override]
        return True

    async def transcribe(self, source, *, language="auto", **kwargs):
        return ASRResult(
            provider=self.provider_id,
            chunks=[
                ASRChunk(start=0.0, end=2.0, text="hi", confidence=1.0),
                ASRChunk(start=2.0, end=4.5, text="there", confidence=1.0),
            ],
            language=language,
        )


def test_engine_no_longer_imports_load_sibling() -> None:
    src = Path(subtitle_engine.__file__).read_text(encoding="utf-8")
    assert "_load_sibling(" not in src
    assert "def _load_sibling" not in src
    assert "_oa_hc_engine" not in src
    assert "highlight-cutter/highlight_engine" not in src


def test_configure_credentials_clear_to_none() -> None:
    subtitle_engine.configure_credentials(dashscope_api_key="x")
    assert subtitle_engine._CREDENTIALS["dashscope_api_key"] == "x"
    subtitle_engine.configure_credentials(dashscope_api_key="")
    assert subtitle_engine._CREDENTIALS["dashscope_api_key"] is None


def test_transcribe_with_contrib_asr_yields_chunks(monkeypatch, tmp_path) -> None:
    fake = _FakeProvider({})
    monkeypatch.setattr(
        subtitle_engine, "_sdk_select_asr",
        lambda preferred, **_kw: fake,
    )
    src = tmp_path / "v.wav"
    src.write_bytes(b"")
    out = asyncio.run(subtitle_engine.transcribe_with_contrib_asr(src))
    assert len(out) == 2
    assert out[0].text == "hi"
    assert isinstance(out[0], subtitle_engine.TranscriptChunk)


def test_transcribe_returns_empty_on_asr_error(monkeypatch, tmp_path) -> None:
    def _raise(*_args, **_kw):
        raise ASRError("nope", retryable=False, provider="auto", kind="config")

    monkeypatch.setattr(subtitle_engine, "_sdk_select_asr", _raise)
    src = tmp_path / "v.wav"
    src.write_bytes(b"")
    out = asyncio.run(subtitle_engine.transcribe_with_contrib_asr(src))
    assert out == []


def test_to_srt_works_on_contrib_chunk_shape() -> None:
    chunks = [
        subtitle_engine.TranscriptChunk(0.0, 1.5, "hello"),
        subtitle_engine.TranscriptChunk(1.5, 4.25, "你好"),
    ]
    srt = subtitle_engine.to_srt(chunks)
    assert "1\n00:00:00,000 --> 00:00:01,500\nhello" in srt
    assert "2\n00:00:01,500 --> 00:00:04,250\n你好" in srt
