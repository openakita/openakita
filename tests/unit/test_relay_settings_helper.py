"""openakita.relay.settings_helper.apply_relay_override.

The shared helper every vendor plugin calls during its ``_settings``
phase to translate ``relay_endpoint`` / ``relay_fallback_policy``
into concrete ``base_url`` / ``api_key`` overrides.

These tests freeze the four guarantees plugin code relies on:

1. Backward compatibility: no ``relay_endpoint`` -> the returned dict
   equals the input minus the two relay keys (which were never set).
2. Successful resolve overlays base_url + api_key and stashes the
   :class:`RelayReference` under ``_relay_reference`` for downstream
   ``supports_model`` checks.
3. Missing relay + policy=official -> warn-and-fall-back (no exception);
   missing relay + policy=strict -> SettingsRelayResolutionError with
   a Chinese ``user_message`` that lists the available relays so the
   plugin can show "did you mean X?" without an extra round trip.
4. Empty relay api_key is treated as "use per-plugin key" — some
   relays serve public endpoints and overwriting auth would surprise.
"""

from __future__ import annotations

import pytest

from openakita.llm.endpoint_manager import EndpointManager
from openakita.relay import (
    SettingsRelayResolutionError,
    apply_relay_override,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    mgr = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    mgr.save_endpoint(
        {
            "name": "yunwu-image",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://yunwu.example.com/v1",
            "model": "wan2.7-image",
            "capabilities": ["image", "video"],
        },
        api_key="sk-yunwu",
        endpoint_type="relay_endpoints",
    )
    mgr.save_endpoint(
        {
            "name": "public-relay",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://public.example.com/v1",
            "model": "x",
            "capabilities": ["image"],
        },
        endpoint_type="relay_endpoints",
    )
    monkeypatch.setenv("OPENAKITA_WORKSPACE", str(tmp_path))
    return tmp_path


def test_no_relay_returns_input_unchanged(workspace):
    settings = {"api_key": "sk-direct", "base_url": "https://direct.example.com", "timeout": 60}
    out = apply_relay_override(settings)
    assert out == settings  # neither relay_endpoint nor relay_fallback_policy were ever in input
    # Returned dict is a copy (caller can mutate freely).
    out["api_key"] = "mutated"
    assert settings["api_key"] == "sk-direct"


def test_relay_keys_stripped_even_when_empty(workspace):
    settings = {
        "api_key": "sk-direct",
        "base_url": "https://direct.example.com",
        "relay_endpoint": "",
        "relay_fallback_policy": "official",
    }
    out = apply_relay_override(settings)
    assert "relay_endpoint" not in out
    assert "relay_fallback_policy" not in out
    assert out["api_key"] == "sk-direct"


def test_relay_overlays_base_url_and_api_key(workspace):
    out = apply_relay_override(
        {
            "api_key": "sk-direct",
            "base_url": "https://direct.example.com",
            "relay_endpoint": "yunwu-image",
        },
        required_capability="image",
    )
    assert out["base_url"] == "https://yunwu.example.com/v1"
    assert out["api_key"] == "sk-yunwu"
    assert "_relay_reference" in out
    assert out["_relay_reference"].name == "yunwu-image"


def test_capability_filter_via_helper(workspace):
    """plugin asks for a capability the relay doesn't claim → strict
    raise / official fallback."""
    with pytest.raises(SettingsRelayResolutionError) as ei:
        apply_relay_override(
            {"relay_endpoint": "yunwu-image", "relay_fallback_policy": "strict"},
            required_capability="audio",  # yunwu-image doesn't claim audio
        )
    assert "audio" in ei.value.user_message or "yunwu-image" in ei.value.user_message


def test_public_relay_keeps_per_plugin_api_key(workspace):
    """Relays without an api_key (public endpoints exist) should NOT
    wipe the user's per-plugin key."""
    out = apply_relay_override(
        {
            "api_key": "sk-direct",
            "base_url": "https://direct.example.com",
            "relay_endpoint": "public-relay",
        },
    )
    assert out["base_url"] == "https://public.example.com/v1"
    assert out["api_key"] == "sk-direct"  # per-plugin key preserved


def test_missing_relay_with_official_falls_back(workspace):
    out = apply_relay_override(
        {
            "api_key": "sk-direct",
            "base_url": "https://direct.example.com",
            "relay_endpoint": "ghost",
            "relay_fallback_policy": "official",
        },
    )
    assert out["api_key"] == "sk-direct"
    assert out["base_url"] == "https://direct.example.com"
    assert "_relay_reference" not in out


def test_missing_relay_with_strict_raises(workspace):
    with pytest.raises(SettingsRelayResolutionError) as ei:
        apply_relay_override(
            {"relay_endpoint": "ghost", "relay_fallback_policy": "strict"},
        )
    assert "ghost" in ei.value.user_message
    assert ei.value.user_message.startswith("中转站")


def test_typeerror_on_non_dict_input():
    with pytest.raises(TypeError):
        apply_relay_override("not-a-dict")
