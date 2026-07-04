"""Mint-runtime regression tests for the smoke-5-sse fix.

Covers RT13 + RT34 (consolidated HIGH from ``tmp_p10/_step2_report.md``):
the mint runtime never wired a per-org event store onto
:class:`OrgRuntime`, and the only SSE handler lived on the legacy
``orgs-spec`` prefix backed by a different store -- so both
``GET /api/v2/orgs/{id}/events`` and the SSE channel 404'd for any
org created via ``POST /api/v2/orgs/from-template``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2_runtime, orgs_v2_stream
from openakita.orgs.runtime import OrgRuntime


def test_org_runtime_lazy_mints_event_store_for_known_org(tmp_path) -> None:
    """``get_event_store`` must lazy-mint when the lookup knows the org.

    Pre-fix the method returned ``None`` for every mint-created org
    because ``_event_stores`` was empty.  Post-fix the runtime asks
    the lookup (``OrgManager``) for the org; a hit triggers eager
    registration of an :class:`OrgEventStore`.  Unknown ids still
    return ``None`` so the route's 404 path is preserved.
    """
    org_id = "org_lazy_test"
    lookup = MagicMock()
    lookup.get_org.return_value = {"id": org_id, "name": "x"}
    lookup.get_org_dir.return_value = str(tmp_path / org_id)
    rt = OrgRuntime(
        lookup=lookup,
        persistence=MagicMock(),
        lifecycle_emitter=MagicMock(),
    )

    es = rt.get_event_store(org_id)
    assert es is not None
    assert hasattr(es, "query") and hasattr(es, "get_audit_log")
    assert rt.get_event_store(org_id) is es  # idempotent

    lookup.get_org.return_value = None
    assert rt.get_event_store("org_missing") is None  # preserves /events 404

    es.append({"event_type": "node_started", "actor": "tester"})
    es.append({"event_type": "node_started", "actor": "other"})
    es.append({"event_type": "task_done", "actor": "tester"})
    assert len(es.query(event_type="node_started", limit=10)) == 2
    assert {e["event_type"] for e in es.query(actor="tester", limit=10)} == {
        "node_started",
        "task_done",
    }
    assert len(es.query(limit=1)) == 1


def _build_stream_app() -> FastAPI:
    app = FastAPI()
    app.state.org_manager = MagicMock(name="OrgManager")
    app.state.org_runtime = MagicMock(name="OrgRuntime")
    app.state.org_command_service = MagicMock(name="OrgCommandService")
    app.state.org_blackboard = MagicMock(name="OrgBlackboard")
    app.state.project_store = MagicMock(name="ProjectStore")
    app.state.node_scheduler = MagicMock(name="NodeScheduler")
    app.include_router(orgs_v2_runtime.router)
    return app


def test_b85_stream_404_when_org_missing() -> None:
    """``GET /api/v2/orgs/{id}/stream`` must 404 when manager has no such org."""
    app = _build_stream_app()
    app.state.org_manager.get.return_value = None
    with TestClient(app) as client:
        resp = client.get("/api/v2/orgs/org_missing/stream")
    assert resp.status_code == 404
    assert "Organization not found" in resp.text


def test_b85_stream_200_event_stream_for_mint_org(monkeypatch) -> None:
    """When the manager resolves the org, response is a ``text/event-stream``.

    ``_event_stream`` is monkey-patched to a finite no-op generator so
    the TestClient does not block on the real ``stream_registry`` loop
    (parity-faithful wire shape is covered by the orgs-spec route's
    own contract tests).
    """

    async def _fake_stream(request, org_id: str) -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        yield f'event: lifecycle\ndata: {{"org_id":"{org_id}","type":"sse_connected"}}\n\n'

    monkeypatch.setattr(orgs_v2_stream, "_event_stream", _fake_stream)

    app = _build_stream_app()
    app.state.org_manager.get.return_value = MagicMock(spec=["id"])
    with TestClient(app) as client:
        resp = client.get("/api/v2/orgs/org_mint/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert resp.headers.get("cache-control", "").startswith("no-cache")
    assert "sse_connected" in resp.text
