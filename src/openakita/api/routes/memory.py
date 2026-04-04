"""
Memory management routes: CRUD + LLM review for semantic memories.

Provides HTTP API for the frontend Memory Management Panel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openakita.memory.types import MemoryType, SemanticMemory

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


@router.post("")
async def create_memory(request: Request, body: MemoryCreateRequest):
    """Create a new memory entry from the chat UI."""
    store = _get_store(request)
    if not store:
        raise HTTPException(503, "Memory store not available")
    try:
        mem_type = MemoryType.FACT
        try:
            mem_type = MemoryType(body.type)
        except ValueError:
            pass
        mem = SemanticMemory(
            type=mem_type,
            content=body.content,
            source="chat_ui",
            subject=body.subject or "",
            predicate=body.predicate or "",
            importance_score=body.importance_score,
            tags=body.tags or [],
        )
        mem_id = store.save_semantic(mem)
        _sync_json(request)
        return {"status": "ok", "id": mem_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to save memory: {e}")


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
        results.sort(key=lambda m: (m.importance_score, m.created_at or datetime.min), reverse=True)
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
        raw_nodes = rs.get_all_nodes(limit=limit)
        node_ids = {n.id for n in raw_nodes}

        for n in raw_nodes:
            ents = [{"name": e.name, "type": e.type} for e in n.entities[:5]]
            group = f"entity:{ents[0]['name']}" if ents else f"type:{n.node_type.value}"
            nodes_out.append({
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
            })

        raw_edges = rs.get_all_edges(node_ids)
        for e in raw_edges:
            if e.source_id in node_ids and e.target_id in node_ids:
                links_out.append({
                    "source": e.source_id,
                    "target": e.target_id,
                    "edge_type": e.edge_type.value,
                    "dimension": e.dimension.value,
                    "weight": e.weight,
                })
    else:
        store = _get_store(request)
        if store:
            import json as _json
            from collections import defaultdict

            all_mems = store.load_all_memories()[:limit]
            subject_map: dict[str, list[str]] = defaultdict(list)
            for m in all_mems:
                nodes_out.append({
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
                })
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
                            links_out.append({
                                "source": m.id, "target": lid,
                                "edge_type": "linked", "dimension": "context", "weight": 0.5,
                            })

            for _subj, ids in subject_map.items():
                if len(ids) >= 2:
                    for i in range(len(ids)):
                        for j in range(i + 1, min(i + 3, len(ids))):
                            links_out.append({
                                "source": ids[i], "target": ids[j],
                                "edge_type": "same_subject", "dimension": "entity", "weight": 0.4,
                            })

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
