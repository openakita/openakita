"""Integration test for the one-click bundle export endpoint.

Generates a balance sheet + income statement for one (org, period) and
verifies ``GET /orgs/{id}/report-bundle/export`` returns a single
multi-sheet workbook holding a cover/audit summary plus one worksheet per
statement.
"""

from __future__ import annotations

import io
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

BASE = "/api/plugins/finance-auto"


@pytest.fixture
async def api(tmp_path: Path):
    router, service, db = build_router_and_service(tmp_path / "bundle_api.sqlite")
    await db.init()
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    client = TestClient(app)
    try:
        yield client, service
    finally:
        await db.close()


def _row(idx, code, name, *, cd=0.0, cc=0.0, pd=0.0, pc=0.0):
    return ParsedRow(
        row_index=idx, raw_code=code, parent_code=code, child_code=None,
        full_code=code, account_name=name,
        opening_debit=0, opening_credit=0,
        period_debit=pd, period_credit=pc,
        closing_debit=cd, closing_credit=cc, aux_text=None,
    )


@pytest.mark.asyncio
async def test_bundle_export_packs_all_statements(api):
    client, service = api

    r = client.post(
        f"{BASE}/orgs",
        json={"name": "打包测试公司", "code": "BUNDLE1",
              "industry": "general", "standard": "small"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org_id, period_id="2025-FY",
        source_file="seed.xlsx", file_size=0, file_sha256=None,
    )
    rows = [
        _row(1, "1001", "库存现金", cd=10000),
        _row(2, "1002", "银行存款", cd=200000),
        _row(3, "1601", "固定资产", cd=1000000),
        _row(4, "2202", "应付账款", cc=300000),
        _row(5, "4001", "实收资本", cc=910000),
        # income-statement drivers
        _row(6, "6001", "主营业务收入", pc=500000),
        _row(7, "6401", "主营业务成本", pd=300000),
    ]
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id="2025-FY", rows=rows
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=len(rows),
        status="ok", error_message=None,
    )

    for kind in ("balance_sheet", "income_statement"):
        gen = client.post(
            f"{BASE}/orgs/{org_id}/reports/{kind}/generate",
            json={"period_id": "2025-FY"},
        )
        assert gen.status_code == 201, gen.text

    # bundle export
    res = client.get(
        f"{BASE}/orgs/{org_id}/report-bundle/export?period_id=2025-FY"
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    titles = wb.sheetnames
    assert "汇总" in titles
    assert "资产负债表" in titles
    assert "利润表" in titles
    # cover/audit summary lists every packed statement.
    cover = wb["汇总"]
    assert "财务报表汇总" in (cover["A1"].value or "")
    wb.close()


@pytest.mark.asyncio
async def test_bundle_export_404_when_no_reports(api):
    client, service = api
    r = client.post(
        f"{BASE}/orgs",
        json={"name": "空公司", "code": "BUNDLE0",
              "industry": "general", "standard": "small"},
    )
    org_id = r.json()["id"]
    res = client.get(
        f"{BASE}/orgs/{org_id}/report-bundle/export?period_id=2025-FY"
    )
    assert res.status_code == 404, res.text
