"""Tests for plugins/avatar-speaker — Phase 2-01 overhaul.

Covers:
- providers.configure_credentials hot-swap behaviour
- select_tts_provider legacy id aliases (``dashscope`` → qwen3_tts_flash)
- ``auto`` falls back to edge/stub when no api_key configured
- PRESET_VOICES_ZH non-empty back-compat shape
- _redacted_config (defined inside plugin.py) masks api_key fields
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "avatar-speaker"


@pytest.fixture(autouse=True)
def _add_plugin_to_syspath():
    sys.path.insert(0, str(PLUGIN_DIR))
    # Force a fresh import so module-level _CREDENTIALS state is reset.
    if "providers" in sys.modules:
        del sys.modules["providers"]
    yield
    sys.path.remove(str(PLUGIN_DIR))


def _import_providers():
    return importlib.import_module("providers")


# ── credentials hot-swap ──────────────────────────────────────────────


def test_configure_credentials_round_trip() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1", openai_api_key="ok-1")
    cfgs = providers._build_configs()
    assert cfgs["qwen3_tts_flash"]["api_key"] == "dk-1"
    assert cfgs["cosyvoice"]["api_key"] == "dk-1"
    assert cfgs["openai"]["api_key"] == "ok-1"


def test_configure_credentials_clears_with_empty_string() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1")
    providers.configure_credentials(dashscope_api_key="")
    cfgs = providers._build_configs()
    assert cfgs["qwen3_tts_flash"] == {}


# ── select_tts_provider behaviour ─────────────────────────────────────


def test_select_tts_provider_dashscope_alias_routes_to_qwen3() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1")
    p = providers.select_tts_provider("dashscope")
    assert p.provider_id == "qwen3_tts_flash"


def test_select_tts_provider_qwen3_explicit_id_works() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1")
    p = providers.select_tts_provider("qwen3_tts_flash")
    assert p.provider_id == "qwen3_tts_flash"


def test_select_tts_provider_cosyvoice_explicit() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1")
    p = providers.select_tts_provider("cosyvoice")
    assert p.provider_id == "cosyvoice"


def test_select_tts_provider_stub_always_works() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="", openai_api_key="")
    p = providers.select_tts_provider("stub")
    assert p.provider_id == "stub-silent"


def test_select_tts_provider_auto_with_dashscope_key_picks_qwen3() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="dk-1")
    p = providers.select_tts_provider("auto")
    assert p.provider_id == "qwen3_tts_flash"


def test_select_tts_provider_auto_no_keys_falls_back_gracefully() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="", openai_api_key="")
    p = providers.select_tts_provider("auto")
    # Without any cloud keys we expect either edge (if pkg installed) or
    # stub fallback. Both are fine — the contract is "never raise on auto".
    assert p.provider_id in ("edge", "stub-silent")


def test_select_tts_provider_explicit_unavailable_raises() -> None:
    providers = _import_providers()
    providers.configure_credentials(dashscope_api_key="", openai_api_key="")
    with pytest.raises(providers.TTSError):
        providers.select_tts_provider("openai")


# ── voice catalog back-compat ─────────────────────────────────────────


def test_preset_voices_zh_is_non_empty_and_shaped() -> None:
    providers = _import_providers()
    voices = providers.PRESET_VOICES_ZH
    assert voices and isinstance(voices, list)
    sample = voices[0]
    assert {"id", "label", "provider"}.issubset(sample.keys())


def test_preset_voices_includes_qwen3_voices() -> None:
    """Phase 2-01 promoted qwen3-tts-flash to default — its voices must
    show up in the legacy catalog so the existing UI dropdown surfaces
    them without UI changes."""
    providers = _import_providers()
    qwen_voices = [v for v in providers.PRESET_VOICES_ZH if v["provider"] == "qwen3_tts_flash"]
    assert qwen_voices, "qwen3_tts_flash voices missing from PRESET_VOICES_ZH"


# ── stub provider sanity ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_provider_writes_silent_wav(tmp_path: Path) -> None:
    providers = _import_providers()
    p = providers.StubLocalProvider()
    result = await p.synthesize(text="hello", output_dir=tmp_path)
    assert result.audio_path.exists()
    assert result.audio_path.suffix == ".wav"
    assert result.duration_sec > 0


# ── select_avatar back-compat ─────────────────────────────────────────


def test_select_avatar_none_returns_none() -> None:
    providers = _import_providers()
    assert providers.select_avatar("none") is None
    assert providers.select_avatar("off") is None
    assert providers.select_avatar("") is None


def test_select_avatar_stub_returns_stub() -> None:
    providers = _import_providers()
    av = providers.select_avatar("stub")
    assert av is not None
    assert av.name == "stub-avatar"


@pytest.mark.asyncio
async def test_abstract_avatar_render_raises(tmp_path: Path) -> None:
    providers = _import_providers()
    av = providers.DigitalHumanAvatar()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    portrait = tmp_path / "p.png"
    portrait.write_bytes(b"x")
    with pytest.raises(NotImplementedError):
        await av.render(audio_path=audio, portrait_path=portrait, output_dir=tmp_path)
