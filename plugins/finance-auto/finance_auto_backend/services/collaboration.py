"""User registry + RBAC + assignment service (v0.3 Part Biz §1).

This service is the *single source of truth* for the four-role permission
matrix introduced in M2 Stage 2.  It owns three concerns:

1. **User registry** — create / list / fetch ``users`` rows.  Roles are
   ``auditor`` / ``manager`` / ``partner`` / ``admin``.
2. **Assignment** — bind a user to a (org, period?, role_in_project)
   triple.  ``period_id = None`` means "整账套".
3. **Permission check** — given ``(user_id, resource, action, target)``,
   answer ``bool``.  Uses the seeded ``permissions`` table plus the user's
   role and current assignments.

The route layer keeps the v0.2 "single local user" semantics on the
out-going API (every write still defaults to ``decided_by='local'``); but
once a real ``user_id`` is passed in (via header / future session
middleware), the same service answers ``check_permission`` correctly.
This is the upgrade path called out in Part Biz §1.4.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import HTTPException

from ..models import (
    AssignmentModel,
    ProjectRole,
    UserCreateRequest,
    UserModel,
    UserRole,
)

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Row → Pydantic converters
# ---------------------------------------------------------------------------


def _row_to_user(row: Any) -> UserModel:
    return UserModel(
        user_id=row["user_id"],
        display_name=row["display_name"],
        role=row["role"],
        email=row["email"] or "",
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=int(row["version"] or 1),
    )


def _row_to_assignment(row: Any) -> AssignmentModel:
    return AssignmentModel(
        id=int(row["id"]),
        user_id=row["user_id"],
        org_id=row["org_id"],
        period_id=row["period_id"],
        role_in_project=row["role_in_project"],
        assigned_at=row["assigned_at"],
        assigned_by=row["assigned_by"] or "local",
        revoked_at=row["revoked_at"],
        version=int(row["version"] or 1),
    )


# ---------------------------------------------------------------------------
# CollaborationService
# ---------------------------------------------------------------------------


# Role hierarchy: partner (4) > manager (3) > auditor (2) > admin (1) but
# admin is special (system-only actions).  We use this map only to compare
# the *seniority* of a user's role vs another user's role in the same org;
# the actual action permission is looked up from the ``permissions`` table.
_ROLE_RANK: dict[str, int] = {"admin": 1, "auditor": 2, "manager": 3, "partner": 4}


class CollaborationService:
    """Async, stateless wrapper around an ``aiosqlite.Connection``."""

    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    # ----- users ----------------------------------------------------------

    async def register_user(self, payload: UserCreateRequest) -> UserModel:
        now = _utcnow_iso()
        try:
            await self.conn.execute(
                "INSERT INTO users(user_id, display_name, role, email, active, "
                "created_at, updated_at, version) VALUES (?,?,?,?,?,?,?,?)",
                (
                    payload.user_id,
                    payload.display_name,
                    payload.role,
                    payload.email or "",
                    int(payload.active),
                    now,
                    now,
                    1,
                ),
            )
            await self.conn.commit()
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(
                    status_code=409,
                    detail=f"user_id already exists: {payload.user_id}",
                ) from exc
            raise HTTPException(
                status_code=500, detail=f"register_user failed: {exc}"
            ) from exc
        return await self.get_user(payload.user_id)

    async def list_users(
        self,
        *,
        role: UserRole | None = None,
        active_only: bool = True,
    ) -> list[UserModel]:
        clauses: list[str] = []
        args: list[Any] = []
        if active_only:
            clauses.append("active=1")
        if role:
            clauses.append("role=?")
            args.append(role)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self.conn.execute(
            f"SELECT * FROM users {where} ORDER BY created_at ASC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_user(r) for r in rows]

    async def get_user(self, user_id: str) -> UserModel:
        async with self.conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"user not found: {user_id}"
            )
        return _row_to_user(row)

    async def get_user_or_none(self, user_id: str) -> UserModel | None:
        async with self.conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    # ----- assignments ----------------------------------------------------

    async def assign(
        self,
        *,
        user_id: str,
        org_id: str,
        period_id: str | None,
        role_in_project: ProjectRole,
        assigned_by: str = "local",
    ) -> AssignmentModel:
        # Verify the user exists (fail-fast).
        await self.get_user(user_id)

        now = _utcnow_iso()
        try:
            await self.conn.execute(
                "INSERT INTO assignments(user_id, org_id, period_id, "
                "role_in_project, assigned_at, assigned_by, version) "
                "VALUES (?,?,?,?,?,?,1)",
                (user_id, org_id, period_id, role_in_project, now, assigned_by),
            )
            await self.conn.commit()
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE" in str(exc).upper():
                # Same triple already assigned; reactivate if revoked.
                async with self.conn.execute(
                    "SELECT id FROM assignments WHERE user_id=? AND org_id=? "
                    "AND IFNULL(period_id,'')=IFNULL(?,'') AND role_in_project=?",
                    (user_id, org_id, period_id, role_in_project),
                ) as cur:
                    existing = await cur.fetchone()
                if existing is None:
                    raise HTTPException(
                        status_code=500, detail=f"assign UPSERT lost: {exc}"
                    ) from exc
                aid = int(existing["id"])
                await self.conn.execute(
                    "UPDATE assignments SET revoked_at=NULL, assigned_at=?, "
                    "assigned_by=?, version=version+1 WHERE id=?",
                    (now, assigned_by, aid),
                )
                await self.conn.commit()
            else:
                raise HTTPException(
                    status_code=500, detail=f"assign failed: {exc}"
                ) from exc
        async with self.conn.execute(
            "SELECT * FROM assignments WHERE user_id=? AND org_id=? "
            "AND IFNULL(period_id,'')=IFNULL(?,'') AND role_in_project=?",
            (user_id, org_id, period_id, role_in_project),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_assignment(row)

    async def revoke_assignment(self, *, assignment_id: int) -> AssignmentModel:
        async with self.conn.execute(
            "SELECT * FROM assignments WHERE id=?", (assignment_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        if row["revoked_at"]:
            return _row_to_assignment(row)
        now = _utcnow_iso()
        await self.conn.execute(
            "UPDATE assignments SET revoked_at=?, version=version+1 WHERE id=?",
            (now, assignment_id),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT * FROM assignments WHERE id=?", (assignment_id,),
        ) as cur:
            updated = await cur.fetchone()
        return _row_to_assignment(updated)

    async def list_assignments(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        include_revoked: bool = False,
    ) -> list[AssignmentModel]:
        clauses: list[str] = []
        args: list[Any] = []
        if org_id:
            clauses.append("org_id=?")
            args.append(org_id)
        if user_id:
            clauses.append("user_id=?")
            args.append(user_id)
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self.conn.execute(
            f"SELECT * FROM assignments {where} ORDER BY assigned_at DESC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_assignment(r) for r in rows]

    # ----- permission check ----------------------------------------------

    async def check_permission(
        self,
        *,
        user_id: str,
        resource: str,
        action: str,
        org_id: str | None = None,
        period_id: str | None = None,
    ) -> bool:
        """Return True iff the user is allowed to perform ``action`` on
        ``resource`` (optionally scoped to a specific ``org`` / ``period``).

        Algorithm:

        1. Look up the user; if they don't exist OR are inactive → False.
           (The special seeded user ``'local'`` is treated as ``admin``
           with full access to keep v0.2 single-user mode working.)
        2. Pull the matching ``permissions`` rows for the user's role +
           resource + action.  No row → False.
        3. For each matching row, evaluate ``scope``:

           * ``None`` / ``"all"``     — always True.
           * ``"own"``                — TODO (no concept of "own" yet).
           * ``"assigned"``           — True iff the user has an active
             ``assignments`` row matching the org / period.

        ``period_id=None`` means "整账套"; an "assigned" check passes if
        the user has *any* unrevoked assignment for the org.
        """
        # v0.2 backward compat: 'local' user has full access.
        if user_id == "local":
            return True
        user = await self.get_user_or_none(user_id)
        if user is None or not user.active:
            return False

        async with self.conn.execute(
            "SELECT scope FROM permissions WHERE role=? AND resource=? AND action=?",
            (user.role, resource, action),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return False
        for row in rows:
            scope = row["scope"]
            if scope in (None, "", "all"):
                return True
            if scope == "assigned":
                if not org_id:
                    # Without an org context, an "assigned" grant is too
                    # narrow to evaluate → deny.
                    continue
                clauses = ["user_id=?", "org_id=?", "revoked_at IS NULL"]
                args: list[Any] = [user_id, org_id]
                if period_id:
                    clauses.append(
                        "(period_id IS NULL OR period_id=?)"
                    )
                    args.append(period_id)
                where = " AND ".join(clauses)
                async with self.conn.execute(
                    f"SELECT 1 FROM assignments WHERE {where} LIMIT 1",
                    tuple(args),
                ) as cur:
                    if await cur.fetchone():
                        return True
            if scope == "own":
                # Reserved for future use; nothing maps to "own" yet.
                continue
        return False

    # ----- role hierarchy convenience ------------------------------------

    @staticmethod
    def role_rank(role: str) -> int:
        return _ROLE_RANK.get(role, 0)


__all__ = [
    "CollaborationService",
    "_row_to_assignment",
    "_row_to_user",
]
