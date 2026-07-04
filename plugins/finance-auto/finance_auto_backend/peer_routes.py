"""HTTP layer for the M3 Biz Stage 5 peer-comparison feature.

Four endpoints under the plugin prefix:

* ``GET  /peer-benchmarks``                                 — list seeded benchmarks.
* ``POST /orgs/{org_id}/peer-comparison/run``               — execute + persist a run.
* ``GET  /orgs/{org_id}/peer-comparison/results``           — list runs per org.
* ``GET  /orgs/{org_id}/peer-comparison/results/{result_id}``— single run.

All endpoints are thin shims over
``services/peer_comparison.PeerComparisonService``.  The wire-up in
``routes.build_router`` already includes an ImportError-guarded call to
:func:`register_peer_endpoints` (committed in Stage 3), so this stage's
sole job is to ship the module — no edits to ``routes.py`` needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .rbac import require_permission
from .services.peer_comparison import PeerComparisonError, PeerComparisonService

if TYPE_CHECKING:
    from .routes import FinanceAutoService


class _RunRequest(BaseModel):
    period_id: str = Field(..., min_length=1, description="会计期间 ID，如 2025-FY")
    industry_code: str | None = Field(
        default=None,
        description=(
            "可选；缺省时回退到 organizations.industry。"
            "已知值：manufacturing / restaurant / tech_service"
        ),
    )


def register_peer_endpoints(router: APIRouter, service: "FinanceAutoService") -> None:
    pc = PeerComparisonService(service)

    @router.get(
        "/peer-benchmarks",
        summary="列出同业基准数据 (3 industries × 4 metrics = 12)",
    )
    async def list_benchmarks(
        industry_code: str | None = Query(default=None),
    ) -> dict[str, Any]:
        rows = await pc.list_benchmarks(industry_code=industry_code)
        return {"benchmarks": rows, "total": len(rows)}

    @router.post(
        "/orgs/{org_id}/peer-comparison/run",
        status_code=201,
        summary="基于最新报表计算同业对比并持久化",
    )
    async def run_comparison(
        org_id: str,
        payload: _RunRequest,
        _user: str = Depends(require_permission("peer_comparison", "run")),
    ) -> dict[str, Any]:
        try:
            return await pc.run_comparison(
                org_id=org_id,
                period_id=payload.period_id,
                industry_code=payload.industry_code,
            )
        except PeerComparisonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/orgs/{org_id}/peer-comparison/results",
        summary="列出该账套的同业对比历史",
    )
    async def list_results(org_id: str) -> dict[str, Any]:
        rows = await pc.list_results(org_id=org_id)
        return {"results": rows, "total": len(rows)}

    @router.get(
        "/orgs/{org_id}/peer-comparison/results/{result_id}",
        summary="读取单次同业对比结果",
    )
    async def get_result(org_id: str, result_id: int) -> dict[str, Any]:
        # Ensure the org exists first so a stray org id returns 404
        # before we even hit the result query.
        await service.get_org(org_id)
        return await pc.get_result(result_id=result_id)


__all__ = ["register_peer_endpoints"]
