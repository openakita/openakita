"""HTTP endpoints for M2 Biz Stage 3 — reclassification rules engine.

Surface (5 endpoints, all under the plugin prefix)
--------------------------------------------------
``POST   /orgs/{org_id}/reclassification-rules``          — 创建规则
``GET    /orgs/{org_id}/reclassification-rules``          — 列出规则（含全局）
``POST   /orgs/{org_id}/reclassification-runs/preview``   — 试运行（不落 ParseIssue）
``POST   /orgs/{org_id}/reclassification-runs/apply``     — 应用（金额>阈值则生成 ParseIssue）
``GET    /orgs/{org_id}/reclassification-runs``           — 列出 run（包含 items）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from .models import (
    ReclassificationRuleCreateRequest,
    ReclassificationRuleListResponse,
    ReclassificationRuleModel,
    ReclassificationRunModel,
    ReclassificationRunRequest,
)
from .services.reclassification import (
    ReclassificationError,
    ReclassificationService,
)

if TYPE_CHECKING:  # avoid circular import at runtime
    from .routes import FinanceAutoService


def register_reclassification_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    """Attach reclassification HTTP endpoints onto the shared router."""

    def _svc() -> ReclassificationService:
        return ReclassificationService(service.db.conn)

    @router.post(
        "/orgs/{org_id}/reclassification-rules",
        status_code=201,
        response_model=ReclassificationRuleModel,
    )
    async def create_rule(
        org_id: str, payload: ReclassificationRuleCreateRequest
    ) -> ReclassificationRuleModel:
        try:
            return await _svc().create_rule(org_id=org_id, payload=payload)
        except ReclassificationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/orgs/{org_id}/reclassification-rules",
        response_model=ReclassificationRuleListResponse,
    )
    async def list_rules(
        org_id: str, active_only: bool = Query(default=False)
    ) -> ReclassificationRuleListResponse:
        rules = await _svc().list_rules(org_id=org_id, active_only=active_only)
        return ReclassificationRuleListResponse(rules=rules, total=len(rules))

    @router.post(
        "/orgs/{org_id}/reclassification-runs/preview",
        status_code=201,
        response_model=ReclassificationRunModel,
    )
    async def preview_run(
        org_id: str, payload: ReclassificationRunRequest
    ) -> ReclassificationRunModel:
        try:
            return await _svc().run(org_id=org_id, payload=payload, mode="preview")
        except ReclassificationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/orgs/{org_id}/reclassification-runs/apply",
        status_code=201,
        response_model=ReclassificationRunModel,
    )
    async def apply_run(
        org_id: str, payload: ReclassificationRunRequest
    ) -> ReclassificationRunModel:
        try:
            return await _svc().run(org_id=org_id, payload=payload, mode="apply")
        except ReclassificationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/orgs/{org_id}/reclassification-runs",
        response_model=list[ReclassificationRunModel],
    )
    async def list_runs(
        org_id: str, period_id: str | None = Query(default=None)
    ) -> list[ReclassificationRunModel]:
        return await _svc().list_runs(org_id=org_id, period_id=period_id)


__all__ = ["register_reclassification_endpoints"]
