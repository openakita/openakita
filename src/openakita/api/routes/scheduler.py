"""
Scheduler routes: CRUD for scheduled tasks.

Provides HTTP API for the frontend to manage scheduled tasks:
- List all tasks
- Create a new task
- Update an existing task
- Delete a task
- Toggle enable/disable
- Trigger a task immediately
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


def _notify_scheduler_change(action: str = "update") -> None:
    """Fire-and-forget WS broadcast for scheduler state changes."""
    try:
        from openakita.api.routes.websocket import broadcast_event
        asyncio.ensure_future(broadcast_event("scheduler:task_update", {"action": action}))
    except Exception:
        pass


def _get_scheduler(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    if hasattr(agent, "task_scheduler"):
        return agent.task_scheduler
    local = getattr(agent, "_local_agent", None)
    if local and hasattr(local, "task_scheduler"):
        return local.task_scheduler
    return None


class TaskCreateRequest(BaseModel):
    name: str
    task_type: str = "reminder"  # reminder | task
    trigger_type: str = "once"  # once | interval | cron
    trigger_config: dict = Field(default_factory=dict)
    reminder_message: str | None = None
    prompt: str = ""
    channel_id: str | None = None
    chat_id: str | None = None
    enabled: bool = True


class TaskUpdateRequest(BaseModel):
    name: str | None = None
    task_type: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    reminder_message: str | None = None
    prompt: str | None = None
    channel_id: str | None = None
    chat_id: str | None = None
    enabled: bool | None = None


@router.get("/api/scheduler/tasks")
async def list_tasks(
    request: Request,
    offset: int = 0,
    limit: int = 50,
    enabled_only: bool = False,
):
    """List scheduled tasks with pagination."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized", "tasks": []}

    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    all_tasks = scheduler.list_tasks(enabled_only=enabled_only)
    total = len(all_tasks)
    page = all_tasks[offset : offset + limit]
    return {
        "tasks": [t.to_dict() for t in page],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/api/scheduler/tasks/{task_id}")
async def get_task(request: Request, task_id: str):
    """Get a single task by ID."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    task = scheduler.get_task(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    return {"task": task.to_dict()}


@router.post("/api/scheduler/tasks")
async def create_task(request: Request, body: TaskCreateRequest):
    """Create a new scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    from openakita.scheduler.task import ScheduledTask, TaskSource, TaskType, TriggerType

    try:
        trigger_type = TriggerType(body.trigger_type)
    except ValueError:
        return JSONResponse(status_code=422, content={"error": f"Invalid trigger_type: {body.trigger_type}"})

    try:
        task_type = TaskType(body.task_type)
    except ValueError:
        return JSONResponse(status_code=422, content={"error": f"Invalid task_type: {body.task_type}"})

    description = body.reminder_message or body.prompt or body.name
    task = ScheduledTask.create(
        name=body.name,
        description=description,
        trigger_type=trigger_type,
        trigger_config=body.trigger_config,
        task_type=task_type,
        reminder_message=body.reminder_message,
        prompt=body.prompt,
    )
    task.task_source = TaskSource.MANUAL
    task.channel_id = body.channel_id or None
    task.chat_id = body.chat_id or None
    task.enabled = body.enabled

    try:
        task_id = await scheduler.add_task(task)
    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    _notify_scheduler_change("create")
    return {"status": "ok", "task_id": task_id, "task": task.to_dict()}


@router.put("/api/scheduler/tasks/{task_id}")
async def update_task(request: Request, task_id: str, body: TaskUpdateRequest):
    """Update an existing scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    task = scheduler.get_task(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    updates: dict = {}

    if body.name is not None:
        updates["name"] = body.name
    if body.reminder_message is not None:
        updates["reminder_message"] = body.reminder_message
    if body.prompt is not None:
        updates["prompt"] = body.prompt
    if body.channel_id is not None:
        updates["channel_id"] = body.channel_id or None
    if body.chat_id is not None:
        updates["chat_id"] = body.chat_id or None

    if body.task_type is not None:
        from openakita.scheduler.task import TaskType
        try:
            updates["task_type"] = TaskType(body.task_type)
        except ValueError:
            return JSONResponse(status_code=422, content={"error": f"Invalid task_type: {body.task_type}"})

    if body.trigger_type is not None:
        from openakita.scheduler.task import TriggerType
        try:
            updates["trigger_type"] = TriggerType(body.trigger_type)
        except ValueError:
            return JSONResponse(status_code=422, content={"error": f"Invalid trigger_type: {body.trigger_type}"})

    if body.trigger_config is not None:
        updates["trigger_config"] = body.trigger_config

    if updates.get("name") or updates.get("reminder_message") or updates.get("prompt"):
        updates["description"] = (
            updates.get("reminder_message")
            or updates.get("prompt")
            or updates.get("name")
            or task.description
        )

    if updates:
        success = await scheduler.update_task(task_id, updates)
        if not success:
            return JSONResponse(status_code=500, content={"error": "Update failed"})

    if body.enabled is not None:
        if body.enabled:
            await scheduler.enable_task(task_id)
        else:
            await scheduler.disable_task(task_id)

    updated = scheduler.get_task(task_id)
    _notify_scheduler_change("update")
    return {"status": "ok", "task": updated.to_dict() if updated else None}


@router.delete("/api/scheduler/tasks/{task_id}")
async def delete_task(request: Request, task_id: str):
    """Delete a scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    task = scheduler.get_task(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    if not task.deletable:
        return JSONResponse(status_code=403, content={"error": "System task cannot be deleted, use disable instead"})

    result = await scheduler.remove_task(task_id)
    if result == "system_task":
        return JSONResponse(status_code=403, content={"error": "System task cannot be deleted, use disable instead"})
    if result == "not_found":
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    _notify_scheduler_change("delete")
    return {"status": "ok", "task_id": task_id}


@router.post("/api/scheduler/tasks/{task_id}/toggle")
async def toggle_task(request: Request, task_id: str):
    """Toggle task enabled/disabled."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    task = scheduler.get_task(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "Task not found"})

    if task.enabled:
        await scheduler.disable_task(task_id)
    else:
        await scheduler.enable_task(task_id)

    updated = scheduler.get_task(task_id)
    _notify_scheduler_change("toggle")
    return {"status": "ok", "task": updated.to_dict() if updated else None}


@router.post("/api/scheduler/tasks/{task_id}/trigger")
async def trigger_task(request: Request, task_id: str):
    """Trigger a task to run immediately."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    from openakita.core.engine_bridge import to_engine

    execution = await to_engine(scheduler.trigger_now(task_id))
    if execution is None:
        return JSONResponse(status_code=404, content={"error": "Task not found or trigger failed"})

    _notify_scheduler_change("trigger")
    return {"status": "ok", "execution": execution.to_dict()}


@router.get("/api/scheduler/executions")
async def list_executions(
    request: Request,
    task_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List execution history, optionally filtered by task_id."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized", "executions": []}

    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    all_execs = scheduler.get_executions(task_id=task_id)
    total = len(all_execs)
    all_execs_reversed = list(reversed(all_execs))
    page = all_execs_reversed[offset : offset + limit]
    return {
        "executions": [e.to_dict() for e in page],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/api/scheduler/tasks/{task_id}/executions")
async def list_task_executions(
    request: Request,
    task_id: str,
    limit: int = 20,
):
    """List execution history for a specific task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized", "executions": []}

    limit = max(1, min(limit, 100))
    execs = scheduler.get_executions(task_id=task_id, limit=limit)
    return {
        "executions": [e.to_dict() for e in reversed(execs)],
        "total": len(execs),
    }


@router.get("/api/scheduler/channels")
async def list_channels(request: Request):
    """List available IM channels with chat_id for notification targeting."""
    agent = getattr(request.app.state, "agent", None)
    local = None if agent is None else getattr(agent, "_local_agent", agent)

    gateway = None
    executor = getattr(local, "_task_executor", None)
    if executor and getattr(executor, "gateway", None):
        gateway = executor.gateway
    if not gateway:
        gateway = getattr(local, "_gateway", None)

    if not gateway:
        return {"channels": []}

    import json
    from datetime import datetime as dt

    results: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    session_manager = getattr(gateway, "session_manager", None)

    skip_channels = {"desktop"}

    def _add_or_merge(entry: dict) -> None:
        """Add a channel entry, merging chat_name into existing if needed."""
        pair = (entry["channel_id"], entry["chat_id"])
        if pair in seen:
            idx = seen[pair]
            existing = results[idx]
            if not existing.get("chat_name") and entry.get("chat_name"):
                existing["chat_name"] = entry["chat_name"]
            if not existing.get("chat_type") and entry.get("chat_type"):
                existing["chat_type"] = entry["chat_type"]
            if not existing.get("display_name") and entry.get("display_name"):
                existing["display_name"] = entry["display_name"]
            return
        seen[pair] = len(results)
        results.append(entry)

    if session_manager:
        # 1. Active memory sessions
        sessions = session_manager.list_sessions()
        if sessions:
            sessions.sort(
                key=lambda s: getattr(s, "last_active", dt.min), reverse=True
            )
            for s in sessions:
                if getattr(s, "state", None) and str(s.state.value) == "closed":
                    continue
                ch = getattr(s, "channel", None)
                cid = getattr(s, "chat_id", None)
                if not ch or not cid or ch in skip_channels:
                    continue
                _add_or_merge({
                    "channel_id": ch,
                    "chat_id": cid,
                    "user_id": getattr(s, "user_id", None),
                    "last_active": getattr(s, "last_active", dt.min).isoformat(),
                    "chat_name": getattr(s, "chat_name", "") or "",
                    "chat_type": getattr(s, "chat_type", "private") or "private",
                    "display_name": getattr(s, "display_name", "") or "",
                })

        # 2. Persisted sessions from file
        sessions_file = getattr(session_manager, "storage_path", None)
        if sessions_file:
            sessions_file = sessions_file / "sessions.json"
            if sessions_file.exists():
                try:
                    with open(sessions_file, encoding="utf-8") as f:
                        raw = json.load(f)
                    raw.sort(key=lambda s: s.get("last_active", ""), reverse=True)
                    for s in raw:
                        ch = s.get("channel")
                        cid = s.get("chat_id")
                        state = s.get("state", "")
                        if not ch or not cid or state == "closed" or ch in skip_channels:
                            continue
                        _add_or_merge({
                            "channel_id": ch,
                            "chat_id": cid,
                            "user_id": s.get("user_id"),
                            "last_active": s.get("last_active", ""),
                            "chat_name": s.get("chat_name", ""),
                            "chat_type": s.get("chat_type", "private"),
                            "display_name": s.get("display_name", ""),
                        })
                except Exception as e:
                    logger.warning(f"Failed to read sessions file: {e}")

        # 3. Channel registry (persists even after sessions expire)
        registry = getattr(session_manager, "_channel_registry", None)
        if registry and isinstance(registry, dict):
            for ch, entry in registry.items():
                if ch in skip_channels:
                    continue
                # 兼容新格式（list of dicts）和旧格式（单 dict）
                items = entry if isinstance(entry, list) else [entry] if isinstance(entry, dict) else []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    cid = item.get("chat_id")
                    if not cid:
                        continue
                    pair = (ch, cid)
                    if pair in seen:
                        continue
                    _add_or_merge({
                        "channel_id": ch,
                        "chat_id": cid,
                        "user_id": item.get("user_id"),
                        "last_active": item.get("last_seen", ""),
                        "chat_name": "",
                        "chat_type": "private",
                        "display_name": "",
                    })

    # 4. Running gateway adapters — show configured bots even without sessions
    adapters = getattr(gateway, "_adapters", {})
    started = getattr(gateway, "_started_adapters", set())
    for adapter_name, adapter in adapters.items():
        if adapter_name in skip_channels:
            continue
        if not getattr(adapter, "is_running", False) and adapter_name not in started:
            continue
        already_listed = any(
            adapter_name == entry["channel_id"] for entry in results
        )
        if already_listed:
            continue
        fallback_chat_id = ""
        if session_manager:
            target = session_manager.get_known_channel_target(adapter_name)
            if target:
                fallback_chat_id = target[1]
        _add_or_merge({
            "channel_id": adapter_name,
            "chat_id": fallback_chat_id,
            "user_id": None,
            "last_active": "",
            "chat_name": "",
            "chat_type": "private",
            "display_name": "",
        })

    alias_store = getattr(gateway, "chat_aliases", None)
    if alias_store:
        for entry in results:
            ch = entry.get("channel_id", "")
            cid = entry.get("chat_id", "")
            if ch and cid:
                a = alias_store.get_alias(ch, cid)
                if a:
                    entry["alias"] = a

    # Enrich with bot display names from settings
    from openakita.config import settings
    bot_name_map: dict[str, str] = {}
    for b in getattr(settings, "im_bots", []):
        if isinstance(b, dict) and b.get("id") and b.get("name"):
            bot_name_map[b["id"]] = b["name"]
    for entry in results:
        ch = entry.get("channel_id", "")
        parts = ch.split(":", 1)
        bot_id = parts[1] if len(parts) > 1 else ch
        if bot_id in bot_name_map:
            entry["bot_display_name"] = bot_name_map[bot_id]

    return {"channels": results}


@router.get("/api/scheduler/stats")
async def scheduler_stats(request: Request):
    """Get scheduler statistics."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return JSONResponse(status_code=503, content={"error": "Agent not initialized"})

    return scheduler.get_stats()
