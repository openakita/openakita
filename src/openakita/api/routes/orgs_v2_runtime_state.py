"""Memory + Events + Activity + Messages + audit + Policies (P-RC-9 P9.7beta-4).

Mints cluster 3.4 of ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
-- 12 endpoints (B42-B53) covering blackboard memory CRUD,
event-store queries (events / activity / messages / audit),
and policy markdown CRUD.

Wiring matrix:

* memory CRUD -> :class:`OrgBlackboard` (P9.1) via the
  ``_get_blackboard`` helper. Reads use ``query`` /
  ``read_org`` / ``read_department`` / ``read_node``; writes
  use the scope-typed ``write_org`` / ``write_department`` /
  ``write_node``; deletes use ``delete_entry``.
* events / activity / messages / audit ->
  :class:`OrgRuntime.get_event_store` (P9.6) + ``get_org_dir``
  on :class:`OrgManager` (P9.5) for the JSONL communication log.
* policies -> :class:`OrgManager.get_org_dir` (file IO under
  ``<org_dir>/policies/``).

ADR refs: ADR-0011 (D-3 layer separation), ADR-0012 (no shim
under v1; ``MemoryScope`` / ``MemoryType`` enums imported from
``openakita.runtime.orgs`` not v1 ``openakita.orgs``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from .orgs_v2_runtime import _get_blackboard, _get_manager, _get_runtime, router

logger = logging.getLogger(__name__)


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _org_dir(mgr: Any, org_id: str) -> Path:
    return Path(mgr.get_org_dir(org_id))


# ---------------------------------------------------------------------------
# B42-B44: blackboard memory CRUD
# ---------------------------------------------------------------------------


@router.get("/{org_id}/memory", summary="B42 query org memory")
def query_memory(request: Request, org_id: str) -> list[dict[str, Any]]:
    from openakita.runtime.orgs import MemoryScope, MemoryType

    bb = _get_blackboard(request)
    qp = request.query_params
    scope = qp.get("scope")
    memory_type = qp.get("type")
    tag = qp.get("tag")
    limit = _safe_int(qp.get("limit"), 50)
    try:
        scope_enum = MemoryScope(scope) if scope else None
        type_enum = MemoryType(memory_type) if memory_type else None
    except ValueError as exc:
        raise HTTPException(400, f"Invalid scope or memory_type: {exc}") from exc
    entries = bb.query(scope=scope_enum, memory_type=type_enum, tag=tag, limit=limit)
    return [e.to_dict() if hasattr(e, "to_dict") else e for e in entries]


@router.post("/{org_id}/memory", status_code=201, summary="B43 add memory entry")
async def add_memory(request: Request, org_id: str) -> dict[str, Any]:
    from openakita.runtime.orgs import MemoryScope, MemoryType

    bb = _get_blackboard(request)
    body = await request.json()
    try:
        scope = MemoryScope(body.get("scope", "org"))
        mt = MemoryType(body.get("memory_type", "fact"))
    except ValueError as exc:
        raise HTTPException(400, f"Invalid scope or memory_type: {exc}") from exc
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content is required")
    kwargs = {
        "memory_type": mt,
        "tags": body.get("tags", []),
        "importance": body.get("importance", 0.5),
    }
    if scope == MemoryScope.ORG:
        entry = bb.write_org(content, source_node="user", **kwargs)
    elif scope == MemoryScope.DEPARTMENT:
        dept = body.get("scope_owner")
        if not dept:
            raise HTTPException(400, "scope_owner required for department scope")
        entry = bb.write_department(dept, content, "user", **kwargs)
    else:
        node_id = body.get("scope_owner")
        if not node_id:
            raise HTTPException(400, "scope_owner required for node scope")
        entry = bb.write_node(node_id, content, **kwargs)
    return entry.to_dict() if hasattr(entry, "to_dict") else entry


@router.delete("/{org_id}/memory/{memory_id}", summary="B44 delete memory entry")
def delete_memory(request: Request, org_id: str, memory_id: str) -> dict[str, Any]:
    if not _get_blackboard(request).delete_entry(memory_id):
        raise HTTPException(404, f"Memory entry not found: {memory_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B45-B48: events + activity + messages + audit
# ---------------------------------------------------------------------------


@router.get("/{org_id}/events", summary="B45 query event store")
def query_events(request: Request, org_id: str) -> list[dict[str, Any]]:
    es = _get_runtime(request).get_event_store(org_id)
    if es is None:
        raise HTTPException(404, f"Event store not found for org: {org_id}")
    qp = request.query_params
    return es.query(
        event_type=qp.get("event_type"),
        actor=qp.get("actor"),
        since=qp.get("since"),
        until=qp.get("until"),
        chain_id=qp.get("chain_id"),
        task_id=qp.get("task_id"),
        limit=_safe_int(qp.get("limit"), 100),
    )


@router.get("/{org_id}/activity", summary="B46 unified activity feed")
def query_activity(request: Request, org_id: str) -> dict[str, Any]:
    """Thin envelope returning event-store entries; full v1 merge (events +
    comm log + command_service rows) rides P9.7gamma when the contract
    suite pins the cross-source merge semantics."""
    es = _get_runtime(request).get_event_store(org_id)
    if es is None:
        return {"items": [], "count": 0}
    qp = request.query_params
    limit = max(1, min(_safe_int(qp.get("limit"), 100), 500))
    events = es.query(since=qp.get("since"), limit=limit) or []
    return {"items": events, "count": len(events)}


@router.get("/{org_id}/messages", summary="B47 list inter-node messages")
def query_messages(request: Request, org_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    comm_log = _org_dir(mgr, org_id) / "logs" / "communications.jsonl"
    if not comm_log.is_file():
        return {"messages": [], "count": 0}
    qp = request.query_params
    limit = _safe_int(qp.get("limit"), 100)
    from_node = qp.get("from_node")
    to_node = qp.get("to_node")
    messages: list[dict[str, Any]] = []
    for line in reversed(comm_log.read_text(encoding="utf-8").strip().split("\n")):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if from_node and msg.get("from_node") != from_node:
            continue
        if to_node and msg.get("to_node") != to_node:
            continue
        messages.append(msg)
        if len(messages) >= limit:
            break
    return {"messages": messages, "count": len(messages)}


@router.get("/{org_id}/audit-log", summary="B48 audit log entries")
def get_audit_log(request: Request, org_id: str) -> list[dict[str, Any]]:
    es = _get_runtime(request).get_event_store(org_id)
    if es is None:
        return []
    days = _safe_int(request.query_params.get("days"), 7)
    audit = getattr(es, "get_audit_log", None)
    if audit is None:
        return []
    return audit(days=days)


# ---------------------------------------------------------------------------
# B49-B53: policies CRUD (file IO under <org_dir>/policies/)
# ---------------------------------------------------------------------------


@router.get("/{org_id}/policies", summary="B49 list policy files")
def list_policies(request: Request, org_id: str) -> list[dict[str, Any]]:
    pdir = _org_dir(_get_manager(request), org_id) / "policies"
    if not pdir.is_dir():
        return []
    return [{"filename": f.name, "size": f.stat().st_size} for f in sorted(pdir.glob("*.md"))]


@router.get("/{org_id}/policies/search", summary="B50 search policies")
def search_policies(request: Request, org_id: str) -> list[dict[str, Any]]:
    query = request.query_params.get("q", "")
    if not query:
        raise HTTPException(400, "Query parameter 'q' is required")
    rt = _get_runtime(request)
    policies = getattr(rt, "get_policies", lambda _oid: None)(org_id)
    if policies is None or not hasattr(policies, "search"):
        return []
    return policies.search(query)


@router.get("/{org_id}/policies/{filename}", summary="B51 read policy file")
def read_policy(request: Request, org_id: str, filename: str) -> dict[str, Any]:
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    p = _org_dir(_get_manager(request), org_id) / "policies" / filename
    if not p.is_file():
        raise HTTPException(404, f"Policy not found: {filename}")
    return {"filename": filename, "content": p.read_text(encoding="utf-8")}


@router.put("/{org_id}/policies/{filename}", summary="B52 write policy file")
async def write_policy(request: Request, org_id: str, filename: str) -> dict[str, Any]:
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    body = await request.json()
    content = body.get("content", "")
    p = _org_dir(_get_manager(request), org_id) / "policies" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True}


@router.delete("/{org_id}/policies/{filename}", summary="B53 delete policy file")
def delete_policy(request: Request, org_id: str, filename: str) -> dict[str, Any]:
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    p = _org_dir(_get_manager(request), org_id) / "policies" / filename
    if not p.is_file():
        raise HTTPException(404, f"Policy not found: {filename}")
    p.unlink()
    return {"ok": True}
