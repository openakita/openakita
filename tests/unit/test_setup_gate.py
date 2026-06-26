"""Tests for the Setup gate middleware and its supporting state helpers.

Covers:
- ``is_setup_complete`` / ``should_require_setup`` truth table.
- Middleware path allowlist.
- 428 envelope shape.
- TRUST_PROXY-aware loopback bypass.
- WS path unaffected (returns 404 from the handler chain, not 428 from gate).

Why ``httpx.AsyncClient`` + ``ASGITransport``: that combo defaults the client
host to ``127.0.0.1``, matching the existing auth-flow tests. Starlette's
sync ``TestClient`` reports the client host as ``"testclient"`` which would
defeat the loopback bypass we want to assert.
"""

from __future__ import annotations

import os
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI

from openakita.api.auth import WebAccessConfig
from openakita.api.middleware_setup_gate import (
    SETUP_GATE_ALLOW_PATHS,
    SETUP_GATE_ALLOW_PREFIXES,
    create_setup_gate_middleware,
)
from openakita.api.setup_state import is_setup_complete, should_require_setup

# ``asyncio_mode = auto`` in pyproject.toml means async test functions are
# automatically picked up — no explicit ``pytest.mark.asyncio`` needed.


# ---------------------------------------------------------------------------
# State helpers (pure)
# ---------------------------------------------------------------------------


def test_is_setup_complete_false_on_fresh(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    assert is_setup_complete(cfg) is False


def test_is_setup_complete_true_after_setting_password(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    cfg.change_password("hunter22")
    assert is_setup_complete(cfg) is True


def test_should_require_setup_false_when_already_set(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    cfg.change_password("hunter22")
    fake_request = mock.MagicMock()
    assert should_require_setup(fake_request, cfg) is False


def test_should_require_setup_false_for_loopback_even_without_password(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    fake_request = mock.MagicMock()
    fake_request.client.host = "127.0.0.1"
    fake_request.headers = {}
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TRUST_PROXY", None)
        assert should_require_setup(fake_request, cfg) is False


def test_should_require_setup_true_for_lan_caller_without_password(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    fake_request = mock.MagicMock()
    fake_request.client.host = "192.168.1.50"
    fake_request.headers = {}
    assert should_require_setup(fake_request, cfg) is True


# ---------------------------------------------------------------------------
# Middleware (HTTP integration via httpx.AsyncClient + ASGITransport)
# ---------------------------------------------------------------------------


def _build_app_without_password(tmp_path):
    cfg = WebAccessConfig(tmp_path)
    assert not cfg.has_password_set
    app = FastAPI()
    app.middleware("http")(create_setup_gate_middleware(cfg))

    @app.get("/api/secret")
    def secret():
        return {"ok": True}

    @app.get("/api/auth/setup-status")
    def setup_status():
        return {"setup_required": True}

    @app.get("/api/auth/setup")
    def setup_get():
        return {"hint": "POST to set password"}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/web/index.html")
    def web_index():
        return {"page": "spa"}

    return app, cfg


def _make_async_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_setup_gate_returns_428_for_lan_caller(tmp_path, monkeypatch):
    app, _cfg = _build_app_without_password(tmp_path)
    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _make_async_client(app) as c:
        resp = await c.get("/api/secret", headers={"X-Forwarded-For": "10.0.0.5"})
    assert resp.status_code == 428
    body = resp.json()
    assert body["error"] == "setup_required"
    assert body["setup_url"].endswith("/setup")


async def test_setup_gate_allows_loopback_through(tmp_path):
    app, _cfg = _build_app_without_password(tmp_path)
    async with _make_async_client(app) as c:
        resp = await c.get("/api/secret")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_setup_gate_passes_setup_endpoints_for_any_caller(tmp_path, monkeypatch):
    app, _cfg = _build_app_without_password(tmp_path)
    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _make_async_client(app) as c:
        for path in ("/api/auth/setup", "/api/auth/setup-status", "/api/health"):
            resp = await c.get(path, headers={"X-Forwarded-For": "10.0.0.5"})
            assert resp.status_code == 200, f"{path} should bypass setup gate"


async def test_setup_gate_passes_spa_shell(tmp_path, monkeypatch):
    app, _cfg = _build_app_without_password(tmp_path)
    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _make_async_client(app) as c:
        resp = await c.get("/web/index.html", headers={"X-Forwarded-For": "10.0.0.5"})
    assert resp.status_code == 200


async def test_setup_gate_skips_when_password_already_set(tmp_path, monkeypatch):
    cfg = WebAccessConfig(tmp_path)
    cfg.change_password("hunter22")
    app = FastAPI()
    app.middleware("http")(create_setup_gate_middleware(cfg))

    @app.get("/api/secret")
    def secret():
        return {"ok": True}

    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _make_async_client(app) as c:
        resp = await c.get("/api/secret", headers={"X-Forwarded-For": "10.0.0.5"})
    assert resp.status_code == 200


async def test_setup_gate_passes_non_api_paths_through_silently(tmp_path, monkeypatch):
    """Non-API navigation paths fall through so the SPA can render and handle setup."""
    cfg = WebAccessConfig(tmp_path)
    app = FastAPI()
    app.middleware("http")(create_setup_gate_middleware(cfg))

    @app.get("/random-spa-route")
    def spa():
        return {"ok": True}

    monkeypatch.setenv("TRUST_PROXY", "true")
    async with _make_async_client(app) as c:
        resp = await c.get("/random-spa-route", headers={"X-Forwarded-For": "10.0.0.5"})
    assert resp.status_code == 200, "SPA routes are not API; gate must not 428"


# ---------------------------------------------------------------------------
# Allowlist sanity checks (sync, no app needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/setup",
        "/api/auth/setup-status",
        "/api/auth/change-password",
        "/api/health",
        "/api/healthz",
        "/api/readyz",
    ],
)
def test_critical_endpoints_in_allowlist(path):
    assert path in SETUP_GATE_ALLOW_PATHS


def test_ws_prefix_intentionally_not_in_allowlist():
    assert "/ws/" not in SETUP_GATE_ALLOW_PREFIXES, (
        "WS gating is handled by the WS token check, not the HTTP setup gate"
    )
