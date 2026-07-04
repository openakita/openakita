"""EX-P2-10 v1.0.0-rc1 — DELETE /orgs/{org_id} endpoint tests.

Verifies the new cascade-delete endpoint added in v1.0.0-rc1:

* ``cascade=false`` (default) refuses to delete an org with any
  dependent rows (409 + ``org_not_empty`` + per-table diagnostics).
* ``cascade=true`` deletes the org and everything linked to it —
  including the 17 FK-cascade tables, the 4 non-FK ``org_id`` tables,
  consolidation_groups / consolidation_members, and any on-disk
  backup files registered against the org.
* Authorisation: only ``admin`` + ``partner`` roles can call DELETE;
  ``manager`` / ``auditor`` / unknown users get ``403 rbac_denied``.
* 404 on a non-existent org_id.

The tests use the same in-memory TestClient pattern as
``test_rbac_e2e.py``; they share none of its fixtures so this file
stays standalone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.models import OrganizationCreate, UserCreateRequest
from finance_auto_backend.routes import build_router_and_service
from finance_auto_backend.services.collaboration import CollaborationService

BASE = "/api/plugins/finance-auto"
USER_HEADER = "X-OpenAkita-User-Id"


def _hdrs(user_id: str) -> dict[str, str]:
    return {USER_HEADER: user_id}


@pytest.fixture()
def delete_app(tmp_path: Path):
    """Boot a real router + DB + seeded users for the DELETE /orgs tests."""
    router, svc, db = build_router_and_service(tmp_path / "delete.sqlite")
    asyncio.run(db.init())

    async def _seed() -> dict[str, Any]:
        # Two orgs: ``org_empty`` for the cascade=false happy path,
        # ``org_full`` for the cascade=true cleanup + 409 refusal.
        empty = await svc.create_org(OrganizationCreate(
            name="EmptyOrg", code="EMPTY",
            standard="small", fiscal_start="2025-01-01",
        ))
        full = await svc.create_org(OrganizationCreate(
            name="FullOrg", code="FULL",
            standard="small", fiscal_start="2025-01-01",
        ))
        # Seed the "full" org with a period + a manual_input row so the
        # cascade=false branch trips on a real dependent.
        await svc.ensure_period(org_id=full.id, period_id="2025-FY")
        await svc.db.conn.execute(
            "INSERT INTO manual_inputs(id, org_id, period_id, field_key, "
            "field_label, value, value_type, source, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, '', '100', 'cny', 'manual', 'local', "
            "datetime('now'))",
            ("mi_test", full.id, "2025-FY", "test_field"),
        )
        # Also seed a non-FK row: backup_history points at a tmp file
        # we'll write below so cascade=true exercises the unlink path.
        backup_path = tmp_path / "backups" / f"{full.id}.tar.gz"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(b"fake-backup-data")
        await svc.db.conn.execute(
            "INSERT INTO backup_history(org_id, backup_path, size_bytes, "
            "status, encrypted) VALUES (?, ?, ?, 'completed', 1)",
            (full.id, str(backup_path), len(b"fake-backup-data")),
        )
        # And a learning_samples row (non-FK).
        await svc.db.conn.execute(
            "INSERT INTO learning_samples(id, org_id, pattern_type, "
            "pattern_signature, action, source_decision_id, created_at) "
            "VALUES ('ls_test', ?, 'classify', 'sig', '{}', 'd', "
            "datetime('now'))",
            (full.id,),
        )
        await svc.db.conn.commit()

        # Users + perms: v14 migration seeds ``org.delete`` for admin
        # and partner; manager + auditor should NOT pass.
        collab = CollaborationService(svc.db.conn)
        for u in (
            UserCreateRequest(user_id="u_admin",   display_name="Alice",
                              role="admin",   email="a@x.com"),
            UserCreateRequest(user_id="u_partner", display_name="Dave",
                              role="partner", email="d@x.com"),
            UserCreateRequest(user_id="u_mgr",     display_name="Bob",
                              role="manager", email="b@x.com"),
            UserCreateRequest(user_id="u_aud",     display_name="Carol",
                              role="auditor", email="c@x.com"),
        ):
            await collab.register_user(u)

        return {
            "empty_id": empty.id,
            "full_id": full.id,
            "backup_path": str(backup_path),
        }

    seeded = asyncio.run(_seed())
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    client = TestClient(app)
    yield client, svc, seeded
    asyncio.run(db.close())


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------


def test_delete_org_admin_allowed_on_empty(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['empty_id']}",
        headers=_hdrs("u_admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] is True
    assert body["org_id"] == seed["empty_id"]
    assert body["cascade"] is False
    assert body["org_rows_deleted"] == 1
    # And the org is really gone.
    res2 = client.get(f"{BASE}/orgs")
    ids = [o["id"] for o in res2.json()["organizations"]]
    assert seed["empty_id"] not in ids


def test_delete_org_partner_allowed(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['empty_id']}",
        headers=_hdrs("u_partner"),
    )
    assert res.status_code == 200, res.text


def test_delete_org_manager_denied(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['empty_id']}",
        headers=_hdrs("u_mgr"),
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"
    assert res.json()["detail"]["resource"] == "org"
    assert res.json()["detail"]["action"] == "delete"


def test_delete_org_auditor_denied(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['empty_id']}",
        headers=_hdrs("u_aud"),
    )
    assert res.status_code == 403, res.text


def test_delete_org_unknown_user_denied(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['empty_id']}",
        headers=_hdrs("u_ghost"),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# cascade=false refuses when dependent rows exist
# ---------------------------------------------------------------------------


def test_delete_org_no_cascade_refuses_on_non_empty(delete_app) -> None:
    client, _svc, seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/{seed['full_id']}",
        headers=_hdrs("u_admin"),
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "org_not_empty"
    assert detail["org_id"] == seed["full_id"]
    # The seeded rows should each appear in the diagnostic.
    assert detail["dependents"].get("manual_inputs", 0) >= 1
    assert detail["dependents"].get("accounting_periods", 0) >= 1
    assert detail["dependents"].get("backup_history", 0) >= 1
    assert detail["dependents"].get("learning_samples", 0) >= 1
    assert detail["total_dependents"] >= 4


# ---------------------------------------------------------------------------
# cascade=true wipes everything, including on-disk backup files
# ---------------------------------------------------------------------------


def test_delete_org_cascade_true_purges_all(delete_app) -> None:
    client, svc, seed = delete_app
    backup_path = Path(seed["backup_path"])
    assert backup_path.exists(), "fixture setup failed: backup file missing"

    res = client.delete(
        f"{BASE}/orgs/{seed['full_id']}?cascade=true",
        headers=_hdrs("u_admin"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] is True
    assert body["cascade"] is True
    assert body["org_rows_deleted"] == 1
    # Each seeded table should be reported as purged.
    purged = body["tables_purged"]
    assert purged.get("manual_inputs", 0) >= 1
    assert purged.get("accounting_periods", 0) >= 1
    assert purged.get("backup_history", 0) >= 1
    assert purged.get("learning_samples", 0) >= 1
    # On-disk file unlinked.
    assert body["backup_files_removed"] >= 1
    assert not backup_path.exists()

    # And: a fresh dependency count must come back empty.
    counts = asyncio.run(svc._count_org_dependents(seed["full_id"]))
    assert counts == {}, counts


# ---------------------------------------------------------------------------
# 404 for unknown org_id (even with admin)
# ---------------------------------------------------------------------------


def test_delete_org_404_for_unknown_id(delete_app) -> None:
    client, _svc, _seed = delete_app
    res = client.delete(
        f"{BASE}/orgs/does_not_exist",
        headers=_hdrs("u_admin"),
    )
    assert res.status_code == 404, res.text
