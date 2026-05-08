import json
from types import SimpleNamespace

import pytest

from openakita.api.routes import bug_report
from openakita.api.routes import config as config_routes


class _FakeEndpointManager:
    def __init__(self) -> None:
        self.saved_api_key = "unset"

    def save_endpoint(
        self,
        *,
        endpoint: dict,
        api_key: str | None = None,
        endpoint_type: str = "endpoints",
        expected_version: str | None = None,
        original_name: str | None = None,
    ) -> dict:
        self.saved_api_key = api_key
        saved = dict(endpoint)
        saved.setdefault("api_key_env", "OPENAI_API_KEY")
        saved["endpoint_type"] = endpoint_type
        return saved

    def get_version(self) -> str:
        return "test-version"


@pytest.mark.asyncio
async def test_save_endpoint_returns_ok_when_runtime_reload_fails(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "failed", "reloaded": False, "reason": "boom"},
    )

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={"name": "primary", "provider": "openai", "model": "gpt-4"},
            api_key="sk-real",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert response["saved"] is True
    assert response["reload"]["status"] == "failed"
    assert "配置已保存" in response["warning"]
    assert manager.saved_api_key == "sk-real"


@pytest.mark.asyncio
async def test_save_endpoint_ignores_masked_api_key(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "ok", "reloaded": True},
    )

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={"name": "primary", "provider": "openai", "model": "gpt-4"},
            api_key="sk-****abcd",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert "warning" not in response
    assert manager.saved_api_key is None


def test_collect_endpoint_summary_redacts_keys_and_keeps_diagnostic_fields(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = data_dir / "llm_endpoints.json"
    config_path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "name": "primary",
                        "provider": "openai",
                        "api_type": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4",
                        "api_key_env": "OPENAI_API_KEY",
                        "context_window": 128000,
                    }
                ],
                "compiler_endpoints": [],
                "stt_endpoints": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n", encoding="utf-8")

    import openakita.llm.config as llm_config

    monkeypatch.setattr(llm_config, "get_default_config_path", lambda: config_path)

    summary = bug_report._collect_endpoint_summary()

    assert summary["counts"] == {"endpoints": 1, "compiler_endpoints": 0, "stt_endpoints": 0}
    endpoint = summary["endpoints"][0]
    assert endpoint["base_url_host"] == "api.openai.com"
    assert endpoint["key_present"] is True
    assert "sk-real-secret" not in json.dumps(summary)
