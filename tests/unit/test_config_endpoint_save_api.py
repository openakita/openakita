import json
from types import SimpleNamespace

import pytest

from openakita.api.routes import bug_report
from openakita.api.routes import config as config_routes
from openakita.llm.runtime_config import apply_llm_runtime_config


class _FakeEndpointManager:
    def __init__(self) -> None:
        self.saved_api_key = "unset"
        self.enabled = False

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

    def toggle_endpoint(self, name: str, endpoint_type: str = "endpoints") -> dict:
        self.enabled = not self.enabled
        return {"name": name, "endpoint_type": endpoint_type, "enabled": self.enabled}


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


@pytest.mark.asyncio
async def test_toggle_endpoint_returns_runtime_reload_result(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {
            "status": "ok",
            "reloaded": True,
            "main_reloaded": True,
            "pool_invalidated": True,
        },
    )

    response = await config_routes.toggle_endpoint(
        config_routes.ToggleEndpointRequest(name="primary"),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert response["endpoint"]["enabled"] is True
    assert response["reload"]["main_reloaded"] is True
    assert response["reload"]["pool_invalidated"] is True


def test_apply_llm_runtime_config_refreshes_all_runtime_components(tmp_path, monkeypatch):
    config_path = tmp_path / "data" / "llm_endpoints.json"
    config_path.parent.mkdir()
    config_path.write_text('{"endpoints": [], "stt_endpoints": []}', encoding="utf-8")

    class FakeClient:
        def __init__(self) -> None:
            self._config_path = None
            self.endpoints = [object()]
            self.reload_called = False

        def reload(self) -> bool:
            self.reload_called = True
            return True

    class FakeBrain:
        def __init__(self) -> None:
            self._llm_client = FakeClient()
            self.compiler_reloaded = False

        def reload_compiler_client(self) -> bool:
            self.compiler_reloaded = True
            return True

    class FakeSttClient:
        def __init__(self) -> None:
            self.reloaded_with = None

        def reload(self, endpoints) -> None:
            self.reloaded_with = endpoints

    class FakePool:
        def __init__(self) -> None:
            self.reason = None

        def notify_runtime_config_changed(self, reason: str) -> None:
            self.reason = reason

    brain = FakeBrain()
    gateway = SimpleNamespace(stt_client=FakeSttClient())
    pool = FakePool()

    monkeypatch.setattr(
        "openakita.llm.config.load_endpoints_config",
        lambda path=None: ([], [], ["stt"], {}),
    )

    result = apply_llm_runtime_config(
        agent=SimpleNamespace(brain=brain),
        gateway=gateway,
        pool=pool,
        config_path=config_path,
        reason="llm_config:test",
    )

    assert result["status"] == "ok"
    assert result["main_reloaded"] is True
    assert result["compiler_reloaded"] is True
    assert result["stt_reloaded"] is True
    assert result["pool_invalidated"] is True
    assert brain._llm_client.reload_called is True
    assert brain._llm_client._config_path == config_path
    assert brain.compiler_reloaded is True
    assert gateway.stt_client.reloaded_with == ["stt"]
    assert pool.reason == "llm_config:test"


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
