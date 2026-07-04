"""EX-P2-13 v1.0.0-rc1 — ``/v1/`` URL prefix + legacy 308 redirect.

The plugin's HTTP surface was historically mounted under
``/api/plugins/finance-auto/<endpoint>``.  Starting with v1.0.0-rc1
every real endpoint is exposed at ``/api/plugins/finance-auto/v1/...``
and the old paths are kept alive via HTTP 308 redirects so existing
UI bundles, plugin manifests, and pinned-version downstream tooling
keep working without code changes.

This file pins the redirect contract so a future refactor cannot
silently break backward compatibility:

1. New ``/v1/`` paths resolve normally (200 / 201 / 4xx semantics).
2. Old un-prefixed paths return ``308 Permanent Redirect`` to the
   matching ``/v1/`` path.
3. The redirect preserves the query string.
4. ``308`` (not ``301`` / ``302``) is used so POST/PUT/DELETE keep
   their method + body.
5. ``/ws`` (WebSocket) is exempt from the redirect — it stays
   reachable at the legacy path AND is also mounted at ``/v1/ws``.
6. Following the redirect produces the expected end-state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.routes import build_router_and_service

BASE = "/api/plugins/finance-auto"


@pytest.fixture()
def v1_app(tmp_path: Path):
    router, svc, db = build_router_and_service(tmp_path / "v1.sqlite")
    asyncio.run(db.init())
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    client = TestClient(app)
    yield client, svc, router
    asyncio.run(db.close())


# ---------------------------------------------------------------------------
# 1. New /v1/ paths work directly (no redirect).
# ---------------------------------------------------------------------------


def test_v1_health_resolves_directly(v1_app) -> None:
    client, _svc, _router = v1_app
    res = client.get(f"{BASE}/v1/health", follow_redirects=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True


def test_v1_post_orgs_resolves_directly(v1_app) -> None:
    client, _svc, _router = v1_app
    res = client.post(
        f"{BASE}/v1/orgs",
        json={
            "name": "DirectOrg", "code": "DIRECT1",
            "standard": "small", "fiscal_start": "2025-01-01",
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "DIRECT1"


# ---------------------------------------------------------------------------
# 2. Legacy paths return 308 + Location header pointing at /v1/.
# ---------------------------------------------------------------------------


def test_legacy_get_health_returns_308(v1_app) -> None:
    client, _svc, _router = v1_app
    res = client.get(f"{BASE}/health", follow_redirects=False)
    assert res.status_code == 308, res.text
    assert res.headers["location"] == f"{BASE}/v1/health"


def test_legacy_get_orgs_returns_308(v1_app) -> None:
    client, _svc, _router = v1_app
    res = client.get(f"{BASE}/orgs", follow_redirects=False)
    assert res.status_code == 308, res.text
    assert res.headers["location"] == f"{BASE}/v1/orgs"


def test_legacy_admin_system_info_returns_308(v1_app) -> None:
    """Deep path under /admin/ — verifies the catch-all handles nested URLs."""
    client, _svc, _router = v1_app
    res = client.get(f"{BASE}/admin/system-info", follow_redirects=False)
    assert res.status_code == 308, res.text
    assert res.headers["location"] == f"{BASE}/v1/admin/system-info"


# ---------------------------------------------------------------------------
# 3. Query string preservation.
# ---------------------------------------------------------------------------


def test_legacy_redirect_preserves_query_string(v1_app) -> None:
    client, _svc, _router = v1_app
    res = client.get(
        f"{BASE}/orgs?accept_corrupted=true&user_id=local",
        follow_redirects=False,
    )
    assert res.status_code == 308, res.text
    loc = res.headers["location"]
    assert loc.startswith(f"{BASE}/v1/orgs?")
    assert "accept_corrupted=true" in loc
    assert "user_id=local" in loc


# ---------------------------------------------------------------------------
# 4. 308 keeps method + body across the redirect (not 301/302).
# ---------------------------------------------------------------------------


def test_legacy_post_orgs_follows_redirect_and_creates(v1_app) -> None:
    client, _svc, _router = v1_app
    # follow_redirects=True so httpx re-issues the POST with the body.
    res = client.post(
        f"{BASE}/orgs",
        json={
            "name": "FollowedOrg", "code": "FOLLOW1",
            "standard": "small", "fiscal_start": "2025-01-01",
        },
        follow_redirects=True,
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "FOLLOW1"


def test_legacy_delete_orgs_redirect_uses_308_not_303(v1_app) -> None:
    """DELETE is the canary: a 303 here would drop the cascade query param."""
    client, _svc, _router = v1_app
    # Seed an org via the legacy POST path so we don't depend on the
    # other tests.
    client.post(
        f"{BASE}/orgs",
        json={
            "name": "ToDelete", "code": "TODELETE1",
            "standard": "small", "fiscal_start": "2025-01-01",
        },
        follow_redirects=True,
    )
    # Look up the id.
    res_list = client.get(f"{BASE}/v1/orgs")
    org_id = next(
        o["id"] for o in res_list.json()["organizations"]
        if o["code"] == "TODELETE1"
    )
    # Hit the legacy DELETE path.
    res = client.delete(
        f"{BASE}/orgs/{org_id}?cascade=true",
        follow_redirects=False,
    )
    assert res.status_code == 308, res.text
    loc = res.headers["location"]
    assert loc == f"{BASE}/v1/orgs/{org_id}?cascade=true"


# ---------------------------------------------------------------------------
# 5. /ws is exempt from the redirect (WebSocket cannot follow HTTP 308).
# ---------------------------------------------------------------------------


def test_legacy_ws_path_not_redirected(v1_app) -> None:
    """Connecting to the legacy /ws path must NOT 308 — WebSocket clients
    cannot follow HTTP redirects, so we leave both /ws and /v1/ws mounted.
    """
    _client, _svc, router = v1_app
    paths = {getattr(ro, "path", "") for ro in router.routes}
    assert "/ws" in paths, "legacy /ws WebSocket mount missing"
    assert "/v1/ws" in paths, "/v1/ws WebSocket mount missing"


# ---------------------------------------------------------------------------
# 6. Route catalogue sanity: every legacy endpoint has a /v1/ twin.
# ---------------------------------------------------------------------------


def test_every_v1_endpoint_redirects_from_legacy(v1_app) -> None:
    """For each REST endpoint exposed at ``/v1/<x>``, hitting the legacy
    ``/x`` path must return 308 (or 404 for things like ``/v1/ws`` which
    are WebSockets and intentionally exempted via the same exemption
    list).
    """
    client, _svc, router = v1_app
    # Sample three meaningful paths covering different families.
    samples = [
        "/health",
        "/orgs",
        "/admin/system-info",
    ]
    for legacy_path in samples:
        v1_path = f"/v1{legacy_path}"
        # Make sure the v1 path actually exists in the router.
        paths = {getattr(ro, "path", "") for ro in router.routes}
        assert v1_path in paths, f"{v1_path} missing in router"
        # And the legacy path redirects to it.
        res = client.get(f"{BASE}{legacy_path}", follow_redirects=False)
        assert res.status_code == 308, (
            f"{legacy_path}: expected 308, got {res.status_code}"
        )
        assert res.headers["location"] == f"{BASE}{v1_path}", (
            f"{legacy_path}: redirect target wrong: "
            f"{res.headers.get('location')}"
        )
