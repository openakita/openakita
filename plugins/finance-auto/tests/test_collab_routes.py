"""End-to-end tests for the M2 Biz Stage 2 collaboration routes.

Boots an in-process FastAPI + fresh SQLite + KeyManager-disabled service.
Exercises every collab endpoint added by ``register_collab_endpoints``
plus the review-workflow state machine and the comment system.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.routes import build_router_and_service


BASE = "/api/plugins/finance-auto"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "collab.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI(title="finance-auto collab test")
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    yield TestClient(app)
    asyncio.run(db.close())


def _make_org(client: TestClient, *, name: str, code: str) -> str:
    r = client.post(f"{BASE}/orgs", json={"name": name, "code": code, "standard": "small"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make_user(client: TestClient, *, uid: str, role: str) -> None:
    r = client.post(f"{BASE}/users", json={
        "user_id": uid, "display_name": uid.upper(), "role": role,
    })
    assert r.status_code == 201, r.text


def _seed_report(service, org_id: str, period_id: str) -> str:
    """Insert a minimal report row directly (no full report pipeline)
    so the review-workflow endpoints have a target to attach to."""
    import asyncio
    rid = f"rep_test_{org_id[-4:]}"

    async def _go() -> None:
        await service.db.conn.execute(
            "INSERT INTO reports(id, org_id, period_id, sheet_kind, "
            "accounting_standard, template_id, template_version, status, "
            "cell_count, warnings_json, source_import_id, backend_used, "
            "output_path, generated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, org_id, period_id, "balance_sheet", "small_enterprise",
             "bs_se_v1", 1, "ok", 0, "[]", None, "inline", None,
             "2026-05-23T18:00:00Z"),
        )
        await service.db.conn.commit()
    asyncio.run(_go())
    return rid


def test_register_user_and_list(client: TestClient) -> None:
    _make_user(client, uid="usr_alice", role="auditor")
    _make_user(client, uid="usr_bob", role="manager")
    _make_user(client, uid="usr_carol", role="partner")
    r = client.get(f"{BASE}/users")
    assert r.status_code == 200
    users = r.json()["users"]
    assert {u["user_id"] for u in users} >= {"usr_alice", "usr_bob", "usr_carol"}
    # Role filter.
    r2 = client.get(f"{BASE}/users", params={"role": "auditor"})
    assert all(u["role"] == "auditor" for u in r2.json()["users"])


def test_register_user_duplicate_409(client: TestClient) -> None:
    _make_user(client, uid="usr_dup", role="auditor")
    r = client.post(f"{BASE}/users", json={
        "user_id": "usr_dup", "display_name": "Dup", "role": "auditor",
    })
    assert r.status_code == 409


def test_assign_and_list(client: TestClient) -> None:
    org_id = _make_org(client, name="Test Org A", code="TEST_A")
    _make_user(client, uid="usr_lead", role="auditor")
    r = client.post(f"{BASE}/orgs/{org_id}/assignments", json={
        "user_id": "usr_lead", "period_id": "2025-FY",
        "role_in_project": "lead_auditor",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == "usr_lead"
    assert body["role_in_project"] == "lead_auditor"
    # Re-issue same triple → reactivates / 200 with version bump.
    r2 = client.post(f"{BASE}/orgs/{org_id}/assignments", json={
        "user_id": "usr_lead", "period_id": "2025-FY",
        "role_in_project": "lead_auditor",
    })
    assert r2.status_code == 201, r2.text
    # List.
    r3 = client.get(f"{BASE}/orgs/{org_id}/assignments")
    assert r3.status_code == 200
    rows = r3.json()["assignments"]
    assert any(a["user_id"] == "usr_lead" for a in rows)


def test_review_workflow_full_happy_path(tmp_path: Path) -> None:
    db_path = tmp_path / "wf.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    client = TestClient(app)
    try:
        org_id = _make_org(client, name="WF Org", code="WF_ORG")
        report_id = _seed_report(service, org_id, "2025-FY")

        # 'local' has full access; we can skip user creation to keep this
        # test focused on the state machine.
        # submit (draft -> pending_review).
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/submit",
            json={"auditor_id": "local"},
        )
        assert r.status_code == 201, r.text
        wf = r.json()
        assert wf["status"] == "pending_review"
        wf_id = wf["workflow_id"]

        # approve (-> reviewed -> pending_signoff, auto-advanced).
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/approve",
            json={"actor_id": "local", "note": "looks good"},
        )
        assert r.status_code == 200, r.text
        wf = r.json()
        assert wf["status"] == "pending_signoff", wf

        # sign-off (-> signed_off, terminal).
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/sign-off",
            json={"actor_id": "local", "note": "approved by partner"},
        )
        assert r.status_code == 200, r.text
        wf = r.json()
        assert wf["status"] == "signed_off"
        assert wf["signed_off_at"] is not None
        assert wf["workflow_id"] == wf_id  # same workflow, advanced.

        # History captures every transition (3 hops + auto-advance + initial).
        assert len(wf["history"]) >= 3
    finally:
        asyncio.run(db.close())


def test_review_workflow_request_changes_returns(tmp_path: Path) -> None:
    db_path = tmp_path / "wf2.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    client = TestClient(app)
    try:
        org_id = _make_org(client, name="WF2", code="WF2")
        report_id = _seed_report(service, org_id, "2025-FY")

        client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/submit",
            json={"auditor_id": "local"},
        )
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/request-changes",
            json={"actor_id": "local", "reason": "missing cell BS_2202"},
        )
        assert r.status_code == 200, r.text
        wf = r.json()
        assert wf["status"] == "returned"
        assert wf["return_reason"] == "missing cell BS_2202"

        # Re-submit should bounce returned -> pending_review.
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/review/submit",
            json={"auditor_id": "local"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "pending_review"
    finally:
        asyncio.run(db.close())


def test_comment_and_listing(tmp_path: Path) -> None:
    db_path = tmp_path / "cmt.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    client = TestClient(app)
    try:
        org_id = _make_org(client, name="CMT", code="CMT")
        report_id = _seed_report(service, org_id, "2025-FY")
        cell_id = "cel_BS_1001_xxx"
        r = client.post(
            f"{BASE}/orgs/{org_id}/reports/{report_id}/cells/{cell_id}/comments",
            json={
                "body": "请复核货币资金期末余额",
                "kind": "review_question",
                "author_id": "local",
            },
        )
        assert r.status_code == 201, r.text
        c = r.json()
        assert c["cell_id"] == cell_id
        assert c["resolved"] is False
        # List comments for this report.
        r2 = client.get(f"{BASE}/orgs/{org_id}/reports/{report_id}/comments")
        assert r2.status_code == 200
        body = r2.json()
        assert body["total"] >= 1
        assert any(x["body"] == "请复核货币资金期末余额" for x in body["comments"])
    finally:
        asyncio.run(db.close())


def test_check_permission_denies_unknown_user(tmp_path: Path) -> None:
    db_path = tmp_path / "perm.sqlite"
    router, service, db = build_router_and_service(db_path)
    asyncio.run(db.init())
    try:
        from finance_auto_backend.services.collaboration import CollaborationService
        collab = CollaborationService(service.db.conn)
        ok_local = asyncio.run(collab.check_permission(
            user_id="local", resource="report", action="write", org_id="o1",
        ))
        assert ok_local is True
        denied_unknown = asyncio.run(collab.check_permission(
            user_id="ghost_user", resource="report", action="write", org_id="o1",
        ))
        assert denied_unknown is False
    finally:
        asyncio.run(db.close())


def test_check_permission_assigned_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "perm2.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    client = TestClient(app)
    try:
        org_id = _make_org(client, name="P2", code="P2")
        _make_user(client, uid="usr_a", role="auditor")
        _make_user(client, uid="usr_other", role="auditor")
        client.post(f"{BASE}/orgs/{org_id}/assignments", json={
            "user_id": "usr_a", "period_id": "2025-FY",
            "role_in_project": "lead_auditor",
        })
        from finance_auto_backend.services.collaboration import CollaborationService
        collab = CollaborationService(service.db.conn)
        assigned = asyncio.run(collab.check_permission(
            user_id="usr_a", resource="report", action="write", org_id=org_id,
        ))
        assert assigned is True
        unassigned = asyncio.run(collab.check_permission(
            user_id="usr_other", resource="report", action="write", org_id=org_id,
        ))
        assert unassigned is False
        # Partner: no assignment needed (scope=all).
        _make_user(client, uid="usr_partner", role="partner")
        partner = asyncio.run(collab.check_permission(
            user_id="usr_partner", resource="report", action="write", org_id=org_id,
        ))
        assert partner is True
    finally:
        asyncio.run(db.close())
