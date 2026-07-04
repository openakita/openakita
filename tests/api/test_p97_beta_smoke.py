"""P9.7beta smoke tests -- wiring sanity for the 83 mint endpoints.

Each cluster (B1-B83) gets at least one smoke test demonstrating
that the v2 route is mounted, parses its inputs, and delegates to
the expected ``app.state.*`` subsystem method. The full contract
suite (status-code matrix + error envelopes + side-effect
assertions + Pydantic response validation) rides P9.7gamma per
charter section 6 ("contract ~120 cases / ~1 600 LOC").

Smoke pattern:

* :class:`unittest.mock.MagicMock` stands in for each P9.1-P9.6
  subsystem on ``app.state``. The mocks are configured with the
  return values the endpoint passes back to the client, so the
  smoke can pin the 200/201 response shape without needing real
  ``OrgManager`` / ``OrgRuntime`` / ``ProjectStore`` instances.
* Tests assert (a) HTTP status code matches charter spec, (b) the
  expected subsystem method was called with the expected
  positional / kwargs payload, (c) the response envelope contains
  the keys the v1 oracle returned (where the v2 mint preserves
  the shape; gamma will lock byte-equality).

P9.7beta-1 ships the first 17 (cluster 3.1; B1-B17 -- Org CRUD +
templates + lifecycle). Subsequent beta commits append clusters
3.2-3.6.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2_runtime


@pytest.fixture
def mint_app() -> FastAPI:
    """A bare app with the v2 runtime router mounted and 6 mock subsystems."""
    app = FastAPI()
    app.state.org_manager = MagicMock(name="OrgManager")
    app.state.org_runtime = MagicMock(name="OrgRuntime")
    app.state.org_command_service = MagicMock(name="OrgCommandService")
    app.state.org_blackboard = MagicMock(name="OrgBlackboard")
    app.state.project_store = MagicMock(name="ProjectStore")
    app.state.node_scheduler = MagicMock(name="NodeScheduler")
    # ``get_org_snapshot`` is OPTIONAL on the runtime -- the get_org
    # endpoint falls back to the manager when it is absent. Force the
    # absence here so the smoke pins the manager-path.
    app.state.org_runtime.get_org_snapshot = None
    app.include_router(orgs_v2_runtime.router)
    return app


@pytest.fixture
def mint_client(mint_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(mint_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Local helpers -- standard fake org/template dicts the mocks return.
# ---------------------------------------------------------------------------


def _fake_org(org_id: str = "org_test", name: str = "Test Org") -> Any:
    """Return an object with ``to_dict()`` returning a minimal org envelope."""
    obj = MagicMock(spec=["to_dict"])
    obj.to_dict.return_value = {
        "id": org_id,
        "name": name,
        "status": "dormant",
        "description": "",
        "nodes": [],
        "edges": [],
    }
    return obj


# ---------------------------------------------------------------------------
# B1-B2 -- list + create
# ---------------------------------------------------------------------------


def test_b1_list_orgs_delegates_to_manager(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.list_orgs.return_value = [
        {"id": "org_a", "name": "A", "status": "active"},
    ]
    resp = mint_client.get("/api/v2/orgs?include_archived=true")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "org_a"
    mint_app.state.org_manager.list_orgs.assert_called_once_with(include_archived=True)


def test_b2_create_org_returns_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create.return_value = _fake_org("org_new", "Marketing")
    resp = mint_client.post("/api/v2/orgs", json={"name": "Marketing"})
    assert resp.status_code == 201
    assert resp.json()["id"] == "org_new"
    mint_app.state.org_manager.create.assert_called_once()


def test_b2_create_org_rejects_missing_name(mint_client: TestClient) -> None:
    """OrgCreate ``name`` is required (min_length=1)."""
    resp = mint_client.post("/api/v2/orgs", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# B3-B4 -- avatars
# ---------------------------------------------------------------------------


def test_b3_avatar_presets_returns_bundled_list(mint_client: TestClient) -> None:
    """v2 reaches the free function in ``openakita.orgs._runtime_templates`` (was v1 ``tool_categories``)."""
    resp = mint_client.get("/api/v2/orgs/avatar-presets")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_b4_avatar_upload_writes_file_and_returns_url(
    mint_client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openakita.config import settings

    # ``data_dir`` is a @property computed as ``project_root / "data"``;
    # monkeypatch the underlying field instead.
    monkeypatch.setattr(settings, "project_root", tmp_path, raising=False)
    files = {"file": ("a.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 32), "image/png")}
    resp = mint_client.post("/api/v2/orgs/avatars/upload", files=files)
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("/api/avatars/")
    assert (tmp_path / "data" / "avatars" / body["filename"]).exists()


# ---------------------------------------------------------------------------
# B5-B7 -- templates
# ---------------------------------------------------------------------------


def test_b5_list_templates_delegates(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.list_templates.return_value = [{"id": "t1", "name": "Software"}]
    resp = mint_client.get("/api/v2/orgs/templates")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "t1"


def test_b6_plugin_workbench_templates_returns_list(mint_client: TestClient) -> None:
    """Free-function bridge; agent missing -> empty list."""
    resp = mint_client.get("/api/v2/orgs/plugin-workbench-templates")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_b7_get_template_404_when_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get_template.return_value = None
    resp = mint_client.get("/api/v2/orgs/templates/unknown")
    assert resp.status_code == 404


def test_b7_get_template_returns_payload(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get_template.return_value = {"id": "t1", "name": "Software"}
    resp = mint_client.get("/api/v2/orgs/templates/t1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Software"


# ---------------------------------------------------------------------------
# B8-B9 -- from-template + import
# ---------------------------------------------------------------------------


def test_b8_from_template_201_and_calls_manager(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create_from_template.return_value = _fake_org("org_t", "From T")
    resp = mint_client.post(
        "/api/v2/orgs/from-template",
        json={"template_id": "t1", "name": "From T"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "org_t"
    mint_app.state.org_manager.create_from_template.assert_called_once()


def test_b8_from_template_400_when_template_id_missing(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/from-template", json={"name": "x"})
    assert resp.status_code == 400


def test_b9_import_org_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create.return_value = _fake_org("org_imp", "Imp")
    payload = json.dumps({"organization": {"name": "Imp"}}).encode("utf-8")
    files = {"file": ("org.json", io.BytesIO(payload), "application/json")}
    resp = mint_client.post("/api/v2/orgs/import", files=files)
    assert resp.status_code == 201
    assert resp.json()["organization"]["id"] == "org_imp"


# ---------------------------------------------------------------------------
# B10-B12 -- single-org CRUD
# ---------------------------------------------------------------------------


def test_b10_get_org_uses_manager_fallback(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_x", "X")
    resp = mint_client.get("/api/v2/orgs/org_x")
    assert resp.status_code == 200
    assert resp.json()["name"] == "X"


def test_b10_get_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.get("/api/v2/orgs/nope")
    assert resp.status_code == 404


def test_b11_update_org_calls_manager_update(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_u", "Old")
    mint_app.state.org_manager.update.return_value = _fake_org("org_u", "NewName")
    resp = mint_client.put("/api/v2/orgs/org_u", json={"name": "NewName"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "NewName"
    mint_app.state.org_manager.update.assert_called_once()


def test_b11_update_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.put("/api/v2/orgs/missing", json={"name": "x"})
    assert resp.status_code == 404


def test_b12_delete_org_returns_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_d")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_b12_delete_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete.return_value = False
    resp = mint_client.delete("/api/v2/orgs/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B13-B17 -- duplicate / archive / unarchive / save-as-template / export
# ---------------------------------------------------------------------------


def test_b13_duplicate_org_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_src", "Src")
    mint_app.state.org_manager.duplicate.return_value = _fake_org("org_dup", "Src (copy)")
    resp = mint_client.post("/api/v2/orgs/org_src/duplicate", json={"name": "Src (copy)"})
    assert resp.status_code == 201
    assert resp.json()["id"] == "org_dup"


def test_b14_archive_org(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_a", "A")
    archived = _fake_org("org_a", "A")
    archived.to_dict.return_value["status"] = "archived"
    mint_app.state.org_manager.archive.return_value = archived
    resp = mint_client.post("/api/v2/orgs/org_a/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_b15_unarchive_org(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_a", "A")
    mint_app.state.org_manager.unarchive.return_value = _fake_org("org_a", "A")
    resp = mint_client.post("/api/v2/orgs/org_a/unarchive")
    assert resp.status_code == 200
    assert resp.json()["id"] == "org_a"


def test_b16_save_as_template_returns_template_id(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_a", "A")
    mint_app.state.org_manager.save_as_template.return_value = "tpl_xyz"
    resp = mint_client.post(
        "/api/v2/orgs/org_a/save-as-template",
        json={"template_id": "tpl_xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["template_id"] == "tpl_xyz"


def test_b17_export_org_returns_envelope(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org("org_a", "A")
    resp = mint_client.post("/api/v2/orgs/org_a/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "akita-org"
    assert body["organization"]["id"] == "org_a"


# ===========================================================================
# Cluster 3.2 -- Node lifecycle + schedules + identity + MCP (B18-B33).
# ===========================================================================


def _fake_org_with_node(org_id: str = "org_x", node_id: str = "n1") -> Any:
    org = MagicMock(spec=["get_node", "to_dict"])
    node = MagicMock(id=node_id, role_title="r", status=MagicMock(value="idle"))
    org.get_node.return_value = node
    org.to_dict.return_value = {"id": org_id, "name": "X", "nodes": [{"id": node_id}]}
    return org


def _wire_org_node(mint_app: FastAPI, tmp_path) -> None:
    mint_app.state.org_manager.get.return_value = _fake_org_with_node()
    mint_app.state.org_manager.get_org_dir.return_value = str(tmp_path / "org_x")


# ---------------------------------------------------------------------------
# B18-B21: schedules CRUD
# ---------------------------------------------------------------------------


def test_b18_list_node_schedules(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_org_node(mint_app, Path("./_unused"))
    sched = MagicMock(spec=["to_dict"])
    sched.to_dict.return_value = {"id": "s1", "type": "cron"}
    mint_app.state.org_manager.get_node_schedules.return_value = [sched]
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/schedules")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "s1", "type": "cron"}]


def test_b18_list_404_missing_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    org = MagicMock(spec=["get_node", "to_dict"])
    org.get_node.return_value = None
    mint_app.state.org_manager.get.return_value = org
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/missing/schedules")
    assert resp.status_code == 404


def test_b19_create_node_schedule(
    mint_app: FastAPI, mint_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_org_node(mint_app, Path("./_unused"))
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "s1", "type": "interval"}
    # ``NodeSchedule.from_dict`` is exercised inside the route; stub it.
    from openakita.orgs import NodeSchedule

    monkeypatch.setattr(NodeSchedule, "from_dict", staticmethod(lambda d: d), raising=False)
    mint_app.state.org_manager.add_node_schedule.return_value = fake
    # The route hardens against trigger-less schedules (v10 E3.8): an ``interval``
    # schedule must carry ``interval_s`` (or run_at/cron) or it would never fire
    # and the endpoint returns 422. Send a complete interval body so the smoke
    # test exercises the happy path it intends.
    resp = mint_client.post(
        "/api/v2/orgs/org_x/nodes/n1/schedules",
        json={"schedule_type": "interval", "interval_s": 60},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "s1"


def test_b20_update_node_schedule(mint_app: FastAPI, mint_client: TestClient) -> None:
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "s1"}
    mint_app.state.org_manager.update_node_schedule.return_value = fake
    resp = mint_client.put("/api/v2/orgs/org_x/nodes/n1/schedules/s1", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["id"] == "s1"


def test_b20_update_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.update_node_schedule.return_value = None
    resp = mint_client.put("/api/v2/orgs/org_x/nodes/n1/schedules/missing", json={})
    assert resp.status_code == 404


def test_b21_delete_node_schedule(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete_node_schedule.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_x/nodes/n1/schedules/s1")
    assert resp.status_code == 200


def test_b21_delete_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete_node_schedule.return_value = False
    resp = mint_client.delete("/api/v2/orgs/org_x/nodes/n1/schedules/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B22-B25: identity + MCP file IO
# ---------------------------------------------------------------------------


def test_b22_get_node_identity_missing_files(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/identity")
    assert resp.status_code == 200
    assert resp.json() == {"SOUL.md": None, "AGENT.md": None, "ROLE.md": None}


def test_b23_update_node_identity_writes_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.put(
        "/api/v2/orgs/org_x/nodes/n1/identity",
        json={"ROLE.md": "I am a node"},
    )
    assert resp.status_code == 200
    written = tmp_path / "org_x" / "nodes" / "n1" / "identity" / "ROLE.md"
    assert written.read_text(encoding="utf-8") == "I am a node"


def test_b24_get_node_mcp_default_inherit(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "inherit"}


def test_b25_update_node_mcp_writes_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_node(mint_app, tmp_path)
    resp = mint_client.put(
        "/api/v2/orgs/org_x/nodes/n1/mcp",
        json={"mode": "override", "servers": []},
    )
    assert resp.status_code == 200
    written = tmp_path / "org_x" / "nodes" / "n1" / "mcp_config.json"
    assert json.loads(written.read_text(encoding="utf-8"))["mode"] == "override"


# ---------------------------------------------------------------------------
# B26-B29: status controllers
# ---------------------------------------------------------------------------


def test_b26_freeze_node_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _ok(*args, **kwargs):
        return {"result": "frozen"}

    mint_app.state.org_runtime.freeze_node = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/nodes/n1/freeze", json={"reason": "test"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_b27_unfreeze_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _ok(*args, **kwargs):
        return {"result": "unfrozen"}

    mint_app.state.org_runtime.unfreeze_node = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/nodes/n1/unfreeze")
    assert resp.status_code == 200


def test_b28_set_node_offline(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _ok(*args, **kwargs):
        return None

    mint_app.state.org_runtime.set_node_status = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/nodes/n1/offline")
    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"


def test_b29_set_node_online(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _ok(*args, **kwargs):
        return None

    mint_app.state.org_runtime.set_node_status = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/nodes/n1/online")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


# ---------------------------------------------------------------------------
# B30-B33: dismiss + observability snapshots
# ---------------------------------------------------------------------------


def test_b30_dismiss_node(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _ok(*args, **kwargs):
        return True

    mint_app.state.org_runtime.dismiss_node = MagicMock(side_effect=_ok)
    resp = mint_client.delete("/api/v2/orgs/org_x/nodes/n1/dismiss")
    assert resp.status_code == 200


def test_b30_dismiss_node_400(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _no(*args, **kwargs):
        return False

    mint_app.state.org_runtime.dismiss_node = MagicMock(side_effect=_no)
    resp = mint_client.delete("/api/v2/orgs/org_x/nodes/n1/dismiss")
    assert resp.status_code == 400


def test_b31_get_node_thinking(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_node_thinking.return_value = {
        "node_id": "n1",
        "timeline": [],
    }
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/thinking")
    assert resp.status_code == 200
    assert resp.json()["node_id"] == "n1"


def test_b32_preview_node_prompt(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.preview_node_prompt.return_value = {
        "node_id": "n1",
        "full_prompt": "...",
        "char_count": 3,
    }
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/prompt-preview")
    assert resp.status_code == 200
    assert resp.json()["char_count"] == 3


def test_b33_get_node_status_snapshot(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_node_status_snapshot.return_value = {
        "id": "n1",
        "role_title": "r",
        "status": "idle",
    }
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


# ===========================================================================
# Cluster 3.3 -- Runtime control + Commands + Broadcast (B34-B41).
# ===========================================================================


def _async_returns(value: Any):
    async def _run(*args, **kwargs):
        return value

    return _run


# ---------------------------------------------------------------------------
# B34-B37: lifecycle verbs
# ---------------------------------------------------------------------------


def test_b34_start_org_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    started = MagicMock(spec=["to_dict"])
    started.to_dict.return_value = {"id": "org_x", "status": "active"}
    mint_app.state.org_runtime.start_org = MagicMock(side_effect=_async_returns(started))
    resp = mint_client.post("/api/v2/orgs/org_x/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


def test_b34_start_org_400_on_value_error(mint_app: FastAPI, mint_client: TestClient) -> None:
    async def _raise(*a, **k):
        raise ValueError("Already running")

    mint_app.state.org_runtime.start_org = MagicMock(side_effect=_raise)
    resp = mint_client.post("/api/v2/orgs/org_x/start")
    assert resp.status_code == 400


def test_b35_stop_org_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    stopped = MagicMock(spec=["to_dict"])
    stopped.to_dict.return_value = {"id": "org_x", "status": "stopped"}
    mint_app.state.org_runtime.stop_org = MagicMock(side_effect=_async_returns(stopped))
    resp = mint_client.post("/api/v2/orgs/org_x/stop")
    assert resp.status_code == 200


def test_b36_pause_org_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    paused = MagicMock(spec=["to_dict"])
    paused.to_dict.return_value = {"id": "org_x", "status": "paused"}
    mint_app.state.org_runtime.pause_org = MagicMock(side_effect=_async_returns(paused))
    resp = mint_client.post("/api/v2/orgs/org_x/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


def test_b37_resume_org_calls_runtime(mint_app: FastAPI, mint_client: TestClient) -> None:
    resumed = MagicMock(spec=["to_dict"])
    resumed.to_dict.return_value = {"id": "org_x", "status": "active"}
    mint_app.state.org_runtime.resume_org = MagicMock(side_effect=_async_returns(resumed))
    resp = mint_client.post("/api/v2/orgs/org_x/resume")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# B38-B40: command submit + poll + cancel
# ---------------------------------------------------------------------------


def test_b38_submit_command_calls_service(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.submit = MagicMock(
        side_effect=_async_returns({"command_id": "cmd1", "status": "queued"})
    )
    resp = mint_client.post(
        "/api/v2/orgs/org_x/command",
        json={"content": "say hello"},
    )
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "cmd1"
    mint_app.state.org_command_service.submit.assert_called_once()


def test_b38_submit_command_422_on_empty(mint_client: TestClient) -> None:
    """``content`` has ``min_length=1``; empty body fails validation."""
    resp = mint_client.post("/api/v2/orgs/org_x/command", json={"content": ""})
    assert resp.status_code == 422


def test_b39_get_command_status(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.get_status.return_value = {
        "command_id": "cmd1",
        "status": "done",
    }
    resp = mint_client.get("/api/v2/orgs/org_x/commands/cmd1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


def test_b39_get_command_status_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.get_status.return_value = None
    resp = mint_client.get("/api/v2/orgs/org_x/commands/missing")
    assert resp.status_code == 404


def test_b40_cancel_command(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = MagicMock(
        side_effect=_async_returns({"command_id": "cmd1", "status": "cancelled"})
    )
    resp = mint_client.post("/api/v2/orgs/org_x/commands/cmd1/cancel", json={"reason": "user"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_b40_cancel_command_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = MagicMock(side_effect=_async_returns(None))
    resp = mint_client.post("/api/v2/orgs/org_x/commands/missing/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B41: broadcast
# ---------------------------------------------------------------------------


def test_b41_broadcast(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.broadcast_to_org = MagicMock(
        side_effect=_async_returns({"delivered": 3})
    )
    resp = mint_client.post("/api/v2/orgs/org_x/broadcast", json={"content": "hi"})
    assert resp.status_code == 200
    assert resp.json()["result"]["delivered"] == 3


def test_b41_broadcast_400_empty_content(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/org_x/broadcast", json={"content": ""})
    assert resp.status_code == 400


# ===========================================================================
# Cluster 3.4 -- Memory + Events + Activity + Messages + audit + Policies.
# ===========================================================================


def _wire_org_dir(mint_app: FastAPI, tmp_path) -> Path:
    base = tmp_path / "org_x"
    mint_app.state.org_manager.get_org_dir.return_value = str(base)
    return base


# ---------------------------------------------------------------------------
# B42-B44: blackboard memory CRUD
# ---------------------------------------------------------------------------


def test_b42_query_memory(mint_app: FastAPI, mint_client: TestClient) -> None:
    entry = MagicMock(spec=["to_dict"])
    entry.to_dict.return_value = {"id": "m1", "content": "hi"}
    mint_app.state.org_blackboard.query.return_value = [entry]
    resp = mint_client.get("/api/v2/orgs/org_x/memory?scope=org&limit=10")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "m1"


def test_b42_query_memory_400_bad_scope(mint_app: FastAPI, mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/org_x/memory?scope=bogus")
    assert resp.status_code == 400


def test_b43_add_memory_org_scope(mint_app: FastAPI, mint_client: TestClient) -> None:
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "m2", "content": "hi"}
    mint_app.state.org_blackboard.write_org.return_value = fake
    resp = mint_client.post(
        "/api/v2/orgs/org_x/memory",
        json={"scope": "org", "memory_type": "fact", "content": "hi"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "m2"


def test_b43_add_memory_400_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/org_x/memory", json={"content": ""})
    assert resp.status_code == 400


def test_b44_delete_memory(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.delete_entry.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_x/memory/m1")
    assert resp.status_code == 200


def test_b44_delete_memory_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.delete_entry.return_value = False
    resp = mint_client.delete("/api/v2/orgs/org_x/memory/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B45-B48: events + activity + messages + audit
# ---------------------------------------------------------------------------


def test_b45_query_events(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.query.return_value = [{"event_type": "user_command", "actor": "user"}]
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/org_x/events?limit=5")
    assert resp.status_code == 200
    assert resp.json()[0]["event_type"] == "user_command"


def test_b46_query_activity(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.query.return_value = [{"event_type": "broadcast"}]
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/org_x/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert "items" in body


def test_b47_messages_empty_when_no_log(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _wire_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/messages")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_b47_messages_reads_log(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    base = _wire_org_dir(mint_app, tmp_path)
    (base / "logs").mkdir(parents=True)
    (base / "logs" / "communications.jsonl").write_text(
        json.dumps({"from_node": "a", "to_node": "b", "content": "x"}) + "\n",
        encoding="utf-8",
    )
    resp = mint_client.get("/api/v2/orgs/org_x/messages")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_b48_audit_log_empty_when_es_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/org_x/audit-log")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# B49-B53: policies file IO
# ---------------------------------------------------------------------------


def test_b49_list_policies_empty(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _wire_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/policies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b50_search_policies_400_when_no_query(mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/org_x/policies/search")
    assert resp.status_code == 400


def test_b51_read_policy_404(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _wire_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/policies/missing.md")
    assert resp.status_code == 404


def test_b52_write_policy_creates_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    base = _wire_org_dir(mint_app, tmp_path)
    resp = mint_client.put(
        "/api/v2/orgs/org_x/policies/test.md",
        json={"content": "# test"},
    )
    assert resp.status_code == 200
    written = base / "policies" / "test.md"
    assert written.read_text(encoding="utf-8") == "# test"


def test_b53_delete_policy(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    base = _wire_org_dir(mint_app, tmp_path)
    (base / "policies").mkdir(parents=True)
    (base / "policies" / "x.md").write_text("hi", encoding="utf-8")
    resp = mint_client.delete("/api/v2/orgs/org_x/policies/x.md")
    assert resp.status_code == 200
    assert not (base / "policies" / "x.md").exists()


def test_path_traversal_blocked(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _wire_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/org_x/policies/..%2Fsecret")
    # FastAPI's percent-decode preserves ``..``; the route checks string ``..``
    assert resp.status_code in (400, 404)


# ===========================================================================
# Cluster 3.5 -- Inbox + Scaling + Reports + Stats + Status (B54-B67).
# ===========================================================================


def _wire_inbox(mint_app: FastAPI) -> Any:
    inbox = MagicMock()
    inbox.unread_count.return_value = 0
    inbox.pending_approval_count.return_value = 0
    inbox.list_messages.return_value = []
    inbox.mark_read.return_value = True
    inbox.mark_all_read.return_value = 0
    mint_app.state.org_runtime.get_inbox.return_value = inbox
    return inbox


def _wire_scaler(mint_app: FastAPI) -> Any:
    scaler = MagicMock()
    mint_app.state.org_runtime.get_scaler = MagicMock(return_value=scaler)
    return scaler


# ---------------------------------------------------------------------------
# B54-B57: inbox
# ---------------------------------------------------------------------------


def test_b54_list_inbox_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app)
    resp = mint_client.get("/api/v2/orgs/org_x/inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == []
    assert body["unread_count"] == 0


def test_b55_mark_inbox_read_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app)
    resp = mint_client.post("/api/v2/orgs/org_x/inbox/m1/read")
    assert resp.status_code == 200


def test_b55_mark_inbox_read_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    inbox = _wire_inbox(mint_app)
    inbox.mark_read.return_value = False
    resp = mint_client.post("/api/v2/orgs/org_x/inbox/m1/read")
    assert resp.status_code == 404


def test_b56_mark_all_read(mint_app: FastAPI, mint_client: TestClient) -> None:
    inbox = _wire_inbox(mint_app)
    inbox.mark_all_read.return_value = 5
    resp = mint_client.post("/api/v2/orgs/org_x/inbox/read-all")
    assert resp.status_code == 200
    assert resp.json()["marked"] == 5


def test_b57_resolve_approval(mint_app: FastAPI, mint_client: TestClient) -> None:
    inbox = _wire_inbox(mint_app)
    fake = MagicMock(spec=["to_dict"])
    fake.to_dict.return_value = {"id": "m1", "status": "resolved"}
    inbox.resolve_approval.return_value = fake
    resp = mint_client.post("/api/v2/orgs/org_x/inbox/m1/resolve", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


def test_b57_resolve_approval_400_bad_decision(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app)
    resp = mint_client.post("/api/v2/orgs/org_x/inbox/m1/resolve", json={"decision": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B58-B62: scaling
# ---------------------------------------------------------------------------


def test_b58_list_scaling_requests(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(
        id="r1",
        request_type="clone",
        requester_node_id="n1",
        role_title="Worker",
        status="pending",
        created_at="2026-01-01",
    )
    scaler.get_pending_requests.return_value = [req]
    resp = mint_client.get("/api/v2/orgs/org_x/scaling/requests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == "r1"
    assert body[0]["status"] == "pending"


def test_b59_approve_scaling(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(id="r1", status="approved", result_node_id="n2")

    async def _ok(*args, **kwargs):
        return req

    scaler.approve_request = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/scaling/r1/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_b60_reject_scaling(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.reject_request.return_value = MagicMock(id="r1", status="rejected")
    resp = mint_client.post("/api/v2/orgs/org_x/scaling/r1/reject", json={"reason": "no"})
    assert resp.status_code == 200


def test_b61_scale_clone(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(id="r2", status="approved", result_node_id="n3")

    async def _ok(*args, **kwargs):
        return req

    scaler.request_clone = MagicMock(side_effect=_ok)
    resp = mint_client.post("/api/v2/orgs/org_x/scale/clone", json={"source_node_id": "n1"})
    assert resp.status_code == 200
    assert resp.json()["result_node_id"] == "n3"


def test_b61_scale_clone_400(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_scaler(mint_app)
    resp = mint_client.post("/api/v2/orgs/org_x/scale/clone", json={})
    assert resp.status_code == 400


def test_b62_scale_recruit(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.request_recruit.return_value = MagicMock(id="r3", status="pending")
    resp = mint_client.post(
        "/api/v2/orgs/org_x/scale/recruit",
        json={"role_title": "Marketer", "parent_node_id": "n1"},
    )
    assert resp.status_code == 200


def test_b62_scale_recruit_400(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_scaler(mint_app)
    resp = mint_client.post("/api/v2/orgs/org_x/scale/recruit", json={"role_title": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B63-B67: status / stats / reports
# ---------------------------------------------------------------------------


def test_b63_status_snapshot(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_status_snapshot = MagicMock(
        return_value={"org_id": "org_x", "status": "active"}
    )
    resp = mint_client.get("/api/v2/orgs/org_x/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


def test_b63_status_404_when_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_status_snapshot = MagicMock(return_value=None)
    resp = mint_client.get("/api/v2/orgs/org_x/status")
    assert resp.status_code == 404


def test_b64_stats(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_stats = MagicMock(
        return_value={"org_id": "org_x", "node_count": 3}
    )
    resp = mint_client.get("/api/v2/orgs/org_x/stats")
    assert resp.status_code == 200
    assert resp.json()["node_count"] == 3


def test_b65_list_reports_empty(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    mint_app.state.org_manager.get_org_dir.return_value = str(tmp_path / "org_x")
    resp = mint_client.get("/api/v2/orgs/org_x/reports")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b66_report_summary(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.generate_summary_report.return_value = {"summary": "ok", "days": 7}
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/org_x/reports/summary")
    assert resp.status_code == 200
    assert resp.json()["summary"] == "ok"


def test_b67_generate_report(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    es = MagicMock()
    out = tmp_path / "report.md"
    es.generate_report_markdown.return_value = out
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.post("/api/v2/orgs/org_x/reports/generate", json={"days": 3})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ===========================================================================
# Cluster 3.6 -- Projects + tasks (B68-B83).
# ===========================================================================


def _fake_project(pid: str = "p1", org_id: str = "org_x") -> Any:
    proj = MagicMock(spec=["to_dict"])
    proj.to_dict.return_value = {"id": pid, "org_id": org_id, "name": "P"}
    return proj


def _fake_task(tid: str = "t1", project_id: str = "p1", status: str = "todo") -> Any:
    task = MagicMock(spec=["to_dict", "project_id", "id", "status", "chain_id", "assignee_node_id"])
    task.id = tid
    task.project_id = project_id
    task.status = MagicMock(value=status)
    task.chain_id = None
    task.assignee_node_id = None
    task.to_dict.return_value = {
        "id": tid,
        "project_id": project_id,
        "status": status,
        "title": "X",
    }
    return task


# ---------------------------------------------------------------------------
# B68-B72: project CRUD
# ---------------------------------------------------------------------------


def test_b68_list_projects(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.list_projects.return_value = [_fake_project("p1")]
    resp = mint_client.get("/api/v2/orgs/org_x/projects")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "p1"


def test_b69_create_project(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.create_project.return_value = _fake_project("p2")
    resp = mint_client.post(
        "/api/v2/orgs/org_x/projects",
        json={"name": "Project A"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "p2"


def test_b69_create_project_422_missing_name(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/org_x/projects", json={})
    assert resp.status_code == 422


def test_b70_get_project(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_project.return_value = _fake_project("p1")
    resp = mint_client.get("/api/v2/orgs/org_x/projects/p1")
    assert resp.status_code == 200


def test_b70_get_project_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_project.return_value = None
    resp = mint_client.get("/api/v2/orgs/org_x/projects/missing")
    assert resp.status_code == 404


def test_b71_update_project(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_project.return_value = _fake_project("p1")
    resp = mint_client.put("/api/v2/orgs/org_x/projects/p1", json={"description": "new"})
    assert resp.status_code == 200


def test_b72_delete_project(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_project.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_x/projects/p1")
    assert resp.status_code == 200


def test_b72_delete_project_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_project.return_value = False
    resp = mint_client.delete("/api/v2/orgs/org_x/projects/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B73-B77: tasks CRUD + dispatch + cancel
# ---------------------------------------------------------------------------


def test_b73_create_task(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.add_task.return_value = _fake_task("t1", "p1")
    resp = mint_client.post(
        "/api/v2/orgs/org_x/projects/p1/tasks",
        json={"title": "Task 1"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "t1"


def test_b74_update_task(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.update_task.return_value = _fake_task("t1")
    resp = mint_client.put("/api/v2/orgs/org_x/projects/p1/tasks/t1", json={"title": "X2"})
    assert resp.status_code == 200


def test_b75_delete_task(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.delete_task.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_x/projects/p1/tasks/t1")
    assert resp.status_code == 200


def test_b76_dispatch_task(mint_app: FastAPI, mint_client: TestClient) -> None:
    task = _fake_task("t1", "p1")
    mint_app.state.project_store.get_task.return_value = (task, _fake_project("p1"))
    resp = mint_client.post("/api/v2/orgs/org_x/projects/p1/tasks/t1/dispatch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["dispatched"] is True
    assert "chain_id" in body


def test_b76_dispatch_task_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task.return_value = (None, None)
    resp = mint_client.post("/api/v2/orgs/org_x/projects/p1/tasks/missing/dispatch")
    assert resp.status_code == 404


def test_b77_cancel_task(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import TaskStatus

    task = MagicMock(
        id="t1",
        project_id="p1",
        status=TaskStatus.IN_PROGRESS,
        chain_id="c1",
        assignee_node_id="n1",
    )
    mint_app.state.project_store.get_task.return_value = (task, _fake_project("p1"))
    resp = mint_client.post(
        "/api/v2/orgs/org_x/projects/p1/tasks/t1/cancel",
        json={"reason": "user"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# B78-B81: cross-project aggregation + detail + tree + timeline
# ---------------------------------------------------------------------------


def test_b78_list_all_tasks(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = [{"id": "t1", "status": "in_progress"}]
    resp = mint_client.get("/api/v2/orgs/org_x/tasks?status=in_progress")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "t1"


def test_b79_get_task_detail(mint_app: FastAPI, mint_client: TestClient) -> None:
    task = _fake_task("t1")
    mint_app.state.project_store.get_task.return_value = (task, None)
    mint_app.state.project_store.get_subtasks.return_value = []
    mint_app.state.project_store.get_ancestors.return_value = []
    resp = mint_client.get("/api/v2/orgs/org_x/tasks/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "t1"
    assert body["subtasks"] == []


def test_b80_get_task_tree(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task_tree.return_value = {
        "id": "t1",
        "children": [],
    }
    resp = mint_client.get("/api/v2/orgs/org_x/tasks/t1/tree")
    assert resp.status_code == 200
    assert resp.json()["id"] == "t1"


def test_b80_get_task_tree_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.get_task_tree.return_value = None
    resp = mint_client.get("/api/v2/orgs/org_x/tasks/missing/tree")
    assert resp.status_code == 404


def test_b81_get_task_timeline(mint_app: FastAPI, mint_client: TestClient) -> None:
    task = MagicMock(
        execution_log=[{"at": "2026-01-01", "event": "started"}],
        chain_id=None,
    )
    mint_app.state.project_store.get_task.return_value = (task, None)
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/org_x/tasks/t1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert len(body["timeline"]) == 1


# ---------------------------------------------------------------------------
# B82-B83: per-node task / active-plan queries
# ---------------------------------------------------------------------------


def test_b82_get_node_tasks(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.side_effect = lambda **kwargs: (
        [{"id": "t1"}] if kwargs.get("assignee") == "n1" else []
    )
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned"][0]["id"] == "t1"
    assert body["delegated"] == []


def test_b83_get_node_active_plan_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = []
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/active-plan")
    assert resp.status_code == 200
    assert resp.json() == {"plan": None}


def test_b83_get_node_active_plan(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.project_store.all_tasks.return_value = [
        {
            "id": "t1",
            "title": "Big Task",
            "status": "in_progress",
            "plan_steps": [{"id": "s1", "status": "todo"}],
            "progress_pct": 25,
        },
    ]
    resp = mint_client.get("/api/v2/orgs/org_x/nodes/n1/active-plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["progress_pct"] == 25
