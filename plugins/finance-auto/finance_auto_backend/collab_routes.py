"""HTTP endpoints for M2 Biz Stage 2 — collaboration + review workflow.

Mounted onto the W1 ``APIRouter`` by :func:`register_collab_endpoints`.

Surface (10 endpoints)
----------------------
``POST   /users``                                       — 注册用户
``GET    /users``                                       — 列出用户
``POST   /orgs/{org_id}/assignments``                   — 指派
``GET    /orgs/{org_id}/assignments``                   — 列出指派
``POST   /orgs/{org_id}/reports/{report_id}/review/submit``
``POST   /orgs/{org_id}/reports/{report_id}/review/approve``
``POST   /orgs/{org_id}/reports/{report_id}/review/request-changes``
``POST   /orgs/{org_id}/reports/{report_id}/review/sign-off``
``POST   /orgs/{org_id}/reports/{report_id}/cells/{cell_id}/comments``
``GET    /orgs/{org_id}/reports/{report_id}/comments``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from .models import (
    AssignmentCreateRequest,
    AssignmentListResponse,
    AssignmentModel,
    CommentCreateRequest,
    CommentListResponse,
    CommentModel,
    ReviewStatus,
    ReviewWorkflowActionRequest,
    ReviewWorkflowModel,
    ReviewWorkflowSubmitRequest,
    UserCreateRequest,
    UserListResponse,
    UserModel,
    UserRole,
)
from .services.collaboration import CollaborationService
from .services.review_workflow import ReviewWorkflowService

if TYPE_CHECKING:
    from .routes import FinanceAutoService

logger = logging.getLogger(__name__)


def _build_services(service: "FinanceAutoService") -> tuple[CollaborationService, ReviewWorkflowService]:
    collab = CollaborationService(service.db.conn)
    review = ReviewWorkflowService(service.db.conn, collab)
    return collab, review


async def _resolve_workflow_id(
    service: "FinanceAutoService", *, org_id: str, report_id: str,
) -> int | None:
    async with service.db.conn.execute(
        "SELECT workflow_id FROM review_workflows WHERE org_id=? AND report_id=? "
        "ORDER BY workflow_id DESC LIMIT 1",
        (org_id, report_id),
    ) as cur:
        row = await cur.fetchone()
    return int(row["workflow_id"]) if row else None


def register_collab_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    # ---- users ----------------------------------------------------------

    @router.post(
        "/users", status_code=201,
        summary="注册新用户（M2 期间走 admin 手工录入）",
    )
    async def register_user(payload: UserCreateRequest) -> UserModel:
        collab, _ = _build_services(service)
        return await collab.register_user(payload)

    @router.get(
        "/users",
        summary="列出已注册用户",
    )
    async def list_users(
        role: UserRole | None = Query(default=None),
        active_only: bool = Query(default=True),
    ) -> UserListResponse:
        collab, _ = _build_services(service)
        users = await collab.list_users(role=role, active_only=active_only)
        return UserListResponse(users=users, total=len(users))

    # ---- assignments ----------------------------------------------------

    @router.post(
        "/orgs/{org_id}/assignments", status_code=201,
        summary="指派 user 到 org（可选 period）",
    )
    async def create_assignment(
        org_id: str, payload: AssignmentCreateRequest,
    ) -> AssignmentModel:
        await service.get_org(org_id)
        collab, _ = _build_services(service)
        return await collab.assign(
            user_id=payload.user_id,
            org_id=org_id,
            period_id=payload.period_id,
            role_in_project=payload.role_in_project,
            assigned_by=payload.assigned_by,
        )

    @router.get(
        "/orgs/{org_id}/assignments",
        summary="列出该账套的所有指派（默认不含已撤销）",
    )
    async def list_assignments(
        org_id: str,
        include_revoked: bool = Query(default=False),
    ) -> AssignmentListResponse:
        await service.get_org(org_id)
        collab, _ = _build_services(service)
        rows = await collab.list_assignments(
            org_id=org_id, include_revoked=include_revoked,
        )
        return AssignmentListResponse(assignments=rows, total=len(rows))

    # ---- review workflow ------------------------------------------------

    @router.post(
        "/orgs/{org_id}/reports/{report_id}/review/submit", status_code=201,
        summary="提交报表进入复核流程",
    )
    async def review_submit(
        org_id: str, report_id: str, payload: ReviewWorkflowSubmitRequest,
    ) -> ReviewWorkflowModel:
        await service.get_org(org_id)
        # Look up the period for the report so the workflow row is fully populated.
        async with service.db.conn.execute(
            "SELECT period_id FROM reports WHERE org_id=? AND id=?",
            (org_id, report_id),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="report not found")
        period_id = r["period_id"]
        _, review = _build_services(service)
        return await review.submit_for_review(
            org_id=org_id, period_id=period_id,
            report_id=report_id, payload=payload,
        )

    @router.post(
        "/orgs/{org_id}/reports/{report_id}/review/approve",
        summary="经理批准复核（reviewed → pending_signoff）",
    )
    async def review_approve(
        org_id: str, report_id: str, payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        await service.get_org(org_id)
        wf_id = await _resolve_workflow_id(service, org_id=org_id, report_id=report_id)
        if wf_id is None:
            raise HTTPException(status_code=404, detail="no workflow for this report")
        _, review = _build_services(service)
        return await review.approve_review(workflow_id=wf_id, payload=payload)

    @router.post(
        "/orgs/{org_id}/reports/{report_id}/review/request-changes",
        summary="复核人 / 合伙人打回（→ returned，附 reason）",
    )
    async def review_request_changes(
        org_id: str, report_id: str, payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        await service.get_org(org_id)
        wf_id = await _resolve_workflow_id(service, org_id=org_id, report_id=report_id)
        if wf_id is None:
            raise HTTPException(status_code=404, detail="no workflow for this report")
        _, review = _build_services(service)
        return await review.request_changes(workflow_id=wf_id, payload=payload)

    @router.post(
        "/orgs/{org_id}/reports/{report_id}/review/sign-off",
        summary="合伙人最终签字（→ signed_off）",
    )
    async def review_sign_off(
        org_id: str, report_id: str, payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        await service.get_org(org_id)
        wf_id = await _resolve_workflow_id(service, org_id=org_id, report_id=report_id)
        if wf_id is None:
            raise HTTPException(status_code=404, detail="no workflow for this report")
        _, review = _build_services(service)
        return await review.sign_off(workflow_id=wf_id, payload=payload)

    # ---- comments -------------------------------------------------------

    @router.post(
        "/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/comments",
        status_code=201,
        summary="挂一条评论到报表 cell",
    )
    async def add_cell_comment(
        org_id: str,
        report_id: str,
        cell_id: str,
        payload: CommentCreateRequest,
    ) -> CommentModel:
        await service.get_org(org_id)
        # workflow attachment is optional; resolve the latest if any.
        wf_id = await _resolve_workflow_id(service, org_id=org_id, report_id=report_id)
        _, review = _build_services(service)
        return await review.add_comment(
            org_id=org_id, cell_id=cell_id, report_id=report_id,
            workflow_id=wf_id, payload=payload,
        )

    @router.get(
        "/orgs/{org_id}/reports/{report_id}/comments",
        summary="列出某报表上的评论（默认含已解决）",
    )
    async def list_report_comments(
        org_id: str, report_id: str,
        resolved: bool | None = Query(default=None),
        cell_id: str | None = Query(default=None),
    ) -> CommentListResponse:
        await service.get_org(org_id)
        _, review = _build_services(service)
        rows = await review.list_comments(
            org_id=org_id, report_id=report_id,
            cell_id=cell_id, resolved=resolved,
        )
        return CommentListResponse(comments=rows, total=len(rows))

    @router.get(
        "/orgs/{org_id}/reports/{report_id}/workflows",
        summary="列出该报表的所有 review workflow",
    )
    async def list_report_workflows(
        org_id: str, report_id: str,
        status: ReviewStatus | None = Query(default=None),
    ) -> list[ReviewWorkflowModel]:
        await service.get_org(org_id)
        _, review = _build_services(service)
        return await review.list_workflows(
            org_id=org_id, report_id=report_id, status=status,
        )


__all__ = ["register_collab_endpoints"]
