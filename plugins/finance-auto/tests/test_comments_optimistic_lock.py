"""Unit tests for the comments table optimistic-lock fix (P2-6).

§2.4 of the M3 audit reported that ``ReviewWorkflowService.resolve_comment``
declared a ``version`` column on the ``comments`` table and bumped it
via ``SET version=version+1`` but failed to add ``WHERE id=? AND
version=?`` to the UPDATE — so concurrent resolves silently raced
last-write-wins instead of producing a 409 contention error like every
other Part Infra C3 table.

These tests exercise the service method directly (no HTTP route yet
exposes comment.resolve) using an in-process SQLite database.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.models import CommentCreateRequest, OrganizationCreate
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services.collaboration import CollaborationService
from finance_auto_backend.services.review_workflow import ReviewWorkflowService


async def _bootstrap(tmp_path: Path):
    db_path = tmp_path / "comments.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    service = FinanceAutoService(db)
    org = await service.create_org(
        OrganizationCreate(name="评论锁测试", code="COMM-LOCK-001")
    )
    collab = CollaborationService(db.conn)
    review = ReviewWorkflowService(db.conn, collab)
    return db, service, org.id, review


async def _make_comment(
    review: ReviewWorkflowService, *, org_id: str
) -> int:
    payload = CommentCreateRequest(
        body="测试评论 — 用于验证乐观锁",
        kind="general",
        author_id="local",
    )
    comment = await review.add_comment(
        org_id=org_id, cell_id="cell_dummy", report_id=None,
        workflow_id=None, payload=payload,
    )
    return comment.id


@pytest.mark.asyncio
async def test_resolve_comment_without_expected_version_now_rejects(tmp_path: Path):
    """Round-2 optimisation #1: the opt-in fallback was deleted, so a
    call that omits ``expected_version`` must now raise HTTP 409
    ``missing_expected_version`` instead of silently winning."""
    db, _svc, org_id, review = await _bootstrap(tmp_path)
    try:
        cid = await _make_comment(review, org_id=org_id)
        with pytest.raises(HTTPException) as exc_info:
            await review.resolve_comment(
                comment_id=cid, actor_id="local",
            )
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["error"] == "missing_expected_version"
        assert detail["comment_id"] == cid

        # Comment must still be unresolved at v1.
        async with db.conn.execute(
            "SELECT resolved, version FROM comments WHERE id=?", (cid,),
        ) as cur:
            row = await cur.fetchone()
        assert row["resolved"] == 0
        assert row["version"] == 1

        # Strict path still works (and is idempotent on a second call).
        resolved = await review.resolve_comment(
            comment_id=cid, actor_id="local", expected_version=1,
        )
        assert resolved.resolved is True
        assert resolved.version == 2
        # Second resolve already-resolved → no-op idempotent return,
        # even without expected_version (no UPDATE is executed).
        again = await review.resolve_comment(
            comment_id=cid, actor_id="local", expected_version=2,
        )
        assert again.version == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_resolve_comment_rejects_stale_expected_version(tmp_path: Path):
    """expected_version mismatch → HTTPException 409 with structured
    detail; the comment must remain unresolved."""
    db, _svc, org_id, review = await _bootstrap(tmp_path)
    try:
        cid = await _make_comment(review, org_id=org_id)
        # Comment was inserted at version=1 (per add_comment INSERT).
        # Pretend caller thinks it's still at version 99 → should 409.
        with pytest.raises(HTTPException) as exc_info:
            await review.resolve_comment(
                comment_id=cid, actor_id="local",
                expected_version=99,
            )
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["error"] == "version_conflict"
        assert detail["expected_version"] == 99
        assert detail["current_version"] == 1

        # Comment is still unresolved + still at v1.
        async with db.conn.execute(
            "SELECT resolved, version FROM comments WHERE id=?", (cid,),
        ) as cur:
            row = await cur.fetchone()
        assert row["resolved"] == 0
        assert row["version"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_resolve_comment_accepts_matching_expected_version(tmp_path: Path):
    """Pass expected_version equal to the live version → succeeds + bumps."""
    db, _svc, org_id, review = await _bootstrap(tmp_path)
    try:
        cid = await _make_comment(review, org_id=org_id)
        resolved = await review.resolve_comment(
            comment_id=cid, actor_id="local",
            expected_version=1,
        )
        assert resolved.resolved is True
        assert resolved.version == 2
    finally:
        await db.close()
