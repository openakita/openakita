"""Tests for openakita_plugin_sdk.contrib.asr."""

from __future__ import annotations

from pathlib import Path

import pytest
from openakita_plugin_sdk.contrib.asr import (
    PROVIDER_PRIORITY_CHINA,
    PROVIDER_PRIORITY_GLOBAL,
    ASRChunk,
    ASRError,
    ASRResult,
    BaseASRProvider,
    DashScopeParaformerProvider,
    StubASRProvider,
    WhisperLocalProvider,
    available_providers,
    build_provider,
    list_provider_ids,
    select_provider,
)
from openakita_plugin_sdk.contrib.asr.dashscope_paraformer import (
    _flatten_paraformer_transcript,
)


def test_base_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseASRProvider({})  # type: ignore[abstract]


def test_list_provider_ids() -> None:
    ids = list_provider_ids()
    assert "dashscope_paraformer" in ids
    assert "whisper_local" in ids
    assert "stub" in ids


def test_priority_constants_distinct() -> None:
    assert PROVIDER_PRIORITY_CHINA[0] == "dashscope_paraformer"
    assert PROVIDER_PRIORITY_GLOBAL[0] == "whisper_local"


def test_build_provider_known() -> None:
    assert isinstance(build_provider("stub"), StubASRProvider)


def test_build_provider_unknown_raises() -> None:
    with pytest.raises(ASRError):
        build_provider("not-a-provider")


def test_dashscope_requires_api_key() -> None:
    assert DashScopeParaformerProvider({}).is_available() is False
    assert DashScopeParaformerProvider({"api_key": "k"}).is_available() is True


def test_whisper_local_availability_reflects_pathlookup(monkeypatch: pytest.MonkeyPatch) -> None:
    p = WhisperLocalProvider({})
    monkeypatch.setattr(
        "openakita_plugin_sdk.contrib.asr.whisper_local.shutil.which",
        lambda _: None,
    )
    assert p.is_available() is False
    monkeypatch.setattr(
        "openakita_plugin_sdk.contrib.asr.whisper_local.shutil.which",
        lambda _: "/usr/bin/whisper-cli",
    )
    assert p.is_available() is True


def test_stub_provider_always_available_and_returns_chunk() -> None:
    p = StubASRProvider({})
    assert p.is_available() is True


@pytest.mark.asyncio
async def test_stub_provider_transcribe_yields_one_chunk(tmp_path: Path) -> None:
    p = StubASRProvider({})
    src = tmp_path / "fake.mp3"
    src.write_bytes(b"x")
    result = await p.transcribe(src)
    assert isinstance(result, ASRResult)
    assert len(result.chunks) == 1
    assert result.chunks[0].text.startswith("[stub")


def test_available_providers_matrix() -> None:
    """Without api_key paraformer must be filtered out; stub and whisper
    depend on environment but should not raise."""
    avail = available_providers(configs={"stub": {}})
    avail_ids = {p.provider_id for p in avail}
    assert "dashscope_paraformer" not in avail_ids
    assert "stub" in avail_ids


def test_select_excludes_stub_unless_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openakita_plugin_sdk.contrib.asr.whisper_local.shutil.which",
        lambda _: None,
    )
    with pytest.raises(ASRError):
        select_provider("auto", configs={}, region="cn", allow_stub=False)
    p = select_provider("auto", configs={}, region="cn", allow_stub=True)
    assert p.provider_id == "stub"


def test_select_explicit_provider_unavailable_raises() -> None:
    with pytest.raises(ASRError):
        select_provider("dashscope_paraformer", configs={})


def test_paraformer_transcribe_requires_source_url(tmp_path: Path) -> None:
    p = DashScopeParaformerProvider({"api_key": "k"})
    src = tmp_path / "audio.mp3"
    src.write_bytes(b"x")
    import asyncio
    with pytest.raises(ASRError) as exc_info:
        asyncio.run(p.transcribe(src))
    assert "publicly reachable" in str(exc_info.value)


def test_flatten_paraformer_transcript_handles_typical_payload() -> None:
    payload = {
        "transcripts": [
            {
                "sentences": [
                    {"begin_time": 0, "end_time": 1500, "text": "你好", "confidence": 0.9},
                    {"begin_time": 1500, "end_time": 3000, "text": "world"},
                    {"begin_time": "bad", "end_time": "bad", "text": "skip"},
                ]
            }
        ]
    }
    out = _flatten_paraformer_transcript(payload)
    assert len(out) == 2
    assert out[0] == ASRChunk(start=0.0, end=1.5, text="你好", confidence=0.9)
    assert out[1].text == "world"


def test_flatten_paraformer_empty_payload() -> None:
    assert _flatten_paraformer_transcript({}) == []
    assert _flatten_paraformer_transcript({"transcripts": []}) == []
