"""API-layer tests for the playbook-capable scheduler create/update routes.

These tests exercise TaskCreateRequest / TaskUpdateRequest through the actual
FastAPI routes mounted on a minimal app, with the scheduler stubbed to a
MagicMock so the tests do not require a running scheduler loop."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app_with_scheduler(scheduler_mock):
    """Build a FastAPI app with the scheduler router mounted and a stub scheduler."""
    from openakita.api.routes.scheduler import router

    app = FastAPI()
    app.state.agent = SimpleNamespace(task_scheduler=scheduler_mock)
    app.include_router(router)
    return app


def _fresh_scheduler_mock():
    sched = MagicMock()
    captured = MagicMock()
    captured.task = None

    async def _add(task):
        captured.task = task
        return task.id

    sched.add_task = _add
    sched.captured = captured
    return sched


@pytest.mark.asyncio
async def test_create_task_without_new_fields_still_accepted():
    """Backward-compat guard: requests that omit action/metadata/agent_profile_id
    must continue to work (existing reminder + task flows)."""
    sched = _fresh_scheduler_mock()
    app = _make_app_with_scheduler(sched)
    with TestClient(app) as client, \
         patch("openakita.api.routes.scheduler._notify_scheduler_change"):
        res = client.post("/api/scheduler/tasks", json={
            "name": "basic reminder",
            "task_type": "reminder",
            "trigger_type": "once",
            "trigger_config": {"run_at": "2030-01-01T00:00:00"},
            "reminder_message": "hi",
        })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert sched.captured.task is not None
    assert sched.captured.task.action is None
    assert sched.captured.task.metadata == {}


@pytest.mark.asyncio
async def test_create_task_accepts_action_metadata_profile():
    """Playbook POST: action + metadata + agent_profile_id must land on the
    ScheduledTask instance so the scheduler dispatcher routes correctly."""
    sched = _fresh_scheduler_mock()
    app = _make_app_with_scheduler(sched)
    playbook_meta = {
        "playbook": {
            "documents": [{"filename": "/abs/plan.md", "reset_on_completion": False}],
            "prompt": "Do the next unchecked task.",
            "loop_enabled": False,
            "max_loops": None,
            "worktree": {"enabled": False},
        }
    }
    with TestClient(app) as client, \
         patch("openakita.api.routes.scheduler._notify_scheduler_change"):
        res = client.post("/api/scheduler/tasks", json={
            "name": "nightly playbook",
            "task_type": "task",
            "trigger_type": "cron",
            "trigger_config": {"cron": "0 2 * * *"},
            "action": "system:autorun_playbook",
            "agent_profile_id": "claude-code-pair",
            "metadata": playbook_meta,
        })
    assert res.status_code == 200, res.text
    saved = sched.captured.task
    assert saved.action == "system:autorun_playbook"
    assert saved.agent_profile_id == "claude-code-pair"
    assert saved.metadata == playbook_meta


def _existing_task_stub(task_id: str = "task_abc"):
    """A MagicMock that looks like a ScheduledTask the scheduler would return
    from get_task. Only the fields the update handler reads matter."""
    task = MagicMock()
    task.id = task_id
    task.action = None
    task.metadata = {}
    task.agent_profile_id = "default"
    task.name = "existing"
    return task


@pytest.mark.asyncio
async def test_update_task_forwards_action_metadata_profile():
    """PUT route: changing action + metadata + agent_profile_id must reach the
    stored ScheduledTask. Omitted fields are left untouched (partial update
    semantics mirror every other field on this endpoint)."""
    existing = _existing_task_stub()
    sched = MagicMock()
    sched.get_task = MagicMock(return_value=existing)
    sched.update_task = AsyncMock(return_value=True)
    app = _make_app_with_scheduler(sched)

    new_meta = {"playbook": {"documents": [{"filename": "/a.md"}], "prompt": "x"}}
    with TestClient(app) as client, \
         patch("openakita.api.routes.scheduler._notify_scheduler_change"):
        res = client.put(f"/api/scheduler/tasks/{existing.id}", json={
            "action": "system:autorun_playbook",
            "metadata": new_meta,
            "agent_profile_id": "claude-code-pair",
        })

    assert res.status_code == 200, res.text
    sched.update_task.assert_awaited_once()
    _args, kwargs = sched.update_task.await_args
    updates = kwargs.get("updates") or _args[1]
    assert updates["action"] == "system:autorun_playbook"
    assert updates["metadata"] == new_meta
    assert updates["agent_profile_id"] == "claude-code-pair"


@pytest.mark.asyncio
async def test_update_task_partial_leaves_metadata_untouched():
    """PUT without action/metadata/agent_profile_id must NOT include them in
    the updates dict (otherwise we'd silently overwrite stored metadata with
    None)."""
    existing = _existing_task_stub()
    sched = MagicMock()
    sched.get_task = MagicMock(return_value=existing)
    sched.update_task = AsyncMock(return_value=True)
    app = _make_app_with_scheduler(sched)

    with TestClient(app) as client, \
         patch("openakita.api.routes.scheduler._notify_scheduler_change"):
        res = client.put(f"/api/scheduler/tasks/{existing.id}", json={
            "name": "renamed",
        })

    assert res.status_code == 200, res.text
    _args, kwargs = sched.update_task.await_args
    updates = kwargs.get("updates") or _args[1]
    assert "action" not in updates
    assert "metadata" not in updates
    assert "agent_profile_id" not in updates
    assert updates["name"] == "renamed"
