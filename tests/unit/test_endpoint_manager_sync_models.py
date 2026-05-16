"""EndpointManager.sync_endpoint_models — write-back of relay catalog.

These tests exercise the full save -> sync -> reload loop with
:func:`probe_models` stubbed out. They verify three contracts:

1. Successful sync writes supported_models + models_synced_at, clears
   any stale models_sync_error, and the saved JSON survives a round-
   trip through EndpointConfig.from_dict.
2. Failed sync preserves the previous catalog (does NOT wipe it),
   stamps models_sync_error with the user-facing Chinese message,
   and still returns ok=False instead of raising.
3. Unknown endpoint name surfaces a KeyError so the API can map it
   to 404 instead of silently no-op-ing.
"""

from __future__ import annotations

import pytest

from openakita.llm.endpoint_manager import EndpointManager
from openakita.llm.model_probe import ProbeAuthError
from openakita.llm.types import EndpointConfig


@pytest.fixture
def manager_with_relay(tmp_path):
    mgr = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    mgr.save_endpoint(
        {
            "name": "yunwu-relay",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://relay.example.com/v1",
            "model": "gpt-4o",
            "priority": 10,
        },
        api_key="sk-relay",
    )
    return mgr


def test_sync_writes_supported_models_and_clears_error(monkeypatch, manager_with_relay):
    def fake_probe(**kwargs):
        # Confirm we were called with the right base + key.
        assert kwargs["base_url"] == "https://relay.example.com/v1"
        assert kwargs["api_key"] == "sk-relay"
        return ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]

    monkeypatch.setattr("openakita.llm.endpoint_manager.probe_models", fake_probe, raising=False)
    # The import inside the method shadows the attribute we just patched;
    # monkeypatch the model_probe symbol the helper actually uses.
    monkeypatch.setattr("openakita.llm.model_probe.probe_models", fake_probe)

    result = manager_with_relay.sync_endpoint_models("yunwu-relay")
    assert result["ok"] is True
    assert result["model_count"] == 3
    assert "gpt-4o" in result["models"]
    assert result["error"] is None
    assert isinstance(result["synced_at"], float)

    # Saved JSON survives a round-trip through EndpointConfig
    saved = manager_with_relay.list_endpoints()
    assert len(saved) == 1
    cfg = EndpointConfig.from_dict(saved[0])
    assert cfg.supported_models == ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]
    assert cfg.supports_model("gpt-4o") is True
    assert cfg.supports_model("gemini-pro") is False
    assert cfg.models_sync_error is None


def test_sync_failure_preserves_previous_catalog(monkeypatch, manager_with_relay):
    # Seed a previous successful sync.
    monkeypatch.setattr(
        "openakita.llm.model_probe.probe_models",
        lambda **kw: ["gpt-4o", "gpt-4o-mini"],
    )
    manager_with_relay.sync_endpoint_models("yunwu-relay")

    # Now the next sync 401s.
    def fake_probe_fail(**kwargs):
        raise ProbeAuthError(
            "HTTP 401", user_message="API Key 被中转站拒绝（HTTP 401）", status=401
        )

    monkeypatch.setattr("openakita.llm.model_probe.probe_models", fake_probe_fail)
    result = manager_with_relay.sync_endpoint_models("yunwu-relay")

    assert result["ok"] is False
    assert "401" in result["error"]
    # Previous catalog MUST survive — users still see real model dropdown
    saved = manager_with_relay.list_endpoints()[0]
    cfg = EndpointConfig.from_dict(saved)
    assert cfg.supported_models == ["gpt-4o", "gpt-4o-mini"]
    assert cfg.models_sync_error is not None and "401" in cfg.models_sync_error


def test_sync_unknown_endpoint_raises_keyerror(manager_with_relay):
    with pytest.raises(KeyError, match="not-a-real-endpoint"):
        manager_with_relay.sync_endpoint_models("not-a-real-endpoint")


def test_sync_endpoint_without_apikey_still_runs(monkeypatch, tmp_path):
    """Some relays expose /v1/models without auth — the sync must not
    crash just because the endpoint hasn't been issued a key yet."""
    mgr = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    mgr.save_endpoint(
        {
            "name": "open-relay",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://open.example.com/v1",
            "model": "any",
            "priority": 1,
        },
    )
    captured: dict = {}

    def fake_probe(**kwargs):
        captured.update(kwargs)
        return ["m1"]

    monkeypatch.setattr("openakita.llm.model_probe.probe_models", fake_probe)
    result = mgr.sync_endpoint_models("open-relay")
    assert result["ok"] is True
    assert captured["api_key"] == ""  # empty, not None / missing
