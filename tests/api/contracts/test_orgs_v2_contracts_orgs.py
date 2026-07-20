"""Contract tests for cluster 3.1 OrgManager endpoints (B1-B17).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_orgs.py``
(P9.7beta-1). For each endpoint covers happy / 404 / 422 /
409 (where applicable) per the charter section 6 contract
matrix. Reuses the duck-typed mock subsystem fixtures from
``tests/api/contracts/conftest.py`` so the assertions stay
focused on response envelopes + status codes.

503 is exercised by the alpha-2 smoke suite; auth is reused
v1 pattern (D-4 LOCKED) so neither family is asserted here.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.contracts.conftest import fake_org

# ---------------------------------------------------------------------------
# B1: GET /api/v2/orgs (list)
# ---------------------------------------------------------------------------


def test_b1_list_orgs_happy_default(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.list_orgs.return_value = []
    resp = mint_client.get("/api/v2/orgs")
    assert resp.status_code == 200
    assert resp.json() == []
    mint_app.state.org_manager.list_orgs.assert_called_once_with(include_archived=False)


def test_b1_list_orgs_include_archived_propagates(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.list_orgs.return_value = [{"id": "a", "status": "archived"}]
    resp = mint_client.get("/api/v2/orgs?include_archived=true")
    assert resp.status_code == 200
    mint_app.state.org_manager.list_orgs.assert_called_once_with(include_archived=True)


# ---------------------------------------------------------------------------
# B2: POST /api/v2/orgs (create)
# ---------------------------------------------------------------------------


def test_b2_create_org_returns_201_with_id(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create.return_value = fake_org("org_n", "Eng")
    resp = mint_client.post("/api/v2/orgs", json={"name": "Eng", "description": "team"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "org_n"
    assert body["name"] == "Eng"


@pytest.mark.parametrize(
    "payload",
    [
        {},  # name missing
        {"name": ""},  # min_length=1
        {"name": "ok", "unknown_field": True},  # extra forbid
    ],
)
def test_b2_create_org_422_pydantic_violations(
    mint_client: TestClient, payload: dict[str, object]
) -> None:
    resp = mint_client.post("/api/v2/orgs", json=payload)
    assert resp.status_code == 422


def test_b2_create_org_409_on_name_conflict(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import OrgNameConflictError

    mint_app.state.org_manager.create.side_effect = OrgNameConflictError(
        name="Eng", conflict_org_id="org_a"
    )
    resp = mint_client.post("/api/v2/orgs", json={"name": "Eng"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "org_name_conflict"
    assert detail["conflict_org_id"] == "org_a"


# ---------------------------------------------------------------------------
# B3: GET /api/v2/orgs/avatar-presets
# ---------------------------------------------------------------------------


def test_b3_avatar_presets_returns_list(mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/avatar-presets")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# B4: POST /api/v2/orgs/avatars/upload
# ---------------------------------------------------------------------------


def test_b4_avatar_upload_400_when_unsupported_type(mint_client: TestClient) -> None:
    files = {"file": ("a.gif", io.BytesIO(b"GIF89a"), "image/gif")}
    resp = mint_client.post("/api/v2/orgs/avatars/upload", files=files)
    assert resp.status_code == 400


def test_b4_avatar_upload_400_when_too_large(
    mint_client: TestClient,
) -> None:
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (3 * 1024 * 1024)
    files = {"file": ("a.png", io.BytesIO(big), "image/png")}
    resp = mint_client.post("/api/v2/orgs/avatars/upload", files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B5: GET /api/v2/orgs/templates (list)
# ---------------------------------------------------------------------------


def test_b5_list_templates_returns_envelope(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.list_templates.return_value = [
        {"id": "t1", "name": "Software"},
        {"id": "t2", "name": "Marketing"},
    ]
    resp = mint_client.get("/api/v2/orgs/templates")
    assert resp.status_code == 200
    assert {x["id"] for x in resp.json()} == {"t1", "t2"}


def test_b5_list_templates_empty(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.list_templates.return_value = []
    resp = mint_client.get("/api/v2/orgs/templates")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# B6: GET /api/v2/orgs/plugin-workbench-templates
# ---------------------------------------------------------------------------


def test_b6_plugin_workbench_templates_no_agent(mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/plugin-workbench-templates")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# B7: GET /api/v2/orgs/templates/{id}
# ---------------------------------------------------------------------------


def test_b7_get_template_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get_template.return_value = {"id": "t1", "name": "X"}
    resp = mint_client.get("/api/v2/orgs/templates/t1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "t1"


def test_b7_get_template_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get_template.return_value = None
    resp = mint_client.get("/api/v2/orgs/templates/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B8: POST /api/v2/orgs/from-template
# ---------------------------------------------------------------------------


def test_b8_from_template_happy_returns_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create_from_template.return_value = fake_org("org_t", "T")
    resp = mint_client.post("/api/v2/orgs/from-template", json={"template_id": "t1", "name": "T"})
    assert resp.status_code == 201
    assert resp.json()["id"] == "org_t"


def test_b8_from_template_400_when_template_id_missing(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/from-template", json={"name": "x"})
    assert resp.status_code == 400


def test_b8_from_template_404_when_template_not_found(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.create_from_template.side_effect = FileNotFoundError("t1.json")
    resp = mint_client.post("/api/v2/orgs/from-template", json={"template_id": "t1"})
    assert resp.status_code == 404


def test_b8_from_template_409_on_name_conflict(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import OrgNameConflictError

    mint_app.state.org_manager.create_from_template.side_effect = OrgNameConflictError(
        name="Dup", conflict_org_id="org_a"
    )
    resp = mint_client.post("/api/v2/orgs/from-template", json={"template_id": "t1", "name": "Dup"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "org_name_conflict"


# ---------------------------------------------------------------------------
# B9: POST /api/v2/orgs/import
# ---------------------------------------------------------------------------


def test_b9_import_org_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.create.return_value = fake_org("org_i", "Imp")
    payload = json.dumps({"organization": {"name": "Imp"}}).encode()
    files = {"file": ("o.json", io.BytesIO(payload), "application/json")}
    resp = mint_client.post("/api/v2/orgs/import", files=files)
    assert resp.status_code == 201
    body = resp.json()
    assert body["organization"]["id"] == "org_i"
    assert body["renamed"] is False


def test_b9_import_org_400_when_invalid_json(mint_client: TestClient) -> None:
    files = {"file": ("o.json", io.BytesIO(b"not-json"), "application/json")}
    resp = mint_client.post("/api/v2/orgs/import", files=files)
    assert resp.status_code == 400


def test_b9_import_org_400_when_missing_organization_key(
    mint_client: TestClient,
) -> None:
    files = {"file": ("o.json", io.BytesIO(b'{"x":1}'), "application/json")}
    resp = mint_client.post("/api/v2/orgs/import", files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B10: GET /api/v2/orgs/{org_id}
# ---------------------------------------------------------------------------


def test_b10_get_org_uses_runtime_snapshot_when_present(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    snap = MagicMock(return_value=fake_org("org_x", "X-runtime"))
    mint_app.state.org_runtime.get_org_snapshot = snap
    resp = mint_client.get("/api/v2/orgs/org_x")
    assert resp.status_code == 200
    assert resp.json()["name"] == "X-runtime"
    snap.assert_called_once_with("org_x")


def test_b10_get_org_falls_back_to_manager(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("org_x", "X-mgr")
    resp = mint_client.get("/api/v2/orgs/org_x")
    assert resp.status_code == 200
    assert resp.json()["name"] == "X-mgr"


def test_b10_get_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.get("/api/v2/orgs/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B11: PUT /api/v2/orgs/{org_id}
# ---------------------------------------------------------------------------


def test_b11_update_org_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("org_u", "old")
    mint_app.state.org_manager.update.return_value = fake_org("org_u", "new")
    resp = mint_client.put("/api/v2/orgs/org_u", json={"name": "new"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "new"


def test_b11_update_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.put("/api/v2/orgs/missing", json={"name": "x"})
    assert resp.status_code == 404


def test_b11_update_org_422_on_extra_field(mint_client: TestClient) -> None:
    resp = mint_client.put("/api/v2/orgs/org_u", json={"name": "x", "evil": 1})
    assert resp.status_code == 422


def test_b11_update_org_409_on_conflict(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import OrgNameConflictError

    mint_app.state.org_manager.get.return_value = fake_org("org_u", "old")
    mint_app.state.org_manager.update.side_effect = OrgNameConflictError(
        name="other", conflict_org_id="org_v"
    )
    resp = mint_client.put("/api/v2/orgs/org_u", json={"name": "other"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "org_name_conflict"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# B84: PATCH /api/v2/orgs/{org_id} (partial update; smoke F-5 closure)
# ---------------------------------------------------------------------------


def test_b84_patch_org_partial_name(mint_app: FastAPI, mint_client: TestClient) -> None:
    """Smoke F-5: PATCH must hit the mint runtime store, not the 308 shim."""
    mint_app.state.org_manager.get.return_value = fake_org("org_p", "old")
    mint_app.state.org_manager.update.return_value = fake_org("org_p", "renamed")
    resp = mint_client.patch("/api/v2/orgs/org_p", json={"name": "renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "renamed"
    # Confirm only the name field reached the manager (exclude_none semantics).
    args, kwargs = mint_app.state.org_manager.update.call_args
    payload = args[1] if len(args) > 1 else kwargs.get("data") or kwargs
    assert payload == {"name": "renamed"}


def test_b84_patch_org_partial_description(mint_app: FastAPI, mint_client: TestClient) -> None:
    """Smoke F-5: description-only PATCH must not nuke the existing name."""
    mint_app.state.org_manager.get.return_value = fake_org("org_p", "keepme")
    mint_app.state.org_manager.update.return_value = fake_org(
        "org_p", "keepme", description="brand new desc"
    )
    resp = mint_client.patch("/api/v2/orgs/org_p", json={"description": "brand new desc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "keepme"
    assert body["description"] == "brand new desc"
    args, kwargs = mint_app.state.org_manager.update.call_args
    payload = args[1] if len(args) > 1 else kwargs.get("data") or kwargs
    assert payload == {"description": "brand new desc"}


def test_b84_patch_org_404_when_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    """Smoke F-5: PATCH on an unknown id must 404 (not 308 -> spec store)."""
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.patch("/api/v2/orgs/nope", json={"name": "x"})
    assert resp.status_code == 404, resp.text


# B12: DELETE /api/v2/orgs/{org_id}
# ---------------------------------------------------------------------------


def test_b12_delete_org_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete.return_value = True
    resp = mint_client.delete("/api/v2/orgs/org_d")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_b12_delete_org_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.delete.return_value = False
    resp = mint_client.delete("/api/v2/orgs/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B13-B17: duplicate / archive / unarchive / save-as-template / export
# ---------------------------------------------------------------------------


def test_b13_duplicate_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("org_a", "A")
    mint_app.state.org_manager.duplicate.return_value = fake_org("org_a_copy", "A copy")
    resp = mint_client.post("/api/v2/orgs/org_a/duplicate", json={"name": "A copy"})
    assert resp.status_code == 201
    assert resp.json()["id"] == "org_a_copy"


def test_b13_duplicate_404_when_source_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.post("/api/v2/orgs/missing/duplicate", json={"name": "X"})
    assert resp.status_code == 404


def test_b14_archive_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("o1", "A")
    mint_app.state.org_manager.archive.return_value = fake_org("o1", "A", status="archived")
    resp = mint_client.post("/api/v2/orgs/o1/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_b14_archive_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.post("/api/v2/orgs/missing/archive")
    assert resp.status_code == 404


def test_b15_unarchive_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("o1", "A")
    mint_app.state.org_manager.unarchive.return_value = fake_org("o1", "A", status="dormant")
    resp = mint_client.post("/api/v2/orgs/o1/unarchive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dormant"


def test_b15_unarchive_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.post("/api/v2/orgs/missing/unarchive")
    assert resp.status_code == 404


def test_b16_save_as_template_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("o1", "A")
    mint_app.state.org_manager.save_as_template.return_value = "tpl_a"
    resp = mint_client.post("/api/v2/orgs/o1/save-as-template", json={"template_id": "tpl_a"})
    assert resp.status_code == 200
    assert resp.json() == {"template_id": "tpl_a"}


def test_b16_save_as_template_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.post("/api/v2/orgs/missing/save-as-template", json={})
    assert resp.status_code == 404


def test_b17_export_returns_envelope(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = fake_org("o1", "A")
    resp = mint_client.post("/api/v2/orgs/o1/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "akita-org"
    assert body["version"] == "1.0"
    assert body["organization"]["id"] == "o1"


def test_b17_export_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_manager.get.return_value = None
    resp = mint_client.post("/api/v2/orgs/missing/export")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# smoke-B3: PUT must accept the full v2-frontend save payload
# (user_persona, operation_mode, layout_locked, auto_persist_final_answer,
#  watchdog_*, runtime_overrides, heartbeat_*, standup_enabled, nodes, edges) --
# fields the original OrgPatch skeleton rejected with 422. See OrgEditorView.tsx
# buildSavePayload for the exact shape.
# ---------------------------------------------------------------------------


def test_b3_update_org_accepts_full_frontend_snapshot(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    """Regression for the complete wire snapshot the frontend posts on every save."""
    mint_app.state.org_manager.get.return_value = fake_org("org_full", "full")
    mint_app.state.org_manager.update.return_value = fake_org("org_full", "full")
    body = {
        "name": "full",
        "description": "d",
        "user_persona": {"title": "User", "display_name": "U", "description": ""},
        "operation_mode": "command",
        "core_business": "",
        "layout_locked": False,
        "workspace_dir": "",
        "auto_persist_final_answer": True,
        "watchdog_enabled": True,
        "watchdog_interval_s": 30,
        "watchdog_stuck_threshold_s": 1800,
        "watchdog_silence_threshold_s": 1800,
        "runtime_overrides": {
            "supervisor_hard_ceiling_s": 1800,
            "supervisor_soft_ceiling_ratio": 0.8,
            "supervisor_soft_watchdog_grace_ratio": 0.5,
        },
        "heartbeat_enabled": False,
        "heartbeat_interval_s": 600,
        "standup_enabled": False,
        "nodes": [{"id": "n1", "role_title": "Lead"}],
        "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
    }
    resp = mint_client.put("/api/v2/orgs/org_full", json=body)
    assert resp.status_code == 200, resp.text
    update = mint_app.state.org_manager.update
    assert update.call_args.args[1]["runtime_overrides"] == body["runtime_overrides"]


def test_b3_update_org_still_rejects_unknown_field(mint_client: TestClient) -> None:
    """smoke-B3: extra=forbid invariant preserved -- truly unknown keys still 422."""
    resp = mint_client.put(
        "/api/v2/orgs/org_u",
        json={"name": "x", "completely_made_up_key": True},
    )
    assert resp.status_code == 422
