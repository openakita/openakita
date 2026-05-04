"""Phase 2.4 — tts_client.py: engine routing + dependency error + duration probe."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from manga_inline.vendor_client import VendorError
from tts_client import (
    MangaTTSClient,
    _probe_audio_duration,
    _synth_cosyvoice,
    _synth_edge,
    resolve_engine,
)

# ─── Engine routing ─────────────────────────────────────────────────────


def test_resolve_engine_for_known_edge_voice() -> None:
    assert resolve_engine("zh-CN-XiaoyiNeural") == "edge"


def test_resolve_engine_for_known_cosyvoice() -> None:
    assert resolve_engine("longxiaochun") == "cosyvoice"


def test_resolve_engine_falls_back_to_edge_for_zh_prefix() -> None:
    """Unknown voice id but cn-en-locale style → edge (free)."""
    assert resolve_engine("zh-CN-NewVoiceNotInRegistry") == "edge"


def test_resolve_engine_falls_back_to_cosyvoice_for_other() -> None:
    """A user-cloned CosyVoice that didn't make it into VOICES_BY_ID
    still routes to the paid path."""
    assert resolve_engine("user_cloned_voice_123") == "cosyvoice"


# ─── synth() input validation ───────────────────────────────────────────


async def test_synth_rejects_empty_text(tmp_path: Path) -> None:
    c = MangaTTSClient()
    with pytest.raises(ValueError, match="text must not be empty"):
        await c.synth(text="", voice_id="zh-CN-XiaoyiNeural", output_path=tmp_path / "x.mp3")


async def test_synth_rejects_empty_voice(tmp_path: Path) -> None:
    c = MangaTTSClient()
    with pytest.raises(ValueError, match="voice_id must not be empty"):
        await c.synth(text="x", voice_id="", output_path=tmp_path / "x.mp3")


# ─── Edge-TTS dependency surface ────────────────────────────────────────


async def test_edge_path_surfaces_install_hint_when_pkg_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Force-import ``edge_tts`` to raise so the missing-dep path runs.

    We can't rely on the real edge_tts being installed in CI; this test
    deterministically fakes the ImportError and asserts the VendorError
    carries a ``pip install`` line and ``kind="dependency"``.
    """
    import builtins

    real_import = builtins.__import__

    def faux_import(name: str, *args: Any, **kw: Any) -> Any:
        if name == "edge_tts":
            raise ImportError("simulated missing edge-tts")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", faux_import)
    with pytest.raises(VendorError) as exc:
        await _synth_edge(
            text="hi",
            voice_id="zh-CN-XiaoyiNeural",
            output_path=tmp_path / "out.mp3",
        )
    assert exc.value.kind == "dependency"
    assert "pip install edge-tts" in str(exc.value)


# ─── CosyVoice dependency surface ───────────────────────────────────────


async def test_cosyvoice_path_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(VendorError) as exc:
        await _synth_cosyvoice(
            text="x",
            voice_id="longxiaochun",
            output_path=tmp_path / "out.mp3",
            api_key="",
        )
    assert exc.value.kind == "auth"


async def test_cosyvoice_path_surfaces_install_hint_when_sdk_missing(
    monkeypatch, tmp_path: Path
) -> None:
    import builtins

    real_import = builtins.__import__

    def faux_import(name: str, *args: Any, **kw: Any) -> Any:
        if name in ("dashscope", "dashscope.audio.tts_v2"):
            raise ImportError("simulated missing dashscope")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", faux_import)
    with pytest.raises(VendorError) as exc:
        await _synth_cosyvoice(
            text="x",
            voice_id="longxiaochun",
            output_path=tmp_path / "out.mp3",
            api_key="sk-x",
        )
    assert exc.value.kind == "dependency"
    assert "pip install dashscope" in str(exc.value)


# ─── Hot-reload settings ────────────────────────────────────────────────


async def test_tts_client_reads_settings_for_cosyvoice(monkeypatch, tmp_path: Path) -> None:
    """The cosyvoice synth path pulls dashscope_api_key from the
    read_settings callable on every call — confirm it propagates."""
    store: dict[str, Any] = {"dashscope_api_key": ""}
    c = MangaTTSClient(read_settings=lambda: dict(store))

    captured: dict[str, str] = {}

    async def fake_cosy(
        *, text: str, voice_id: str, output_path: Path, api_key: str, audio_format: str
    ) -> dict[str, Any]:
        captured["api_key"] = api_key
        captured["voice"] = voice_id
        return {
            "bytes": b"x",
            "duration_sec": 1.0,
            "engine": "cosyvoice",
            "voice_id": voice_id,
            "path": str(output_path),
        }

    # Patch the module-level helper that synth() dispatches to.
    import tts_client

    monkeypatch.setattr(tts_client, "_synth_cosyvoice", fake_cosy)

    # First call sees an empty key → propagates to the fake.
    await c.synth(text="hi", voice_id="longxiaochun", output_path=tmp_path / "x.mp3")
    assert captured["api_key"] == ""

    # User edits Settings; next call sees the new key with no plugin reload.
    store["dashscope_api_key"] = "sk-newer"
    await c.synth(text="hi2", voice_id="longxiaochun", output_path=tmp_path / "y.mp3")
    assert captured["api_key"] == "sk-newer"


async def test_tts_client_routes_edge_voice_to_edge_synth(monkeypatch, tmp_path: Path) -> None:
    c = MangaTTSClient(read_settings=lambda: {})

    called: list[str] = []

    async def fake_edge(*, text, voice_id, output_path, speed):
        called.append("edge")
        return {
            "bytes": b"x",
            "duration_sec": 1.0,
            "engine": "edge",
            "voice_id": voice_id,
            "path": str(output_path),
        }

    async def fake_cosy(*, text, voice_id, output_path, api_key, audio_format):
        called.append("cosy")
        return {
            "bytes": b"x",
            "duration_sec": 1.0,
            "engine": "cosyvoice",
            "voice_id": voice_id,
            "path": str(output_path),
        }

    import tts_client

    monkeypatch.setattr(tts_client, "_synth_edge", fake_edge)
    monkeypatch.setattr(tts_client, "_synth_cosyvoice", fake_cosy)

    await c.synth(text="hi", voice_id="zh-CN-XiaoyiNeural", output_path=tmp_path / "a.mp3")
    await c.synth(text="hi", voice_id="longxiaochun", output_path=tmp_path / "b.mp3")
    assert called == ["edge", "cosy"]


# ─── Duration probe ─────────────────────────────────────────────────────


def test_probe_duration_falls_back_to_5s_on_unknown(tmp_path: Path) -> None:
    p = tmp_path / "garbage.bin"
    p.write_bytes(b"x" * 100)
    assert _probe_audio_duration(p) == 5.0


def test_probe_duration_reads_riff_byte_rate(tmp_path: Path) -> None:
    """Hand-craft a minimal WAV header so the RIFF fallback reads it.

    44-byte RIFF header + 16000 bytes of audio at byte_rate=16000 →
    1.0 second duration.
    """
    header = bytearray(44)
    header[:4] = b"RIFF"
    header[8:12] = b"WAVE"
    struct.pack_into("<I", header, 28, 16000)  # byte_rate
    p = tmp_path / "tone.wav"
    p.write_bytes(bytes(header) + b"\x00" * 16000)
    duration = _probe_audio_duration(p)
    assert 0.99 <= duration <= 1.01


def test_probe_duration_falls_back_when_mutagen_raises(tmp_path: Path, monkeypatch) -> None:
    """If mutagen is installed but raises (e.g. corrupt mp3), the RIFF
    fallback still gets a chance — and if THAT also fails, we return 5.0."""
    p = tmp_path / "broken.dat"
    p.write_bytes(b"")
    assert _probe_audio_duration(p) == 5.0
