"""
Memory management routes: CRUD + LLM review for semantic memories.

Provides HTTP API for the frontend Memory Management Panel.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openakita.memory.retention import apply_retention
from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memory"])

# In-process review task state (single-task, no need for DB persistence)
_review_task: asyncio.Task | None = None
_review_cancel: asyncio.Event | None = None
_review_progress: dict = {}
_review_lock = asyncio.Lock()


def _get_store(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    mm = getattr(agent, "memory_manager", None)
    if mm:
        return mm.store
    local = getattr(agent, "_local_agent", None)
    if local:
        mm = getattr(local, "memory_manager", None)
        if mm:
            return mm.store
    return None


def _get_manager(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    mm = getattr(agent, "memory_manager", None)
    if mm:
        return mm
    local = getattr(agent, "_local_agent", None)
    if local:
        return getattr(local, "memory_manager", None)
    return None


def _sync_json(request: Request):
    """After store mutations, reload manager's in-memory cache and flush to JSON."""
    mm = _get_manager(request)
    if mm and hasattr(mm, "_reload_from_sqlite"):
        mm._reload_from_sqlite()


def _get_lifecycle(request: Request):
    mm = _get_manager(request)
    if not mm:
        return None
    try:
        from openakita.config import settings
        from openakita.memory.lifecycle import LifecycleManager

        return LifecycleManager(
            store=mm.store,
            extractor=mm.extractor,
            identity_dir=settings.identity_path,
        )
    except Exception as e:
        logger.warning(f"Failed to create LifecycleManager: {e}")
        return None


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    importance_score: float | None = None
    tags: list[str] | None = None


class MemoryCreateRequest(BaseModel):
    type: str = "fact"
    content: str
    subject: str = ""
    predicate: str = ""
    importance_score: float = 0.8
    tags: list[str] = []


class ClaimLegacyRequest(BaseModel):
    include_inactive: bool = True
    include_default_graph_nodes: bool = True


IDENTITY_SLOT_ALIASES: dict[str, str] = {
    "姓名": "user.name",
    "名字": "user.name",
    "称呼": "user.name",
    "name": "user.name",
    "年龄": "user.age",
    "age": "user.age",
    "城市": "user.city",
    "所在地": "user.city",
    "位置": "user.city",
    "居住地": "user.city",
    "location": "user.city",
    "city": "user.city",
    "职业": "user.job",
    "工作": "user.job",
    "职位": "user.job",
    "job": "user.job",
    "profession": "user.job",
    "宠物": "user.pet",
    "pet": "user.pet",
}

TASK_LOG_PATTERNS = (
    "本轮",
    "工具调用",
    "调用了",
    "执行了",
    "读取了",
    "创建了文件",
    "写入了文件",
    "测试报告",
    "trace_",
    "llm_request",
    "llm_response",
)


def _serialize(mem: Any) -> dict:
    return {
        "id": mem.id,
        "type": mem.type.value if hasattr(mem.type, "value") else str(mem.type),
        "priority": mem.priority.value if hasattr(mem.priority, "value") else str(mem.priority),
        "content": mem.content,
        "source": mem.source,
        "subject": mem.subject or "",
        "predicate": mem.predicate or "",
        "tags": mem.tags or [],
        "importance_score": mem.importance_score,
        "confidence": mem.confidence,
        "access_count": mem.access_count,
        "created_at": mem.created_at.isoformat() if mem.created_at else None,
        "updated_at": mem.updated_at.isoformat() if mem.updated_at else None,
        "last_accessed_at": mem.last_accessed_at.isoformat() if mem.last_accessed_at else None,
        "expires_at": mem.expires_at.isoformat() if mem.expires_at else None,
        "scope": getattr(mem, "scope", "user"),
        "scope_owner": getattr(mem, "scope_owner", ""),
        "user_id": getattr(mem, "user_id", "default"),
        "workspace_id": getattr(mem, "workspace_id", "default"),
    }


def _current_owner(request: Request) -> tuple[str, str]:
    mm = _get_manager(request)
    if mm and hasattr(mm, "_current_owner"):
        try:
            return mm._current_owner()
        except Exception:
            pass
    return "default", "default"


def _owner_counts(store: Any) -> dict[str, Any]:
    db = getattr(store, "db", None)
    conn = getattr(db, "_conn", None)
    if conn is None:
        return {"total": 0, "by_scope": {}, "by_owner": []}

    by_scope = {
        (row[0] or "global"): row[1]
        for row in conn.execute(
            "SELECT COALESCE(scope, 'global') AS scope, COUNT(*) FROM memories GROUP BY scope"
        ).fetchall()
    }
    by_owner = [
        {
            "scope": row[0] or "global",
            "scope_owner": row[1] or "",
            "user_id": row[2] or "default",
            "workspace_id": row[3] or "default",
            "count": row[4],
        }
        for row in conn.execute(
            """
            SELECT COALESCE(scope, 'global'),
                   COALESCE(scope_owner, ''),
                   COALESCE(user_id, 'default'),
                   COALESCE(workspace_id, 'default'),
                   COUNT(*)
            FROM memories
            GROUP BY scope, scope_owner, user_id, workspace_id
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
    ]
    total = sum(by_scope.values())
    return {"total": total, "by_scope": by_scope, "by_owner": by_owner}


def _graph_owner_counts(mm: Any) -> dict[str, Any]:
    if not mm or not mm._ensure_relational() or not mm.relational_store:
        return {"total_nodes": 0, "by_owner": []}
    conn = getattr(mm.relational_store, "_conn", None)
    if conn is None:
        return {"total_nodes": 0, "by_owner": []}
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(user_id, 'default'),
                   COALESCE(workspace_id, 'default'),
                   COUNT(*)
            FROM mdrm_nodes
            GROUP BY user_id, workspace_id
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
    except Exception:
        return {"total_nodes": 0, "by_owner": []}
    by_owner = [
        {"user_id": row[0] or "default", "workspace_id": row[1] or "default", "count": row[2]}
        for row in rows
    ]
    return {"total_nodes": sum(row["count"] for row in by_owner), "by_owner": by_owner}


def _claim_graph_nodes(
    mm: Any,
    *,
    memory_ids: set[str],
    user_id: str,
    workspace_id: str,
    include_default_graph_nodes: bool,
) -> int:
    if not mm or not mm._ensure_relational() or not mm.relational_store:
        return 0
    conn = getattr(mm.relational_store, "_conn", None)
    if conn is None:
        return 0
    updated = 0
    try:
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            cur = conn.execute(
                f"""
                UPDATE mdrm_nodes
                SET user_id = ?, workspace_id = ?
                WHERE id IN ({placeholders})
                """,
                [user_id, workspace_id, *memory_ids],
            )
            updated += cur.rowcount if cur.rowcount is not None else 0
        conn.commit()
    except Exception as e:
        logger.warning(f"[MemoryAPI] Claim graph nodes failed: {e}")
        return updated
    return updated


def _memory_type_value(mem: Any) -> str:
    return mem.type.value if hasattr(mem.type, "value") else str(mem.type)


def _normalize_legacy_tags(mem: Any, *extra: str) -> list[str]:
    existing = getattr(mem, "tags", None) or []
    return sorted({str(t) for t in [*existing, *extra] if str(t).strip()})


def _is_reviewed_legacy(mem: Any) -> bool:
    if getattr(mem, "superseded_by", None):
        return True
    tags = {str(t) for t in (getattr(mem, "tags", None) or [])}
    return "legacy_pending_review" in tags or any(t.startswith("legacy_reason:") for t in tags)


def _legacy_review_counts(store: Any) -> dict[str, int]:
    legacy = store.load_all_memories(
        scope="legacy_quarantine",
        scope_owner="",
        user_id="legacy",
        workspace_id=None,
        include_inactive=True,
    )
    pending = sum(1 for mem in legacy if not _is_reviewed_legacy(mem))
    reviewed = len(legacy) - pending
    return {"total": len(legacy), "pending": pending, "reviewed": reviewed}


def _identity_slot_for(subject: str, predicate: str) -> str:
    if (subject or "").strip().lower() not in {"用户", "user", "当前用户", "我"}:
        return ""
    pred = (predicate or "").strip().lower()
    for alias, slot in IDENTITY_SLOT_ALIASES.items():
        if alias.lower() == pred:
            return slot
    if pred.startswith("preference.") or pred.startswith("偏好."):
        return f"user.preference.{pred.split('.', 1)[1]}"
    return ""


def _infer_legacy_subject_predicate(mem: Any) -> tuple[str, str]:
    subject = (getattr(mem, "subject", "") or "").strip()
    predicate = (getattr(mem, "predicate", "") or "").strip()
    if subject and predicate:
        return subject, predicate
    content = (getattr(mem, "content", "") or "").strip()
    patterns = [
        (r"^(?:用户|我)(?:叫|名叫|名字是|姓名是)\s*([^，。；\s]{1,30})", "姓名"),
        (r"^(?:用户|我)(?:年龄是|今年)\s*(\d{1,3})\s*岁?", "年龄"),
        (r"^(?:用户|我)(?:住在|居住在|所在地是|城市是|来自)\s*([^，。；]{1,30})", "城市"),
        (r"^(?:用户|我)(?:喜欢|偏好)\s*([^，。；]{1,80})", "偏好"),
    ]
    for pattern, pred in patterns:
        if re.search(pattern, content):
            return "用户", pred
    return subject, predicate


def _looks_like_task_log(mem: Any) -> bool:
    content = (getattr(mem, "content", "") or "").strip().lower()
    if any(p.lower() in content for p in TASK_LOG_PATTERNS):
        return True
    if re.search(r"\b(read_file|write_file|run_shell|pytest|npm run|git diff)\b", content):
        return True
    return False


def _legacy_candidate_reason(mem: Any, subject: str, predicate: str) -> str:
    content = (getattr(mem, "content", "") or "").strip()
    if not content:
        return "empty_content"
    if len(content) > 800:
        return "too_long"
    if _looks_like_task_log(mem):
        return "task_log"
    mem_type = _memory_type_value(mem)
    if mem_type not in {t.value for t in MemoryType}:
        return "unknown_type"
    if mem_type == MemoryType.FACT.value and not (subject and predicate):
        return "unstructured_fact"
    return ""


def _legacy_sort_key(mem: Any) -> tuple[str, str]:
    updated = getattr(mem, "updated_at", None) or getattr(mem, "created_at", None)
    created = getattr(mem, "created_at", None)
    return (
        updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
        created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
    )


def _active_slot_index(store: Any, user_id: str, workspace_id: str) -> set[str]:
    active = store.load_all_memories(
        scope="user",
        scope_owner="",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    return {
        slot
        for mem in active
        if (slot := _identity_slot_for(getattr(mem, "subject", ""), getattr(mem, "predicate", "")))
    }


def _mark_legacy_reviewed(store: Any, mem: Any, reason: str, superseded_by: str | None = None) -> None:
    updates: dict[str, Any] = {
        "tags": _normalize_legacy_tags(mem, "legacy_pending_review", f"legacy_reason:{reason}"),
    }
    if superseded_by:
        updates["superseded_by"] = superseded_by
    store.db.update_memory(mem.id, updates)


def _safe_import_legacy_memories(
    store: Any,
    legacy: list[Any],
    *,
    user_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    identity_groups: dict[str, list[tuple[Any, str, str]]] = {}
    accepted_general: list[tuple[Any, str, str]] = []
    rejected = 0
    conflict_skipped = 0
    active_slots = _active_slot_index(store, user_id, workspace_id)

    for mem in legacy:
        if _is_reviewed_legacy(mem):
            continue
        subject, predicate = _infer_legacy_subject_predicate(mem)
        reason = _legacy_candidate_reason(mem, subject, predicate)
        if reason:
            _mark_legacy_reviewed(store, mem, reason)
            rejected += 1
            continue
        slot = _identity_slot_for(subject, predicate)
        if slot:
            identity_groups.setdefault(slot, []).append((mem, subject, predicate))
        else:
            accepted_general.append((mem, subject, predicate))

    to_promote: list[tuple[Any, str, str]] = []
    for slot, items in identity_groups.items():
        items.sort(key=lambda item: _legacy_sort_key(item[0]), reverse=True)
        winner = items[0]
        if slot in active_slots:
            for mem, _subject, _predicate in items:
                _mark_legacy_reviewed(store, mem, "conflicts_with_current_user")
                conflict_skipped += 1
            continue
        to_promote.append(winner)
        for mem, _subject, _predicate in items[1:]:
            _mark_legacy_reviewed(store, mem, "legacy_identity_conflict", superseded_by=winner[0].id)
            conflict_skipped += 1

    to_promote.extend(accepted_general)

    promoted_ids: set[str] = set()
    for mem, subject, predicate in to_promote:
        importance = min(max(float(getattr(mem, "importance_score", 0.5) or 0.5), 0.2), 0.65)
        updates = {
            "scope": "user",
            "scope_owner": "",
            "user_id": user_id,
            "workspace_id": workspace_id,
            "subject": subject,
            "predicate": predicate,
            "importance_score": importance,
            "priority": _priority_for_importance(
                mem.type if isinstance(mem.type, MemoryType) else MemoryType(_memory_type_value(mem)),
                importance,
            ).value,
            "confidence": min(float(getattr(mem, "confidence", 0.5) or 0.5), 0.7),
            "tags": _normalize_legacy_tags(mem, "legacy_imported"),
        }
        if store.db.update_memory(mem.id, updates):
            promoted_ids.add(mem.id)

    return {
        "promoted_ids": promoted_ids,
        "promoted": len(promoted_ids),
        "rejected": rejected,
        "conflict_skipped": conflict_skipped,
        "reviewed": len(legacy),
    }


def _priority_for_importance(mem_type: MemoryType, importance: float) -> MemoryPriority:
    if importance >= 0.85 or mem_type == MemoryType.RULE:
        return MemoryPriority.PERMANENT
    if importance >= 0.6:
        return MemoryPriority.LONG_TERM
    return MemoryPriority.SHORT_TERM


@router.post("")
async def create_memory(request: Request, body: MemoryCreateRequest):
    """Create a new memory entry from the chat UI."""
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")
    try:
        content = body.content.strip()
        if not content:
            raise HTTPException(400, "Memory content cannot be empty")
        try:
            mem_type = MemoryType(body.type)
        except ValueError:
            raise HTTPException(400, f"Invalid memory type: {body.type}") from None
        mem = SemanticMemory(
            type=mem_type,
            priority=_priority_for_importance(mem_type, body.importance_score),
            content=content,
            source="chat_ui",
            subject=body.subject or "",
            predicate=body.predicate or "",
            importance_score=body.importance_score,
            tags=body.tags or [],
        )
        apply_retention(mem)
        mm = _get_manager(request)
        if mm and hasattr(mm, "save_user_memory"):
            mem_id = mm.save_user_memory(mem, scope="user")
        else:
            user_id, workspace_id = _current_owner(request)
            mem_id = store.save_semantic(
                mem,
                scope="user",
                user_id=user_id,
                workspace_id=workspace_id,
            )
        _sync_json(request)
        return {"status": "ok", "id": mem_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to save memory: {e}")


@router.get("")
async def list_memories(
    request: Request,
    type: str | None = None,
    search: str | None = None,
    q: str | None = None,
    min_score: float = 0.0,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "importance_score",
    sort_order: str = "desc",
    include_inactive: bool = False,
):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    # 兼容别名：?q= 与 ?search= 等价；search 优先，避免同时给两个值时行为不一致
    search = search or q

    if search:
        user_id, workspace_id = _current_owner(request)
        results = store.search_semantic(
            search,
            limit=limit,
            filter_type=type,
            scope="user",
            user_id=user_id,
            workspace_id=workspace_id,
            include_inactive=include_inactive,
        )
        return {
            "memories": [_serialize(m) for m in results],
            "total": len(results),
            "limit": limit,
            "offset": 0,
        }

    user_id, workspace_id = _current_owner(request)
    results, total = store.query_paged(
        memory_type=type,
        min_importance=min_score if min_score > 0 else None,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        scope="user",
        scope_owner="",
        user_id=user_id,
        workspace_id=workspace_id,
        include_inactive=include_inactive,
    )
    return {
        "memories": [_serialize(m) for m in results],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats")
async def memory_stats(request: Request):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    user_id, workspace_id = _current_owner(request)
    all_mems = store.load_all_memories(
        scope="user",
        scope_owner="",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    by_type: dict[str, int] = {}
    total_score = 0.0
    for m in all_mems:
        t = m.type.value if hasattr(m.type, "value") else str(m.type)
        by_type[t] = by_type.get(t, 0) + 1
        total_score += m.importance_score

    return {
        "total": len(all_mems),
        "by_type": by_type,
        "avg_score": round(total_score / len(all_mems), 2) if all_mems else 0,
    }


@router.get("/migration-status")
async def memory_migration_status(request: Request):
    """Diagnose legacy memory visibility after owner-scoped migration."""
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    user_id, workspace_id = _current_owner(request)
    current_visible = store.count_memories(
        scope="user",
        scope_owner="",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    legacy_counts = _legacy_review_counts(store)
    all_counts = _owner_counts(store)
    graph_counts = _graph_owner_counts(_get_manager(request))
    return {
        "current_owner": {"user_id": user_id, "workspace_id": workspace_id},
        "current_visible": current_visible,
        "legacy_quarantine": legacy_counts["total"],
        "legacy_pending": legacy_counts["pending"],
        "legacy_reviewed": legacy_counts["reviewed"],
        "semantic": all_counts,
        "graph": graph_counts,
        "has_recoverable_legacy": legacy_counts["pending"] > 0,
    }


@router.post("/claim-legacy")
async def claim_legacy_memories(request: Request, body: ClaimLegacyRequest | None = None):
    """Safely import quarantined legacy memories into the current desktop owner."""
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")
    body = body or ClaimLegacyRequest()
    user_id, workspace_id = _current_owner(request)
    legacy = store.load_all_memories(
        scope="legacy_quarantine",
        scope_owner="",
        user_id="legacy",
        workspace_id=None,
        include_inactive=body.include_inactive,
    )
    report = _safe_import_legacy_memories(
        store,
        legacy,
        user_id=user_id,
        workspace_id=workspace_id,
    )

    graph_updated = _claim_graph_nodes(
        _get_manager(request),
        memory_ids=report["promoted_ids"],
        user_id=user_id,
        workspace_id=workspace_id,
        include_default_graph_nodes=body.include_default_graph_nodes,
    )
    _sync_json(request)
    return {
        "ok": True,
        "claimed": report["promoted"],
        "promoted": report["promoted"],
        "reviewed": report["reviewed"],
        "rejected": report["rejected"],
        "conflict_skipped": report["conflict_skipped"],
        "graph_nodes_updated": graph_updated,
        "current_owner": {"user_id": user_id, "workspace_id": workspace_id},
    }


@router.post("/review")
async def trigger_review(request: Request):
    """Start async LLM-driven memory review. Returns immediately with task status."""
    global _review_task, _review_cancel, _review_progress

    async with _review_lock:
        if _review_task and not _review_task.done():
            return {"ok": True, "status": "already_running", "progress": _review_progress}

        lifecycle = _get_lifecycle(request)
        if not lifecycle:
            raise HTTPException(503, "Lifecycle manager not available")

        _review_cancel = asyncio.Event()
        _review_progress = {
            "status": "running",
            "batch": 0,
            "total_batches": 0,
            "total_memories": 0,
            "processed": 0,
            "report": {"deleted": 0, "updated": 0, "merged": 0, "kept": 0, "errors": 0},
            "started_at": time.time(),
        }

        def on_progress(data: dict) -> None:
            _review_progress.update(data)

        async def _run_review() -> None:
            global _review_progress
            try:
                result = await lifecycle.review_memories_with_llm(
                    progress_callback=on_progress,
                    cancel_event=_review_cancel,
                )

                _review_progress["status"] = (
                    "cancelled" if _review_progress.get("cancelled") else "done"
                )
                _review_progress["report"] = result
                _review_progress["finished_at"] = time.time()

                try:
                    if lifecycle.identity_dir:
                        lifecycle.refresh_memory_md(lifecycle.identity_dir)
                    lifecycle._sync_vector_store()
                    _sync_json(request)
                except Exception as e:
                    logger.warning(f"[MemoryAPI] Post-review sync failed: {e}")
            except Exception as e:
                logger.error(f"[MemoryAPI] Background review failed: {e}")
                _review_progress["status"] = "error"
                _review_progress["error"] = str(e)
                _review_progress["finished_at"] = time.time()

        _review_task = asyncio.create_task(_run_review())

    return {"ok": True, "status": "started", "progress": _review_progress}


@router.get("/review/status")
async def review_status():
    """Poll current review task progress."""
    if not _review_task:
        return {"status": "idle"}
    return {"status": _review_progress.get("status", "unknown"), "progress": _review_progress}


@router.post("/review/cancel")
async def cancel_review():
    """Request cancellation of the running review task."""
    if not _review_task or _review_task.done():
        return {"ok": False, "reason": "no_running_task"}
    if _review_cancel:
        _review_cancel.set()
    return {"ok": True}


@router.post("/batch-delete")
async def batch_delete(request: Request):
    data = await request.json()
    ids = data.get("ids", [])
    if not ids:
        raise HTTPException(400, "No IDs provided")

    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    deleted = 0
    for mid in ids:
        if store.delete_semantic(mid):
            deleted += 1

    _sync_json(request)
    return {"deleted": deleted, "total": len(ids)}


@router.get("/graph")
async def get_memory_graph(request: Request, limit: int = 500):
    """Return the relational memory graph for 3D visualization."""
    mm = _get_manager(request)
    if not mm:
        raise HTTPException(503, "Memory manager not available")

    nodes_out: list[dict] = []
    links_out: list[dict] = []
    mode = "mode1"

    mode_cfg = mm._get_memory_mode()
    if mode_cfg != "mode1" and mm._ensure_relational() and mm.relational_store:
        rs = mm.relational_store
        mode = "mode2"
        user_id, workspace_id = _current_owner(request)
        raw_nodes = rs.get_all_nodes(limit=limit, user_id=user_id, workspace_id=workspace_id)
        node_ids = {n.id for n in raw_nodes}

        for n in raw_nodes:
            ents = [{"name": e.name, "type": e.type} for e in n.entities[:5]]
            group = f"entity:{ents[0]['name']}" if ents else f"type:{n.node_type.value}"
            nodes_out.append(
                {
                    "id": n.id,
                    "content": n.content[:200],
                    "node_type": n.node_type.value.upper(),
                    "importance": n.importance,
                    "entities": ents,
                    "action_category": n.action_category,
                    "occurred_at": n.occurred_at.isoformat() if n.occurred_at else None,
                    "session_id": n.session_id,
                    "project": n.project,
                    "group": group,
                }
            )

        raw_edges = rs.get_all_edges(node_ids)
        for e in raw_edges:
            if e.source_id in node_ids and e.target_id in node_ids:
                links_out.append(
                    {
                        "source": e.source_id,
                        "target": e.target_id,
                        "edge_type": e.edge_type.value,
                        "dimension": e.dimension.value,
                        "weight": e.weight,
                    }
                )
    else:
        store = _get_store(request)
        if store:
            import json as _json
            from collections import defaultdict

            user_id, workspace_id = _current_owner(request)
            all_mems = store.load_all_memories(
                scope="user",
                scope_owner="",
                user_id=user_id,
                workspace_id=workspace_id,
            )[:limit]
            subject_map: dict[str, list[str]] = defaultdict(list)
            for m in all_mems:
                nodes_out.append(
                    {
                        "id": m.id,
                        "content": (m.content or "")[:200],
                        "node_type": (m.type.value if hasattr(m.type, "value") else "FACT").upper(),
                        "importance": m.importance_score,
                        "entities": [],
                        "action_category": "",
                        "occurred_at": m.created_at.isoformat() if m.created_at else None,
                        "session_id": "",
                        "project": "",
                        "group": f"type:{m.type.value if hasattr(m.type, 'value') else 'fact'}",
                    }
                )
                if m.subject:
                    subject_map[m.subject].append(m.id)

                linked_ids = getattr(m, "linked_memory_ids", None)
                if not linked_ids:
                    meta = getattr(m, "metadata", {}) or {}
                    if isinstance(meta, str):
                        try:
                            meta = _json.loads(meta)
                        except Exception:
                            meta = {}
                    linked_ids = meta.get("linked_memory_ids", [])
                if isinstance(linked_ids, list):
                    node_set = {n["id"] for n in nodes_out}
                    for lid in linked_ids:
                        if lid in node_set:
                            links_out.append(
                                {
                                    "source": m.id,
                                    "target": lid,
                                    "edge_type": "linked",
                                    "dimension": "context",
                                    "weight": 0.5,
                                }
                            )

            for _subj, ids in subject_map.items():
                if len(ids) >= 2:
                    for i in range(len(ids)):
                        for j in range(i + 1, min(i + 3, len(ids))):
                            links_out.append(
                                {
                                    "source": ids[i],
                                    "target": ids[j],
                                    "edge_type": "same_subject",
                                    "dimension": "entity",
                                    "weight": 0.4,
                                }
                            )

    return {
        "nodes": nodes_out,
        "links": links_out,
        "meta": {
            "total_nodes": len(nodes_out),
            "total_edges": len(links_out),
            "mode": mode,
        },
    }


@router.get("/{memory_id}")
async def get_memory(request: Request, memory_id: str):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    mem = store.get_semantic(memory_id)
    if not mem:
        raise HTTPException(404, "Memory not found")
    return _serialize(mem)


@router.put("/{memory_id}")
async def update_memory(request: Request, memory_id: str, body: MemoryUpdateRequest):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    updates: dict = {}
    if body.content is not None:
        content = body.content.strip()
        if not content:
            raise HTTPException(400, "Memory content cannot be empty")
        updates["content"] = content
    if body.importance_score is not None:
        updates["importance_score"] = body.importance_score
    if body.tags is not None:
        updates["tags"] = body.tags

    if not updates:
        raise HTTPException(400, "No fields to update")

    ok = store.update_semantic(memory_id, updates)
    if not ok:
        raise HTTPException(404, "Memory not found")
    _sync_json(request)
    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    ok = store.delete_semantic(memory_id)
    if not ok:
        raise HTTPException(404, "Memory not found")
    _sync_json(request)
    return {"ok": True}


@router.post("/refresh-md")
async def refresh_md(request: Request):
    """Regenerate MEMORY.md from current DB state."""
    lifecycle = _get_lifecycle(request)
    if not lifecycle:
        raise HTTPException(503, "Lifecycle manager not available")

    if not lifecycle.identity_dir:
        raise HTTPException(500, "Identity directory not configured")

    lifecycle.refresh_memory_md(lifecycle.identity_dir)
    return {"ok": True}

