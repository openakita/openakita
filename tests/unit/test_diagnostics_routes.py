"""End-to-end-ish tests for the new diagnostics routes.

These run against a minimal FastAPI app so we don't bring up the full
Agent/SessionManager stack; they just verify that the new routes are
wired and behave defensively when state is empty.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import health


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health.router)
    return app


def test_last_link_diagnostic_returns_empty_when_unset():
    client = TestClient(_build_app())
    resp = client.get("/api/diagnostics/last-link")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_last_link_diagnostic_returns_state_value_when_present():
    app = _build_app()
    app.state.last_link_diagnostic = {
        "requested_url": "https://example.com/a",
        "final_url": "https://www.example.com/a",
        "status": "ok",
    }
    resp = TestClient(app).get("/api/diagnostics/last-link")
    assert resp.status_code == 200
    body = resp.json()
    assert body["requested_url"] == "https://example.com/a"
    assert body["final_url"] == "https://www.example.com/a"


def test_clear_session_caches_endpoint_returns_cleared_map():
    client = TestClient(_build_app())
    resp = client.post("/api/diagnostics/clear-session-caches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "cleared" in body
    assert body["cleared"].get("web_fetch") is True
