"""Tests for avatar-speaker providers (offline — uses stub).

Updated for Phase 2-01 of the overhaul playbook: providers were
re-routed onto ``openakita_plugin_sdk.contrib.tts`` and credentials
are now injected explicitly via ``configure_credentials`` instead of
being read from ``os.environ`` inside the provider.
"""

from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from providers import (  # noqa: E402
    DigitalHumanAvatar,
    StubAvatar,
    StubLocalProvider,
    TTSError,
    configure_credentials,
    select_avatar,
    select_tts_provider,
)


def test_select_tts_falls_back_to_stub_or_edge() -> None:
    configure_credentials(dashscope_api_key="", openai_api_key="")
    p = select_tts_provider("auto")
    # Either edge-tts is installed → "edge", or fully missing → "stub-silent".
    assert p.provider_id in ("edge", "stub-silent")


def test_select_tts_stub_explicit() -> None:
    p = select_tts_provider("stub")
    assert isinstance(p, StubLocalProvider)


def test_select_tts_openai_no_key_raises() -> None:
    configure_credentials(dashscope_api_key="", openai_api_key="")
    with pytest.raises(TTSError):
        select_tts_provider("openai")


def test_stub_provider_writes_silent_wav(tmp_path) -> None:
    p = StubLocalProvider()
    res = asyncio.run(p.synthesize(text="hello world", voice="x", output_dir=tmp_path))
    assert res.audio_path.exists()
    assert res.duration_sec > 0
    with wave.open(str(res.audio_path)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getnframes() > 0


def test_select_avatar_none_returns_none() -> None:
    assert select_avatar("none") is None
    assert select_avatar("") is None


def test_select_avatar_stub_writes_text(tmp_path) -> None:
    av = select_avatar("stub")
    assert isinstance(av, StubAvatar)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    portrait = tmp_path / "p.png"
    portrait.write_bytes(b"y")
    out = asyncio.run(
        av.render(audio_path=audio, portrait_path=portrait, output_dir=tmp_path)
    )
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "Stub avatar" in txt and "P3" in txt


def test_avatar_base_raises_not_implemented(tmp_path) -> None:
    av = DigitalHumanAvatar()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    portrait = tmp_path / "p.png"
    portrait.write_bytes(b"y")
    with pytest.raises(NotImplementedError) as ei:
        asyncio.run(
            av.render(audio_path=audio, portrait_path=portrait, output_dir=tmp_path)
        )
    assert "P3" in str(ei.value)
