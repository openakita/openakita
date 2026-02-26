"""
Memory management routes: CRUD + LLM review for semantic memories.

Provides HTTP API for the frontend Memory Management Panel.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memory"])


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
    }


@router.get("")
async def list_memories(
    request: Request,
    type: str | None = None,
    search: str | None = None,
    min_score: float = 0.0,
    limit: int = 200,
):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    if search:
        results = store.search_semantic(search, limit=limit, filter_type=type)
    else:
        results = store.load_all_memories()
        if type:
            results = [m for m in results if (m.type.value if hasattr(m.type, "value") else str(m.type)) == type]
        if min_score > 0:
            results = [m for m in results if m.importance_score >= min_score]
        results.sort(key=lambda m: m.importance_score, reverse=True)
        results = results[:limit]

    return {
        "memories": [_serialize(m) for m in results],
        "total": len(results),
    }


@router.get("/stats")
async def memory_stats(request: Request):
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")

    all_mems = store.load_all_memories()
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
        updates["content"] = body.content
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


@router.post("/review")
async def trigger_review(request: Request):
    """Trigger LLM-driven memory review (same as consolidate_memories tool)."""
    lifecycle = _get_lifecycle(request)
    if not lifecycle:
        raise HTTPException(503, "Lifecycle manager not available")

    result = await lifecycle.review_memories_with_llm()

    if lifecycle.identity_dir:
        lifecycle.refresh_memory_md(lifecycle.identity_dir)

    lifecycle._sync_vector_store()
    _sync_json(request)

    return {
        "ok": True,
        "review": result,
    }


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
