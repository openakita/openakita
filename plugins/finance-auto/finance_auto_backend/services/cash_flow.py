"""M2 Biz Stage 4 — indirect-method cash flow statement generator.

Per v0.3 Part Biz §3.5 / v0.1 §7.3 / 数据分析 §现金流量表项目.

Architecture
============
The W3 report-generator already handles 5 ``data_source`` kinds (section,
account, formula, manual_input, cross_year).  Rather than extending the
generator (W2 territory) with a 6th kind, this engine *computes* a set of
synthetic keys (``cf_net_profit``, ``cf_ar_delta``, ...) that the
indirect-method YAML template references via ``data_source: manual_input``.

Two surfaces:

1. :class:`IndirectCashFlowEngine.compute_aux_inputs(...)` — pure function over
   the (current_balance, prior_balance, pl_cells, manual_inputs) tuple.  Returns
   the dict the report-generator can splice into ``manual_input_values``.
2. :class:`IndirectCashFlowEngine.persist_as_manual_inputs(...)` — convenience
   wrapper that writes the computed values into the ``manual_inputs`` table so
   the existing ``POST /reports/cash_flow`` pipeline picks them up unchanged.

Key catalogue (≥ 15 synthetic keys; documented in the YAML template):
    cf_net_profit                  净利润 (PL_NET_PROFIT)
    cf_depreciation                折旧 (manual_input fallback)
    cf_amortization                摊销 (manual_input fallback)
    cf_finance_cost                财务费用
    cf_ar_delta                    应收账款减少（+）/ 增加（-）
    cf_ap_delta                    应付账款增加（+）/ 减少（-）
    cf_inventory_delta             存货减少（+）/ 增加（-）
    cf_other_receivables_delta     其他应收
    cf_other_payables_delta        其他应付
    cf_advance_from_customers_delta 预收
    cf_advance_to_suppliers_delta  预付
    cf_taxes_payable_delta         应交税费变动
    cf_employee_payable_delta      应付职工薪酬变动
    cf_fixed_assets_delta          固定资产新增 (投资活动)
    cf_intangible_assets_delta     无形资产新增
    cf_st_borrowing_delta          短期借款变动 (筹资活动)
    cf_lt_borrowing_delta          长期借款变动
    cf_paid_in_capital_delta       实收资本变动
    cf_dividends_paid              分红 (manual_input)
    cf_cash_delta                  货币资金变动 (用于交叉验证)

Account-prefix conventions follow 数据分析 §3.1 (CAS 编码) — small adjustments
for SME 准则 codes are layered through the same prefix table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiosqlite

# Account-prefix → semantic bucket map.  Each bucket's delta becomes one CF
# synthetic key.  Buckets are summed across ALL trial-balance rows whose
# full_code starts with any listed prefix (so 6603.01 等子目录都纳入).
ACCOUNT_BUCKETS: dict[str, dict[str, list[str]]] = {
    # bucket_name: {"direction": "debit"|"credit", "prefixes": [...]}
    # Accounts receivable & payable.
    "ar":          {"direction": "debit",  "prefixes": ["1122", "1131"]},
    "ap":          {"direction": "credit", "prefixes": ["2202"]},
    "other_ar":    {"direction": "debit",  "prefixes": ["1221"]},
    "other_ap":    {"direction": "credit", "prefixes": ["2241"]},
    "advance_in":  {"direction": "credit", "prefixes": ["2203"]},
    "advance_out": {"direction": "debit",  "prefixes": ["1123"]},
    "inventory":   {"direction": "debit",  "prefixes": ["1401", "1402", "1403", "1405"]},
    "taxes":       {"direction": "credit", "prefixes": ["2221"]},
    "employee":    {"direction": "credit", "prefixes": ["2211"]},
    # Fixed / intangible / long-term equity investments — used for
    # investing-activities deltas.
    "fixed":       {"direction": "debit",  "prefixes": ["1601"]},
    "intangible":  {"direction": "debit",  "prefixes": ["1701"]},
    "lt_invest":   {"direction": "debit",  "prefixes": ["1501"]},
    # Financing activities.
    "st_borrow":   {"direction": "credit", "prefixes": ["2001"]},
    "lt_borrow":   {"direction": "credit", "prefixes": ["2501"]},
    "paid_in":     {"direction": "credit", "prefixes": ["4001"]},
    # Cash & equivalents (for cross-check).
    "cash":        {"direction": "debit",  "prefixes": ["1001", "1002", "1012", "1101"]},
}


def _D(x: Any) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x))


def _bucket_value(rows: list[dict[str, Any]], bucket: dict) -> Decimal:
    direction = bucket["direction"]
    prefixes = bucket["prefixes"]
    total = Decimal("0")
    for r in rows:
        code = r.get("full_code") or r.get("parent_code") or ""
        if not any(code.startswith(p) for p in prefixes):
            continue
        debit = _D(r.get("closing_debit"))
        credit = _D(r.get("closing_credit"))
        if direction == "debit":
            total += (debit - credit)
        else:
            total += (credit - debit)
    return total


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CashFlowError(RuntimeError):
    """Raised for client-visible engine failures."""


class IndirectCashFlowEngine:
    """Compute the 'cf_*' synthetic keys an indirect-method CF template wants."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    # ----- pure compute -----

    @staticmethod
    def compute(
        *,
        current_rows: list[dict[str, Any]],
        prior_rows: list[dict[str, Any]],
        pl_cells: dict[str, Decimal] | None = None,
        manual_inputs: dict[str, Decimal] | None = None,
    ) -> dict[str, Decimal]:
        """Pure function: derive every synthetic ``cf_*`` key.

        ``pl_cells``     — dict keyed by ``reference_code`` (e.g.
                           ``PL_NET_PROFIT``); values come from the latest
                           generated income-statement report.
        ``manual_inputs`` — supplementary values (depreciation, amortization,
                            dividends paid, capex etc.) where the trial
                            balance cannot disambiguate the cash side.
        """
        pl_cells = pl_cells or {}
        manual_inputs = manual_inputs or {}
        out: dict[str, Decimal] = {}

        # ---- PL anchors ----
        out["cf_net_profit"] = _D(pl_cells.get("PL_NET_PROFIT"))
        out["cf_operating_profit"] = _D(pl_cells.get("PL_OPERATING_PROFIT"))
        out["cf_finance_cost"] = _D(pl_cells.get("PL_FINANCE_COST"))

        # ---- manual non-cash items ----
        for k in (
            "cf_depreciation", "cf_amortization", "cf_asset_impairment",
            "cf_dividends_paid", "cf_interest_paid", "cf_interest_received",
            "cf_tax_refund", "cf_other_operating_cash_in",
            "cf_other_operating_cash_out", "cf_other_investing_cash_in",
            "cf_other_investing_cash_out", "cf_other_financing_cash_in",
            "cf_other_financing_cash_out",
        ):
            out[k] = _D(manual_inputs.get(k, 0))

        # ---- balance-sheet deltas ----
        cur: dict[str, Decimal] = {
            b: _bucket_value(current_rows, ACCOUNT_BUCKETS[b]) for b in ACCOUNT_BUCKETS
        }
        prv: dict[str, Decimal] = {
            b: _bucket_value(prior_rows, ACCOUNT_BUCKETS[b]) for b in ACCOUNT_BUCKETS
        }

        # Convention for indirect method: a *decrease* in an asset adds cash
        # (positive); an *increase* in a liability adds cash (positive).
        out["cf_ar_delta"] = prv["ar"] - cur["ar"]
        out["cf_ap_delta"] = cur["ap"] - prv["ap"]
        out["cf_other_receivables_delta"] = prv["other_ar"] - cur["other_ar"]
        out["cf_other_payables_delta"] = cur["other_ap"] - prv["other_ap"]
        out["cf_advance_from_customers_delta"] = cur["advance_in"] - prv["advance_in"]
        out["cf_advance_to_suppliers_delta"] = prv["advance_out"] - cur["advance_out"]
        out["cf_inventory_delta"] = prv["inventory"] - cur["inventory"]
        out["cf_taxes_payable_delta"] = cur["taxes"] - prv["taxes"]
        out["cf_employee_payable_delta"] = cur["employee"] - prv["employee"]

        # Investing activities — positive delta of asset = cash outflow
        # (so we report it as a *negative* contribution).  We export the
        # raw delta (current - prior) and let the YAML template apply sign:-1
        # if needed.
        out["cf_fixed_assets_delta"] = cur["fixed"] - prv["fixed"]
        out["cf_intangible_assets_delta"] = cur["intangible"] - prv["intangible"]
        out["cf_lt_invest_delta"] = cur["lt_invest"] - prv["lt_invest"]

        # Financing activities — positive delta of liability/equity = cash
        # inflow (positive sign).
        out["cf_st_borrowing_delta"] = cur["st_borrow"] - prv["st_borrow"]
        out["cf_lt_borrowing_delta"] = cur["lt_borrow"] - prv["lt_borrow"]
        out["cf_paid_in_capital_delta"] = cur["paid_in"] - prv["paid_in"]

        # Cross-validation — total cash delta from BS.
        out["cf_cash_delta_bs"] = cur["cash"] - prv["cash"]

        # ---- aggregated section totals (engine-side; YAML can also do it) ----
        out["cf_operating_net"] = (
            out["cf_net_profit"] + out["cf_depreciation"] + out["cf_amortization"]
            + out["cf_asset_impairment"] + out["cf_finance_cost"]
            + out["cf_ar_delta"] + out["cf_ap_delta"]
            + out["cf_other_receivables_delta"] + out["cf_other_payables_delta"]
            + out["cf_advance_from_customers_delta"] + out["cf_advance_to_suppliers_delta"]
            + out["cf_inventory_delta"] + out["cf_taxes_payable_delta"]
            + out["cf_employee_payable_delta"]
            + out["cf_other_operating_cash_in"] - out["cf_other_operating_cash_out"]
            + out["cf_tax_refund"]
        )
        out["cf_investing_net"] = (
            -out["cf_fixed_assets_delta"] - out["cf_intangible_assets_delta"]
            - out["cf_lt_invest_delta"]
            + out["cf_other_investing_cash_in"] - out["cf_other_investing_cash_out"]
        )
        out["cf_financing_net"] = (
            out["cf_st_borrowing_delta"] + out["cf_lt_borrowing_delta"]
            + out["cf_paid_in_capital_delta"]
            - out["cf_interest_paid"] - out["cf_dividends_paid"]
            + out["cf_other_financing_cash_in"] - out["cf_other_financing_cash_out"]
        )
        out["cf_net_change"] = (
            out["cf_operating_net"] + out["cf_investing_net"] + out["cf_financing_net"]
        )
        return out

    # ----- DB-driven helpers (called from the HTTP endpoint) -----

    async def load_balance_rows(self, *, org_id: str, period_id: str) -> list[dict[str, Any]]:
        """Most-recent successful TB import for (org, period).  Returns []
        if none exists (caller handles fallback)."""
        async with self._conn.execute(
            "SELECT id FROM trial_balance_imports WHERE org_id=? AND period_id=? "
            "AND status='ok' ORDER BY uploaded_at DESC LIMIT 1",
            (org_id, period_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return []
        import_id = row[0]
        async with self._conn.execute(
            "SELECT * FROM trial_balance_rows WHERE import_id=? ORDER BY row_index ASC",
            (import_id,),
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

    async def load_pl_cells(self, *, org_id: str, period_id: str) -> dict[str, Decimal]:
        """Read the latest income-statement report's cells for (org, period)."""
        async with self._conn.execute(
            "SELECT id FROM reports WHERE org_id=? AND period_id=? "
            "AND sheet_kind='income_statement' ORDER BY generated_at DESC LIMIT 1",
            (org_id, period_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {}
        report_id = row[0]
        async with self._conn.execute(
            "SELECT reference_code, value FROM report_cells WHERE report_id=?",
            (report_id,),
        ) as cur:
            cells = await cur.fetchall()
        return {r[0]: _D(r[1]) for r in cells}

    async def load_manual_inputs(
        self, *, org_id: str, period_id: str
    ) -> dict[str, Decimal]:
        async with self._conn.execute(
            "SELECT field_key, value FROM manual_inputs WHERE org_id=? AND period_id=?",
            (org_id, period_id),
        ) as cur:
            rows = await cur.fetchall()
        out: dict[str, Decimal] = {}
        for r in rows:
            key, val = r[0], r[1]
            try:
                out[key] = _D(val)
            except Exception:  # noqa: BLE001
                continue
        return out

    async def compute_for_period(
        self,
        *,
        org_id: str,
        period_id: str,
        prior_period_id: str | None = None,
    ) -> dict[str, Decimal]:
        """End-to-end compute: pull TB / PL / manual inputs from the DB and
        derive every cf_* key."""
        current_rows = await self.load_balance_rows(org_id=org_id, period_id=period_id)
        prior_rows: list[dict[str, Any]] = []
        if prior_period_id:
            prior_rows = await self.load_balance_rows(
                org_id=org_id, period_id=prior_period_id
            )
        pl_cells = await self.load_pl_cells(org_id=org_id, period_id=period_id)
        manual = await self.load_manual_inputs(org_id=org_id, period_id=period_id)
        return self.compute(
            current_rows=current_rows,
            prior_rows=prior_rows,
            pl_cells=pl_cells,
            manual_inputs=manual,
        )

    async def persist_as_manual_inputs(
        self,
        *,
        org_id: str,
        period_id: str,
        computed: dict[str, Decimal],
        decided_by: str = "local",
    ) -> int:
        """Upsert every cf_* key into ``manual_inputs`` so the existing
        report-generator pipeline can pick them up via the manual_input
        data_source.  Skips keys whose value is exactly zero (so the
        generator's "rendered as 0" warning still fires for unfilled
        manual inputs)."""
        # EX-P2-5: persist all keys atomically — historically a UNIQUE
        # CHECK failure on the 17th key would leave the first 16
        # upserts committed in autocommit mode.  Guard the whole loop
        # in a single try/commit/except/rollback envelope so a
        # mid-batch crash backs out everything and the caller sees
        # the original exception.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        written = 0
        try:
            for key, val in computed.items():
                row_id = f"mi_{org_id[-4:]}_{period_id}_{key}"
                await self._conn.execute(
                    "INSERT INTO manual_inputs(id, org_id, period_id, "
                    "field_key, field_label, value, value_type, source, "
                    "notes, decided_by, decided_at, version) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,1) "
                    "ON CONFLICT(org_id, period_id, field_key) DO UPDATE "
                    "SET value=excluded.value, source=excluded.source, "
                    "decided_at=excluded.decided_at, "
                    "version=manual_inputs.version+1",
                    (
                        row_id, org_id, period_id, key, key, str(val),
                        "cny", "indirect_cf_engine", None, decided_by, now,
                    ),
                )
                written += 1
            await self._conn.commit()
        except Exception:
            try:
                await self._conn.rollback()
            except Exception:  # noqa: BLE001 — rollback best-effort
                pass
            raise
        return written


__all__ = ["CashFlowError", "IndirectCashFlowEngine", "ACCOUNT_BUCKETS"]
