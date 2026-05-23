"""Integration test for the report-generation API.

Builds a real FastAPI app with the build_router_and_service factory, seeds
an org + a balance import (no file parsing -- we go straight to the
service), then exercises the four W2 endpoints:

* POST /orgs/{id}/reports/balance_sheet/generate
* GET  /orgs/{id}/reports
* GET  /orgs/{id}/reports/{id}
* GET  /orgs/{id}/reports/{id}/export?format=xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
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
    db_path = tmp_path / "report_api.sqlite"
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
async def test_full_report_lifecycle(api):
    client, service = api

    org_payload = {
        "name": "测试公司",
        "code": "DEMO_REP_1",
        "industry": "general",
        "standard": "small",
    }
    r = client.post("/api/plugins/finance-auto/orgs", json=org_payload)
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org_id,
        period_id="2025-FY",
        source_file="seed.xlsx",
        file_size=0,
        file_sha256=None,
    )
    parsed = [
        ParsedRow(
            row_index=1, raw_code="1001", parent_code="1001", child_code=None,
            full_code="1001", account_name="库存现金",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=10000, closing_credit=0, aux_text=None,
        ),
        ParsedRow(
            row_index=2, raw_code="1002", parent_code="1002", child_code=None,
            full_code="1002", account_name="银行存款",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=200000, closing_credit=0, aux_text=None,
        ),
        ParsedRow(
            row_index=3, raw_code="1601", parent_code="1601", child_code=None,
            full_code="1601", account_name="固定资产",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=1000000, closing_credit=0, aux_text=None,
        ),
        ParsedRow(
            row_index=4, raw_code="2202", parent_code="2202", child_code=None,
            full_code="2202", account_name="应付账款",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=0, closing_credit=300000, aux_text=None,
        ),
        ParsedRow(
            row_index=5, raw_code="4001", parent_code="4001", child_code=None,
            full_code="4001", account_name="实收资本",
            opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
            closing_debit=0, closing_credit=910000, aux_text=None,
        ),
    ]
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id="2025-FY", rows=parsed
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=len(parsed),
        status="ok", error_message=None,
    )

    r = client.post(
        f"/api/plugins/finance-auto/orgs/{org_id}/reports/balance_sheet/generate",
        json={"period_id": "2025-FY"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    report_id = body["report"]["id"]
    assert body["report"]["sheet_kind"] == "balance_sheet"
    assert body["report"]["accounting_standard"] == "small_enterprise"
    cells = {c["reference_code"]: c for c in body["cells"]}
    assert cells["BS_1001"]["value"] == pytest.approx(210000.0)
    assert cells["BS_TOTAL_ASSETS"]["value"] > 0

    r = client.get(f"/api/plugins/finance-auto/orgs/{org_id}/reports")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get(
        f"/api/plugins/finance-auto/orgs/{org_id}/reports/{report_id}"
    )
    assert r.status_code == 200
    assert r.json()["report"]["id"] == report_id

    r = client.get(
        f"/api/plugins/finance-auto/orgs/{org_id}/reports/{report_id}/export"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    out_path = (PLUGIN_ROOT / "tests" / "_tmp_export.xlsx").resolve()
    out_path.write_bytes(r.content)
    try:
        wb = openpyxl.load_workbook(str(out_path))
        ws = wb.active
        assert "balance_sheet" in (ws.title or "")
        # Find the BS_1001 row by code in column B
        found = False
        for row in ws.iter_rows(min_row=3, max_col=3):
            if row[1].value == "1001|1002|1012":
                assert row[2].value == pytest.approx(210000.0)
                found = True
                break
        assert found
        wb.close()
    finally:
        out_path.unlink(missing_ok=True)
