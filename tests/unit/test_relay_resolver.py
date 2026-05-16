"""openakita.relay.resolver — plugin-facing relay endpoint lookup.

Verifies the three contracts plugins rely on:

1. ``list_relay_endpoints`` only returns enabled relays by default
   (the user's toggle on the LLMView page MUST be honoured) and can
   be filtered by capability so a TTS plugin never sees an image
   relay.
2. ``resolve_relay_endpoint`` is case- and whitespace-insensitive on
   the lookup name; missing names raise RelayNotFound carrying the
   ``available`` list so the plugin can show "did you mean X?".
3. ``RelayReference.supports_model`` matches the LLM core
   ``EndpointConfig.supports_model`` behaviour bit-for-bit so the
   plugin layer's "grey out unsupported model" UI matches the LLM
   layer's eligibility filter.

The tests use real EndpointManager + real on-disk JSON in tmp_path
to make sure the full ``api_key_env`` -> .env lookup path works.
"""

from __future__ import annotations

import os

import pytest

from openakita.llm.endpoint_manager import EndpointManager
from openakita.relay import RelayNotFound, list_relay_endpoints, resolve_relay_endpoint


@pytest.fixture
def workspace_with_relays(tmp_path, monkeypatch):
    mgr = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    # Image relay (enabled, with synced catalog)
    mgr.save_endpoint(
        {
            "name": "yunwu-image",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://relay.example.com/v1",
            "model": "wan2.7-image",
            "capabilities": ["image"],
            "supported_models": ["wan2.7-image", "wan2.7-image-pro"],
            "models_synced_at": 1735200000.0,
        },
        api_key="sk-image",
        endpoint_type="relay_endpoints",
    )
    # TTS relay (enabled, no synced catalog)
    mgr.save_endpoint(
        {
            "name": "free-tts",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://tts.example.com/v1",
            "model": "cosyvoice-v2",
            "capabilities": ["tts"],
        },
        api_key="sk-tts",
        endpoint_type="relay_endpoints",
    )
    # Video relay (DISABLED — must not surface to plugins by default)
    mgr.save_endpoint(
        {
            "name": "off-video",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://video.example.com/v1",
            "model": "wan2.6-i2v",
            "capabilities": ["video"],
            "enabled": False,
        },
        api_key="sk-video",
        endpoint_type="relay_endpoints",
    )
    yield tmp_path


def test_list_returns_enabled_relays_only(workspace_with_relays):
    names = [r.name for r in list_relay_endpoints(workspace_with_relays)]
    assert "yunwu-image" in names
    assert "free-tts" in names
    assert "off-video" not in names  # disabled


def test_list_can_include_disabled_for_settings_ui(workspace_with_relays):
    names = [
        r.name for r in list_relay_endpoints(workspace_with_relays, enabled_only=False)
    ]
    assert "off-video" in names


def test_capability_filter_drops_non_matching(workspace_with_relays):
    image_refs = list_relay_endpoints(
        workspace_with_relays, required_capability="image"
    )
    assert [r.name for r in image_refs] == ["yunwu-image"]
    tts_refs = list_relay_endpoints(
        workspace_with_relays, required_capability="tts"
    )
    assert [r.name for r in tts_refs] == ["free-tts"]


def test_resolve_returns_resolved_api_key(workspace_with_relays):
    ref = resolve_relay_endpoint("yunwu-image", workspace_with_relays)
    assert ref.base_url == "https://relay.example.com/v1"
    assert ref.api_key == "sk-image"
    assert "image" in ref.capabilities
    assert ref.supported_models == ["wan2.7-image", "wan2.7-image-pro"]
    assert ref.models_synced_at == 1735200000.0


def test_resolve_is_case_insensitive(workspace_with_relays):
    ref = resolve_relay_endpoint("  YUNWU-IMAGE  ", workspace_with_relays)
    assert ref.name == "yunwu-image"


def test_resolve_unknown_raises_with_available_hint(workspace_with_relays):
    with pytest.raises(RelayNotFound) as ei:
        resolve_relay_endpoint("ghost", workspace_with_relays)
    assert ei.value.name == "ghost"
    assert "yunwu-image" in ei.value.available
    assert "yunwu-image" in str(ei.value)


def test_resolve_disabled_relay_raises(workspace_with_relays):
    with pytest.raises(RelayNotFound):
        resolve_relay_endpoint("off-video", workspace_with_relays)


def test_resolve_capability_mismatch_raises(workspace_with_relays):
    with pytest.raises(RelayNotFound):
        resolve_relay_endpoint(
            "yunwu-image", workspace_with_relays, required_capability="tts"
        )


def test_supports_model_mirrors_endpoint_config(workspace_with_relays):
    ref = resolve_relay_endpoint("yunwu-image", workspace_with_relays)
    assert ref.supports_model("wan2.7-image") is True
    assert ref.supports_model("WAN2.7-IMAGE") is True
    assert ref.supports_model("not-in-catalog") is False
    # Empty target = "no specific model" -> always allowed
    assert ref.supports_model("") is True

    # TTS relay has no probed catalog -> permissive
    tts = resolve_relay_endpoint("free-tts", workspace_with_relays)
    assert tts.supports_model("anything") is True


def test_workspace_resolution_falls_back_to_env(workspace_with_relays, monkeypatch):
    """OPENAKITA_WORKSPACE is set by openakita serve; honour it when
    the caller doesn't pass an explicit workspace_dir."""
    monkeypatch.setenv("OPENAKITA_WORKSPACE", str(workspace_with_relays))
    names = [r.name for r in list_relay_endpoints()]
    assert "yunwu-image" in names
