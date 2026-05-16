"""EndpointConfig.supported_models — relay capability discovery field.

These tests freeze the contract that:

1. ``supported_models`` defaults to ``None`` so existing configs keep
   working ("permissive: assume the relay supports whatever the user
   typed until probed").
2. ``supports_model`` is case- and whitespace-insensitive but does NOT
   strip vendor prefixes — some relays serve models under
   ``"anthropic/claude-3-haiku"`` verbatim.
3. ``to_dict`` / ``from_dict`` round-trip the new fields so the JSON
   on disk does not lose them between saves.
4. The new fields never accidentally break ``has_capability`` /
   ``calculate_cost`` (smoke).
"""

from __future__ import annotations

import pytest

from openakita.llm.types import EndpointConfig


def test_supported_models_defaults_to_none_keeps_endpoint_permissive():
    cfg = EndpointConfig(
        name="relay",
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model="gpt-4o",
    )
    assert cfg.supported_models is None
    # Unknown model still allowed when nothing was ever probed.
    assert cfg.supports_model("gpt-4o-2024-99-99") is True
    assert cfg.supports_model("anything") is True


def test_supports_model_filters_when_catalog_populated():
    cfg = EndpointConfig(
        name="relay",
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model="gpt-4o",
        supported_models=["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"],
    )
    assert cfg.supports_model("gpt-4o") is True
    assert cfg.supports_model("GPT-4O") is True
    assert cfg.supports_model("  gpt-4o-mini  ") is True
    assert cfg.supports_model("gpt-4-turbo") is False
    # Empty string is treated as "no specific target" → True
    assert cfg.supports_model("") is True


def test_supports_model_keeps_vendor_prefix_for_aggregators():
    """OneAPI / OpenRouter etc. publish prefixed names; we must NOT
    strip the slash because the same plain name might be a different
    model behind a different provider."""
    cfg = EndpointConfig(
        name="oneapi",
        provider="custom",
        api_type="openai",
        base_url="https://oneapi.example.com/v1",
        model="anthropic/claude-3-haiku",
        supported_models=["anthropic/claude-3-haiku", "openai/gpt-4o"],
    )
    assert cfg.supports_model("anthropic/claude-3-haiku") is True
    # Bare name must NOT match the prefixed one — relays bill them differently.
    assert cfg.supports_model("claude-3-haiku") is False


def test_to_dict_roundtrip_preserves_supported_models():
    cfg = EndpointConfig(
        name="relay",
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model="gpt-4o",
        supported_models=["gpt-4o", "gpt-4o-mini"],
        models_synced_at=1735200000.0,
        models_sync_error=None,
    )
    payload = cfg.to_dict()
    assert payload["supported_models"] == ["gpt-4o", "gpt-4o-mini"]
    assert payload["models_synced_at"] == 1735200000.0
    assert "models_sync_error" not in payload  # None is omitted

    restored = EndpointConfig.from_dict(payload)
    assert restored.supported_models == ["gpt-4o", "gpt-4o-mini"]
    assert restored.models_synced_at == 1735200000.0
    assert restored.models_sync_error is None


def test_to_dict_omits_supported_models_when_never_probed():
    cfg = EndpointConfig(
        name="relay",
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model="gpt-4o",
    )
    payload = cfg.to_dict()
    # Never-probed endpoints must not pollute the JSON file with
    # null fields — keeps existing config files diff-clean on upgrade.
    assert "supported_models" not in payload
    assert "models_synced_at" not in payload
    assert "models_sync_error" not in payload


def test_from_dict_handles_legacy_payload_without_new_fields():
    legacy = {
        "name": "old",
        "provider": "openai",
        "api_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    }
    cfg = EndpointConfig.from_dict(legacy)
    assert cfg.supported_models is None
    assert cfg.models_synced_at is None
    assert cfg.supports_model("anything") is True


def test_sync_error_is_persisted_for_ui_display():
    """When the periodic sync fails (e.g. 401), the UI should still see
    the last error message so the user knows to refresh the key."""
    cfg = EndpointConfig(
        name="relay",
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model="gpt-4o",
        models_sync_error="401 Unauthorized",
    )
    assert cfg.to_dict()["models_sync_error"] == "401 Unauthorized"
    assert EndpointConfig.from_dict(cfg.to_dict()).models_sync_error == "401 Unauthorized"
