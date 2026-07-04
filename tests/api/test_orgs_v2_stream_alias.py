"""SSE alias + legacy validation through :class:`OrgManager`.

v22 P0 fix (exploratory v10 report §19 "SSE org not found"): the
``/api/v2/orgs-spec/{id}/stream`` legacy route and the Sprint-9
``/api/v2/orgs/{id}/events/stream`` alias both routed org-existence
validation through the legacy :class:`~openakita.orgs.store.JsonOrgStore`
(``data/orgs_v2.json``). After Sprint-9 the mint
``POST /api/v2/orgs/from-template`` writes only to
:class:`~openakita.orgs.manager.OrgManager`
(``data/orgs/<id>/org.json``) and never writes to ``orgs_v2.json``,
so every freshly minted org's SSE stream 404'd. This module pins
the new contract: validation goes through ``request.app.state.org_manager``
so both routes see the same registry the mint POST writes to.

We exercise both surfaces three ways:

* a direct call to ``_build_streaming_response`` with a manager-minted
  org id proves the validator itself trusts the manager surface;
* a direct call to each route handler (``stream_org_progress`` for the
  legacy path; ``stream_org_events`` for the Sprint-9 alias) with the
  same manager-minted id proves the handler is wired to the validator
  and returns ``200 / text/event-stream`` -- we never start the
  long-poll generator, so the 15 s queue ``wait_for`` cannot pin the
  test wall-clock;
* a synchronous ``TestClient`` 404 hit on both URLs proves the URL
  resolver + path-param parsing are real and that the same 404
  envelope flows through both surfaces.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openakita.api.routes import (
    orgs_v2_runtime,
    orgs_v2_stream,
)
from openakita.api.routes.orgs_v2_runtime_dispatch import stream_org_events
from openakita.api.routes.orgs_v2_stream import (
    _build_streaming_response,
    stream_org_progress,
)
from openakita.config import settings
from openakita.orgs import reset_default_store
from openakita.orgs.manager import OrgManager
from openakita.runtime.stream_registry import reset_org_stream_buses

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_stream_registry() -> Iterator[None]:
    """Per-test SSE bus reset so subscriptions cannot leak across cases."""
    reset_org_stream_buses()
    yield
    reset_org_stream_buses()


@pytest.fixture
def manager(tmp_path, monkeypatch) -> OrgManager:
    """Real :class:`OrgManager` rooted at ``tmp_path`` (no shared FS state)."""
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    # Keep the JSON store fixture rooted at tmp_path too so a regression
    # that reaches for the legacy store cannot accidentally observe a
    # sibling test's data in the workspace ``data/`` dir.
    reset_default_store(path=tmp_path / "orgs_v2.json")
    return OrgManager(tmp_path)


@pytest.fixture
def app(manager: OrgManager) -> FastAPI:
    """FastAPI app with both SSE routers + the real manager on app.state."""
    app = FastAPI()
    app.state.org_manager = manager
    # Order matters: include the runtime router (which carries the
    # Sprint-9 alias) and the legacy spec router so both URL patterns
    # resolve in the same TestClient.
    app.include_router(orgs_v2_runtime.router)
    app.include_router(orgs_v2_stream.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Bare-bones :class:`Request` stand-in for the route validator.

    ``_build_streaming_response`` only touches ``request.app.state``
    (to lift :class:`OrgManager`) before constructing
    :class:`StreamingResponse`. ``_event_stream`` would later poke
    ``request.is_disconnected``, but the validator never starts the
    generator -- the body isn't consumed, so the SSE bus stays idle.
    """

    def __init__(self, manager: OrgManager) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(org_manager=manager))


def _mint_org_via_manager(manager: OrgManager, *, name: str = "v22 mint org") -> str:
    """Create an org through :class:`OrgManager.create` and return its id.

    Mirrors the production mint path ``POST /api/v2/orgs/from-template ->
    OrgManager.create_from_template -> OrgManager.create``: persisted
    only under ``<data>/orgs/<id>/org.json``, never to ``orgs_v2.json``.
    """
    org = manager.create({"name": name, "description": "", "nodes": [], "edges": []})
    return org.id


# ---------------------------------------------------------------------------
# Direct function / handler assertions
# ---------------------------------------------------------------------------


def test_build_streaming_response_accepts_manager_minted_org(manager: OrgManager) -> None:
    """``_build_streaming_response`` resolves a manager-minted org id.

    A unit-level guard for the v22 lookup swap: the legacy code path
    reached for ``get_default_store().get`` -- which would NEVER see a
    manager-minted org -- so the equivalent of this assertion would
    have failed loudly. Now it must succeed.
    """
    org_id = _mint_org_via_manager(manager, name="unit-build-mint")
    response = _build_streaming_response(_FakeRequest(manager), org_id)  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"


async def test_sse_alias_finds_org_created_via_manager(manager: OrgManager) -> None:
    """Sprint-9 alias handler ``stream_org_events`` resolves a manager-minted org."""
    org_id = _mint_org_via_manager(manager, name="alias-mint")
    response = await stream_org_events(_FakeRequest(manager), org_id)  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    # Headers carry the SSE keep-alive triplet so the frontend's
    # EventSource (no buffering, no caching) latches the connection.
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


async def test_sse_legacy_finds_org_created_via_manager(manager: OrgManager) -> None:
    """Legacy handler ``stream_org_progress`` also goes through the manager."""
    org_id = _mint_org_via_manager(manager, name="legacy-mint")
    response = await stream_org_progress(_FakeRequest(manager), org_id)  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


async def test_sse_legacy_handler_404_for_unknown_org(manager: OrgManager) -> None:
    """``stream_org_progress`` 404s for an id the manager doesn't know."""
    with pytest.raises(HTTPException) as exc:
        await stream_org_progress(_FakeRequest(manager), "org_never_minted")  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail)


async def test_sse_alias_handler_404_for_unknown_org(manager: OrgManager) -> None:
    """``stream_org_events`` 404s for an id the manager doesn't know."""
    with pytest.raises(HTTPException) as exc:
        await stream_org_events(_FakeRequest(manager), "org_never_minted")  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# Route-level wiring assertions (404 path -- no streaming body involved)
# ---------------------------------------------------------------------------


def test_sse_404_when_org_missing(client: TestClient) -> None:
    """An id never minted should 404 on both URLs with the same detail.

    This also proves the URL resolver + path-param parsing on both
    surfaces actually delegate to ``_build_streaming_response`` (which
    is the only place the new manager-based check lives). The 404
    completes immediately, so the synchronous ``TestClient`` is fine
    here -- no SSE body to flush.
    """
    # Alias surface.
    resp_a = client.get("/api/v2/orgs/org_does_not_exist/events/stream")
    assert resp_a.status_code == 404
    assert "not found" in resp_a.json()["detail"]

    # Legacy surface.
    resp_l = client.get("/api/v2/orgs-spec/org_does_not_exist/stream")
    assert resp_l.status_code == 404
    assert "not found" in resp_l.json()["detail"]
