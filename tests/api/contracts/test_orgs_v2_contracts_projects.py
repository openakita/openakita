"""Contract tests for cluster 3.6 Projects + tasks (B68-B83).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_projects.py``
(P9.7beta-6). Largest of the six clusters (16 endpoints) covering
project CRUD (B68-B72), task CRUD inside projects (B73-B75), task
dispatch / cancel cross-subsystem (B76-B77), cross-project task
aggregation (B78), single-task detail / tree / timeline (B79-B81),
and per-node task / active-plan queries (B82-B83).

Mocks the ProjectStore with duck-typed envelopes returning
``to_dict()`` shapes plus ``(task, project)`` tuples for the
``get_task`` accessor. Cross-subsystem dispatch (B76) wires both
the ProjectStore + OrgCommandService + OrgRuntime; cancel (B77)
wires ProjectStore + OrgRuntime.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.contracts.conftest import _async_return, fake_project, fake_task


# ---------------------------------------------------------------------------
# B68-B72: project CRUD
# ---------------------------------------------------------------------------


def test_b68_list_projects_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.list_projects.return_value = []
    resp = mint_client.get("/api/v2/orgs/o1/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b68_list_projects_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.list_projects.return_value = [
        fake_project("p1"),
        fake_project("p2"),
    ]
    resp = mint_client.get("/api/v2/orgs/o1/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert {p["id"] for p in body} == {"p1", "p2"}


def test_b69_create_project_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.create_project.return_value = fake_project("p3")
    resp = mint_client.post("/api/v2/orgs/o1/projects", json={"name": "Sprint", "description": "X"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "p3"


def test_b69_create_project_422_when_name_missing(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/projects", json={})
    assert resp.status_code == 422


def test_b69_create_project_422_when_extra_field(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/projects", json={"name": "X", "evil": True})
    assert resp.status_code == 422


def test_b70_get_project_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_project.return_value = fake_project("p1")
    resp = mint_client.get("/api/v2/orgs/o1/projects/p1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "p1"


def test_b70_get_project_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_project.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/projects/missing")
    assert resp.status_code == 404


def test_b71_update_project_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_project.return_value = fake_project(
        "p1", description="updated"
    )
    resp = mint_client.put("/api/v2/orgs/o1/projects/p1", json={"description": "updated"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated"


def test_b71_update_project_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_project.return_value = None
    resp = mint_client.put("/api/v2/orgs/o1/projects/missing", json={"description": "x"})
    assert resp.status_code == 404


def test_b71_update_project_422_extra_field(mint_client: TestClient) -> None:
    resp = mint_client.put("/api/v2/orgs/o1/projects/p1", json={"description": "x", "evil": 1})
    assert resp.status_code == 422


def test_b72_delete_project_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_project.return_value = True
    resp = mint_client.delete("/api/v2/orgs/o1/projects/p1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_b72_delete_project_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_project.return_value = False
    resp = mint_client.delete("/api/v2/orgs/o1/projects/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B73-B75: tasks CRUD
# ---------------------------------------------------------------------------


def test_b73_create_task_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.add_task.return_value = fake_task("t1", "p1")
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks", json={"title": "Task A"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "t1"


def test_b73_create_task_404_when_project_missing(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.project_store.add_task.return_value = None
    resp = mint_client.post("/api/v2/orgs/o1/projects/missing/tasks", json={"title": "X"})
    assert resp.status_code == 404


def test_b74_update_task_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_task.return_value = fake_task("t1", "p1")
    resp = mint_client.put("/api/v2/orgs/o1/projects/p1/tasks/t1", json={"title": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "t1"


def test_b74_update_task_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_task.return_value = None
    resp = mint_client.put("/api/v2/orgs/o1/projects/p1/tasks/missing", json={})
    assert resp.status_code == 404


def test_b75_delete_task_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_task.return_value = True
    resp = mint_client.delete("/api/v2/orgs/o1/projects/p1/tasks/t1")
    assert resp.status_code == 200


def test_b75_delete_task_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_task.return_value = False
    resp = mint_client.delete("/api/v2/orgs/o1/projects/p1/tasks/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B76: dispatch task
# ---------------------------------------------------------------------------


def test_b76_dispatch_task_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    task = MagicMock(project_id="p1", assignee_node_id="n1", title="Task")
    mint_app.state.project_store.get_task.return_value = (task, None)
    mint_app.state.org_command_service.submit_task_dispatch = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/t1/dispatch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["dispatched"] is True
    assert body["chain_id"].startswith("dispatch:t1:")


def test_b76_dispatch_task_404_when_task_missing(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.project_store.get_task.return_value = (None, None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/missing/dispatch")
    assert resp.status_code == 404


def test_b76_dispatch_task_404_when_project_mismatch(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    task = MagicMock(project_id="other_project", assignee_node_id="n1", title="X")
    mint_app.state.project_store.get_task.return_value = (task, None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/t1/dispatch")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B77: cancel task
# ---------------------------------------------------------------------------


def test_b77_cancel_task_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import TaskStatus

    task = MagicMock(
        project_id="p1",
        status=TaskStatus.IN_PROGRESS,
        assignee_node_id="n1",
        chain_id="c1",
    )
    mint_app.state.project_store.get_task.return_value = (task, None)
    mint_app.state.org_runtime.cancel_node_task = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/t1/cancel", json={"reason": "user"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_b77_cancel_task_404_when_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task.return_value = (None, None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/missing/cancel", json={})
    assert resp.status_code == 404


def test_b77_cancel_task_returns_false_when_not_in_progress(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    from openakita.orgs import TaskStatus

    task = MagicMock(project_id="p1", status=TaskStatus.TODO)
    mint_app.state.project_store.get_task.return_value = (task, None)
    resp = mint_client.post("/api/v2/orgs/o1/projects/p1/tasks/t1/cancel", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not in_progress" in body["error"]


# ---------------------------------------------------------------------------
# B78-B81: task aggregation + detail + tree + timeline
# ---------------------------------------------------------------------------


def test_b78_list_all_tasks_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = [
        {"id": "t1", "status": "todo"},
        {"id": "t2", "status": "in_progress"},
    ]
    resp = mint_client.get("/api/v2/orgs/o1/tasks?status=todo")
    assert resp.status_code == 200
    assert {t["id"] for t in resp.json()} == {"t1", "t2"}


def test_b78_list_all_tasks_filters_propagate(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = []
    mint_client.get("/api/v2/orgs/o1/tasks?assignee=n1&chain_id=c1&root_only=true")
    args, kwargs = mint_app.state.project_store.all_tasks.call_args
    assert kwargs["assignee"] == "n1"
    assert kwargs["chain_id"] == "c1"
    assert kwargs["root_only"] is True


def test_b79_get_task_detail_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task.return_value = (fake_task("t1"), None)
    mint_app.state.project_store.get_subtasks.return_value = [fake_task("t2")]
    mint_app.state.project_store.get_ancestors.return_value = []
    resp = mint_client.get("/api/v2/orgs/o1/tasks/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "t1"
    assert body["subtasks"][0]["id"] == "t2"


def test_b79_get_task_detail_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task.return_value = (None, None)
    resp = mint_client.get("/api/v2/orgs/o1/tasks/missing")
    assert resp.status_code == 404


def test_b80_get_task_tree_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task_tree.return_value = {
        "id": "t1",
        "children": [{"id": "t2"}],
    }
    resp = mint_client.get("/api/v2/orgs/o1/tasks/t1/tree")
    assert resp.status_code == 200
    assert resp.json()["children"][0]["id"] == "t2"


def test_b80_get_task_tree_404_when_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task_tree.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/tasks/missing/tree")
    assert resp.status_code == 404


def test_b81_get_task_timeline_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    task = MagicMock(
        execution_log=[{"at": "2026-01-01T00:00:00", "event": "started"}],
        chain_id=None,
    )
    mint_app.state.project_store.get_task.return_value = (task, None)
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/tasks/t1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["event"] == "started"


def test_b81_get_task_timeline_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task.return_value = (None, None)
    resp = mint_client.get("/api/v2/orgs/o1/tasks/missing/timeline")
    assert resp.status_code == 404


def test_b81_get_task_timeline_merges_event_store(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    task = MagicMock(execution_log=[], chain_id="c1")
    mint_app.state.project_store.get_task.return_value = (task, None)
    es = MagicMock()
    es.query.return_value = [
        {"timestamp": "2026-01-02T00:00:00", "event_type": "delivered", "actor": "n1"},
    ]
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/o1/tasks/t1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["event"] == "delivered"


# ---------------------------------------------------------------------------
# B82-B83: per-node tasks + active-plan
# ---------------------------------------------------------------------------


def test_b82_node_tasks_envelope(mint_app: FastAPI, mint_client: TestClient) -> None:
    def _all(**kwargs):
        if kwargs.get("assignee") == "n1":
            return [{"id": "t1"}]
        if kwargs.get("delegated_by") == "n1":
            return [{"id": "t2"}]
        return []

    mint_app.state.project_store.all_tasks.side_effect = _all
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned"][0]["id"] == "t1"
    assert body["delegated"][0]["id"] == "t2"


def test_b83_active_plan_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = []
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/active-plan")
    assert resp.status_code == 200
    assert resp.json() == {"plan": None}


def test_b83_active_plan_returns_first_in_progress(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.project_store.all_tasks.return_value = [
        {
            "id": "t1",
            "title": "Big",
            "status": "in_progress",
            "plan_steps": [{"id": "s1"}],
            "progress_pct": 30,
        },
    ]
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/active-plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["progress_pct"] == 30
    assert len(body["plan_steps"]) == 1
