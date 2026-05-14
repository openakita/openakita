"""C12 §14.5 — pending_approvals HTTP API.

Endpoints:

- ``GET /api/pending_approvals``                — list active (status==pending)
- ``GET /api/pending_approvals?include=all``    — list all in-memory entries
- ``GET /api/pending_approvals/stats``          — counts by status
- ``GET /api/pending_approvals/{id}``           — single entry
- ``POST /api/pending_approvals/{id}/resolve``  — owner allow/deny + resume task

The resolve endpoint is the owner-side trigger for R3-5 "approve & re-run +
30s replay": when ``decision="allow"`` the underlying scheduled task (if any)
gets a ``ReplayAuthorization`` written to its session metadata and is
re-scheduled to run immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _store():
    from openakita.core.pending_approvals import get_pending_approvals_store

    return get_pending_approvals_store()


def _scheduler(request: Request):
    """Same lookup as scheduler.py — agent or local_agent has ``task_scheduler``."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    if hasattr(agent, "task_scheduler"):
        return agent.task_scheduler
    local = getattr(agent, "_local_agent", None)
    if local and hasattr(local, "task_scheduler"):
        return local.task_scheduler
    return None


def _serialize(entry: Any) -> dict[str, Any]:
    """Drop heavy fields (decision_chain / decision_meta) from the default
    list view — the detail endpoint returns the full entry."""
    d = entry.to_dict()
    return {
        k: v
        for k, v in d.items()
        if k not in ("decision_chain", "decision_meta")
    }


# ----------------------------------------------------------------------------
# GET endpoints
# ----------------------------------------------------------------------------


@router.get("/api/pending_approvals")
async def list_pending(include: str = "active") -> JSONResponse:
    """List pending approvals.

    Query: ?include=active (default) returns only status=='pending';
           ?include=all returns everything in-memory (incl. resolved/expired
           entries that haven't been archived yet).
    """
    store = _store()
    if include == "all":
        entries = store.list_all()
    else:
        entries = store.list_active()
    return JSONResponse(
        {
            "entries": [_serialize(e) for e in entries],
            "count": len(entries),
        }
    )


@router.get("/api/pending_approvals/stats")
async def stats() -> JSONResponse:
    return JSONResponse(_store().stats())


@router.get("/api/pending_approvals/{pending_id}")
async def get_pending(pending_id: str) -> JSONResponse:
    entry = _store().get(pending_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"pending_id {pending_id!r} not found")
    return JSONResponse(entry.to_dict())


# ----------------------------------------------------------------------------
# POST resolve
# ----------------------------------------------------------------------------


class ResolveBody(BaseModel):
    decision: Literal["allow", "deny"]
    resolved_by: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=2000)


@router.post("/api/pending_approvals/{pending_id}/resolve")
async def resolve_pending(
    pending_id: str, body: ResolveBody, request: Request
) -> JSONResponse:
    """C12 §14.5 + R3-5: owner approves or denies a pending tool call.

    On ``decision="allow"`` for a scheduled task, this also:

    1. Writes a 30-second ``ReplayAuthorization`` to the session metadata
       so when the task re-runs, the same ``tool_name`` + ``params`` hits
       the engine's step 7 ``replay`` shortcut and gets ALLOW without the
       owner being re-prompted.
    2. Transitions the scheduled task back to SCHEDULED + immediate next_run
       (within ``advance_seconds``) so the scheduler loop picks it up next tick.

    On ``decision="deny"`` the task is marked FAILED with the deny reason —
    no auto-disable bump because the failure was deliberate, not a runtime
    error.
    """
    store = _store()
    entry = store.get(pending_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"pending_id {pending_id!r} not found")
    was_active = entry.is_active()

    # ``store.resolve`` is idempotent: if status is already non-pending
    # it returns the existing entry untouched. We *always* call it and
    # then route follow-up by the ACTUAL final state (``updated.resolution``),
    # not by our request body. This closes the concurrent-resolve race:
    # two POSTs with different decisions both see is_active() == True,
    # but only the first actor wins inside store.resolve(); the second
    # sees the winner's resolution and routes follow-up consistently
    # (no double resume/fail on the same task).
    updated = store.resolve(
        pending_id,
        decision=body.decision,
        resolved_by=body.resolved_by,
        note=body.note,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="entry vanished mid-resolve")

    # Detect "we lost the race" — the entry was already resolved by
    # somebody else (or was already non-active when we entered). Return
    # the AUTHORITATIVE state and do NOT trigger follow-up (the original
    # actor's request handler already did).
    if not was_active:
        return JSONResponse(
            {
                "status": "already_resolved",
                "entry": updated.to_dict(),
                "follow_up": {"task_resumed": False, "task_failed": False},
            },
            status_code=200,
        )

    follow_up: dict[str, Any] = {"task_resumed": False, "task_failed": False}

    if updated.task_id:
        scheduler = _scheduler(request)
        if scheduler is None:
            logger.warning(
                "[pending_approvals] resolved %s but scheduler unavailable; "
                "task %s NOT auto-resumed",
                pending_id,
                updated.task_id,
            )
        else:
            # Branch on the AUTHORITATIVE resolution, not the request body.
            # Guards against a stale request body (e.g., owner clicked
            # "allow" then "deny" milliseconds apart — only the first
            # store.resolve writes; the second body is moot).
            if updated.resolution == "allow":
                follow_up = await _resume_task(scheduler, updated)
            elif updated.resolution == "deny":
                follow_up = await _fail_task(scheduler, updated)

    return JSONResponse(
        {
            "status": "ok",
            "entry": updated.to_dict(),
            "follow_up": follow_up,
        }
    )


# ----------------------------------------------------------------------------
# Resume / fail task helpers (R3-5)
# ----------------------------------------------------------------------------


REPLAY_TTL_SECONDS = 30.0  # plan §14.7: 30 second replay window


def _prune_expired_replay_auths(raw: Any, *, now: float) -> list[dict[str, Any]]:
    """Drop expired entries from ``task.metadata.replay_authorizations``.

    Bounded growth: a long-lived task (daily cron, e.g.) accumulates one
    auth per approval. Without pruning, the list grows monotonically
    forever, even though engine step 7 ignores expired ones. Pruning at
    write time keeps the persisted JSON tidy AND keeps the lifted
    PolicyContext list short (engine iterates all entries per call).
    """
    if not isinstance(raw, list):
        return []
    kept: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            exp = float(item.get("expires_at", 0))
        except (TypeError, ValueError):
            continue
        if exp > now:
            kept.append(item)
    return kept


async def _resume_task(scheduler: Any, entry: Any) -> dict[str, Any]:
    """Inject ReplayAuthorization into task metadata + reschedule task.

    Concurrency model:
    - Acquire ``scheduler._lock`` FIRST, then mutate + persist atomically.
      Mutating task.status/next_run before taking the lock leaks an
      intermediate state to a concurrent scheduler tick (which could
      observe SCHEDULED + past next_run and try to run while we're still
      writing).
    - Re-check state under lock — a tick that already advanced the task
      out of AWAITING_APPROVAL means we lost the race; bail with no
      mutation.

    Replay payload (consumed by engine step 7 ``_check_replay_authorization``):
    - ``expires_at = now + 30s``
    - ``original_message`` = entry.user_message (== task.prompt at the
      time of deferral), so engine step 7 matches by user_message equality.
      Falls back to entry.tool_name only for legacy entries on disk that
      predate the user_message capture.
    - ``operation`` = "" (engine step 7 primary match is by message;
      operation match is secondary and OperationKind dictionary differs
      from ApprovalClass enum so we don't try).
    """
    from datetime import datetime, timedelta

    from openakita.scheduler.task import TaskStatus

    try:
        lock = scheduler._lock
    except AttributeError:
        return {"task_resumed": False, "reason": "scheduler has no _lock"}

    now = time.time()
    next_run_at = datetime.now() + timedelta(seconds=2)

    async with lock:
        task = scheduler._tasks.get(entry.task_id) if hasattr(scheduler, "_tasks") else None
        if task is None:
            return {"task_resumed": False, "reason": f"task {entry.task_id!r} not found"}
        if task.status != TaskStatus.AWAITING_APPROVAL:
            return {
                "task_resumed": False,
                "reason": f"task in unexpected state {task.status.value}",
            }

        if not isinstance(task.metadata, dict):
            task.metadata = {}

        # Prune-then-append keeps the list bounded.
        existing = _prune_expired_replay_auths(
            task.metadata.get("replay_authorizations"), now=now
        )
        original_msg = (entry.user_message or "").strip() or entry.tool_name
        existing.append(
            {
                "expires_at": now + REPLAY_TTL_SECONDS,
                "original_message": original_msg,
                "confirmation_id": entry.id,
                "operation": "",
            }
        )
        task.metadata["replay_authorizations"] = existing
        task.metadata.pop("awaiting_approval_marker", None)
        task.metadata["resumed_from_approval_at"] = now
        task.metadata["resumed_from_approval_id"] = entry.id

        task.status = TaskStatus.SCHEDULED
        task.next_run = next_run_at
        task.updated_at = datetime.now()

        try:
            scheduler._save_tasks()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[pending_approvals] task save failed after resume: %s — "
                "in-memory state updated, will persist on next save",
                exc,
            )

    return {
        "task_resumed": True,
        "next_run": next_run_at.isoformat(),
        "replay_ttl_seconds": REPLAY_TTL_SECONDS,
        "replay_auths_active": len(existing),
    }


async def _fail_task(scheduler: Any, entry: Any) -> dict[str, Any]:
    """Mark linked task as FAILED with the deny reason. Lock-first."""
    from datetime import datetime

    from openakita.scheduler.task import TaskStatus

    try:
        lock = scheduler._lock
    except AttributeError:
        return {"task_failed": False, "reason": "scheduler has no _lock"}

    async with lock:
        task = scheduler._tasks.get(entry.task_id) if hasattr(scheduler, "_tasks") else None
        if task is None:
            return {"task_failed": False, "reason": f"task {entry.task_id!r} not found"}
        if task.status != TaskStatus.AWAITING_APPROVAL:
            return {
                "task_failed": False,
                "reason": f"unexpected state {task.status.value}",
            }

        task.status = TaskStatus.FAILED
        now_dt = datetime.now()
        task.updated_at = now_dt
        task.last_run = task.last_run or now_dt
        if not isinstance(task.metadata, dict):
            task.metadata = {}
        task.metadata["last_error"] = (
            f"Owner denied pending approval {entry.id}; note={entry.note or '-'}"
        )
        task.metadata.pop("awaiting_approval_marker", None)

        try:
            scheduler._save_tasks()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[pending_approvals] task save failed after deny: %s",
                exc,
            )
    return {"task_failed": True}
