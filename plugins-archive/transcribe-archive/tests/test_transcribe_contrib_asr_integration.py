"""Phase 2-05 contrib.asr integration tests for transcribe-archive.

Verifies that:

* the engine exposes :class:`ContribAdapterProvider` and converts
  ``ASRChunk`` objects from a fake :mod:`contrib.asr` provider into the
  word-level ``Word`` shape the chunker / cache layer expects;
* :class:`Plugin._build_provider` routes through ``contrib.asr.select_provider``
  and wraps the result with the adapter (so ``provider_id`` carries the
  ``contrib:`` prefix);
* the legacy ``stub`` provider id still bypasses the adapter and returns
  the in-tree ``StubProvider`` (smoke / offline path);
* cache key composition includes the inner provider id so swapping the
  contrib provider transparently invalidates stale chunks.
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
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def engine_module():
    import transcribe_engine as eng
    return eng


@pytest.fixture()
def plugin_module():
    import plugin as plg
    return plg


def _make_fake_asr_chunks() -> list[Any]:
    from _shared.asr import ASRChunk
    return [
        ASRChunk(text="hello", start=0.0, end=1.0, confidence=0.9),
        ASRChunk(text="world", start=1.0, end=2.0, confidence=0.8),
    ]


class _FakeASRResult:
    def __init__(self) -> None:
        self.chunks = _make_fake_asr_chunks()


class _FakeContribProvider:
    """Async, file-shaped provider matching contrib.asr.BaseASRProvider's
    public surface that ContribAdapterProvider knows how to drive."""

    provider_id = "fake_dashscope"

    async def transcribe(self, audio_path: Path, *, language: str) -> Any:
        await asyncio.sleep(0)
        return _FakeASRResult()


def test_contrib_adapter_provider_id_is_prefixed(engine_module) -> None:
    inner = _FakeContribProvider()
    adapter = engine_module.ContribAdapterProvider(inner)
    assert adapter.provider_id == "contrib:fake_dashscope"


def test_contrib_adapter_chunk_returns_word_objects(engine_module, tmp_path: Path) -> None:
    """The adapter must bridge the async file API into the engine's
    sync chunk-shaped protocol and yield Word objects with sane bounds."""
    inner = _FakeContribProvider()
    adapter = engine_module.ContribAdapterProvider(inner)
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"")
    words = adapter.transcribe_chunk(audio, language="zh")
    assert len(words) == 2
    assert words[0].text == "hello"
    assert words[0].start == 0.0
    assert words[0].end == 1.0
    assert 0.0 <= words[0].confidence <= 1.0
    assert words[1].text == "world"


def test_contrib_adapter_cache_key_includes_inner_provider(engine_module) -> None:
    inner = _FakeContribProvider()
    adapter = engine_module.ContribAdapterProvider(
        inner, cache_args={"language": "zh", "model": "base"},
    )
    args = adapter.args_for_cache_key()
    assert args["inner_provider"] == "fake_dashscope"
    assert args["language"] == "zh"
    assert args["model"] == "base"


def test_contrib_adapter_swallows_provider_errors_to_empty_words(
    engine_module, tmp_path: Path,
) -> None:
    """A failing inner provider must surface as zero words for that chunk
    so the engine can flag the chunk as failed without aborting the job."""

    class _Boom:
        provider_id = "boom"

        async def transcribe(self, audio_path: Path, *, language: str):
            raise RuntimeError("network down")

    adapter = engine_module.ContribAdapterProvider(_Boom())
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"")
    assert adapter.transcribe_chunk(audio, language="zh") == []


def test_plugin_build_provider_stub_returns_stub(plugin_module) -> None:
    """The 'stub' shortcut MUST stay outside contrib.asr — it's the
    offline/smoke path and is intentionally dependency-free."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    prov = p._build_provider("stub", {}, cfg={})
    from transcribe_engine import StubProvider
    assert isinstance(prov, StubProvider)


def test_plugin_build_provider_routes_through_contrib(
    plugin_module, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stub provider ids must go through contrib.asr.select_provider
    and be wrapped with ContribAdapterProvider."""
    fake = _FakeContribProvider()

    def _fake_select(provider_id, *, configs, region, allow_stub):
        assert provider_id == "dashscope_paraformer"
        assert region == "cn"
        assert allow_stub is False
        assert configs.get("dashscope_paraformer", {}).get("api_key") == "sk-test"
        return fake

    monkeypatch.setattr(plugin_module, "_sdk_select_asr", _fake_select)
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    prov = p._build_provider(
        "dashscope_paraformer",
        {},
        cfg={"dashscope_api_key": "sk-test", "asr_region": "cn"},
    )
    assert prov.provider_id == "contrib:fake_dashscope"


def test_plugin_build_provider_translates_asrerror_to_value_error(
    plugin_module, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When contrib.asr can't satisfy the request, the plugin must
    raise a ValueError with an actionable next-step message rather than
    leaking the lower-level ASRError type."""
    from _shared.asr import ASRError

    def _fake_select(*args, **kw):
        raise ASRError("no providers available")

    monkeypatch.setattr(plugin_module, "_sdk_select_asr", _fake_select)
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    with pytest.raises(ValueError) as exc:
        p._build_provider("dashscope_paraformer", {}, cfg={})
    assert "contrib.asr" in str(exc.value)


def test_plugin_redacts_sensitive_config_values(plugin_module) -> None:
    cfg = {
        "dashscope_api_key": "sk-abcdef123456",
        "whisper_api_key": "ab",
        "asr_region": "cn",
    }
    redacted = plugin_module._redacted_config(cfg)
    assert redacted["asr_region"] == "cn"
    assert redacted["dashscope_api_key"].startswith("***")
    assert redacted["dashscope_api_key"].endswith("3456")
    assert redacted["whisper_api_key"] == "***"
