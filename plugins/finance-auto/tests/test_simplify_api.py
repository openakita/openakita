"""Integration test for W3 Stage 2 — per-cell simplify PATCH + details GET."""

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
    db_path = tmp_path / "simplify_api.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/finance-auto")
    client = TestClient(app)
    try:
        yield client, service
    finally:
        await db.close()


def _make_ar_rows() -> list[ParsedRow]:
    """Build 25 应收账款 sub-account rows (1122.客户N) with descending amounts."""
    rows: list[ParsedRow] = []
    # Some non-AR background rows
    rows.append(ParsedRow(
        row_index=1, raw_code="1001", parent_code="1001", child_code=None,
        full_code="1001", account_name="库存现金",
        opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
        closing_debit=1000, closing_credit=0,
    ))
    rows.append(ParsedRow(
        row_index=2, raw_code="4001", parent_code="4001", child_code=None,
        full_code="4001", account_name="实收资本",
        opening_debit=0, opening_credit=0, period_debit=0, period_credit=0,
        closing_debit=0, closing_credit=500000,
    ))
    # 25 AR sub-account rows
    for i in range(25):
        amt = 10000 - i * 200
        rows.append(ParsedRow(
            row_index=3 + i,
            raw_code=f"1122.{i:03d}",
            parent_code="1122",
            child_code=f"{i:03d}",
            full_code=f"1122.{i:03d}",
            account_name="应收账款",
            aux_text=f"客户{i:03d}有限公司",
            opening_debit=0, opening_credit=0,
            period_debit=0, period_credit=0,
            closing_debit=float(amt), closing_credit=0,
        ))
    return rows


@pytest.mark.asyncio
async def test_simplify_toggle_and_details_round_trip(api):
    client, service = api
    base = "/api/plugins/finance-auto"

    r = client.post(
        f"{base}/orgs",
        json={"name": "演示账套", "code": "SIMP_TEST", "industry": "general",
              "standard": "small"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org_id, period_id="2025-FY", source_file="seed.xlsx",
        file_size=0, file_sha256=None,
    )
    parsed = _make_ar_rows()
    await service.persist_rows(
        import_id=imp.id, org_id=org_id, period_id="2025-FY", rows=parsed,
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=len(parsed),
        status="ok", error_message=None,
    )

    r = client.post(
        f"{base}/orgs/{org_id}/reports/balance_sheet/generate",
        json={"period_id": "2025-FY"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    report_id = body["report"]["id"]
    cells = {c["reference_code"]: c for c in body["cells"]}
    bs_1122 = cells["BS_1122"]
    assert bs_1122["simplified"] is False  # YAML default is disabled
    # Sum of 25 rows: 10000+9800+...+5200 = 25 * (10000 + 5200) / 2 = 190000
    assert bs_1122["value"] == pytest.approx(190000.0)
    assert len(bs_1122["source_rows"]) == 25

    cell_id = bs_1122["id"]

    # Flip simplify on with top_n=10
    r = client.patch(
        f"{base}/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/simplify",
        json={
            "enabled": True, "strategy": "top_n", "top_n": 10,
            "sort_by": "amount_desc", "merge_label": "其他客户",
            "keep_negative_separate": True,
            "footnote_template": "其他 {count} 家客户合计 {amount} 元",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simplified"] is True
    assert body["simplified_top_n"] == 10
    assert len(body["merged_row_ids"]) == 15
    assert body["footnote"].startswith("其他 15 家")

    # Verify the underlying total didn't change
    r = client.get(f"{base}/orgs/{org_id}/reports/{report_id}")
    assert r.status_code == 200
    cells2 = {c["reference_code"]: c for c in r.json()["cells"]}
    assert cells2["BS_1122"]["value"] == pytest.approx(190000.0)
    assert cells2["BS_1122"]["simplified"] is True

    # Details endpoint: 10 visible + 1 merged "其他" row vs 25 full
    r = client.get(
        f"{base}/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/details"
    )
    assert r.status_code == 200
    detail = r.json()
    assert detail["simplified"] is True
    assert len(detail["full_rows"]) == 25
    assert len(detail["visible_rows"]) == 11
    merged_row = next(r for r in detail["visible_rows"] if r["is_merged"])
    assert merged_row["merged_count"] == 15
    assert merged_row["name"] == "其他客户"
    # Sum of visible = total
    assert sum(r["amount"] for r in detail["visible_rows"]) == pytest.approx(190000.0)

    # Disable simplify — falls back to full detail
    r = client.patch(
        f"{base}/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/simplify",
        json={"enabled": False, "strategy": "top_n", "top_n": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["simplified"] is False

    r = client.get(
        f"{base}/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/details"
    )
    assert r.status_code == 200
    detail2 = r.json()
    assert detail2["simplified"] is False
    assert len(detail2["visible_rows"]) == 25
