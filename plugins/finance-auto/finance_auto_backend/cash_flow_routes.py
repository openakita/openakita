"""HTTP endpoints for M2 Biz Stage 4 — indirect cash flow engine.

Surface (3 endpoints)
---------------------
``POST /orgs/{org_id}/cash-flow/compute``    — 计算 cf_* 派生键（不写库）
``POST /orgs/{org_id}/cash-flow/persist``    — 计算并写入 manual_inputs（供报表生成 pipeline 使用）
``GET  /orgs/{org_id}/cash-flow/keys``       — 列出引擎当前发布的 cf_* 键，便于前端反查
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .rbac import require_permission
from .services.cash_flow import (
    ACCOUNT_BUCKETS,
    CashFlowError,
    IndirectCashFlowEngine,
)

if TYPE_CHECKING:
    from .routes import FinanceAutoService


class CashFlowComputeRequest(BaseModel):
    period_id: str
    prior_period_id: str | None = Field(
        default=None,
        description="期初对比期；不传则全部 BS Δ = 0（首次接入场景）",
    )
    decided_by: str = "local"


class CashFlowComputeResponse(BaseModel):
    org_id: str
    period_id: str
    prior_period_id: str | None
    keys: list[str]
    values: dict[str, str]  # Decimal stringified
    has_pl_anchor: bool
    has_balance: bool
    has_prior_balance: bool


class CashFlowPersistResponse(CashFlowComputeResponse):
    persisted: int


def register_cash_flow_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    def _engine() -> IndirectCashFlowEngine:
        return IndirectCashFlowEngine(service.db.conn)

    async def _compute(
        org_id: str, payload: CashFlowComputeRequest
    ) -> dict[str, Any]:
        # Sanity: org must exist.
        try:
            await service.get_org(org_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        eng = _engine()
        current_rows = await eng.load_balance_rows(
            org_id=org_id, period_id=payload.period_id
        )
        prior_rows = (
            await eng.load_balance_rows(
                org_id=org_id, period_id=payload.prior_period_id
            )
            if payload.prior_period_id else []
        )
        pl_cells = await eng.load_pl_cells(
            org_id=org_id, period_id=payload.period_id
        )
        manual = await eng.load_manual_inputs(
            org_id=org_id, period_id=payload.period_id
        )
        computed = eng.compute(
            current_rows=current_rows, prior_rows=prior_rows,
            pl_cells=pl_cells, manual_inputs=manual,
        )
        return {
            "computed": computed,
            "has_pl_anchor": bool(pl_cells.get("PL_NET_PROFIT")),
            "has_balance": bool(current_rows),
            "has_prior_balance": bool(prior_rows),
        }

    @router.post(
        "/orgs/{org_id}/cash-flow/compute",
        status_code=200,
        response_model=CashFlowComputeResponse,
    )
    async def compute_cash_flow(
        org_id: str,
        payload: CashFlowComputeRequest,
        _user: str = Depends(require_permission("cash_flow", "compute")),
    ) -> CashFlowComputeResponse:
        try:
            result = await _compute(org_id, payload)
        except CashFlowError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        computed = result["computed"]
        return CashFlowComputeResponse(
            org_id=org_id,
            period_id=payload.period_id,
            prior_period_id=payload.prior_period_id,
            keys=sorted(computed.keys()),
            values={k: str(v) for k, v in computed.items()},
            has_pl_anchor=result["has_pl_anchor"],
            has_balance=result["has_balance"],
            has_prior_balance=result["has_prior_balance"],
        )

    @router.post(
        "/orgs/{org_id}/cash-flow/persist",
        status_code=201,
        response_model=CashFlowPersistResponse,
    )
    async def persist_cash_flow(
        org_id: str,
        payload: CashFlowComputeRequest,
        _user: str = Depends(require_permission("cash_flow", "manual_input_update")),
    ) -> CashFlowPersistResponse:
        try:
            result = await _compute(org_id, payload)
        except CashFlowError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        computed = result["computed"]
        eng = _engine()
        n = await eng.persist_as_manual_inputs(
            org_id=org_id, period_id=payload.period_id, computed=computed,
            decided_by=payload.decided_by,
        )
        return CashFlowPersistResponse(
            org_id=org_id,
            period_id=payload.period_id,
            prior_period_id=payload.prior_period_id,
            keys=sorted(computed.keys()),
            values={k: str(v) for k, v in computed.items()},
            has_pl_anchor=result["has_pl_anchor"],
            has_balance=result["has_balance"],
            has_prior_balance=result["has_prior_balance"],
            persisted=n,
        )

    @router.get("/orgs/{org_id}/cash-flow/keys")
    async def list_cf_keys(org_id: str) -> dict[str, Any]:
        return {
            "synthetic_keys": [
                "cf_net_profit", "cf_operating_profit", "cf_finance_cost",
                "cf_depreciation", "cf_amortization", "cf_dep_amort",
                "cf_asset_impairment",
                "cf_ar_delta", "cf_ap_delta", "cf_other_receivables_delta",
                "cf_other_payables_delta", "cf_advance_from_customers_delta",
                "cf_advance_to_suppliers_delta", "cf_inventory_delta",
                "cf_taxes_payable_delta", "cf_employee_payable_delta",
                "cf_fixed_assets_delta", "cf_intangible_assets_delta",
                "cf_lt_invest_delta", "cf_st_borrowing_delta",
                "cf_lt_borrowing_delta", "cf_paid_in_capital_delta",
                "cf_dividends_paid", "cf_interest_paid", "cf_interest_received",
                "cf_tax_refund", "cf_other_operating_cash_in",
                "cf_other_operating_cash_out", "cf_other_investing_cash_in",
                "cf_other_investing_cash_out", "cf_other_financing_cash_in",
                "cf_other_financing_cash_out", "cf_operating_net",
                "cf_investing_net", "cf_financing_net", "cf_net_change",
                "cf_cash_delta_bs",
            ],
            "buckets": {
                name: {"direction": b["direction"], "prefixes": b["prefixes"]}
                for name, b in ACCOUNT_BUCKETS.items()
            },
        }


__all__ = ["register_cash_flow_endpoints"]
