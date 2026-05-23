"""HTTP-level tests for the M2 AI management API.

Stage 6 covers six endpoints under ``/api/plugins/finance-auto/ai/``:

* GET    /ai/scenarios
* PATCH  /ai/scenarios/{scenario_id}
* GET    /ai/consent
* POST   /ai/consent/respond
* DELETE /ai/consent/{consent_id}
* GET    /ai/audit-log

The unit tests in ``test_ai_consent.py`` already exercise the
underlying registry / DB layer; this file pins the FastAPI surface so
a future refactor can't silently change the JSON contract the
React-side `AIConsentDialog` and `AIHistoryView` rely on.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from finance_auto_backend.ai.consent import (
    get_dialog_registry,
    reset_dialog_registry_for_tests,
)
from finance_auto_backend.ai.event_bus import reset_event_bus_for_tests
from finance_auto_backend.routes import build_router_and_service


@pytest.fixture
async def app_client(tmp_path):
    reset_event_bus_for_tests()
    reset_dialog_registry_for_tests()
    db_path = tmp_path / "ai_routes.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/finance-auto")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, service
    await db.close()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_scenarios_returns_six(app_client):
    client, _ = app_client
    resp = await client.get("/api/plugins/finance-auto/ai/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6
    ids = sorted(s["scenario_id"] for s in body["scenarios"])
    assert ids == sorted(
        [
            "account_classify_suggest",
            "audit_risk_warning",
            "cash_flow_aux_classify",
            "cross_period_anomaly",
            "erp_source_detect",
            "trial_balance_diagnose",
        ]
    )
    for s in body["scenarios"]:
        assert s["default_sensitivity"] in {"metadata", "aggregated", "raw"}
        assert s["default_enabled"] is True
        assert s["enabled_override"] is None
        assert s["sensitivity_override"] is None


@pytest.mark.asyncio
async def test_patch_scenario_persists_overrides(app_client):
    client, _ = app_client
    resp = await client.patch(
        "/api/plugins/finance-auto/ai/scenarios/erp_source_detect",
        json={"enabled": False, "sensitivity_override": "aggregated"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled_override"] is False
    assert body["sensitivity_override"] == "aggregated"

    # Round-trip via list.
    resp = await client.get("/api/plugins/finance-auto/ai/scenarios")
    erp = next(
        s for s in resp.json()["scenarios"] if s["scenario_id"] == "erp_source_detect"
    )
    assert erp["enabled_override"] is False
    assert erp["sensitivity_override"] == "aggregated"


@pytest.mark.asyncio
async def test_patch_scenario_unknown_returns_404(app_client):
    client, _ = app_client
    resp = await client.patch(
        "/api/plugins/finance-auto/ai/scenarios/no_such_scenario",
        json={"enabled": True},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_consent_empty_initially(app_client):
    client, _ = app_client
    resp = await client.get("/api/plugins/finance-auto/ai/consent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["active_permanent"] == 0
    assert body["consents"] == []


@pytest.mark.asyncio
async def test_consent_respond_unknown_dialog_returns_404(app_client):
    client, _ = app_client
    resp = await client.post(
        "/api/plugins/finance-auto/ai/consent/respond",
        json={"dialog_id": "nope", "decision": "allow_once"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_consent_respond_resolves_pending_dialog(app_client):
    client, _ = app_client
    registry = get_dialog_registry()
    entry = await registry.open(
        scenario_id="erp_source_detect", level="metadata", user_id="local"
    )

    resp = await client.post(
        "/api/plugins/finance-auto/ai/consent/respond",
        json={"dialog_id": entry.dialog_id, "decision": "allow_permanent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow_permanent"

    payload = await entry.future
    assert payload["decision"] == "allow_permanent"


@pytest.mark.asyncio
async def test_revoke_consent_marks_revoked(app_client):
    client, service = app_client
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = await service.db.conn.execute(
        "INSERT INTO ai_consent(user_id, scenario_id, sensitivity_level, "
        "decision, granted_at) VALUES (?,?,?,?,?)",
        ("local", "erp_source_detect", "metadata", "allow_permanent", now),
    )
    consent_id = cur.lastrowid
    await service.db.conn.commit()

    resp = await client.delete(
        f"/api/plugins/finance-auto/ai/consent/{consent_id}"
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None

    # Calling the endpoint again is idempotent.
    again = await client.delete(
        f"/api/plugins/finance-auto/ai/consent/{consent_id}"
    )
    assert again.status_code == 200


@pytest.mark.asyncio
async def test_revoke_only_allows_permanent_decisions(app_client):
    client, service = app_client
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = await service.db.conn.execute(
        "INSERT INTO ai_consent(user_id, scenario_id, sensitivity_level, "
        "decision, granted_at) VALUES (?,?,?,?,?)",
        ("local", "erp_source_detect", "metadata", "allow_once", now),
    )
    consent_id = cur.lastrowid
    await service.db.conn.commit()

    resp = await client.delete(
        f"/api/plugins/finance-auto/ai/consent/{consent_id}"
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_pagination_and_summary(app_client):
    client, service = app_client
    base = datetime.now(timezone.utc)
    for i, outcome in enumerate(["success", "success", "denied", "error"]):
        ts = base.strftime("%Y-%m-%dT%H:%M:") + f"{i:02d}Z"
        await service.db.conn.execute(
            "INSERT INTO llm_call_audit(timestamp, user_id, org_id, "
            "scenario_id, sensitivity_level, model_provider, model_name, "
            "is_local_endpoint, payload_hash, payload_size_bytes, "
            "outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts, "local", "org_demo", "erp_source_detect", "metadata",
                "ollama", "qwen2.5", 1, "h" + str(i), 100, outcome,
            ),
        )
    await service.db.conn.commit()

    resp = await client.get(
        "/api/plugins/finance-auto/ai/audit-log",
        params={"org_id": "org_demo", "limit": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["summary"] == {"success": 2, "denied": 1, "error": 1}

    # Filter by outcome.
    resp = await client.get(
        "/api/plugins/finance-auto/ai/audit-log",
        params={"outcome": "denied"},
    )
    assert resp.json()["total"] == 1
