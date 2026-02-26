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

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


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
    trigger_config: dict = {}
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
async def list_tasks(request: Request):
    """List all scheduled tasks."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized", "tasks": []}

    tasks = scheduler.list_tasks()
    return {
        "tasks": [t.to_dict() for t in tasks],
        "total": len(tasks),
    }


@router.get("/api/scheduler/tasks/{task_id}")
async def get_task(request: Request, task_id: str):
    """Get a single task by ID."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    task = scheduler.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

    return {"task": task.to_dict()}


@router.post("/api/scheduler/tasks")
async def create_task(request: Request, body: TaskCreateRequest):
    """Create a new scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    from openakita.scheduler.task import ScheduledTask, TaskType, TriggerType

    try:
        trigger_type = TriggerType(body.trigger_type)
    except ValueError:
        return {"error": f"Invalid trigger_type: {body.trigger_type}"}

    try:
        task_type = TaskType(body.task_type)
    except ValueError:
        return {"error": f"Invalid task_type: {body.task_type}"}

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
    task.channel_id = body.channel_id or None
    task.chat_id = body.chat_id or None
    task.enabled = body.enabled

    task_id = await scheduler.add_task(task)
    return {"status": "ok", "task_id": task_id, "task": task.to_dict()}


@router.put("/api/scheduler/tasks/{task_id}")
async def update_task(request: Request, task_id: str, body: TaskUpdateRequest):
    """Update an existing scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    task = scheduler.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

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
            return {"error": f"Invalid task_type: {body.task_type}"}

    if body.trigger_type is not None:
        from openakita.scheduler.task import TriggerType
        try:
            updates["trigger_type"] = TriggerType(body.trigger_type)
        except ValueError:
            return {"error": f"Invalid trigger_type: {body.trigger_type}"}

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
            return {"error": "Update failed"}

    if body.enabled is not None:
        if body.enabled:
            await scheduler.enable_task(task_id)
        else:
            await scheduler.disable_task(task_id)

    updated = scheduler.get_task(task_id)
    return {"status": "ok", "task": updated.to_dict() if updated else None}


@router.delete("/api/scheduler/tasks/{task_id}")
async def delete_task(request: Request, task_id: str):
    """Delete a scheduled task."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    task = scheduler.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

    if not task.deletable:
        return {"error": "System task cannot be deleted, use disable instead"}

    success = await scheduler.remove_task(task_id)
    if not success:
        return {"error": "Delete failed"}

    return {"status": "ok", "task_id": task_id}


@router.post("/api/scheduler/tasks/{task_id}/toggle")
async def toggle_task(request: Request, task_id: str):
    """Toggle task enabled/disabled."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    task = scheduler.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

    if task.enabled:
        await scheduler.disable_task(task_id)
    else:
        await scheduler.enable_task(task_id)

    updated = scheduler.get_task(task_id)
    return {"status": "ok", "task": updated.to_dict() if updated else None}


@router.post("/api/scheduler/tasks/{task_id}/trigger")
async def trigger_task(request: Request, task_id: str):
    """Trigger a task to run immediately."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    execution = await scheduler.trigger_now(task_id)
    if execution is None:
        return {"error": "Task not found or trigger failed"}

    return {"status": "ok", "execution": execution.to_dict()}


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
    seen: set[tuple[str, str]] = set()
    session_manager = getattr(gateway, "session_manager", None)

    skip_channels = {"desktop"}

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
                pair = (ch, cid)
                if pair in seen:
                    continue
                seen.add(pair)
                results.append({
                    "channel_id": ch,
                    "chat_id": cid,
                    "user_id": getattr(s, "user_id", None),
                    "last_active": getattr(s, "last_active", dt.min).isoformat(),
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
                        pair = (ch, cid)
                        if pair in seen:
                            continue
                        seen.add(pair)
                        results.append({
                            "channel_id": ch,
                            "chat_id": cid,
                            "user_id": s.get("user_id"),
                            "last_active": s.get("last_active", ""),
                        })
                except Exception as e:
                    logger.warning(f"Failed to read sessions file: {e}")

        # 3. Channel registry (persists even after sessions expire)
        registry = getattr(session_manager, "_channel_registry", None)
        if registry and isinstance(registry, dict):
            for ch, entry in registry.items():
                if ch in skip_channels or not isinstance(entry, dict):
                    continue
                cid = entry.get("chat_id")
                if not cid:
                    continue
                pair = (ch, cid)
                if pair in seen:
                    continue
                seen.add(pair)
                results.append({
                    "channel_id": ch,
                    "chat_id": cid,
                    "user_id": entry.get("user_id"),
                    "last_active": entry.get("last_seen", ""),
                })

    return {"channels": results}


@router.get("/api/scheduler/stats")
async def scheduler_stats(request: Request):
    """Get scheduler statistics."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return {"error": "Agent not initialized"}

    return scheduler.get_stats()
