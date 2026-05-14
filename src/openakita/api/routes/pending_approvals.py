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
    if not entry.is_active():
        # Idempotent: already resolved/expired — return current state.
        return JSONResponse(
            {"status": "already_resolved", "entry": entry.to_dict()},
            status_code=200,
        )

    updated = store.resolve(
        pending_id,
        decision=body.decision,
        resolved_by=body.resolved_by,
        note=body.note,
    )
    if updated is None:
        # Race: entry vanished between get() and resolve(). Surface as 409.
        raise HTTPException(status_code=409, detail="entry vanished mid-resolve")

    follow_up: dict[str, Any] = {"task_resumed": False, "task_failed": False}

    # Resume / fail the linked scheduled task, if any
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
            if body.decision == "allow":
                follow_up = await _resume_task(scheduler, updated)
            else:
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


async def _resume_task(scheduler: Any, entry: Any) -> dict[str, Any]:
    """Inject ReplayAuthorization into session metadata + reschedule task.

    The replay authorization carries:
    - ``expires_at = now + 30s``
    - ``original_message`` = the tool_name (PolicyEngineV2 step 7 matches
      against ctx.user_message; tool_name is a deterministic anchor)
    - ``operation`` = ApprovalClass value (so step 7 matches by category)

    The scheduler's existing ``trigger_in_background`` path is the reuse
    point — we set ``task.next_run = now + advance_seconds`` and let the
    scheduler loop pick it up on the next tick.
    """
    from datetime import datetime, timedelta

    from openakita.scheduler.task import TaskStatus

    task = None
    try:
        task = scheduler._tasks.get(entry.task_id)
    except AttributeError:
        pass
    if task is None:
        return {"task_resumed": False, "reason": f"task {entry.task_id!r} not found"}

    if task.status != TaskStatus.AWAITING_APPROVAL:
        return {
            "task_resumed": False,
            "reason": f"task in unexpected state {task.status.value}",
        }

    # Stamp replay authorization in task metadata so executor can lift it
    # into PolicyContext.replay_authorizations on next run. Stored as raw
    # dict (PolicyContext._coerce_replay_auths converts on the way in).
    if not isinstance(task.metadata, dict):
        task.metadata = {}
    auths = list(task.metadata.get("replay_authorizations", []))
    # Prefer the captured user_message (scheduler ctx.user_message at deferral
    # time == task.prompt), so engine step 7 matches by equality on rerun.
    # Fallback to tool_name when older entries on disk lack user_message.
    original_msg = (entry.user_message or "").strip() or entry.tool_name
    auths.append(
        {
            "expires_at": time.time() + REPLAY_TTL_SECONDS,
            "original_message": original_msg,
            "confirmation_id": entry.id,
            # Operation field is best-effort hint for engine step 7 secondary
            # match; equality on user_message above is the primary path.
            "operation": "",
        }
    )
    task.metadata["replay_authorizations"] = auths
    task.metadata.pop("awaiting_approval_marker", None)
    task.metadata["resumed_from_approval_at"] = time.time()
    task.metadata["resumed_from_approval_id"] = entry.id

    # Transition AWAITING_APPROVAL → SCHEDULED + immediate next_run
    task.status = TaskStatus.SCHEDULED
    task.next_run = datetime.now() + timedelta(seconds=2)  # next tick
    task.updated_at = datetime.now()

    # Persist the task state change
    try:
        async with scheduler._lock:
            scheduler._save_tasks()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[pending_approvals] task save failed after resume: %s — "
            "in-memory state still updated, will persist on next save",
            exc,
        )

    return {
        "task_resumed": True,
        "next_run": task.next_run.isoformat(),
        "replay_ttl_seconds": REPLAY_TTL_SECONDS,
    }


async def _fail_task(scheduler: Any, entry: Any) -> dict[str, Any]:
    """Mark linked task as FAILED with the deny reason."""
    from openakita.scheduler.task import TaskStatus

    task = scheduler._tasks.get(entry.task_id) if hasattr(scheduler, "_tasks") else None
    if task is None:
        return {"task_failed": False, "reason": f"task {entry.task_id!r} not found"}
    if task.status != TaskStatus.AWAITING_APPROVAL:
        return {"task_failed": False, "reason": f"unexpected state {task.status.value}"}

    # Direct transition AWAITING_APPROVAL → FAILED (legal per task.py state machine)
    task.status = TaskStatus.FAILED
    task.updated_at = task.last_run = (task.last_run or _now_dt())
    if not isinstance(task.metadata, dict):
        task.metadata = {}
    task.metadata["last_error"] = (
        f"Owner denied pending approval {entry.id}; note={entry.note or '-'}"
    )
    task.metadata.pop("awaiting_approval_marker", None)

    try:
        async with scheduler._lock:
            scheduler._save_tasks()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[pending_approvals] task save failed after deny: %s",
            exc,
        )
    return {"task_failed": True}


def _now_dt():
    from datetime import datetime

    return datetime.now()
