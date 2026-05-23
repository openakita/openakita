"""Inbox + Scaling + Reports + Stats + Status (P-RC-9 P9.7beta-5).

Mints cluster 3.5 of ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
-- 14 endpoints (B54-B67) covering org inbox CRUD, scaling
governance (requests / approve / reject / clone / recruit),
status snapshot, stats aggregation, and reports list /
summary / generate.

Wiring matrix:

* inbox -> :class:`OrgRuntime.get_inbox(org_id)` (P9.6).
* scaling -> ``OrgRuntime.get_scaler()`` (P9.6 sibling; duck-typed).
* status / stats -> :class:`OrgRuntime` duck-typed methods
  (``get_status_snapshot`` / ``get_stats``); integration with
  the existing P9.6 ``NodeStatusController`` + ``OrgManager``
  stat aggregators lands in P9.7gamma.
* reports list -> ``OrgManager.get_org_dir`` file IO; summary
  + generate -> ``OrgRuntime.get_event_store(org_id)``.

Divergence from v1 (charter R5 risk acknowledgment):

* B63 v1 ``GET /{org_id}/status`` returns SSE for real-time
  status streaming. The v2 mint ships a JSON snapshot envelope
  (``rt.get_status_snapshot(org_id)``); SSE streaming rides
  P9.7beta-7 (optional) per charter section 3.

ADR refs: ADR-0011 (D-3 layer separation; D-4 R4 granularity
ceiling preserved), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from .orgs_v2_runtime import (
    _get_manager,
    _get_runtime,
    _runtime_method_not_wired,
    _subsystem_unavailable,
    router,
)

logger = logging.getLogger(__name__)

_VALID_DECISIONS = {"approve", "reject"}


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# B54-B57: inbox
# ---------------------------------------------------------------------------


@router.get("/{org_id}/inbox", summary="B54 list inbox messages")
def list_inbox(request: Request, org_id: str) -> dict[str, Any]:
    inbox = _get_runtime(request).get_inbox(org_id)
    if inbox is None:
        return {"messages": [], "unread_count": 0, "pending_approvals": 0}
    qp = request.query_params
    messages = inbox.list_messages(
        org_id,
        unread_only=qp.get("unread_only", "").lower() == "true",
        category=qp.get("category"),
        pending_approval_only=qp.get("pending_approval", "").lower() == "true",
        limit=_safe_int(qp.get("limit"), 50),
        offset=_safe_int(qp.get("offset"), 0),
    )
    return {
        "messages": [m.to_dict() if hasattr(m, "to_dict") else m for m in messages],
        "unread_count": inbox.unread_count(org_id),
        "pending_approvals": inbox.pending_approval_count(org_id),
    }


@router.post("/{org_id}/inbox/{msg_id}/read", summary="B55 mark inbox message read")
def mark_inbox_read(request: Request, org_id: str, msg_id: str) -> dict[str, Any]:
    inbox = _get_runtime(request).get_inbox(org_id)
    if inbox is None or not inbox.mark_read(org_id, msg_id):
        raise HTTPException(404, "Message not found or already read")
    return {"ok": True}


@router.post("/{org_id}/inbox/read-all", summary="B56 mark all inbox messages read")
def mark_all_inbox_read(request: Request, org_id: str) -> dict[str, Any]:
    inbox = _get_runtime(request).get_inbox(org_id)
    count = inbox.mark_all_read(org_id) if inbox is not None else 0
    return {"marked": count}


@router.post("/{org_id}/inbox/{msg_id}/resolve", summary="B57 resolve approval")
async def resolve_inbox_approval(request: Request, org_id: str, msg_id: str) -> dict[str, Any]:
    inbox = _get_runtime(request).get_inbox(org_id)
    if inbox is None:
        raise HTTPException(404, "Inbox not found")
    body = await request.json()
    decision = body.get("decision", "").strip().lower()
    if decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"Invalid decision. Must be one of: {sorted(_VALID_DECISIONS)}")
    msg = inbox.resolve_approval(org_id, msg_id, decision, by="user")
    if not msg:
        raise HTTPException(404, "Message not found or not an approval")
    return msg.to_dict() if hasattr(msg, "to_dict") else msg


# ---------------------------------------------------------------------------
# B58-B62: scaling
# ---------------------------------------------------------------------------


def _get_scaler(request: Request) -> Any:
    """Lift the scaler sibling off the runtime; 503 if not wired.

    The Scaler is a runtime-level subsystem (not a single duck-typed
    method), so the structured 503 uses the bare subsystem envelope
    rather than the ``runtime_method:<name>`` shape. Frontend can
    branch on ``detail.subsystem == "scaler"`` for scaling panels.
    """
    rt = _get_runtime(request)
    fn = getattr(rt, "get_scaler", None)
    scaler = fn() if callable(fn) else None
    if scaler is None:
        raise _subsystem_unavailable("scaler", "Scaler")
    return scaler


@router.get("/{org_id}/scaling/requests", summary="B58 list scaling requests")
def list_scaling_requests(request: Request, org_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": getattr(r, "id", None),
            "type": getattr(r, "request_type", None),
            "requester": getattr(r, "requester_node_id", None),
            "role_title": getattr(r, "role_title", None),
            "status": getattr(r, "status", None),
            "created_at": getattr(r, "created_at", None),
        }
        for r in _get_scaler(request).get_pending_requests(org_id)
    ]


@router.post("/{org_id}/scaling/{request_id}/approve", summary="B59 approve scaling")
async def approve_scaling(request: Request, org_id: str, request_id: str) -> dict[str, Any]:
    try:
        req = await _get_scaler(request).approve_request(org_id, request_id, approved_by="user")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": getattr(req, "id", None),
        "status": getattr(req, "status", None),
        "result_node_id": getattr(req, "result_node_id", None),
    }


@router.post("/{org_id}/scaling/{request_id}/reject", summary="B60 reject scaling")
async def reject_scaling(request: Request, org_id: str, request_id: str) -> dict[str, Any]:
    scaler = _get_scaler(request)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    try:
        req = scaler.reject_request(
            org_id, request_id, rejected_by="user", reason=body.get("reason", "")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": getattr(req, "id", None), "status": getattr(req, "status", None)}


@router.post("/{org_id}/scale/clone", summary="B61 scale by clone")
async def scale_clone(request: Request, org_id: str) -> dict[str, Any]:
    body = await request.json()
    source = body.get("source_node_id")
    if not source:
        raise HTTPException(400, "source_node_id is required")
    try:
        req = await _get_scaler(request).request_clone(
            org_id=org_id,
            requester="user",
            source_node_id=source,
            reason=body.get("reason", "manual clone"),
            ephemeral=body.get("ephemeral", True),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": getattr(req, "id", None),
        "status": getattr(req, "status", None),
        "result_node_id": getattr(req, "result_node_id", None),
    }


@router.post("/{org_id}/scale/recruit", summary="B62 scale by recruit")
async def scale_recruit(request: Request, org_id: str) -> dict[str, Any]:
    body = await request.json()
    role = body.get("role_title")
    parent = body.get("parent_node_id")
    if not role or not parent:
        raise HTTPException(400, "role_title and parent_node_id are required")
    try:
        req = _get_scaler(request).request_recruit(
            org_id=org_id,
            requester="user",
            role_title=role,
            role_goal=body.get("role_goal", ""),
            department=body.get("department", ""),
            parent_node_id=parent,
            reason=body.get("reason", "manual recruit"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": getattr(req, "id", None), "status": getattr(req, "status", None)}


# ---------------------------------------------------------------------------
# B63-B67: status / stats / reports
# ---------------------------------------------------------------------------


@router.get("/{org_id}/status", summary="B63 org status snapshot (JSON; SSE rides beta-7)")
def get_org_status(request: Request, org_id: str) -> dict[str, Any]:
    """v2 ships JSON snapshot envelope; SSE streaming is P9.7beta-7 (optional)."""
    rt = _get_runtime(request)
    snap = getattr(rt, "get_status_snapshot", None)
    if not callable(snap):
        raise _runtime_method_not_wired("get_status_snapshot")
    payload = snap(org_id)
    if payload is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return payload


@router.get("/{org_id}/stats", summary="B64 org runtime statistics")
def get_org_stats(request: Request, org_id: str) -> dict[str, Any]:
    rt = _get_runtime(request)
    fn = getattr(rt, "get_stats", None)
    if not callable(fn):
        raise _runtime_method_not_wired("get_stats")
    payload = fn(org_id)
    if payload is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return payload


@router.get("/{org_id}/reports", summary="B65 list report files")
def list_reports(request: Request, org_id: str) -> list[dict[str, Any]]:
    from pathlib import Path

    rdir = Path(_get_manager(request).get_org_dir(org_id)) / "reports"
    if not rdir.is_dir():
        return []
    return [
        {
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in sorted(rdir.glob("*.md"), reverse=True)
    ]


@router.get("/{org_id}/reports/summary", summary="B66 report summary")
def get_report_summary(request: Request, org_id: str) -> dict[str, Any]:
    es = _get_runtime(request).get_event_store(org_id)
    if es is None:
        return {"summary": "", "days": 0}
    days = _safe_int(request.query_params.get("days"), 7)
    fn = getattr(es, "generate_summary_report", None)
    if not callable(fn):
        return {"summary": "", "days": days}
    return fn(days=days)


@router.post("/{org_id}/reports/generate", summary="B67 generate report markdown")
async def generate_report(request: Request, org_id: str) -> dict[str, Any]:
    es = _get_runtime(request).get_event_store(org_id)
    if es is None:
        raise HTTPException(503, "Event store unavailable")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    days = body.get("days", 7)
    fn = getattr(es, "generate_report_markdown", None)
    if not callable(fn):
        raise _runtime_method_not_wired("generate_report_markdown")
    report_path = fn(days=days)
    return {"path": str(report_path), "ok": True}
