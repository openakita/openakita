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
    """v2 reaches the free function in ``openakita.orgs.tool_categories``."""
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
