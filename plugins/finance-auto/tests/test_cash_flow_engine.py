"""Tests for M2 Biz Stage 4 — indirect cash flow engine."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.routes import build_router_and_service
from finance_auto_backend.services.cash_flow import IndirectCashFlowEngine

BASE = "/api/plugins/finance-auto"


def _row(idx: int, full: str, name: str, debit: float, credit: float) -> dict:
    return {
        "id": f"r{idx}", "import_id": "i", "org_id": "o", "period_id": "p",
        "row_index": idx, "raw_code": full, "parent_code": full.split(".")[0],
        "child_code": full.split(".")[1] if "." in full else None,
        "full_code": full, "account_name": name, "aux_text": None,
        "opening_debit": 0.0, "opening_credit": 0.0,
        "period_debit": 0.0, "period_credit": 0.0,
        "closing_debit": debit, "closing_credit": credit,
    }


def test_pure_compute_minimal() -> None:
    """No prior balance + simple PL = operating_net ≈ net_profit."""
    cur = [_row(1, "1122.01", "应收A", 0, 100000)]  # 应收负数 (credit) → 重分类候选, treated as AR=-100k
    out = IndirectCashFlowEngine.compute(
        current_rows=cur,
        prior_rows=[],
        pl_cells={"PL_NET_PROFIT": Decimal("500000"),
                  "PL_OPERATING_PROFIT": Decimal("550000"),
                  "PL_FINANCE_COST": Decimal("20000")},
        manual_inputs={"cf_depreciation": Decimal("30000"),
                       "cf_amortization": Decimal("5000")},
    )
    assert out["cf_net_profit"] == Decimal("500000")
    assert out["cf_depreciation"] == Decimal("30000")
    # AR delta = prior - current = 0 - (-100000) = 100000 (cash inflow).
    assert out["cf_ar_delta"] == Decimal("100000")
    # operating_net ≈ 500k + 30k + 5k + 20k + 100k(AR) = 655k
    assert out["cf_operating_net"] >= Decimal("650000")


def test_pure_compute_full_picture() -> None:
    """Realistic 2-period scenario covering operating + investing + financing."""
    # Current period:
    #   AR closing 200k debit
    #   AP closing 150k credit
    #   Inventory 100k debit
    #   Fixed assets 1,000k debit (purchased 200k more vs prior)
    #   ST borrowing 300k credit (-> +100k from prior)
    cur = [
        _row(1, "1122.01", "应收", 200000, 0),
        _row(2, "2202.01", "应付",      0, 150000),
        _row(3, "1401.01", "存货", 100000, 0),
        _row(4, "1601.01", "固定", 1000000, 0),
        _row(5, "2001.01", "短借",      0, 300000),
        _row(6, "1001.01", "现金", 500000, 0),
    ]
    # Prior period:
    prv = [
        _row(1, "1122.01", "应收", 250000, 0),  # AR 减少 50k
        _row(2, "2202.01", "应付",      0, 120000),  # AP 增加 30k
        _row(3, "1401.01", "存货",  80000, 0),  # Inventory 增加 20k
        _row(4, "1601.01", "固定",  800000, 0),  # +200k capex
        _row(5, "2001.01", "短借",      0, 200000),  # +100k borrowing
        _row(6, "1001.01", "现金", 400000, 0),  # cash +100k
    ]
    out = IndirectCashFlowEngine.compute(
        current_rows=cur, prior_rows=prv,
        pl_cells={"PL_NET_PROFIT": Decimal("180000")},
        manual_inputs={
            "cf_depreciation": Decimal("50000"),
            "cf_dividends_paid": Decimal("30000"),
            "cf_interest_paid": Decimal("10000"),
        },
    )
    assert out["cf_ar_delta"] == Decimal("50000")          # decrease
    assert out["cf_ap_delta"] == Decimal("30000")          # increase
    assert out["cf_inventory_delta"] == Decimal("-20000")  # increase
    assert out["cf_fixed_assets_delta"] == Decimal("200000")  # capex
    assert out["cf_st_borrowing_delta"] == Decimal("100000")  # +100k
    assert out["cf_cash_delta_bs"] == Decimal("100000")
    # Investing net = -fixed - intangible - lt_invest + other_in - other_out
    #               = -200000
    assert out["cf_investing_net"] == Decimal("-200000")
    # Financing net = +100k(st) + 0(lt) + 0(paid) - 10k(interest) - 30k(div)
    #               = +60k
    assert out["cf_financing_net"] == Decimal("60000")
    # At least 8 cf keys non-zero (acceptance requirement: 5+).
    non_zero = [k for k, v in out.items() if v != Decimal("0")]
    assert len(non_zero) >= 8, f"only {len(non_zero)} non-zero keys"


def test_depreciation_derived_from_accumulated_balances() -> None:
    """折旧/摊销从累计折旧(1602)/累计摊销(1702)期间增额推导，不再恒为 0。"""
    # Accumulated depreciation / amortization are contra-asset accounts that
    # sit on the credit side; their period increase = the charge to P&L.
    cur = [
        _row(1, "1602.01", "累计折旧", 0, 300000),
        _row(2, "1702.01", "累计摊销", 0, 50000),
    ]
    prv = [
        _row(1, "1602.01", "累计折旧", 0, 220000),  # +80k depreciation
        _row(2, "1702.01", "累计摊销", 0, 30000),   # +20k amortization
    ]
    out = IndirectCashFlowEngine.compute(
        current_rows=cur,
        prior_rows=prv,
        pl_cells={"PL_NET_PROFIT": Decimal("100000")},
        manual_inputs={},  # no manual override → must derive from the BS
    )
    assert out["cf_depreciation"] == Decimal("80000")
    assert out["cf_amortization"] == Decimal("20000")
    assert out["cf_dep_amort"] == Decimal("100000")
    # The add-backs flow into operating_net: 100k profit + 100k dep/amort.
    assert out["cf_operating_net"] == Decimal("200000")


def test_manual_depreciation_overrides_balance_derivation() -> None:
    """手工录入的折旧值优先于 BS 推导（间接法允许人工覆盖）。"""
    cur = [_row(1, "1602.01", "累计折旧", 0, 300000)]
    prv = [_row(1, "1602.01", "累计折旧", 0, 220000)]  # BS would yield 80k
    out = IndirectCashFlowEngine.compute(
        current_rows=cur,
        prior_rows=prv,
        pl_cells={},
        manual_inputs={"cf_depreciation": Decimal("95000")},
    )
    assert out["cf_depreciation"] == Decimal("95000")
    # Amortization has neither manual value nor a 1702 balance → 0.
    assert out["cf_amortization"] == Decimal("0")
    assert out["cf_dep_amort"] == Decimal("95000")


def test_depreciation_zero_without_prior_period() -> None:
    """单期无对比期时不能从累计余额臆断期间折旧，回退为 0（除非手工录入）。"""
    cur = [_row(1, "1602.01", "累计折旧", 0, 300000)]
    out = IndirectCashFlowEngine.compute(
        current_rows=cur,
        prior_rows=[],
        pl_cells={},
        manual_inputs={},
    )
    assert out["cf_depreciation"] == Decimal("0")
    assert out["cf_dep_amort"] == Decimal("0")


@pytest.fixture()
def app_db(tmp_path: Path):
    db_path = tmp_path / "cf.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    yield app, service
    asyncio.run(db.close())


def _seed_two_periods(client, service) -> tuple[str, str, str]:
    r = client.post(f"{BASE}/orgs", json={
        "name": "CF Org", "code": "CF_ORG", "standard": "cas",
    })
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    period_current = "2025-FY"
    period_prior = "2024-FY"

    async def _go() -> None:
        for pid in (period_current, period_prior):
            await service.db.conn.execute(
                "INSERT OR IGNORE INTO accounting_periods(id, org_id, period_id, "
                "period_kind, start_date, end_date, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"per_{org_id[-4:]}_{pid}", org_id, pid, "year",
                 f"{pid[:4]}-01-01", f"{pid[:4]}-12-31", "2026-05-23T18:00:00Z"),
            )
            imp_id = f"imp_{org_id[-4:]}_{pid}"
            await service.db.conn.execute(
                "INSERT INTO trial_balance_imports(id, org_id, period_id, "
                "source_file, file_size, parser_used, row_count, status, "
                "error_message, uploaded_at, parsed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (imp_id, org_id, pid, "tb.xlsx", 1024, "stub", 4, "ok", None,
                 "2026-05-23T18:00:00Z", "2026-05-23T18:00:00Z"),
            )
            # Different balances per period.
            mult = 1.0 if pid == period_current else 0.8
            for idx, (full, name, dr, cr) in enumerate([
                ("1122.01", "应收", 200000 * mult, 0),
                ("2202.01", "应付", 0, 150000 * mult),
                ("1601.01", "固定", 1000000 * mult, 0),
                ("2001.01", "短借", 0, 300000 * mult),
            ], start=1):
                await service.db.conn.execute(
                    "INSERT INTO trial_balance_rows(id, import_id, org_id, "
                    "period_id, row_index, raw_code, parent_code, child_code, "
                    "full_code, account_name, opening_debit, opening_credit, "
                    "period_debit, period_credit, closing_debit, closing_credit) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"row_{imp_id}_{idx}", imp_id, org_id, pid, idx,
                        full, full.split(".")[0], full.split(".")[1],
                        full, name, 0.0, 0.0, 0.0, 0.0, float(dr), float(cr),
                    ),
                )
        # Seed a PL report (income_statement) with PL_NET_PROFIT.
        rep_id = f"rep_pl_{org_id[-4:]}"
        await service.db.conn.execute(
            "INSERT INTO reports(id, org_id, period_id, sheet_kind, "
            "accounting_standard, template_id, template_version, status, "
            "cell_count, warnings_json, source_import_id, backend_used, "
            "output_path, generated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rep_id, org_id, period_current, "income_statement",
             "general_enterprise", "pl_ge_v1", 1, "ok", 0, "[]",
             None, "inline", None, "2026-05-23T18:00:00Z"),
        )
        await service.db.conn.execute(
            "INSERT INTO report_cells(id, report_id, reference_code, "
            "target_line_no, target_label, indent_level, data_source, "
            "value, sign, is_total, is_tbd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"cel_PL_NET_PROFIT_{rep_id[-6:]}", rep_id, "PL_NET_PROFIT", 99,
             "净利润", 0, "formula", 600000.0, 1, 1, 0),
        )
        await service.db.conn.commit()
    asyncio.run(_go())
    return org_id, period_current, period_prior


def test_endpoint_compute_returns_synthetic_keys(app_db) -> None:
    app, service = app_db
    client = TestClient(app)
    org_id, period_c, period_p = _seed_two_periods(client, service)

    r = client.post(f"{BASE}/orgs/{org_id}/cash-flow/compute", json={
        "period_id": period_c, "prior_period_id": period_p,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org_id"] == org_id
    assert body["period_id"] == period_c
    assert body["has_pl_anchor"] is True
    assert body["has_balance"] is True
    assert body["has_prior_balance"] is True
    keys = body["keys"]
    assert "cf_net_profit" in keys
    assert "cf_ar_delta" in keys
    assert "cf_operating_net" in keys
    assert Decimal(body["values"]["cf_net_profit"]) == Decimal("600000")
    # AR delta: prior 160k - current 200k = -40k (increase = cash out).
    assert Decimal(body["values"]["cf_ar_delta"]) == Decimal("-40000")
    # ≥ 5 non-zero values per acceptance requirement.
    non_zero = [v for v in body["values"].values()
                if Decimal(v) != Decimal("0")]
    assert len(non_zero) >= 5, body["values"]


def test_endpoint_persist_writes_manual_inputs(app_db) -> None:
    app, service = app_db
    client = TestClient(app)
    org_id, period_c, period_p = _seed_two_periods(client, service)

    r = client.post(f"{BASE}/orgs/{org_id}/cash-flow/persist", json={
        "period_id": period_c, "prior_period_id": period_p,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["persisted"] >= 30
    # Verify the values landed in manual_inputs.
    async def _check() -> int:
        async with service.db.conn.execute(
            "SELECT COUNT(*) FROM manual_inputs WHERE org_id=? AND period_id=? "
            "AND field_key LIKE 'cf_%'",
            (org_id, period_c),
        ) as cur:
            r = await cur.fetchone()
        return r[0] if r else 0
    n = asyncio.run(_check())
    assert n >= 30
    # Re-run persist → should upsert (version++); no duplicates.
    r2 = client.post(f"{BASE}/orgs/{org_id}/cash-flow/persist", json={
        "period_id": period_c, "prior_period_id": period_p,
    })
    assert r2.status_code == 201
    n2 = asyncio.run(_check())
    assert n2 == n


def test_endpoint_list_keys(app_db) -> None:
    app, service = app_db
    client = TestClient(app)
    org_id, _, _ = _seed_two_periods(client, service)
    r = client.get(f"{BASE}/orgs/{org_id}/cash-flow/keys")
    assert r.status_code == 200
    body = r.json()
    assert "synthetic_keys" in body
    assert "cf_operating_net" in body["synthetic_keys"]
    assert "buckets" in body
    assert "ar" in body["buckets"]


def test_endpoint_no_prior_balance(app_db) -> None:
    """When prior_period_id omitted, BS deltas = current side only."""
    app, service = app_db
    client = TestClient(app)
    org_id, period_c, _ = _seed_two_periods(client, service)

    r = client.post(f"{BASE}/orgs/{org_id}/cash-flow/compute", json={
        "period_id": period_c,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_prior_balance"] is False
    # AR delta = 0 - 200000 = -200000.
    assert Decimal(body["values"]["cf_ar_delta"]) == Decimal("-200000")
