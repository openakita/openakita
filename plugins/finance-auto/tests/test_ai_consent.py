"""M2 AI Stage 3 — consent checker + WebSocket dialog channel tests.

Exercises:

* permanent grants short-circuit the dialog
* dialog flow grants `allow_once` (60s TTL) and `allow_permanent`
* explicit deny → ConsentDenied + audit row stored
* dialog timeout → ConsentDenied + deny row written
* scenario disabled override → ConsentDenied
* scenario sensitivity override is honoured
* WS broadcaster is invoked on consent.requested
* `auto_decision` test-mode bypass works
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from finance_auto_backend.ai import consent as consent_mod
from finance_auto_backend.ai.consent import (
    ConsentDenied,
    check_consent,
    reset_dialog_registry_for_tests,
)
from finance_auto_backend.ai.event_bus import (
    reset_event_bus_for_tests,
)
from finance_auto_backend.routes import build_router_and_service


@pytest.fixture
async def ai_service(tmp_path):
    db_path = tmp_path / "consent.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    # Seed an organization so foreign-key paths in routes can reference it.
    from finance_auto_backend.models import OrganizationCreate
    await service.create_org(
        OrganizationCreate(name="测试公司", code="TEST_AI")
    )
    yield service
    await db.close()


@pytest.fixture(autouse=True)
def fresh_bus_and_dialogs():
    reset_event_bus_for_tests()
    reset_dialog_registry_for_tests()
    yield


# ---------------------------------------------------------------------------
# auto_decision short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_allow_once_persists(ai_service):
    result = await check_consent(
        ai_service,
        scenario_id="erp_source_detect",
        level="metadata",
        payload={"sample": 1},
        auto_decision="allow_once",
    )
    assert result.allowed is True
    assert result.consent_id is not None
    assert result.decision == "allow_once"

    async with ai_service.db.conn.execute(
        "SELECT decision, revoked_at FROM ai_consent WHERE consent_id=?",
        (result.consent_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["decision"] == "allow_once"
    # allow_once rows ship with revoked_at = granted_at + 60s.
    assert row["revoked_at"] is not None


@pytest.mark.asyncio
async def test_auto_allow_permanent_short_circuits_next_time(ai_service):
    first = await check_consent(
        ai_service,
        scenario_id="erp_source_detect",
        level="metadata",
        payload={"sample": 1},
        auto_decision="allow_permanent",
    )
    assert first.decision == "allow_permanent"

    second = await check_consent(
        ai_service,
        scenario_id="erp_source_detect",
        level="metadata",
        payload={"sample": 2},
    )
    # Should reuse the prior grant — same consent_id.
    assert second.allowed is True
    assert second.consent_id == first.consent_id
    assert second.reason == "prior_grant"


@pytest.mark.asyncio
async def test_auto_deny_raises_consent_denied(ai_service):
    with pytest.raises(ConsentDenied):
        await check_consent(
            ai_service,
            scenario_id="erp_source_detect",
            level="metadata",
            payload={"sample": 1},
            auto_decision="deny",
        )
    async with ai_service.db.conn.execute(
        "SELECT decision FROM ai_consent ORDER BY consent_id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["decision"] == "deny"


# ---------------------------------------------------------------------------
# Scenario overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_disabled_blocks(ai_service):
    await ai_service.db.conn.execute(
        "UPDATE ai_scenarios SET enabled_override=0 WHERE scenario_id=?",
        ("erp_source_detect",),
    )
    await ai_service.db.conn.commit()
    with pytest.raises(ConsentDenied):
        await check_consent(
            ai_service,
            scenario_id="erp_source_detect",
            level="metadata",
            payload={"sample": 1},
            auto_decision="allow_permanent",
        )


@pytest.mark.asyncio
async def test_sensitivity_override_promotes(ai_service):
    await ai_service.db.conn.execute(
        "UPDATE ai_scenarios SET sensitivity_override=? WHERE scenario_id=?",
        ("aggregated", "erp_source_detect"),
    )
    await ai_service.db.conn.commit()
    result = await check_consent(
        ai_service,
        scenario_id="erp_source_detect",
        level="metadata",
        payload={"sample": 1},
        auto_decision="allow_permanent",
    )
    async with ai_service.db.conn.execute(
        "SELECT sensitivity_level FROM ai_consent WHERE consent_id=?",
        (result.consent_id,),
    ) as cur:
        row = await cur.fetchone()
    # The override must have been honoured.
    assert row["sensitivity_level"] == "aggregated"


# ---------------------------------------------------------------------------
# Dialog flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dialog_flow_resolves_to_allow(ai_service):
    """End-to-end: emit → registry resolve → allowed result."""
    bus_events: list[dict] = []

    async def _capture(payload):
        bus_events.append(payload)

    from finance_auto_backend.ai.event_bus import get_event_bus
    bus = get_event_bus()
    bus.subscribe("finance.ai.consent.requested", _capture)

    async def _resolver():
        # Wait until the request has been emitted, then resolve.
        for _ in range(40):
            if bus_events:
                break
            await asyncio.sleep(0.01)
        assert bus_events, "bus did not emit consent request"
        dialog_id = bus_events[0]["dialog_id"]
        registry = consent_mod.get_dialog_registry()
        ok = await registry.resolve(dialog_id, {"decision": "allow_once"})
        assert ok is True

    resolver = asyncio.create_task(_resolver())

    result = await check_consent(
        ai_service,
        scenario_id="erp_source_detect",
        level="metadata",
        payload={"sample": "hello"},
        timeout=2.0,
    )
    await resolver
    assert result.allowed is True
    assert result.decision == "allow_once"


@pytest.mark.asyncio
async def test_dialog_timeout_raises(ai_service):
    with pytest.raises(ConsentDenied):
        await check_consent(
            ai_service,
            scenario_id="erp_source_detect",
            level="metadata",
            payload={"sample": "x"},
            timeout=0.1,
        )
    async with ai_service.db.conn.execute(
        "SELECT decision FROM ai_consent ORDER BY consent_id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["decision"] == "deny"


@pytest.mark.asyncio
async def test_ws_broadcaster_invoked_on_request(ai_service):
    """When a WS broadcaster is plugged in, requests propagate."""
    received: list[dict] = []

    async def fake_broadcast(payload):
        received.append(payload)

    from finance_auto_backend.ai.event_bus import get_event_bus
    get_event_bus().set_ws_broadcaster(fake_broadcast)

    async def _resolver():
        for _ in range(40):
            requests = [
                r for r in received
                if r.get("event") == "ai_consent_request"
            ]
            if requests:
                dialog_id = requests[0]["dialog_id"]
                registry = consent_mod.get_dialog_registry()
                if await registry.resolve(dialog_id, {"decision": "deny"}):
                    return
            await asyncio.sleep(0.01)
        raise AssertionError("no broadcast captured")

    resolver = asyncio.create_task(_resolver())

    with pytest.raises(ConsentDenied):
        await check_consent(
            ai_service,
            scenario_id="erp_source_detect",
            level="metadata",
            payload={"sample": "x"},
            timeout=2.0,
        )
    await resolver
    # The WS-facing payload carries the user-visible event name; the bus
    # routes it to the React side as `ai_consent_request` (per v0.2 §4.6).
    assert any(r.get("event") == "ai_consent_request" for r in received)
