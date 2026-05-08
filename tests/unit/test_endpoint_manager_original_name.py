from __future__ import annotations

import json

import pytest

from openakita.llm.endpoint_manager import EndpointManager


def test_save_endpoint_can_rename_without_deleting_key(tmp_path):
    manager = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    saved = manager.save_endpoint(
        {
            "name": "old",
            "provider": "openai",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o",
            "priority": 10,
        },
        api_key="sk-original",
    )

    renamed = manager.save_endpoint(
        {
            "name": "new",
            "provider": "openai",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "priority": 10,
        },
        original_name="old",
    )

    config = json.loads((tmp_path / "data" / "llm_endpoints.json").read_text(encoding="utf-8"))
    endpoints = config["endpoints"]

    assert [ep["name"] for ep in endpoints] == ["new"]
    assert renamed["api_key_env"] == saved["api_key_env"]
    assert f"{saved['api_key_env']}=sk-original" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_save_endpoint_rename_rejects_existing_name(tmp_path):
    manager = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")
    manager.save_endpoint({"name": "old", "provider": "openai", "model": "a", "priority": 10})
    manager.save_endpoint({"name": "taken", "provider": "openai", "model": "b", "priority": 20})

    with pytest.raises(ValueError, match="already exists"):
        manager.save_endpoint(
            {"name": "taken", "provider": "openai", "model": "c", "priority": 10},
            original_name="old",
        )


def test_save_endpoints_batch_shares_one_api_key_env(tmp_path):
    manager = EndpointManager(tmp_path, config_path=tmp_path / "data" / "llm_endpoints.json")

    saved = manager.save_endpoints(
        [
            {"name": "openai-gpt-4o", "provider": "openai", "model": "gpt-4o", "priority": 10},
            {"name": "openai-gpt-4o-mini", "provider": "openai", "model": "gpt-4o-mini", "priority": 20},
        ],
        api_key="sk-batch",
    )

    config = json.loads((tmp_path / "data" / "llm_endpoints.json").read_text(encoding="utf-8"))
    endpoints = config["endpoints"]

    assert [ep["name"] for ep in endpoints] == ["openai-gpt-4o", "openai-gpt-4o-mini"]
    assert len({ep["api_key_env"] for ep in saved}) == 1
    assert len({ep["api_key_env"] for ep in endpoints}) == 1
    assert "OPENAI_API_KEY=sk-batch" in (tmp_path / ".env").read_text(encoding="utf-8")
