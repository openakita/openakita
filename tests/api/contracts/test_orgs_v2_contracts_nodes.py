"""Contract tests for cluster 3.2 Node lifecycle endpoints (B18-B33).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_nodes.py``
(P9.7beta-2). Cluster covers schedules CRUD (B18-B21), identity
markdown files (B22-B23), MCP config JSON (B24-B25), node status
controllers (B26-B29), and observability snapshots (B30-B33).

All tests target the duck-typed mock subsystems wired by the
shared conftest. Async runtime methods are exercised via
``MagicMock(side_effect=async_fn)`` so the route's ``await`` lands
on a real coroutine return.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _wire_org_node(app: FastAPI, org_dir: Path, node_id: str = "n1") -> None:
    """Wire ``mgr.get(org_id).get_node(node_id)`` + ``mgr.get_org_dir``."""
    org = MagicMock(spec=["get_node", "to_dict"])
    org.get_node.return_value = MagicMock(id=node_id) if node_id else None
    app.state.org_manager.get.return_value = org
    app.state.org_manager.get_org_dir.return_value = str(org_dir)


from tests.api.contracts.conftest import _async_return


# ---------------------------------------------------------------------------
# B18-B21: schedules CRUD
# ---------------------------------------------------------------------------


def test_b18_list_schedules_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_org_node(mint_app, Path("/tmp"))
    sched = MagicMock(spec=["to_dict"])
    sched.to_dict.return_value = {"id": "s1", "type": "cron"}
    mint_app.state.org_manager.get_node_schedules.return_value = [sched]
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/schedules")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "s1", "type": "cron"}]


def test_b18_list_schedules_404_when_org_missing(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.get("/api/v2/orgs/missing/nodes/n1/schedules")
    assert resp.status_code == 404


def test_b18_list_schedules_404_when_node_missing(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    _wire_org_node(mint_app, Path("/tmp"), node_id=None)
    resp = mint_client.get("/api/v2/orgs/o1/nodes/missing/schedules")
    assert resp.status_code == 404


def test_b19_create_schedule_returns_201(
    mint_app: FastAPI,
    mint_client: TestClient,
    monkeypatch,
) -> None:
    _wire_org_node(mint_app, Path("/tmp"))
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "s1", "type": "cron"}
    mint_app.state.org_manager.add_node_schedule.return_value = fake
    # Bypass NodeSchedule.from_dict by patching it to return the input dict.
    import openakita.orgs as runtime_pkg

    monkeypatch.setattr(
        runtime_pkg.NodeSchedule, "from_dict", staticmethod(lambda b: b), raising=False
    )
    resp = mint_client.post(
        "/api/v2/orgs/o1/nodes/n1/schedules",
        json={"type": "cron", "expression": "0 0 * * *"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "s1"


def test_b19_create_schedule_404_when_node_missing(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    _wire_org_node(mint_app, Path("/tmp"), node_id=None)
    resp = mint_client.post("/api/v2/orgs/o1/nodes/missing/schedules", json={"type": "cron"})
    assert resp.status_code == 404


def test_b20_update_schedule_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "s1", "enabled": False}
    mint_app.state.org_manager.update_node_schedule.return_value = fake
    resp = mint_client.put("/api/v2/orgs/o1/nodes/n1/schedules/s1", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_b20_update_schedule_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.update_node_schedule.return_value = None
    resp = mint_client.put("/api/v2/orgs/o1/nodes/n1/schedules/missing", json={})
    assert resp.status_code == 404


def test_b21_delete_schedule_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete_node_schedule.return_value = True
    resp = mint_client.delete("/api/v2/orgs/o1/nodes/n1/schedules/s1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_b21_delete_schedule_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete_node_schedule.return_value = False
    resp = mint_client.delete("/api/v2/orgs/o1/nodes/n1/schedules/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B22-B23: identity files
# ---------------------------------------------------------------------------


def test_b22_get_identity_returns_none_for_missing_files(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"SOUL.md", "AGENT.md", "ROLE.md"}
    assert all(v is None for v in body.values())


def test_b22_get_identity_reads_existing_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    base = tmp_path / "nodes" / "n1" / "identity"
    base.mkdir(parents=True)
    (base / "SOUL.md").write_text("hello", encoding="utf-8")
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["SOUL.md"] == "hello"
    assert body["AGENT.md"] is None


def test_b22_get_identity_404_on_missing_org(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.get("/api/v2/orgs/missing/nodes/n1/identity")
    assert resp.status_code == 404


def test_b23_update_identity_writes_files(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.put(
        "/api/v2/orgs/o1/nodes/n1/identity",
        json={"SOUL.md": "new content", "AGENT.md": "behave"},
    )
    assert resp.status_code == 200
    assert (tmp_path / "nodes" / "n1" / "identity" / "SOUL.md").read_text(
        encoding="utf-8"
    ) == "new content"


def test_b23_update_identity_empty_string_unlinks(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    base = tmp_path / "nodes" / "n1" / "identity"
    base.mkdir(parents=True)
    (base / "ROLE.md").write_text("old", encoding="utf-8")
    resp = mint_client.put("/api/v2/orgs/o1/nodes/n1/identity", json={"ROLE.md": ""})
    assert resp.status_code == 200
    assert not (base / "ROLE.md").exists()


# ---------------------------------------------------------------------------
# B24-B25: MCP config
# ---------------------------------------------------------------------------


def test_b24_get_mcp_default_inherit(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "inherit"}


def test_b24_get_mcp_reads_existing_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    base = tmp_path / "nodes" / "n1"
    base.mkdir(parents=True)
    (base / "mcp_config.json").write_text(
        '{"mode": "override", "servers": ["a"]}', encoding="utf-8"
    )
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/mcp")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "override"


def test_b25_update_mcp_writes_file(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.put("/api/v2/orgs/o1/nodes/n1/mcp", json={"mode": "override", "servers": []})
    assert resp.status_code == 200
    p = tmp_path / "nodes" / "n1" / "mcp_config.json"
    assert p.is_file()


def test_b25_update_mcp_404_on_missing_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_org_node(mint_app, Path("/tmp"), node_id=None)
    resp = mint_client.put("/api/v2/orgs/o1/nodes/missing/mcp", json={"mode": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B26-B29: status controllers (freeze/unfreeze/offline/online)
# ---------------------------------------------------------------------------


def test_b26_freeze_node_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.freeze_node = _async_return({"result": "frozen"})
    resp = mint_client.post("/api/v2/orgs/o1/nodes/n1/freeze", json={"reason": "manual"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    mint_app.state.org_runtime.freeze_node.assert_called_once()


def test_b26_freeze_node_default_reason(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.freeze_node = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/nodes/n1/freeze")
    assert resp.status_code == 200
    args, kwargs = mint_app.state.org_runtime.freeze_node.call_args
    assert kwargs.get("reason") == "user action"


def test_b27_unfreeze_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.unfreeze_node = _async_return({"result": "unfrozen"})
    resp = mint_client.post("/api/v2/orgs/o1/nodes/n1/unfreeze")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_b28_offline_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.set_node_status = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/nodes/n1/offline")
    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"
    mint_app.state.org_runtime.set_node_status.assert_called_once_with("o1", "n1", "offline")


def test_b29_online_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.set_node_status = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/nodes/n1/online")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"
    mint_app.state.org_runtime.set_node_status.assert_called_once_with("o1", "n1", "idle")


# ---------------------------------------------------------------------------
# B30-B33: dismiss + observability snapshots
# ---------------------------------------------------------------------------


def test_b30_dismiss_node_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.dismiss_node = _async_return(True)
    resp = mint_client.delete("/api/v2/orgs/o1/nodes/n1/dismiss")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_b30_dismiss_node_400_when_refused(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.dismiss_node = _async_return(False)
    resp = mint_client.delete("/api/v2/orgs/o1/nodes/n1/dismiss")
    assert resp.status_code == 400


def test_b31_get_node_thinking(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_node_thinking.return_value = {
        "node_id": "n1",
        "timeline": [{"event": "thinking"}],
    }
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/thinking")
    assert resp.status_code == 200
    assert resp.json()["node_id"] == "n1"


def test_b32_preview_node_prompt(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.preview_node_prompt.return_value = {
        "node_id": "n1",
        "full_prompt": "...",
        "char_count": 3,
    }
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/prompt-preview")
    assert resp.status_code == 200
    assert resp.json()["char_count"] == 3


def test_b33_get_node_status_snapshot(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_node_status_snapshot.return_value = {
        "id": "n1",
        "status": "idle",
        "thinking": [],
    }
    resp = mint_client.get("/api/v2/orgs/o1/nodes/n1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "n1"
    assert body["status"] == "idle"
