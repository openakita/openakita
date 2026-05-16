"""POST /api/config/sync-endpoint-models — relay catalog discovery API.

Validates the route correctly delegates to EndpointManager, maps
known error modes to non-500 responses, refreshes live providers on
success, and keeps the failure response shape that the UI banner
component expects.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openakita.api.routes import config as config_routes


class _FakeEndpointManager:
    """Minimal stub: only the methods sync-endpoint-models touches."""

    def __init__(self, *, result: dict | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.called_with: dict | None = None

    def sync_endpoint_models(self, name: str, *, endpoint_type: str = "endpoints", timeout: float = 15.0):
        self.called_with = {"name": name, "endpoint_type": endpoint_type, "timeout": timeout}
        if self._exc is not None:
            raise self._exc
        return self._result or {
            "ok": True,
            "name": name,
            "model_count": 0,
            "models": [],
            "synced_at": 1.0,
            "error": None,
        }

    def get_version(self) -> str:
        return "v-test"


def _fake_request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


@pytest.mark.asyncio
async def test_sync_success_returns_models_and_triggers_reload(monkeypatch):
    manager = _FakeEndpointManager(
        result={
            "ok": True,
            "name": "yunwu",
            "model_count": 2,
            "models": ["gpt-4o", "gpt-4o-mini"],
            "synced_at": 1735200000.0,
            "error": None,
        }
    )
    reload_calls: list[dict] = []
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda req: reload_calls.append({"req": req}) or {"status": "ok", "reloaded": True},
    )

    resp = await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="yunwu"),
        _fake_request(),
    )

    assert resp["status"] == "ok"
    assert resp["ok"] is True
    assert resp["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert resp["model_count"] == 2
    assert resp["error"] is None
    assert resp["version"] == "v-test"
    assert resp["reload"] == {"status": "ok", "reloaded": True}
    assert len(reload_calls) == 1  # live providers refreshed
    assert manager.called_with == {
        "name": "yunwu",
        "endpoint_type": "endpoints",
        "timeout": 15.0,
    }


@pytest.mark.asyncio
async def test_sync_probe_error_returns_error_with_previous_catalog(monkeypatch):
    """Probe failures from EndpointManager surface as status='error'
    NOT 500. The UI banner relies on body.error being the human-
    readable Chinese string passed straight through."""
    manager = _FakeEndpointManager(
        result={
            "ok": False,
            "name": "yunwu",
            "model_count": 0,
            "models": [],
            "synced_at": 1735200000.0,
            "error": "API Key 被中转站拒绝（HTTP 401）",
        }
    )
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda req: {"status": "ok"})

    resp = await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="yunwu"),
        _fake_request(),
    )

    assert resp["status"] == "error"
    assert resp["ok"] is False
    assert resp["error"] == "API Key 被中转站拒绝（HTTP 401）"


@pytest.mark.asyncio
async def test_unknown_endpoint_returns_not_found(monkeypatch):
    manager = _FakeEndpointManager(exc=KeyError("endpoint 'ghost' not found"))
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda req: {"status": "ok"})

    resp = await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="ghost"),
        _fake_request(),
    )
    assert resp["status"] == "not_found"
    assert resp["name"] == "ghost"


@pytest.mark.asyncio
async def test_timeout_is_clamped_to_safe_range(monkeypatch):
    """Don't let a UI typo set a 1-hour blocking probe or a 0.1ms one
    that always times out."""
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda req: {"status": "ok"})

    await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="x", timeout=999.0),
        _fake_request(),
    )
    assert manager.called_with["timeout"] == 60.0

    await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="x", timeout=0.01),
        _fake_request(),
    )
    assert manager.called_with["timeout"] == 2.0


@pytest.mark.asyncio
async def test_unexpected_exception_does_not_500(monkeypatch):
    """Anything else still returns a JSON body the UI can render —
    raw 500s would break the toast/banner flow."""
    manager = _FakeEndpointManager(exc=RuntimeError("disk full"))
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda req: {"status": "ok"})

    resp = await config_routes.sync_endpoint_models(
        config_routes.SyncEndpointModelsRequest(name="yunwu"),
        _fake_request(),
    )
    assert resp["status"] == "error"
    assert "disk full" in resp["error"]
