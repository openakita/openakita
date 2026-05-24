"""Report review workflow + comments service (v0.3 Part Biz §1.6).

State machine
-------------

::

    draft
      └─► pending_review  ── reviewer.approve ──► reviewed
                          ─► reviewer.request_changes ──► returned
      └─► (auto-advance)  reviewed ──► pending_signoff
                          partner.sign_off ──► signed_off
                          partner.request_changes ──► returned

* ``returned`` is the dead-end "needs rework" state; the auditor resubmits
  by hitting ``submit`` again (which advances back to ``pending_review``).
* Optimistic locking: every state transition runs
  ``UPDATE ... WHERE workflow_id=? AND version=?`` then
  ``version=version+1``.  Zero rows affected → 409 Conflict.
* History is appended to ``history_json`` on every transition for the
  audit trail (Part Biz §1.6 mermaid + v0.1 §13.3 hash-chain notes).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import HTTPException

from ..models import (
    CommentCreateRequest,
    CommentModel,
    ReviewStatus,
    ReviewWorkflowActionRequest,
    ReviewWorkflowModel,
    ReviewWorkflowSubmitRequest,
)
from .collaboration import CollaborationService

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Row → Pydantic
# ---------------------------------------------------------------------------


def _row_to_workflow(row: Any) -> ReviewWorkflowModel:
    try:
        history = json.loads(row["history_json"] or "[]")
    except json.JSONDecodeError:
        history = []
    return ReviewWorkflowModel(
        workflow_id=int(row["workflow_id"]),
        org_id=row["org_id"],
        period_id=row["period_id"],
        report_id=row["report_id"],
        target_kind=row["target_kind"] or "report_instance",
        status=row["status"],
        auditor_id=row["auditor_id"],
        reviewer_id=row["reviewer_id"],
        partner_id=row["partner_id"],
        submitted_at=row["submitted_at"],
        reviewed_at=row["reviewed_at"],
        signed_off_at=row["signed_off_at"],
        returned_at=row["returned_at"],
        return_reason=row["return_reason"],
        history=history,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=int(row["version"] or 1),
    )


def _row_to_comment(row: Any) -> CommentModel:
    try:
        mentions = json.loads(row["mentions"] or "[]")
    except json.JSONDecodeError:
        mentions = []
    return CommentModel(
        id=int(row["id"]),
        workflow_id=row["workflow_id"],
        cell_id=row["cell_id"],
        report_id=row["report_id"],
        org_id=row["org_id"],
        parent_id=row["parent_id"],
        kind=row["kind"] or "general",
        author_id=row["author_id"] or "local",
        body=row["body"],
        mentions=list(mentions) if isinstance(mentions, list) else [],
        resolved=bool(row["resolved"]),
        resolved_by=row["resolved_by"],
        resolved_at=row["resolved_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=int(row["version"] or 1),
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


_VALID_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    "draft": {"pending_review"},
    "pending_review": {"reviewed", "returned"},
    "reviewed": {"pending_signoff"},
    "pending_signoff": {"signed_off", "returned"},
    "returned": {"pending_review"},  # auditor re-submits
    "signed_off": set(),  # terminal
}


class ReviewWorkflowService:
    def __init__(self, conn: aiosqlite.Connection, collab: CollaborationService):
        self.conn = conn
        self.collab = collab

    # ---- bookkeeping ----------------------------------------------------

    async def _load(self, workflow_id: int) -> Any:
        async with self.conn.execute(
            "SELECT * FROM review_workflows WHERE workflow_id=?",
            (workflow_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return row

    async def _transition(
        self,
        *,
        workflow_id: int,
        expected_status: set[ReviewStatus],
        new_status: ReviewStatus,
        actor_id: str,
        note: str | None = None,
        reason: str | None = None,
        extra_updates: dict[str, Any] | None = None,
    ) -> ReviewWorkflowModel:
        row = await self._load(workflow_id)
        current: ReviewStatus = row["status"]
        if current not in expected_status:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workflow {workflow_id} is in state {current!r}; "
                    f"action requires one of {sorted(expected_status)}"
                ),
            )
        if new_status not in _VALID_TRANSITIONS.get(current, set()):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"invalid transition {current!r} -> {new_status!r} for "
                    f"workflow {workflow_id}"
                ),
            )

        now = _utcnow_iso()
        try:
            history = json.loads(row["history_json"] or "[]")
        except json.JSONDecodeError:
            history = []
        history.append({
            "from": current,
            "to": new_status,
            "by": actor_id,
            "at": now,
            "note": note,
            "reason": reason,
        })
        history_json = json.dumps(history, ensure_ascii=False)

        updates = dict(extra_updates or {})
        updates.setdefault("status", new_status)
        updates.setdefault("updated_at", now)
        updates.setdefault("history_json", history_json)
        if new_status == "pending_review":
            updates.setdefault("submitted_at", now)
            updates.setdefault("returned_at", None)
            updates.setdefault("return_reason", None)
        elif new_status == "reviewed":
            updates.setdefault("reviewed_at", now)
        elif new_status == "signed_off":
            updates.setdefault("signed_off_at", now)
        elif new_status == "returned":
            updates.setdefault("returned_at", now)
            if reason is not None:
                updates.setdefault("return_reason", reason)

        set_clause = ", ".join(f"{k}=?" for k in updates) + ", version=version+1"
        args = list(updates.values()) + [workflow_id, int(row["version"])]
        cur = await self.conn.execute(
            f"UPDATE review_workflows SET {set_clause} "
            f"WHERE workflow_id=? AND version=?",
            tuple(args),
        )
        if cur.rowcount == 0:
            await self.conn.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workflow {workflow_id} version changed during transition "
                    "(optimistic-lock contention); reload and retry"
                ),
            )
        await self.conn.commit()
        updated = await self._load(workflow_id)
        return _row_to_workflow(updated)

    # ---- public actions -------------------------------------------------

    async def submit_for_review(
        self,
        *,
        org_id: str,
        period_id: str,
        report_id: str | None,
        payload: ReviewWorkflowSubmitRequest,
    ) -> ReviewWorkflowModel:
        """Create a new workflow OR move an existing one back to
        ``pending_review`` (after a ``returned``).

        Caller MUST have ``workflow.submit`` permission scoped to the org.
        """
        if not await self.collab.check_permission(
            user_id=payload.auditor_id,
            resource="workflow",
            action="submit",
            org_id=org_id,
            period_id=period_id,
        ):
            raise HTTPException(
                status_code=403,
                detail=f"user {payload.auditor_id!r} cannot submit workflow for {org_id}",
            )

        # Reuse a workflow already attached to this (org, period, report) if
        # one exists.  Otherwise create a fresh draft → pending_review.
        existing: Any = None
        if report_id:
            async with self.conn.execute(
                "SELECT * FROM review_workflows WHERE org_id=? AND period_id=? "
                "AND report_id=? ORDER BY workflow_id DESC LIMIT 1",
                (org_id, period_id, report_id),
            ) as cur:
                existing = await cur.fetchone()

        if existing is not None and existing["status"] in {"draft", "returned"}:
            return await self._transition(
                workflow_id=int(existing["workflow_id"]),
                expected_status={"draft", "returned"},
                new_status="pending_review",
                actor_id=payload.auditor_id,
                extra_updates={
                    "auditor_id": payload.auditor_id,
                    "reviewer_id": payload.reviewer_id,
                    "partner_id": payload.partner_id,
                },
            )

        if existing is not None and existing["status"] in {
            "pending_review", "reviewed", "pending_signoff", "signed_off"
        }:
            # Already in flight; surface the current state instead of
            # creating a duplicate row.
            return _row_to_workflow(existing)

        now = _utcnow_iso()
        history_json = json.dumps(
            [{"from": "draft", "to": "pending_review", "by": payload.auditor_id,
              "at": now, "note": "initial submit"}],
            ensure_ascii=False,
        )
        cur = await self.conn.execute(
            "INSERT INTO review_workflows(org_id, period_id, report_id, "
            "target_kind, status, auditor_id, reviewer_id, partner_id, "
            "submitted_at, history_json, created_at, updated_at, version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                org_id, period_id, report_id, payload.target_kind,
                "pending_review",
                payload.auditor_id, payload.reviewer_id, payload.partner_id,
                now, history_json, now, now,
            ),
        )
        await self.conn.commit()
        wf_id = cur.lastrowid
        row = await self._load(int(wf_id))
        return _row_to_workflow(row)

    async def approve_review(
        self,
        *,
        workflow_id: int,
        payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        row = await self._load(workflow_id)
        if not await self.collab.check_permission(
            user_id=payload.actor_id,
            resource="workflow",
            action="review",
            org_id=row["org_id"],
            period_id=row["period_id"],
        ):
            raise HTTPException(
                status_code=403,
                detail=f"user {payload.actor_id!r} cannot review workflow",
            )
        # Two-stage advance: pending_review -> reviewed -> pending_signoff.
        wf = await self._transition(
            workflow_id=workflow_id,
            expected_status={"pending_review"},
            new_status="reviewed",
            actor_id=payload.actor_id,
            note=payload.note,
            extra_updates={"reviewer_id": payload.actor_id},
        )
        wf = await self._transition(
            workflow_id=workflow_id,
            expected_status={"reviewed"},
            new_status="pending_signoff",
            actor_id=payload.actor_id,
            note="auto-advance after review approve",
        )
        return wf

    async def sign_off(
        self,
        *,
        workflow_id: int,
        payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        row = await self._load(workflow_id)
        if not await self.collab.check_permission(
            user_id=payload.actor_id,
            resource="workflow",
            action="sign_off",
            org_id=row["org_id"],
            period_id=row["period_id"],
        ):
            raise HTTPException(
                status_code=403,
                detail=f"user {payload.actor_id!r} cannot sign off workflow",
            )
        return await self._transition(
            workflow_id=workflow_id,
            expected_status={"pending_signoff"},
            new_status="signed_off",
            actor_id=payload.actor_id,
            note=payload.note,
            extra_updates={"partner_id": payload.actor_id},
        )

    async def request_changes(
        self,
        *,
        workflow_id: int,
        payload: ReviewWorkflowActionRequest,
    ) -> ReviewWorkflowModel:
        row = await self._load(workflow_id)
        if not await self.collab.check_permission(
            user_id=payload.actor_id,
            resource="workflow",
            action="request_changes",
            org_id=row["org_id"],
            period_id=row["period_id"],
        ):
            # partner sign_off also implies request_changes (override).
            if not await self.collab.check_permission(
                user_id=payload.actor_id,
                resource="workflow",
                action="sign_off",
                org_id=row["org_id"],
                period_id=row["period_id"],
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"user {payload.actor_id!r} cannot request changes",
                )
        reason = payload.reason or payload.note or "no reason given"
        return await self._transition(
            workflow_id=workflow_id,
            expected_status={"pending_review", "pending_signoff"},
            new_status="returned",
            actor_id=payload.actor_id,
            note=payload.note,
            reason=reason,
        )

    async def list_workflows(
        self,
        *,
        org_id: str,
        report_id: str | None = None,
        status: ReviewStatus | None = None,
    ) -> list[ReviewWorkflowModel]:
        clauses: list[str] = ["org_id=?"]
        args: list[Any] = [org_id]
        if report_id:
            clauses.append("report_id=?")
            args.append(report_id)
        if status:
            clauses.append("status=?")
            args.append(status)
        async with self.conn.execute(
            f"SELECT * FROM review_workflows WHERE {' AND '.join(clauses)} "
            f"ORDER BY workflow_id DESC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_workflow(r) for r in rows]

    # ---- comments -------------------------------------------------------

    async def add_comment(
        self,
        *,
        org_id: str,
        cell_id: str | None,
        report_id: str | None,
        workflow_id: int | None,
        payload: CommentCreateRequest,
    ) -> CommentModel:
        if cell_id is None and report_id is None and workflow_id is None:
            raise HTTPException(
                status_code=400,
                detail="must attach the comment to a cell / report / workflow",
            )
        # Permission: comment.write on the org (assigned for auditor/manager,
        # all for partner; 'local' always allowed).
        if not await self.collab.check_permission(
            user_id=payload.author_id,
            resource="comment",
            action="write",
            org_id=org_id,
        ):
            raise HTTPException(
                status_code=403,
                detail=f"user {payload.author_id!r} cannot post comments on {org_id}",
            )
        now = _utcnow_iso()
        cur = await self.conn.execute(
            "INSERT INTO comments(workflow_id, cell_id, report_id, org_id, "
            "parent_id, kind, author_id, body, mentions, resolved, "
            "created_at, updated_at, version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                workflow_id,
                cell_id,
                report_id,
                org_id,
                payload.parent_id,
                payload.kind,
                payload.author_id,
                payload.body,
                json.dumps(payload.mentions or [], ensure_ascii=False),
                0,
                now,
                now,
            ),
        )
        await self.conn.commit()
        cid = cur.lastrowid
        async with self.conn.execute(
            "SELECT * FROM comments WHERE id=?", (cid,),
        ) as q:
            row = await q.fetchone()
        return _row_to_comment(row)

    async def list_comments(
        self,
        *,
        org_id: str,
        report_id: str | None = None,
        cell_id: str | None = None,
        workflow_id: int | None = None,
        resolved: bool | None = None,
    ) -> list[CommentModel]:
        clauses: list[str] = ["org_id=?"]
        args: list[Any] = [org_id]
        if report_id:
            clauses.append("report_id=?")
            args.append(report_id)
        if cell_id:
            clauses.append("cell_id=?")
            args.append(cell_id)
        if workflow_id is not None:
            clauses.append("workflow_id=?")
            args.append(workflow_id)
        if resolved is not None:
            clauses.append("resolved=?")
            args.append(1 if resolved else 0)
        async with self.conn.execute(
            f"SELECT * FROM comments WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at ASC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_comment(r) for r in rows]

    async def resolve_comment(
        self,
        *,
        comment_id: int,
        actor_id: str,
        expected_version: int | None = None,
    ) -> CommentModel:
        """Resolve a comment.

        Optimistic-lock contract (P2-6, hardened in round-2 #1):
        ``expected_version`` is now **mandatory**.  Callers MUST first
        GET the comment, capture ``comment.version``, and pass it back
        on the resolve call.  Missing the token short-circuits with HTTP
        409 ``missing_expected_version`` — the silent-overwrite race
        flagged in M3 audit §2.4 is now structurally impossible because
        every UPDATE goes through ``WHERE id=? AND version=?`` and the
        opt-in fallback has been deleted.  Already-resolved comments are
        still idempotent (no-op return) so retries stay safe.
        """
        if expected_version is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "missing_expected_version",
                    "comment_id": comment_id,
                    "detail": (
                        "expected_version is required on every resolve_comment "
                        "call.  Fetch the comment first to obtain its current "
                        "version then retry."
                    ),
                },
            )
        async with self.conn.execute(
            "SELECT * FROM comments WHERE id=?", (comment_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="comment not found")
        if not await self.collab.check_permission(
            user_id=actor_id,
            resource="comment",
            action="write",
            org_id=row["org_id"],
        ):
            raise HTTPException(
                status_code=403, detail=f"user {actor_id!r} cannot resolve comments"
            )
        if row["resolved"]:
            return _row_to_comment(row)
        current_version = int(row["version"] or 1)
        if expected_version != current_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "comment_id": comment_id,
                    "expected_version": expected_version,
                    "current_version": current_version,
                },
            )
        now = _utcnow_iso()
        cur = await self.conn.execute(
            "UPDATE comments SET resolved=1, resolved_by=?, resolved_at=?, "
            "updated_at=?, version=version+1 WHERE id=? AND version=?",
            (actor_id, now, now, comment_id, current_version),
        )
        rowcount = cur.rowcount
        await cur.close()
        if rowcount == 0:
            # Another resolver beat us between SELECT and UPDATE.
            async with self.conn.execute(
                "SELECT version, resolved FROM comments WHERE id=?",
                (comment_id,),
            ) as cur:
                live = await cur.fetchone()
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "comment_id": comment_id,
                    "expected_version": expected_version,
                    "current_version": int(live["version"])
                    if live else None,
                },
            )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT * FROM comments WHERE id=?", (comment_id,),
        ) as cur:
            updated = await cur.fetchone()
        return _row_to_comment(updated)


__all__ = [
    "ReviewWorkflowService",
    "_row_to_comment",
    "_row_to_workflow",
]
