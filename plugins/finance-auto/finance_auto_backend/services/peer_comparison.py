"""M3 Biz Stage 4 — peer comparison service.

Backs the v0.2 Part 2 §6.1 S5 (同业对比) scenario.  Given an
``(org_id, period_id, industry_code)`` triple, compute four standard
financial metrics from the org's latest ``report_cells`` and rank them
against the quartile benchmarks seeded in ``peer_benchmarks`` (v10
migration).

Metrics
=======

* ``gross_margin``   = (revenue − cost_of_revenue) / revenue
* ``current_ratio``  = current_assets / current_liabilities
* ``asset_turnover`` = revenue / total_assets
* ``debt_ratio``     = total_liabilities / total_assets

For each metric the service compares the org value against (p25, p50,
p75) and classifies it into one of five quartile buckets:

* ``well_below``    — below p25 by at least 50% of (p50 - p25)
* ``below``         — below p25 (inside the 50% band)
* ``median_band``   — between p25 and p75
* ``above``         — above p75 (inside 50% of (p75 - p50))
* ``well_above``    — more than 50% of (p75 - p50) above p75

Result rows persist into ``peer_comparison_results`` for auditability —
the same payload is returned synchronously so the React side can render
the comparison panel without an extra round-trip.

The AI summary slot is left blank in M3.  Sibling B's S5 worker will
populate it once the scenario lands; the schema reserves ``ai_audit_id``
for the link-back without forcing a migration bump.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from ..routes import FinanceAutoService

logger = logging.getLogger(__name__)

# Reference codes the M2 report engine emits — try both the
# small-enterprise and general-enterprise variants so the comparison
# works against whichever template the org has been using.
_REVENUE_CODES = (
    "IS_REVENUE", "IS_GE_REVENUE", "IS_OPERATING_REVENUE",
    "PL_REVENUE", "IS_NET_REVENUE", "IS_SE_REVENUE",
)
_COST_CODES = (
    "IS_COST", "IS_GE_COST", "IS_OPERATING_COST",
    "PL_COST", "IS_SE_COST",
)
_TOTAL_CA_CODES = ("BS_TOTAL_CA", "BS_GE_TOTAL_CA", "BS_TOTAL_CURRENT_ASSETS")
_TOTAL_CL_CODES = ("BS_TOTAL_CL", "BS_GE_TOTAL_CL", "BS_TOTAL_CURRENT_LIABILITIES")
_TOTAL_ASSETS_CODES = ("BS_TOTAL_ASSETS", "BS_GE_TOTAL_ASSETS")
_TOTAL_LIAB_CODES = ("BS_TOTAL_LIABILITIES", "BS_GE_TOTAL_LIABILITIES", "BS_TOTAL_LIAB")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_cell(cells: dict[str, dict[str, Any]], codes: tuple[str, ...]) -> float:
    """Return the first non-zero match, or 0.0 if none of the codes hit.

    Using 0.0 as the sentinel is deliberate — the metric pipeline below
    explicitly guards against division by zero so a missing field
    classifies as ``insufficient_data`` rather than crashing the run.
    """
    for code in codes:
        cell = cells.get(code)
        if cell is None:
            continue
        try:
            v = float(cell.get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        if v != 0.0:
            return v
    return 0.0


def _quartile_assessment(value: float, p25: float, p50: float, p75: float) -> str:
    """Five-bucket classification per the docstring at the top of the file."""
    band_low = max(p50 - p25, 0.0001)
    band_high = max(p75 - p50, 0.0001)
    if value < p25 - 0.5 * band_low:
        return "well_below"
    if value < p25:
        return "below"
    if value <= p75:
        return "median_band"
    if value <= p75 + 0.5 * band_high:
        return "above"
    return "well_above"


class PeerComparisonError(RuntimeError):
    """Raised for client-visible failures (mapped to 4xx by the routes layer)."""


class PeerComparisonService:
    """Compute + persist quartile assessments per (org, period, industry)."""

    def __init__(self, service: "FinanceAutoService"):
        self._svc = service

    # ----------------------- benchmark catalogue --------------------------

    async def list_benchmarks(
        self, *, industry_code: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM peer_benchmarks"
        params: tuple[Any, ...] = ()
        if industry_code:
            sql += " WHERE industry_code=?"
            params = (industry_code,)
        sql += " ORDER BY industry_code ASC, metric_code ASC"
        async with self._svc.db.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_bench(r) for r in rows]

    async def get_benchmark_map(
        self, *, industry_code: str
    ) -> dict[str, dict[str, Any]]:
        """``metric_code → benchmark row`` lookup used by ``run_comparison``."""
        async with self._svc.db.conn.execute(
            "SELECT * FROM peer_benchmarks WHERE industry_code=?",
            (industry_code,),
        ) as cur:
            rows = await cur.fetchall()
        return {r["metric_code"]: self._row_to_bench(r) for r in rows}

    # ----------------------- run + persist --------------------------------

    async def run_comparison(
        self,
        *,
        org_id: str,
        period_id: str,
        industry_code: str | None = None,
    ) -> dict[str, Any]:
        org = await self._svc.get_org(org_id)
        # Allow callers to omit industry_code; fall back to the org's own
        # industry field set at registration time.
        resolved_industry = industry_code or org.industry
        if not resolved_industry:
            raise PeerComparisonError(
                "industry_code is required (org has no industry set)"
            )

        benchmarks = await self.get_benchmark_map(industry_code=resolved_industry)
        if not benchmarks:
            raise PeerComparisonError(
                f"no benchmarks seeded for industry_code={resolved_industry!r}"
            )

        # Pull all metrics in one shot (no separate BS + IS queries).
        bs_cells = await self._load_latest_cells(
            org_id=org_id, period_id=period_id, sheet_kind="balance_sheet"
        )
        is_cells = await self._load_latest_cells(
            org_id=org_id, period_id=period_id, sheet_kind="income_statement"
        )

        revenue = _first_cell(is_cells, _REVENUE_CODES)
        cost = _first_cell(is_cells, _COST_CODES)
        total_ca = _first_cell(bs_cells, _TOTAL_CA_CODES)
        total_cl = _first_cell(bs_cells, _TOTAL_CL_CODES)
        total_assets = _first_cell(bs_cells, _TOTAL_ASSETS_CODES)
        total_liab = _first_cell(bs_cells, _TOTAL_LIAB_CODES)

        raw_metrics: dict[str, float | None] = {
            "gross_margin": (revenue - cost) / revenue if revenue else None,
            "current_ratio": total_ca / total_cl if total_cl else None,
            "asset_turnover": revenue / total_assets if total_assets else None,
            "debt_ratio": total_liab / total_assets if total_assets else None,
        }

        assessed: list[dict[str, Any]] = []
        for metric_code, bench in benchmarks.items():
            org_value = raw_metrics.get(metric_code)
            if org_value is None:
                assessed.append({
                    "metric_code": metric_code,
                    "metric_name": bench["metric_name"],
                    "org_value": None,
                    "p25": bench["p25"],
                    "p50": bench["p50"],
                    "p75": bench["p75"],
                    "delta_vs_median": None,
                    "assessment": "insufficient_data",
                    "sample_size": bench["sample_size"],
                })
                continue
            delta = org_value - bench["p50"]
            assessed.append({
                "metric_code": metric_code,
                "metric_name": bench["metric_name"],
                "org_value": round(org_value, 4),
                "p25": bench["p25"],
                "p50": bench["p50"],
                "p75": bench["p75"],
                "delta_vs_median": round(delta, 4),
                "assessment": _quartile_assessment(
                    org_value, bench["p25"], bench["p50"], bench["p75"]
                ),
                "sample_size": bench["sample_size"],
            })

        now = _utcnow_iso()
        cur = await self._svc.db.conn.execute(
            "INSERT INTO peer_comparison_results(org_id, period_id, industry_code, "
            "metrics_json, ai_summary, ai_audit_id, version, created_at) "
            "VALUES (?,?,?,?,?,?,1,?)",
            (
                org_id,
                period_id,
                resolved_industry,
                json.dumps(assessed, ensure_ascii=False),
                "",
                None,
                now,
            ),
        )
        result_id = cur.lastrowid
        await cur.close()
        await self._svc.db.conn.commit()

        return {
            "id": result_id,
            "org_id": org_id,
            "period_id": period_id,
            "industry_code": resolved_industry,
            "metrics": assessed,
            "ai_summary": "",
            "ai_audit_id": None,
            "version": 1,
            "created_at": now,
        }

    async def list_results(self, *, org_id: str) -> list[dict[str, Any]]:
        # Validate first so the 404 path stays clean.
        await self._svc.get_org(org_id)
        async with self._svc.db.conn.execute(
            "SELECT * FROM peer_comparison_results WHERE org_id=? "
            "ORDER BY created_at DESC, id DESC",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_result(r) for r in rows]

    async def get_result(self, *, result_id: int) -> dict[str, Any]:
        async with self._svc.db.conn.execute(
            "SELECT * FROM peer_comparison_results WHERE id=?", (result_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"result {result_id} not found")
        return self._row_to_result(row)

    # ----------------------- helpers --------------------------------------

    async def _load_latest_cells(
        self, *, org_id: str, period_id: str, sheet_kind: str
    ) -> dict[str, dict[str, Any]]:
        async with self._svc.db.conn.execute(
            "SELECT id FROM reports WHERE org_id=? AND period_id=? AND sheet_kind=? "
            "ORDER BY generated_at DESC LIMIT 1",
            (org_id, period_id, sheet_kind),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {}
        rid = row[0]
        async with self._svc.db.conn.execute(
            "SELECT reference_code, target_label, value FROM report_cells WHERE report_id=?",
            (rid,),
        ) as cur:
            rows = await cur.fetchall()
        return {
            r[0]: {"label": r[1] or r[0], "value": r[2] or 0.0}
            for r in rows
        }

    @staticmethod
    def _row_to_bench(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "industry_code": row["industry_code"],
            "metric_code": row["metric_code"],
            "metric_name": row["metric_name"],
            "period_label": row["period_label"],
            "p25": row["p25"],
            "p50": row["p50"],
            "p75": row["p75"],
            "sample_size": row["sample_size"],
            "source": row["source"],
            "accounting_standard": row["accounting_standard"],
            "version": row["version"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_result(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "org_id": row["org_id"],
            "period_id": row["period_id"],
            "industry_code": row["industry_code"],
            "metrics": json.loads(row["metrics_json"] or "[]"),
            "ai_summary": row["ai_summary"] or "",
            "ai_audit_id": row["ai_audit_id"],
            "version": row["version"],
            "created_at": row["created_at"],
        }


__all__ = [
    "PeerComparisonError",
    "PeerComparisonService",
]
