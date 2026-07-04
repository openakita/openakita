"""HTTP endpoints for M2 Biz Stage 6 — consolidation engine.

Surface (8 endpoints)
---------------------
``POST /consolidation-groups``                            — 建集团
``GET  /consolidation-groups``                            — 列集团
``POST /consolidation-groups/{group_id}/members``         — 加入成员
``GET  /consolidation-groups/{group_id}/members``         — 列成员
``POST /consolidation-groups/{group_id}/eliminations``    — 写抵消分录
``GET  /consolidation-groups/{group_id}/eliminations``    — 列抵消分录
``POST /consolidation-groups/{group_id}/runs``            — 触发合并
``GET  /consolidation-groups/{group_id}/reports``         — 合并报表列表
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from .rbac import require_permission
from .models import (
    ConsolidatedReportListResponse,
    ConsolidatedReportModel,
    ConsolidationGroupCreateRequest,
    ConsolidationGroupListResponse,
    ConsolidationGroupModel,
    ConsolidationMemberCreateRequest,
    ConsolidationMemberModel,
    ConsolidationRunRequest,
    EliminationEntryCreateRequest,
    EliminationEntryListResponse,
    EliminationEntryModel,
)
from .services.consolidation import (
    ConsolidationError,
    ConsolidationService,
)

if TYPE_CHECKING:
    from .routes import FinanceAutoService


def register_consolidation_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    def _svc() -> ConsolidationService:
        return ConsolidationService(service.db.conn)

    # ---- groups ----------------------------------------------------------

    @router.post(
        "/consolidation-groups",
        status_code=201,
        response_model=ConsolidationGroupModel,
    )
    async def create_group(
        payload: ConsolidationGroupCreateRequest,
        _user: str = Depends(require_permission("consolidation", "create_group")),
    ) -> ConsolidationGroupModel:
        try:
            return await _svc().create_group(payload=payload)
        except ConsolidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/consolidation-groups",
        response_model=ConsolidationGroupListResponse,
    )
    async def list_groups() -> ConsolidationGroupListResponse:
        groups = await _svc().list_groups()
        return ConsolidationGroupListResponse(groups=groups, total=len(groups))

    @router.get(
        "/consolidation-groups/{group_id}",
        response_model=ConsolidationGroupModel,
    )
    async def get_group(group_id: int) -> ConsolidationGroupModel:
        try:
            return await _svc().get_group(group_id=group_id)
        except ConsolidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ---- members ---------------------------------------------------------

    @router.post(
        "/consolidation-groups/{group_id}/members",
        status_code=201,
        response_model=ConsolidationMemberModel,
    )
    async def add_member(
        group_id: int,
        payload: ConsolidationMemberCreateRequest,
        _user: str = Depends(require_permission("consolidation", "add_member")),
    ) -> ConsolidationMemberModel:
        try:
            return await _svc().add_member(group_id=group_id, payload=payload)
        except ConsolidationError as exc:
            code = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.get(
        "/consolidation-groups/{group_id}/members",
        response_model=list[ConsolidationMemberModel],
    )
    async def list_members(group_id: int) -> list[ConsolidationMemberModel]:
        try:
            await _svc().get_group(group_id=group_id)
        except ConsolidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _svc().list_members(group_id=group_id)

    # ---- eliminations ----------------------------------------------------

    @router.post(
        "/consolidation-groups/{group_id}/eliminations",
        status_code=201,
        response_model=EliminationEntryModel,
    )
    async def add_elimination(
        group_id: int, payload: EliminationEntryCreateRequest
    ) -> EliminationEntryModel:
        try:
            return await _svc().add_elimination(group_id=group_id, payload=payload)
        except ConsolidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/consolidation-groups/{group_id}/eliminations",
        response_model=EliminationEntryListResponse,
    )
    async def list_eliminations(
        group_id: int, period_id: str | None = Query(default=None)
    ) -> EliminationEntryListResponse:
        try:
            await _svc().get_group(group_id=group_id)
        except ConsolidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        entries = await _svc().list_eliminations(group_id=group_id, period_id=period_id)
        return EliminationEntryListResponse(entries=entries, total=len(entries))

    # ---- run + reports ---------------------------------------------------

    @router.post(
        "/consolidation-groups/{group_id}/runs",
        status_code=201,
        response_model=ConsolidatedReportModel,
    )
    async def run_consolidation(
        group_id: int,
        payload: ConsolidationRunRequest,
        _user: str = Depends(require_permission("consolidation", "run")),
    ) -> ConsolidatedReportModel:
        try:
            return await _svc().run(group_id=group_id, payload=payload)
        except ConsolidationError as exc:
            code = 404 if "not found" in str(exc) else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.get(
        "/consolidation-groups/{group_id}/reports",
        response_model=ConsolidatedReportListResponse,
    )
    async def list_reports(group_id: int) -> ConsolidatedReportListResponse:
        try:
            await _svc().get_group(group_id=group_id)
        except ConsolidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        reports = await _svc().list_reports(group_id=group_id)
        return ConsolidatedReportListResponse(reports=reports, total=len(reports))


__all__ = ["register_consolidation_endpoints"]
