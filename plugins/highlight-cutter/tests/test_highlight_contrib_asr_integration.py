"""Phase 2-03 regression tests: highlight-cutter on contrib.asr.

Verifies that the engine routes through ``openakita_plugin_sdk.contrib.asr``
instead of shelling out to whisper.cpp directly, and that
``configure_credentials`` updates flow through to provider builds.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import highlight_engine  # noqa: E402
from openakita_plugin_sdk.contrib.asr import (  # noqa: E402
    ASRChunk,
    ASRError,
    ASRResult,
    BaseASRProvider,
)


class _FakeProvider(BaseASRProvider):
    """Async provider that returns canned chunks — no whisper / no http."""

    provider_id = "fake"
    requires_api_key = False

    def is_available(self) -> bool:  # type: ignore[override]
        return True

    async def transcribe(self, source, *, language="auto", **kwargs):
        return ASRResult(
            provider=self.provider_id,
            chunks=[
                ASRChunk(start=0.0, end=2.0, text="hello", confidence=0.9),
                ASRChunk(start=2.0, end=4.5, text="world", confidence=0.8),
            ],
            language=language,
        )


def test_engine_no_longer_imports_load_sibling() -> None:
    src = Path(highlight_engine.__file__).read_text(encoding="utf-8")
    assert "_load_sibling(" not in src
    assert "_oa_avatar_providers" not in src


def test_configure_credentials_idempotent_and_clearable() -> None:
    highlight_engine.configure_credentials(dashscope_api_key="abc-123")
    assert highlight_engine._CREDENTIALS["dashscope_api_key"] == "abc-123"
    highlight_engine.configure_credentials(dashscope_api_key="")
    assert highlight_engine._CREDENTIALS["dashscope_api_key"] is None


def test_transcribe_with_contrib_asr_returns_transcript_chunks(monkeypatch, tmp_path) -> None:
    fake = _FakeProvider({})
    monkeypatch.setattr(
        highlight_engine, "_sdk_select_asr",
        lambda preferred, **_kw: fake,
    )
    src = tmp_path / "fake.wav"
    src.write_bytes(b"")
    out = asyncio.run(
        highlight_engine.transcribe_with_contrib_asr(
            src, provider_id="auto", model="base", binary="whisper-cli",
        )
    )
    assert len(out) == 2
    assert out[0].text == "hello"
    assert isinstance(out[0], highlight_engine.TranscriptChunk)


def test_transcribe_with_contrib_asr_returns_empty_when_no_provider(monkeypatch, tmp_path) -> None:
    def _raise(*_args, **_kw):
        raise ASRError("no provider", retryable=False, provider="auto", kind="config")

    monkeypatch.setattr(highlight_engine, "_sdk_select_asr", _raise)
    src = tmp_path / "fake.wav"
    src.write_bytes(b"")
    out = asyncio.run(highlight_engine.transcribe_with_contrib_asr(src))
    assert out == []


def test_build_asr_configs_threads_dashscope_key() -> None:
    highlight_engine.configure_credentials(dashscope_api_key="sk-foo")
    cfgs = highlight_engine._build_asr_configs(model="base", binary="whisper-cli")
    assert cfgs["dashscope_paraformer"] == {"api_key": "sk-foo"}
    assert cfgs["whisper_local"]["binary"] == "whisper-cli"


def test_build_asr_configs_omits_key_when_empty() -> None:
    highlight_engine.configure_credentials(dashscope_api_key="")
    cfgs = highlight_engine._build_asr_configs(model="base", binary="whisper-cli")
    assert cfgs["dashscope_paraformer"] == {}
