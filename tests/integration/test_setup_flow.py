"""End-to-end integration tests for the v1.28 Setup flow.

Covers the contract advertised to the frontend:

- ``GET  /api/auth/setup-status``
- ``POST /api/auth/setup``
- ``POST /api/auth/change-password`` (first-run-friendly behaviour)
- Setup gate 428 envelope for non-loopback callers
- Token rotation when password is set / cleared

All tests use ``httpx.AsyncClient`` + ``ASGITransport`` so the client host
defaults to ``127.0.0.1``; ``X-Forwarded-For`` headers simulate a reverse
proxy / LAN device when needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from openakita.api.server import create_app


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Build a fresh app instance with an isolated data dir per-test.

    The WebAccessConfig file is co-located with the data dir resolved from
    ``settings.project_root``, so we point ``project_root`` at ``tmp_path`` for
    each test to start from a clean "no password" state.
    """
    from openakita.config import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.delenv("OPENAKITA_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/auth/setup-status
# ---------------------------------------------------------------------------


class TestSetupStatus:
    async def test_fresh_install_loopback_reports_not_required(self, client):
        # Trusted local: bypass even though no password is set.
        resp = await client.get("/api/auth/setup-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["setup_required"] is False
        assert body["reason"] == "loopback_trusted"

    async def test_fresh_install_lan_caller_reports_required(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/auth/setup-status",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["setup_required"] is True
        assert body["reason"] == "password_not_set"

    async def test_after_setup_reports_not_required(self, client, app, monkeypatch):
        cfg = app.state.web_access_config
        cfg.change_password("hunter22!")
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/auth/setup-status",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["setup_required"] is False
        assert body["reason"] == "already_set"


# ---------------------------------------------------------------------------
# POST /api/auth/setup
# ---------------------------------------------------------------------------


class TestSetupEndpoint:
    async def test_setup_sets_password_and_returns_access_token(self, client, app):
        resp = await client.post(
            "/api/auth/setup",
            json={"new_password": "hunter22!", "confirm_password": "hunter22!"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["setup_complete"] is True
        assert body["access_token"]
        assert body["token_type"] == "bearer"
        assert app.state.web_access_config.has_password_set
        assert app.state.web_access_config.verify_password("hunter22!")

    async def test_setup_sets_httponly_refresh_cookie(self, client):
        resp = await client.post("/api/auth/setup", json={"new_password": "hunter22!"})
        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "openakita_refresh=" in cookie_header
        assert "httponly" in cookie_header.lower(), (
            "refresh token must be httpOnly to match the login endpoint contract"
        )

    async def test_setup_rejects_short_password(self, client):
        resp = await client.post("/api/auth/setup", json={"new_password": "abc"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "password_too_short"

    async def test_setup_rejects_all_digits(self, client):
        resp = await client.post("/api/auth/setup", json={"new_password": "12345678"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "password_all_digits"

    async def test_setup_rejects_all_letters(self, client):
        resp = await client.post("/api/auth/setup", json={"new_password": "abcdefghij"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "password_all_letters"

    async def test_setup_rejects_mismatched_confirm(self, client):
        resp = await client.post(
            "/api/auth/setup",
            json={"new_password": "hunter22!", "confirm_password": "different!"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "password_mismatch"

    async def test_setup_idempotency_returns_409_after_first(self, client, app):
        # First call sets the password.
        first = await client.post("/api/auth/setup", json={"new_password": "hunter22!"})
        assert first.status_code == 200
        # Second call: password is already set → 409 conflict.
        second = await client.post("/api/auth/setup", json={"new_password": "someoneElseTried99!"})
        assert second.status_code == 409
        assert second.json()["detail"] == "already_set"
        # And the original password still wins.
        assert app.state.web_access_config.verify_password("hunter22!")
        assert not app.state.web_access_config.verify_password("someoneElseTried99!")


# ---------------------------------------------------------------------------
# Setup gate 428 envelope (integration with the real middleware chain)
# ---------------------------------------------------------------------------


class TestSetupGate428:
    async def test_lan_caller_to_protected_endpoint_returns_428(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/memories",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 428
        body = resp.json()
        assert body["error"] == "setup_required"
        assert "setup_url" in body

    async def test_lan_caller_to_setup_endpoint_passes(self, client, monkeypatch):
        """Setup endpoints themselves must remain reachable even before setup."""
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/auth/setup-status",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200

    async def test_after_setup_lan_caller_passes_gate_but_hits_auth(self, client, monkeypatch):
        """Once password is set, gate is open; auth middleware then takes over."""
        # Initial setup via loopback.
        setup = await client.post("/api/auth/setup", json={"new_password": "hunter22!"})
        assert setup.status_code == 200

        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/memories",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        # Auth middleware returns 401 (not 428) — gate is satisfied.
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# change-password first-run compatibility
# ---------------------------------------------------------------------------


class TestChangePasswordFirstRun:
    async def test_loopback_can_set_initial_password_via_change_password(self, client, app):
        """Local-loopback users can call change-password without current_password
        even on a fresh install."""
        resp = await client.post("/api/auth/change-password", json={"new_password": "hunter22!"})
        assert resp.status_code == 200, resp.text
        assert app.state.web_access_config.verify_password("hunter22!")

    async def test_remote_cannot_set_initial_password_via_change_password(
        self, client, monkeypatch
    ):
        """Remote callers must go through /setup, not change-password.

        ``/api/auth/change-password`` is not in ``AUTH_EXEMPT_PATHS``, so the
        auth middleware short-circuits a token-less remote request with 401
        *before* the change-password handler's own first-run guard ever runs.
        That's fine: the remote caller still cannot set the initial password,
        which is the property we care about.

        We accept the three responses that all mean "no":
        - 428 (setup gate, if the gate runs before auth)
        - 403 (change-password handler's own guard, if the gate is bypassed)
        - 401 (auth middleware, the current real-world path)
        """
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.post(
            "/api/auth/change-password",
            json={"new_password": "hunter22!"},
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code in (401, 403, 428)


# ---------------------------------------------------------------------------
# clear_password (used by reset-password CLI)
# ---------------------------------------------------------------------------


class TestClearPassword:
    def test_clear_password_drops_hash_and_bumps_token_version(self, tmp_path):
        from openakita.api.auth import WebAccessConfig

        cfg = WebAccessConfig(tmp_path)
        cfg.change_password("hunter22!")
        assert cfg.has_password_set
        old_version = cfg.token_version

        cfg.clear_password()

        assert not cfg.has_password_set
        assert cfg.token_version == old_version + 1
        # Re-instantiating from disk must agree.
        cfg2 = WebAccessConfig(tmp_path)
        assert not cfg2.has_password_set

    def test_clear_password_invalidates_existing_tokens(self, tmp_path):
        from openakita.api.auth import WebAccessConfig

        cfg = WebAccessConfig(tmp_path)
        cfg.change_password("hunter22!")
        token = cfg.create_access_token()
        assert cfg.validate_access_token(token)

        cfg.clear_password()

        assert not cfg.validate_access_token(token), (
            "old access tokens must stop validating once the password is cleared"
        )
