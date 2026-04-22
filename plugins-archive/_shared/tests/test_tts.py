"""Tests for _shared.tts.

Covers the public surface introduced in SDK 0.6.0:
- BaseTTSProvider contract / api_key wiring
- voice catalog filters
- registry: list / build / available / select with priority logic
- per-provider availability (without hitting any vendor)
- StubLocal-like behaviour: edge falls back gracefully when pkg missing
"""

from __future__ import annotations

import pytest
from _shared.tts import (
    PROVIDER_PRIORITY_CHINA,
    PROVIDER_PRIORITY_GLOBAL,
    VOICE_CATALOG,
    BaseTTSProvider,
    CosyVoiceProvider,
    EdgeTTSProvider,
    OpenAITTSProvider,
    Qwen3TTSFlashProvider,
    TTSError,
    TTSResult,
    available_providers,
    build_provider,
    estimate_duration_sec,
    list_provider_ids,
    list_voices,
    select_provider,
    voice_by_id,
)

# ── base / dataclasses ────────────────────────────────────────────────


def test_estimate_duration_floor_one_second() -> None:
    assert estimate_duration_sec("") == pytest.approx(1.0)
    assert estimate_duration_sec("a") == pytest.approx(1.0)


def test_estimate_duration_scales_with_length() -> None:
    short = estimate_duration_sec("a" * 4)
    longer = estimate_duration_sec("a" * 40)
    assert longer > short


def test_base_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseTTSProvider({})  # type: ignore[abstract]


def test_provider_update_api_key_round_trip() -> None:
    p = OpenAITTSProvider({"api_key": "old"})
    assert p.api_key == "old"
    p.update_api_key("new")
    assert p.api_key == "new"
    p.update_api_key(None)
    assert p.api_key is None


def test_provider_disabled_makes_unavailable() -> None:
    p = Qwen3TTSFlashProvider({"api_key": "k", "disabled": True})
    assert p.is_available() is False


# ── voice catalog ─────────────────────────────────────────────────────


def test_voice_catalog_non_empty_per_provider() -> None:
    providers_seen = {v.provider for v in VOICE_CATALOG}
    assert {"qwen3_tts_flash", "cosyvoice", "edge", "openai"}.issubset(providers_seen)


def test_voice_filters() -> None:
    qwen_voices = list_voices(provider="qwen3_tts_flash")
    assert qwen_voices and all(v.provider == "qwen3_tts_flash" for v in qwen_voices)
    zh = list_voices(language="zh")
    assert zh and all(v.language.startswith("zh") for v in zh)


def test_voice_by_id_known_and_unknown() -> None:
    assert voice_by_id("Cherry") is not None
    assert voice_by_id("does-not-exist") is None


# ── registry: build / list / available ────────────────────────────────


def test_list_provider_ids_stable() -> None:
    ids = list_provider_ids()
    assert "qwen3_tts_flash" in ids
    assert "cosyvoice" in ids
    assert "openai" in ids
    assert "edge" in ids


def test_build_provider_unknown_raises() -> None:
    with pytest.raises(TTSError) as exc_info:
        build_provider("nope")
    assert exc_info.value.kind == "config"


def test_build_provider_with_config() -> None:
    p = build_provider("openai", {"api_key": "sk-test"})
    assert isinstance(p, OpenAITTSProvider)
    assert p.api_key == "sk-test"


def test_available_providers_filters_paid_without_key() -> None:
    """Paid providers without an api_key must NOT appear in available()."""
    avail = available_providers(configs={})
    avail_ids = {p.provider_id for p in avail}
    # qwen3_tts_flash / cosyvoice / openai all need api_key — none should be available
    assert "qwen3_tts_flash" not in avail_ids
    assert "cosyvoice" not in avail_ids
    assert "openai" not in avail_ids


def test_available_providers_includes_paid_with_key() -> None:
    avail = available_providers(configs={
        "qwen3_tts_flash": {"api_key": "k"},
        "cosyvoice": {"api_key": "k"},
        "openai": {"api_key": "k"},
    })
    avail_ids = {p.provider_id for p in avail}
    assert {"qwen3_tts_flash", "cosyvoice", "openai"}.issubset(avail_ids)


# ── registry: select_provider priority ────────────────────────────────


def test_priority_constants_distinct() -> None:
    assert PROVIDER_PRIORITY_CHINA != PROVIDER_PRIORITY_GLOBAL
    assert PROVIDER_PRIORITY_CHINA[0] == "qwen3_tts_flash", (
        "China region MUST prefer Bailian first per the playbook."
    )


def test_select_explicit_provider_when_available() -> None:
    p = select_provider("openai", configs={"openai": {"api_key": "k"}})
    assert p.provider_id == "openai"


def test_select_explicit_provider_unavailable_raises() -> None:
    with pytest.raises(TTSError):
        select_provider("openai", configs={})


def test_select_auto_china_picks_qwen_when_available() -> None:
    p = select_provider("auto", configs={
        "qwen3_tts_flash": {"api_key": "k"},
        "openai": {"api_key": "k"},
    }, region="cn")
    assert p.provider_id == "qwen3_tts_flash"


def test_select_auto_global_picks_openai_when_available() -> None:
    p = select_provider("auto", configs={
        "qwen3_tts_flash": {"api_key": "k"},
        "openai": {"api_key": "k"},
    }, region="us")
    assert p.provider_id == "openai"


def test_select_auto_falls_through_to_edge_if_pkg_present() -> None:
    edge = EdgeTTSProvider({})
    if not edge.is_available():
        pytest.skip("edge-tts not installed in this environment")
    p = select_provider("auto", configs={}, region="cn")
    assert p.provider_id == "edge"


def test_select_auto_no_providers_raises() -> None:
    """Force every provider to be unavailable then assert clean error."""
    # Disable edge explicitly + no api_keys means nothing usable
    with pytest.raises(TTSError) as exc_info:
        select_provider("auto", configs={"edge": {"disabled": True}}, region="cn")
    assert "No TTS provider" in str(exc_info.value)


# ── per-provider basic checks ─────────────────────────────────────────


def test_qwen3_requires_api_key_to_be_available() -> None:
    assert Qwen3TTSFlashProvider({}).is_available() is False
    assert Qwen3TTSFlashProvider({"api_key": "k"}).is_available() is True


def test_cosyvoice_requires_api_key_to_be_available() -> None:
    assert CosyVoiceProvider({}).is_available() is False
    assert CosyVoiceProvider({"api_key": "k"}).is_available() is True


def test_openai_requires_api_key_to_be_available() -> None:
    assert OpenAITTSProvider({}).is_available() is False
    assert OpenAITTSProvider({"api_key": "k"}).is_available() is True


def test_openai_invalid_model_falls_back() -> None:
    p = OpenAITTSProvider({"api_key": "k", "model": "tts-99"})
    assert p.model == "tts-1"


# ── result type sanity ────────────────────────────────────────────────


def test_tts_result_dataclass_has_expected_fields() -> None:
    from pathlib import Path
    r = TTSResult(provider="x", audio_path=Path("a.mp3"), duration_sec=1.0, voice="v")
    assert r.provider == "x"
    assert r.voice == "v"
    assert r.raw == {}
