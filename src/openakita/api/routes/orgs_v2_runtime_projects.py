"""Projects + tasks endpoints (P-RC-9 P9.7beta-6).

Mints cluster 3.6 of ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
-- 16 endpoints (B68-B83) covering project CRUD, task CRUD inside
projects, task dispatch / cancel (cross-subsystem to OrgCommandService),
cross-project task aggregation, single-task detail / tree / timeline,
and per-node task / active-plan queries.

Wiring matrix:

* projects + tasks CRUD -> :class:`ProjectStore` (P9.2) via the
  ``_get_project_store`` helper. ``ProjectCreate`` /
  ``ProjectPatch`` Pydantic shapes (D-3 LOCKED) parse the bodies.
* task dispatch (B76) -> ``ProjectStore.get_task`` +
  :class:`OrgCommandService.submit` (cross-subsystem).
* task cancel (B77) -> ``ProjectStore.update_task`` with status
  flip + optional OrgRuntime.cancel_node_task.

ADR refs: ADR-0011 (D-3 layer separation), ADR-0012 (no shim
under v1; ``OrgProject`` / ``ProjectTask`` constructed from
``openakita.orgs`` not v1).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request

from openakita.api.schemas.orgs_v2 import ProjectCreate, ProjectPatch

from .orgs_v2_runtime import (
    _get_command_service,
    _get_project_store,
    _get_runtime,
    router,
)

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


# ---------------------------------------------------------------------------
# B68-B72: project CRUD
# ---------------------------------------------------------------------------


@router.get("/{org_id}/projects", summary="B68 list projects")
def list_projects(request: Request, org_id: str) -> list[dict[str, Any]]:
    return [_to_dict(p) for p in _get_project_store(request).list_projects()]


@router.post("/{org_id}/projects", summary="B69 create project")
def create_project(request: Request, org_id: str, body: ProjectCreate) -> dict[str, Any]:
    from openakita.orgs import OrgProject, ProjectStatus, ProjectType

    proj = OrgProject(
        org_id=org_id,
        name=body.name,
        description=body.description,
        project_type=ProjectType(body.project_type.value),
        status=ProjectStatus.PLANNING,
        owner_node_id=body.owner_node_id,
    )
    return _to_dict(_get_project_store(request).create_project(proj))


@router.get("/{org_id}/projects/{project_id}", summary="B70 get project")
def get_project(request: Request, org_id: str, project_id: str) -> dict[str, Any]:
    proj = _get_project_store(request).get_project(project_id)
    if proj is None:
        raise HTTPException(404, "Project not found")
    return _to_dict(proj)


@router.put("/{org_id}/projects/{project_id}", summary="B71 update project")
def update_project(
    request: Request, org_id: str, project_id: str, body: ProjectPatch
) -> dict[str, Any]:
    from openakita.orgs import ProjectStatus, ProjectType

    updates: dict[str, Any] = body.model_dump(exclude_none=True)
    if "status" in updates:
        updates["status"] = ProjectStatus(updates["status"])
    if "project_type" in updates:
        updates["project_type"] = ProjectType(updates["project_type"])
    proj = _get_project_store(request).update_project(project_id, updates)
    if proj is None:
        raise HTTPException(404, "Project not found")
    return _to_dict(proj)


@router.delete("/{org_id}/projects/{project_id}", summary="B72 delete project")
def delete_project(request: Request, org_id: str, project_id: str) -> dict[str, Any]:
    if not _get_project_store(request).delete_project(project_id):
        raise HTTPException(404, "Project not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B73-B77: tasks CRUD + dispatch + cancel
# ---------------------------------------------------------------------------


@router.post("/{org_id}/projects/{project_id}/tasks", summary="B73 create task")
async def create_task(request: Request, org_id: str, project_id: str) -> dict[str, Any]:
    from openakita.orgs import ProjectTask, TaskStatus

    body = await request.json()
    task = ProjectTask(
        project_id=project_id,
        title=body.get("title", ""),
        description=body.get("description", ""),
        status=TaskStatus(body.get("status", "todo")),
        assignee_node_id=body.get("assignee_node_id"),
        delegated_by=body.get("delegated_by"),
        chain_id=body.get("chain_id"),
        priority=body.get("priority", 0),
    )
    result = _get_project_store(request).add_task(project_id, task)
    if result is None:
        raise HTTPException(404, "Project not found")
    return _to_dict(result)


@router.put("/{org_id}/projects/{project_id}/tasks/{task_id}", summary="B74 update task")
async def update_task(
    request: Request, org_id: str, project_id: str, task_id: str
) -> dict[str, Any]:
    from openakita.orgs import TaskStatus

    body = await request.json()
    updates: dict[str, Any] = {}
    for key in (
        "title",
        "description",
        "assignee_node_id",
        "delegated_by",
        "chain_id",
        "priority",
        "progress_pct",
        "started_at",
        "delivered_at",
        "completed_at",
    ):
        if key in body:
            updates[key] = body[key]
    if "status" in body:
        updates["status"] = TaskStatus(body["status"])
    task = _get_project_store(request).update_task(project_id, task_id, updates)
    if task is None:
        raise HTTPException(404, "Task not found")
    return _to_dict(task)


@router.delete("/{org_id}/projects/{project_id}/tasks/{task_id}", summary="B75 delete task")
def delete_task(request: Request, org_id: str, project_id: str, task_id: str) -> dict[str, Any]:
    if not _get_project_store(request).delete_task(project_id, task_id):
        raise HTTPException(404, "Task not found")
    return {"ok": True}


@router.post(
    "/{org_id}/projects/{project_id}/tasks/{task_id}/dispatch",
    summary="B76 dispatch task to organization",
)
async def dispatch_task(
    request: Request, org_id: str, project_id: str, task_id: str
) -> dict[str, Any]:
    store = _get_project_store(request)
    task_data, _ = store.get_task(task_id)
    if task_data is None:
        raise HTTPException(404, "Task not found")
    if getattr(task_data, "project_id", project_id) != project_id:
        raise HTTPException(404, "Task not found in this project")
    chain_id = f"dispatch:{task_id}:{uuid.uuid4().hex[:8]}"
    store.update_task(project_id, task_id, {"status": "in_progress", "chain_id": chain_id})
    # Cross-subsystem call: enqueue the task on OrgCommandService.
    svc = _get_command_service(request)
    target = getattr(task_data, "assignee_node_id", None)
    payload = {
        "org_id": org_id,
        "task_id": task_id,
        "chain_id": chain_id,
        "target_node_id": target,
        "title": getattr(task_data, "title", ""),
    }
    submit = getattr(svc, "submit_task_dispatch", None)
    if callable(submit):
        try:
            await submit(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[OrgCmd] dispatch enqueue failed: %s", exc)
    return {"ok": True, "task_id": task_id, "chain_id": chain_id, "dispatched": True}


@router.post(
    "/{org_id}/projects/{project_id}/tasks/{task_id}/cancel",
    summary="B77 cancel dispatched task",
)
async def cancel_dispatched_task(
    request: Request, org_id: str, project_id: str, task_id: str
) -> dict[str, Any]:
    from openakita.orgs import TaskStatus

    store = _get_project_store(request)
    task_data, _ = store.get_task(task_id)
    if task_data is None:
        raise HTTPException(404, "Task not found")
    if getattr(task_data, "project_id", project_id) != project_id:
        raise HTTPException(404, "Task not found in this project")
    if getattr(task_data, "status", None) != TaskStatus.IN_PROGRESS:
        return {"ok": False, "error": "Task is not in_progress"}
    rt = _get_runtime(request)
    target = getattr(task_data, "assignee_node_id", None)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = body.get("reason", "user cancel")
    cancel = getattr(rt, "cancel_node_task", None)
    if callable(cancel) and target:
        try:
            await cancel(org_id, target, reason)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[OrgRT] cancel_node_task failed: %s", exc)
    store.update_task(project_id, task_id, {"status": TaskStatus.CANCELLED, "chain_id": None})
    return {"ok": True, "task_id": task_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# B78-B81: task aggregation + detail + tree + timeline
# ---------------------------------------------------------------------------


@router.get("/{org_id}/tasks", summary="B78 cross-project task list")
def list_all_tasks(request: Request, org_id: str) -> list[dict[str, Any]]:
    qp = request.query_params
    return _get_project_store(request).all_tasks(
        status=qp.get("status"),
        assignee=qp.get("assignee"),
        chain_id=qp.get("chain_id"),
        parent_task_id=qp.get("parent_task_id"),
        root_only=qp.get("root_only", "").lower() == "true",
        project_id=qp.get("project_id"),
    )


@router.get("/{org_id}/tasks/{task_id}", summary="B79 single task detail")
def get_task_detail(request: Request, org_id: str, task_id: str) -> dict[str, Any]:
    store = _get_project_store(request)
    task, _ = store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    result = _to_dict(task)
    if isinstance(result, dict):
        result["subtasks"] = [_to_dict(t) for t in store.get_subtasks(task_id)]
        result["ancestors"] = [_to_dict(t) for t in store.get_ancestors(task_id)]
    return result


@router.get("/{org_id}/tasks/{task_id}/tree", summary="B80 task subtask tree")
def get_task_tree(request: Request, org_id: str, task_id: str) -> dict[str, Any]:
    tree = _get_project_store(request).get_task_tree(task_id)
    if not tree:
        raise HTTPException(404, "Task not found")
    return tree


@router.get("/{org_id}/tasks/{task_id}/timeline", summary="B81 task timeline")
def get_task_timeline(request: Request, org_id: str, task_id: str) -> dict[str, Any]:
    store = _get_project_store(request)
    task, _ = store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    timeline: list[dict[str, Any]] = []
    for entry in getattr(task, "execution_log", None) or []:
        e = entry if isinstance(entry, dict) else {}
        timeline.append(
            {
                "ts": e.get("at", e.get("ts", "")),
                "event": e.get("event", "execution"),
                "actor": e.get("by", e.get("actor", "")),
                "detail": e.get("entry", e.get("detail", "")),
            }
        )
    es = _get_runtime(request).get_event_store(org_id)
    if es is not None:
        chain_id = getattr(task, "chain_id", None)
        events = (
            es.query(
                chain_id=chain_id,
                task_id=task_id if not chain_id else None,
                limit=100,
            )
            or []
        )
        for ev in events:
            timeline.append(
                {
                    "ts": ev.get("timestamp", ""),
                    "event": ev.get("event_type", ""),
                    "actor": ev.get("actor", ""),
                    "detail": str(ev.get("data", "")),
                }
            )
    timeline.sort(key=lambda x: x.get("ts", ""))
    return {"task_id": task_id, "timeline": timeline}


# ---------------------------------------------------------------------------
# B82-B83: per-node task + active-plan queries
# ---------------------------------------------------------------------------


@router.get("/{org_id}/nodes/{node_id}/tasks", summary="B82 per-node tasks")
def get_node_tasks(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    store = _get_project_store(request)
    return {
        "assigned": store.all_tasks(assignee=node_id),
        "delegated": store.all_tasks(delegated_by=node_id),
    }


@router.get("/{org_id}/nodes/{node_id}/active-plan", summary="B83 node active plan")
def get_node_active_plan(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    tasks = _get_project_store(request).all_tasks(assignee=node_id)
    active = [t for t in tasks if t.get("status") == "in_progress" and t.get("plan_steps")]
    if not active:
        return {"plan": None}
    task = active[0]
    return {
        "task_id": task.get("id"),
        "title": task.get("title"),
        "plan_steps": task.get("plan_steps", []),
        "progress_pct": task.get("progress_pct", 0),
    }
