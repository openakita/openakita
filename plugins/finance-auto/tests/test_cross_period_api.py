"""Integration test for the W3 Stage 3 cross-period validator HTTP layer."""

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


def _make_rows(
    *,
    closing_1001: float = 1000,
    closing_1002: float = 5000,
    closing_2202_cr: float = 300,
) -> list[ParsedRow]:
    return [
        ParsedRow(row_index=1, raw_code="1001", parent_code="1001",
                  child_code=None, full_code="1001", account_name="库存现金",
                  opening_debit=closing_1001, opening_credit=0,
                  period_debit=0, period_credit=0,
                  closing_debit=closing_1001, closing_credit=0),
        ParsedRow(row_index=2, raw_code="1002", parent_code="1002",
                  child_code=None, full_code="1002", account_name="银行存款",
                  opening_debit=closing_1002, opening_credit=0,
                  period_debit=0, period_credit=0,
                  closing_debit=closing_1002, closing_credit=0),
        ParsedRow(row_index=3, raw_code="2202", parent_code="2202",
                  child_code=None, full_code="2202", account_name="应付账款",
                  opening_debit=0, opening_credit=closing_2202_cr,
                  period_debit=0, period_credit=0,
                  closing_debit=0, closing_credit=closing_2202_cr),
    ]


@pytest.fixture
async def api(tmp_path: Path):
    db_path = tmp_path / "xperiod.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/finance-auto")
    client = TestClient(app)
    try:
        yield client, service
    finally:
        await db.close()


async def _seed_period(service, *, org_id: str, period_id: str, rows: list[ParsedRow]) -> str:
    await service.ensure_period(org_id=org_id, period_id=period_id)
    imp = await service.insert_pending_import(
        org_id=org_id, period_id=period_id, source_file=f"{period_id}.xlsx",
        file_size=0, file_sha256=None,
    )
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id=period_id, rows=rows,
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=len(rows),
        status="ok", error_message=None,
    )
    return imp.id


@pytest.mark.asyncio
async def test_trigger_then_get_check_round_trip(api):
    client, service = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "跨期演示", "code": "XPC_DEMO", "industry": "general",
              "standard": "small"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    # Prior period: 2024-FY closing
    await _seed_period(service, org_id=org_id, period_id="2024-FY",
                       rows=_make_rows(closing_1001=1000, closing_1002=5000,
                                       closing_2202_cr=300))
    # Current period: 2025-FY opening with a 2000 error on 1001 and 50
    # warning on 2202.
    await _seed_period(service, org_id=org_id, period_id="2025-FY",
                       rows=_make_rows(closing_1001=3000, closing_1002=5000,
                                       closing_2202_cr=350))

    r = client.post(
        f"{base}/orgs/{org_id}/cross-period-checks",
        json={
            "prior_period_id": "2024-FY",
            "current_period_id": "2025-FY",
            "tolerance": 1.0,
            "warn_threshold": 100.0,
            "emit_parse_issues": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total_accounts"] == 3
    assert body["error_count"] == 1
    assert body["warning_count"] == 1
    assert body["exact_count"] == 1
    assert len(body["parse_issue_ids"]) == 1, body
    check_id = body["id"]

    # GET the persisted check & confirm diff payload roundtrips.
    r = client.get(f"{base}/orgs/{org_id}/cross-period-checks/{check_id}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["total_accounts"] == 3
    codes = {d["full_code"]: d for d in detail["differences"]}
    assert codes["1001"]["severity"] == "error"
    assert codes["2202"]["severity"] == "warning"
    assert codes["1002"]["severity"] == "exact"

    # List endpoint shows the check.
    r = client.get(f"{base}/orgs/{org_id}/cross-period-checks")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["error_count"] == 1

    # The error-graded diff produced a ParseIssue (cross_period_mismatch).
    r = client.get(f"{base}/orgs/{org_id}/parse-issues")
    assert r.status_code == 200, r.text
    issues = r.json()["issues"]
    xperiod = [i for i in issues if i["issue_type"] == "cross_period_mismatch"]
    assert len(xperiod) == 1
    assert xperiod[0]["severity"] == "must_fix"
    assert "1001" in xperiod[0]["original_data"]["full_code"]
