from __future__ import annotations

import httpx
import pytest

from openakita.api.routes import config as config_routes
from openakita.api.server import create_app


class _FakeEndpointManager:
    def __init__(self):
        self.endpoints = [
            {
                "name": "primary",
                "provider": "openai",
                "model": "gpt-test",
                "context_window": 128000,
                "max_tokens": 4000,
                "api_key_env": "OPENAI_API_KEY",
                "enabled": True,
            }
        ]

    def list_endpoints(self, endpoint_type="endpoints"):
        assert endpoint_type == "endpoints"
        return [dict(ep) for ep in self.endpoints]

    def save_endpoint(
        self,
        endpoint,
        api_key=None,
        endpoint_type="endpoints",
        expected_version=None,
        original_name=None,
    ):
        assert api_key is None
        assert endpoint_type == "endpoints"
        name = original_name or endpoint["name"]
        for idx, existing in enumerate(self.endpoints):
            if existing["name"] == name:
                self.endpoints[idx] = {**existing, **endpoint}
                return dict(self.endpoints[idx])
        self.endpoints.append(dict(endpoint))
        return dict(endpoint)

    def get_version(self):
        return "fake-version"


@pytest.fixture
async def client(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda request: {"status": "ok"})

    app = create_app()
    app.state.agent = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


async def test_get_context_length_returns_effective_limit(client):
    resp = await client.get("/api/config/context-length?endpoint=primary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["endpoint"] == "primary"
    assert data["context_length"] == 128000
    assert data["context_window"] == 128000
    assert data["context_limit"] > 0
    assert data["output_reserve"] == 4000


async def test_put_context_length_updates_endpoint_and_reloads(client):
    resp = await client.put(
        "/api/config/context-length",
        json={"endpoint": "primary", "context_length": 256000},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["context_length"] == 256000
    assert data["endpoint"] == "primary"
    assert data["endpoint_config"]["context_window"] == 256000
    assert data["reload"] == {"status": "ok"}
