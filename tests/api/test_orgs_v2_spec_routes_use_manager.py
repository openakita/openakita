"""Phase 4 regression: /api/v2/orgs-spec CRUD writes through OrgManager.

v22 RCA RC-1 / v25 E4 background
--------------------------------

Pre-Sprint-13: ``POST /api/v2/orgs-spec`` wrote to
``data/orgs_v2.json`` via :class:`JsonOrgStore.create`, but the
mint path (``OrgManager.create_from_template``) wrote to
``data/orgs/<id>/org.json``. The two write paths never met, so:

* spec API ``GET`` 404'd against mint orgs (v25 E4 SSE 404 symptom)
* the IM canary's ``get_default_store().get`` couldn't see them
  (v25 H2 "not in v2 store" symptom)

Post-Sprint-13: every spec CRUD verb routes through OrgManager
(read or write), with ``data/orgs_v2.json`` only consulted as a
read-only legacy fallback. These tests pin that contract:

* create lands in ``data/orgs/<id>/org.json``, not
  ``data/orgs_v2.json``
* get sees mint orgs (the v25 E4 fix)
* list unions manager + legacy data (deprecation soak)
* delete removes from OrgManager (and only from OrgManager)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.orgs_v2 import router as orgs_v2_router
from openakita.config import settings
from openakita.orgs import reset_default_store, set_default_org_manager
from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import OrgNode


@pytest.fixture
def _spec_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TestClient, OrgManager, Path]:
    """Mount only the orgs-spec router with a tmp-rooted manager.

    Mirrors the production ``api/server.py`` wiring's relevant
    bits: enable v2, point :func:`get_default_store` at a fresh
    JSON shim, and register the same OrgManager via
    :func:`set_default_org_manager` so the SSoT registry agrees
    with the shim's backing manager.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    manager = OrgManager(tmp_path)
    legacy_path = tmp_path / "orgs_v2.json"
    reset_default_store(path=legacy_path, manager=manager)
    set_default_org_manager(manager)
    app = FastAPI()
    app.include_router(orgs_v2_router)
    client = TestClient(app, raise_server_exceptions=True)
    yield client, manager, legacy_path
    set_default_org_manager(None)
    reset_default_store()


def _mint_payload(org_id: str = "org_spec_test", name: str = "Spec Org") -> dict[str, Any]:
    """A minimal valid OrgV2 jsonable -- root LLM node + one helper."""
    return {
        "id": org_id,
        "name": name,
        "description": "spec-route regression",
        "status": "created",
        "nodes": [
            {
                "id": "node_root",
                "org_id": org_id,
                "type": "llm",
                "role": "producer",
                "label": "Producer",
            },
            {
                "id": "node_child",
                "org_id": org_id,
                "type": "llm",
                "role": "writer",
                "label": "Writer",
                "parent_id": "node_root",
            },
        ],
        "edges": [
            {
                "id": "edge_h",
                "org_id": org_id,
                "src": "node_root",
                "dst": "node_child",
                "kind": "hierarchy",
            }
        ],
    }


def test_spec_create_writes_to_orgmanager_not_json_store(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """POST /api/v2/orgs-spec must persist to ``data/orgs/<id>/org.json``.

    This is the write-side half of RC-1: the spec endpoint now
    funnels into OrgManager so spec and mint share one SSoT.
    """
    client, manager, legacy_path = _spec_app
    payload = _mint_payload(org_id="org_spec_via_route")

    response = client.post("/api/v2/orgs-spec", json={"org": payload})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == "org_spec_via_route"
    assert body["name"] == "Spec Org"
    org_dir_file = manager._orgs_dir / "org_spec_via_route" / "org.json"
    assert org_dir_file.exists(), "create must hit OrgManager (data/orgs/<id>/org.json)"
    assert manager.get("org_spec_via_route") is not None
    assert not legacy_path.exists() or "org_spec_via_route" not in (
        legacy_path.read_text(encoding="utf-8") if legacy_path.exists() else ""
    ), "create must NOT touch data/orgs_v2.json (red line D: no reverse mirror)"


def test_spec_create_rejects_id_collision_with_409(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """Id collisions still return 409 -- existing contract."""
    client, manager, _ = _spec_app
    manager.create({"id": "org_dup", "name": "Existing"})

    response = client.post("/api/v2/orgs-spec", json={"org": _mint_payload(org_id="org_dup")})

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_spec_create_rejects_invalid_payload_with_400(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """Bad OrgV2 payloads still produce 400 -- validation is preserved."""
    client, _, _ = _spec_app

    response = client.post("/api/v2/orgs-spec", json={"org": {"id": 123}})

    assert response.status_code == 400


def test_spec_get_finds_mint_org(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """v25 E4 regression: a mint-via-OrgManager org is visible to spec GET.

    Pre-Sprint-13 this returned 404 (the spec route read
    ``data/orgs_v2.json`` only). Now it must 200 with the
    projected OrgV2 wire payload.
    """
    client, manager, _ = _spec_app
    organization = manager.create(
        {
            "id": "org_minted",
            "name": "Minted",
            "nodes": [
                OrgNode(
                    id="node_root",
                    role_title="producer",
                    agent_profile_id="default",
                ).to_dict()
            ],
            "edges": [],
        }
    )
    assert organization.id == "org_minted"

    response = client.get(f"/api/v2/orgs-spec/{organization.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "org_minted"
    assert body["name"] == "Minted"
    assert any(n["id"] == "node_root" for n in body["nodes"])


def test_spec_get_returns_404_for_unknown(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """Unknown ids still 404 -- only the false-negative side is fixed."""
    client, _, _ = _spec_app
    response = client.get("/api/v2/orgs-spec/org_does_not_exist")
    assert response.status_code == 404


def test_spec_list_unions_legacy_data(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """list must show both manager-minted and legacy-JSON orgs.

    During the Sprint-13-H2 deprecation soak, ``data/orgs_v2.json``
    is still a legitimate read source so v25 leftover orgs don't
    suddenly vanish from the API. The merge prefers manager rows
    on id collision (manager == SSoT).
    """
    client, manager, legacy_path = _spec_app
    manager.create({"id": "org_via_manager", "name": "Manager"})
    legacy_path.write_text(
        json.dumps(
            {
                "orgs": {
                    "org_via_legacy": {
                        "id": "org_via_legacy",
                        "name": "Legacy",
                        "description": "",
                        "status": "created",
                        "defaults": {},
                        "nodes": [],
                        "edges": [],
                        "external_tools_index": [],
                        "created_at": "2026-05-01T00:00:00+00:00",
                        "updated_at": "2026-05-01T00:00:00+00:00",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/api/v2/orgs-spec")

    assert response.status_code == 200
    body = response.json()
    ids = {entry["id"] for entry in body["orgs"]}
    assert "org_via_manager" in ids, "manager rows must surface in list"
    assert "org_via_legacy" in ids, "legacy soak rows must still surface"
    assert body["count"] == len(body["orgs"]) >= 2


def test_spec_patch_updates_via_manager(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """patch flows through OrgManager.update -- name/description land in org.json."""
    client, manager, _ = _spec_app
    manager.create({"id": "org_to_patch", "name": "Old", "description": "old desc"})

    response = client.patch(
        "/api/v2/orgs-spec/org_to_patch",
        json={"name": "New", "description": "new desc"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "New"
    assert body["description"] == "new desc"
    on_disk = manager.get("org_to_patch")
    assert on_disk is not None
    assert on_disk.name == "New"
    assert on_disk.description == "new desc"


def test_spec_patch_returns_404_for_unknown(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """Patch against a missing id -> 404."""
    client, _, _ = _spec_app
    response = client.patch("/api/v2/orgs-spec/org_missing", json={"name": "X"})
    assert response.status_code == 404


def test_spec_delete_removes_from_orgmanager(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """delete must remove the on-disk ``data/orgs/<id>/`` -- not the legacy JSON."""
    client, manager, legacy_path = _spec_app
    manager.create({"id": "org_to_delete", "name": "Delme"})
    org_dir = manager._orgs_dir / "org_to_delete"
    assert org_dir.exists()

    response = client.delete("/api/v2/orgs-spec/org_to_delete")

    assert response.status_code == 204
    assert not org_dir.exists(), "delete must hit OrgManager.delete (rmtree the org dir)"
    assert manager.get("org_to_delete") is None


def test_spec_delete_returns_404_for_unknown(
    _spec_app: tuple[TestClient, OrgManager, Path],
) -> None:
    """Delete against a missing id -> 404."""
    client, _, _ = _spec_app
    response = client.delete("/api/v2/orgs-spec/org_missing")
    assert response.status_code == 404
