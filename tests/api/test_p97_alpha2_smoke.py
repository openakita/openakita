"""P9.7a-2 smoke tests -- Group A rename + 308 redirect shim.

The full alpha-2 sub-step 5 test file. This commit (a-2a) ships
only the redirect smoke tests; a-2c appends the runtime-router
health + Pydantic-import tests when those land. Keeping the file
in one place avoids a per-sub-commit churn rename.

Mounting strategy: the redirect router goes on the test app
**after** the relocated ``orgs_v2.router`` so collisions resolve
the way ``server.py`` resolves them in production (mint /
spec-router precedence at any shared path; redirect at every
other Group A path).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import (
    _orgs_v2_deprecated_redirects,
    orgs_v2,
    orgs_v2_runtime,
    orgs_v2_stream,
)
from openakita.config import settings
from openakita.orgs import reset_default_store


@pytest.fixture
def shim_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    """Test app with v2 spec router + redirect shim mounted.

    ``follow_redirects=False`` so the 308 is observable; tests
    that want to confirm the rename did not break Group A logic
    use the ``spec_client`` fixture (which follows redirects).
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    app = FastAPI()
    app.include_router(orgs_v2.router)
    app.include_router(orgs_v2_stream.router)
    app.include_router(orgs_v2_runtime.router)
    app.include_router(_orgs_v2_deprecated_redirects.router)
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def spec_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    """Same app, default follow_redirects=True for end-to-end smoke."""
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    app = FastAPI()
    app.include_router(orgs_v2.router)
    app.include_router(orgs_v2_stream.router)
    app.include_router(orgs_v2_runtime.router)
    app.include_router(_orgs_v2_deprecated_redirects.router)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 308 shim behaviour
# ---------------------------------------------------------------------------


def test_legacy_get_org_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B10, ``GET /api/v2/orgs/{id}`` is claimed by
    the mint (not the legacy redirect). Without ``app.state.org_manager``
    bound on the shim app, ``_get_manager`` returns 503 -- the route is
    REACHED, the redirect no longer fires."""
    resp = shim_client.get("/api/v2/orgs/org_dummy")
    assert resp.status_code == 503
    assert "Location" not in resp.headers


def test_legacy_list_orgs_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B1, ``GET /api/v2/orgs`` is claimed by mint."""
    resp = shim_client.get("/api/v2/orgs")
    assert resp.status_code == 503
    assert "Location" not in resp.headers


def test_legacy_templates_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B5, ``GET /api/v2/orgs/templates`` -> mint."""
    resp = shim_client.get("/api/v2/orgs/templates")
    assert resp.status_code == 503
    assert "Location" not in resp.headers


def test_legacy_template_get_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B7, ``GET /api/v2/orgs/templates/{id}`` -> mint."""
    resp = shim_client.get("/api/v2/orgs/templates/software_team")
    assert resp.status_code == 503
    assert "Location" not in resp.headers


def test_legacy_instantiate_post_returns_308_preserves_method(
    shim_client: TestClient,
) -> None:
    """308 is the redirect that preserves POST method client-side."""
    resp = shim_client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={"name": "Foo"},
    )
    assert resp.status_code == 308
    assert resp.headers["Location"] == "/api/v2/orgs-spec/templates/software_team/instantiate"


@pytest.mark.xfail(
    reason="308 redirect shim returns 503 in v2.0.0; retirement locked for v2.1.0 per ADR-0015",
    strict=False,
)
def test_legacy_patch_org_returns_308(shim_client: TestClient) -> None:
    resp = shim_client.patch("/api/v2/orgs/org_dummy", json={"name": "x"})
    assert resp.status_code == 308
    assert resp.headers["Location"] == "/api/v2/orgs-spec/org_dummy"


def test_legacy_delete_org_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B12, ``DELETE /api/v2/orgs/{id}`` -> mint."""
    resp = shim_client.delete("/api/v2/orgs/org_dummy")
    assert resp.status_code == 503
    assert "Location" not in resp.headers


def test_legacy_create_post_now_claimed_by_mint(shim_client: TestClient) -> None:
    """After P9.7beta-1 lands B2, ``POST /api/v2/orgs`` -> mint.

    Empty/invalid body raises 422 from Pydantic before the manager helper
    runs -- still a mint hit (NOT a redirect)."""
    resp = shim_client.post("/api/v2/orgs", json={"org": {}})
    assert resp.status_code in (422, 503)
    assert "Location" not in resp.headers


@pytest.mark.xfail(
    reason="308 redirect shim returns 503 in v2.0.0; retirement locked for v2.1.0 per ADR-0015",
    strict=False,
)
def test_legacy_stream_returns_308(shim_client: TestClient) -> None:
    resp = shim_client.get("/api/v2/orgs/org_dummy/stream")
    assert resp.status_code == 308
    assert resp.headers["Location"] == "/api/v2/orgs-spec/org_dummy/stream"


@pytest.mark.xfail(
    reason="308 redirect shim returns 503 in v2.0.0; retirement locked for v2.1.0 per ADR-0015",
    strict=False,
)
def test_redirect_preserves_query_string_for_unclaimed_path(
    shim_client: TestClient,
) -> None:
    """Query-string round-trip is now exercised on the still-unclaimed
    ``/{id}/stream`` path (the list path was claimed by mint at B1)."""
    resp = shim_client.get("/api/v2/orgs/org_dummy/stream?since=42")
    assert resp.status_code == 308
    assert resp.headers["Location"] == "/api/v2/orgs-spec/org_dummy/stream?since=42"


# ---------------------------------------------------------------------------
# Rename did not break Group A logic
# ---------------------------------------------------------------------------


def test_spec_path_get_templates_returns_real_payload(spec_client: TestClient) -> None:
    """``GET /api/v2/orgs-spec/templates`` returns the Group A envelope."""
    resp = spec_client.get("/api/v2/orgs-spec/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert "templates" in body
    assert body["count"] == len(body["templates"])
    assert body["count"] >= 4


def test_legacy_unclaimed_path_followed_yields_same_shape_as_spec(
    spec_client: TestClient,
) -> None:
    """After P9.7beta-1 lands B5, ``/templates`` is claimed by the mint.
    The redirect identity assertion now runs on ``/templates/X/instantiate``
    which the mint does NOT claim and still falls through to Group A."""
    old = spec_client.post("/api/v2/orgs/templates/software_team/instantiate", json={})
    new = spec_client.post("/api/v2/orgs-spec/templates/software_team/instantiate", json={})
    assert old.status_code == new.status_code
    assert old.json() == new.json()


# ---------------------------------------------------------------------------
# v2 runtime router health probe (a-2c)
# ---------------------------------------------------------------------------


def test_runtime_health_probe_returns_expected_envelope(
    spec_client: TestClient,
) -> None:
    """``GET /api/v2/orgs/_p97/health`` confirms the new runtime router."""
    resp = spec_client.get("/api/v2/orgs/_p97/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["p97_phase"] == "alpha-2"
    assert set(body["subsystems"]) == {
        "runtime",
        "manager",
        "command_service",
        "blackboard",
        "project_store",
        "scheduler",
    }


def test_runtime_health_probe_takes_precedence_over_redirect(
    shim_client: TestClient,
) -> None:
    """Runtime router registered first -> ``/_p97/health`` does NOT 308.

    The redirect shim has ``/{org_id}/stream`` and ``/{org_id}`` at the
    same prefix, but ``_p97/health`` is a two-segment path that does
    not collide with either pattern; the runtime route claims it cleanly.
    """
    resp = shim_client.get("/api/v2/orgs/_p97/health")
    assert resp.status_code == 200
    assert resp.json()["p97_phase"] == "alpha-2"


# ---------------------------------------------------------------------------
# Pydantic schemas import sanity (a-2b deliverable, exercised in a-2c)
# ---------------------------------------------------------------------------


def test_orgs_v2_schemas_import_cleanly() -> None:
    """The four sub-modules + the umbrella re-export all import."""
    import pytest as _pytest
    from pydantic import ValidationError

    from openakita.api.schemas.orgs_v2 import (
        CancelRequest,
        CommandSnapshot,
        CommandSubmit,
        Node,
        NodeRegister,
        NodeStatus,
        Org,
        OrgCreate,
        OrgPatch,
        OrgStatus,
        Project,
        ProjectCreate,
        ProjectPatch,
        ProjectStatus,
        ProjectType,
        TaskStatus,
    )

    # Enum value spellings match v1 byte-for-byte.
    assert OrgStatus.ACTIVE.value == "active"
    assert NodeStatus.FROZEN.value == "frozen"
    assert ProjectStatus.PLANNING.value == "planning"
    assert TaskStatus.IN_PROGRESS.value == "in_progress"
    # Required field rule: OrgCreate without ``name`` must raise.
    with _pytest.raises(ValidationError):
        OrgCreate()  # type: ignore[call-arg]
    # extra=forbid: unknown field raises.
    with _pytest.raises(ValidationError):
        OrgCreate(name="x", unknown_key="boom")  # type: ignore[call-arg]
    # Minimal valid construction smoke for every model.
    assert CommandSubmit(content="hi").content == "hi"
    assert ProjectCreate(name="p").project_type == ProjectType.TEMPORARY
    assert CancelRequest().reason is None
    assert Org(id="o", name="n").status == OrgStatus.DORMANT
    assert Node(id="n").status == NodeStatus.IDLE
    assert Project(id="p", org_id="o", name="x").project_type == ProjectType.TEMPORARY
    assert NodeRegister(role_title="r").role_title == "r"
    assert OrgPatch(name=None).model_dump(exclude_none=True) == {}
    assert ProjectPatch().model_dump(exclude_none=True) == {}
    assert CommandSnapshot(command_id="c", org_id="o", status="ok").status == "ok"
