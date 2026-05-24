"""Integration tests for the W3 Stage 4 manual_inputs HTTP layer +
cash-flow report generation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from finance_auto_backend.parsers.xls_parser import ParsedRow  # noqa: E402
from finance_auto_backend.routes import build_router_and_service  # noqa: E402


@pytest.fixture
async def api(tmp_path: Path):
    db_path = tmp_path / "manual.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/finance-auto")
    client = TestClient(app)
    try:
        yield client, service
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_initially_empty_then_put_fills_slots(api):
    client, _ = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "现金流账套", "code": "CF_DEMO", "industry": "general",
              "standard": "small"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    r = client.get(f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_count"] == 7  # 7 preset keys
    assert body["filled_count"] == 0
    keys = {s["key"] for s in body["slots"]}
    assert {
        "vat_output", "vat_input", "bill_discount_received",
        "interest_paid", "interest_income", "bank_fee_paid",
        "social_security_paid",
    } == keys

    # PUT a value — fresh slot ⇒ expected_version=0 is required.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "1250.50", "decided_by": "tester", "expected_version": 0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "1250.50"
    assert r.json()["version"] == 1

    # Update — version bumps; client must echo back the live version.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "1500.00", "decided_by": "tester", "expected_version": 1},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2

    # Listing reflects the filled slot.
    r = client.get(f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs")
    body = r.json()
    assert body["filled_count"] == 1
    filled = next(s for s in body["slots"] if s["key"] == "interest_paid")
    assert filled["filled"] is True
    assert filled["record"]["value"] == "1500.00"

    # Reject unknown key (400 takes precedence over the missing-version 409).
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/totally_made_up",
        json={"value": "1", "expected_version": 0},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_cash_flow_report_consumes_manual_inputs(api):
    client, service = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "CF 演示", "code": "CF_CASH", "industry": "general",
              "standard": "small"},
    )
    org_id = r.json()["id"]

    # Seed an import — required by report-routes' load_balance_lines.
    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org_id, period_id="2025-FY", source_file="seed.xlsx",
        file_size=0, file_sha256=None,
    )
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id="2025-FY",
        rows=[ParsedRow(
            row_index=1, raw_code="1001", parent_code="1001", child_code=None,
            full_code="1001", account_name="库存现金",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=1, closing_credit=0,
        )],
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=1,
        status="ok", error_message=None,
    )

    # Fill all 7 manual inputs.
    fields = {
        "vat_output": 100000,
        "vat_input": 60000,
        "bill_discount_received": 5000,
        "interest_paid": 2000,
        "interest_income": 800,
        "bank_fee_paid": 1500,
        "social_security_paid": 20000,
    }
    for k, v in fields.items():
        r = client.put(
            f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/{k}",
            json={"value": str(v), "expected_version": 0},
        )
        assert r.status_code == 200, r.text

    # Generate the cash-flow report.
    r = client.post(
        f"{base}/orgs/{org_id}/reports/cash_flow/generate",
        json={"period_id": "2025-FY"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    cells = {c["reference_code"]: c for c in body["cells"]}
    # Spot-check that manual_input cells are populated with the right sign.
    assert cells["CF_SALES_CASH"]["value"] == pytest.approx(100000.0)
    assert cells["CF_PURCHASE_CASH"]["value"] == pytest.approx(-60000.0)
    assert cells["CF_INTEREST_PAID"]["value"] == pytest.approx(-2000.0)
    # Each manual_input cell carries a source row referencing the field.
    assert "manual_input:vat_output" in cells["CF_SALES_CASH"]["source_rows"]
    # Total = 100000 - 60000 - 20000 - 2000 + 800 + 5000 - 1500 = 22300
    assert cells["CF_OPERATING_NET"]["value"] == pytest.approx(22300.0)


@pytest.mark.asyncio
async def test_put_with_expected_version_rejects_stale_write(api):
    """Optimistic-lock path (P2-2 audit fix): a stale ``expected_version``
    must surface as 409 instead of silently overwriting."""
    client, _ = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "并发测试", "code": "MI_LOCK", "industry": "general",
              "standard": "small"},
    )
    org_id = r.json()["id"]

    # Initial PUT — fresh slot ⇒ expected_version=0; server picks v1.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "1000.00", "decided_by": "tester", "expected_version": 0},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1

    # Simulate two concurrent clients that both observe v1.
    # Client A wins — version goes to 2.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "2000.00", "decided_by": "A", "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2

    # Client B still believes v1 → conflict.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "3000.00", "decided_by": "B", "expected_version": 1},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["expected_version"] == 1
    assert detail["current_version"] == 2

    # Stored value still reflects A's write.
    r = client.get(f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs")
    body = r.json()
    slot = next(s for s in body["slots"] if s["key"] == "interest_paid")
    assert slot["record"]["value"] == "2000.00"
    assert slot["record"]["version"] == 2


@pytest.mark.asyncio
async def test_put_without_expected_version_returns_409_missing_token(api):
    """Round-2 optimisation #1: ``expected_version`` is now mandatory.
    Any PUT that omits it must return HTTP 409 with the structured
    ``missing_expected_version`` error so legacy clients fail loudly
    instead of silently overwriting newer data."""
    client, _ = api
    base = "/api/plugins/finance-auto"
    r = client.post(
        f"{base}/orgs",
        json={"name": "强制版本测试", "code": "MI_STRICT", "industry": "general",
              "standard": "small"},
    )
    org_id = r.json()["id"]

    # Empty slot, no version supplied → 409 with missing_expected_version.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "100", "decided_by": "legacy_client"},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "missing_expected_version"
    assert detail["field_key"] == "interest_paid"
    assert "expected_version" in detail["detail"]

    # Seed a row legitimately so we can re-verify the strict path on an
    # existing slot too.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "100", "expected_version": 0},
    )
    assert r.status_code == 200

    # Existing slot, no version supplied → still 409.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "200"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "missing_expected_version"


@pytest.mark.asyncio
async def test_put_with_expected_version_on_empty_slot(api):
    """expected_version=0 on a brand-new slot succeeds; expected_version=5
    on the same brand-new slot fails."""
    client, _ = api
    base = "/api/plugins/finance-auto"
    r = client.post(
        f"{base}/orgs",
        json={"name": "新建插槽测试", "code": "MI_NEW", "industry": "general",
              "standard": "small"},
    )
    org_id = r.json()["id"]

    # expected_version=0 → treated as "I confirm slot is empty" — succeeds.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/interest_paid",
        json={"value": "100", "expected_version": 0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1

    # Fresh slot for another field — expected_version=7 (unrealistic) →
    # immediate 409 without an INSERT side-effect.
    r = client.put(
        f"{base}/orgs/{org_id}/periods/2025-FY/manual-inputs/vat_output",
        json={"value": "999", "expected_version": 7},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["current_version"] == 0


@pytest.mark.asyncio
async def test_cash_flow_warns_on_missing_manual_inputs(api):
    client, service = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "缺填演示", "code": "CF_MISS", "industry": "general",
              "standard": "small"},
    )
    org_id = r.json()["id"]
    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org_id, period_id="2025-FY", source_file="seed.xlsx",
        file_size=0, file_sha256=None,
    )
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id="2025-FY",
        rows=[ParsedRow(
            row_index=1, raw_code="1001", parent_code="1001", child_code=None,
            full_code="1001", account_name="库存现金",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=1, closing_credit=0,
        )],
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=1,
        status="ok", error_message=None,
    )

    r = client.post(
        f"{base}/orgs/{org_id}/reports/cash_flow/generate",
        json={"period_id": "2025-FY"},
    )
    # Generation succeeds (missing fields warn, not block) — that's the
    # whole point of v0.2 Part 1 §1.4.5 "缺失项报警但不阻塞生成".
    assert r.status_code == 201, r.text
    body = r.json()
    # All 7 manual_input cells should render as 0 and the warnings list
    # should mention each missing key.
    warning_text = " ".join(body["report"]["warnings"])
    assert "vat_output" in warning_text
    assert "interest_paid" in warning_text
    cells = {c["reference_code"]: c for c in body["cells"]}
    assert cells["CF_OPERATING_NET"]["value"] == pytest.approx(0.0)
