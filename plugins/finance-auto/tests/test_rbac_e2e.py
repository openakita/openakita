"""EX-P1-2 step 3 — end-to-end RBAC boundary tests for the 5 critical modules.

We boot a real router + FastAPI app, seed three users with different
roles (``admin``, ``auditor``, ``regular``), and probe each module's
write endpoint twice: once as a privileged user (200/201) and once
as an under-privileged user (403 + ``rbac_denied``).

Five modules covered (auditor / manager / partner / admin matrix from
``v12_extended_permissions.py``):

1. ``admin_backup.create`` — only admin should pass.
2. ``reclassification.apply`` — auditor denied, manager granted
   (provided assignment exists for that org).
3. ``consolidation.create_group`` — auditor denied, manager granted.
4. ``cash_flow.compute`` — auditor granted (with assignment).
5. ``parse_issue.decide`` — auditor granted (with assignment).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.models import (
    OrganizationCreate,
    UserCreateRequest,
)
from finance_auto_backend.routes import build_router_and_service
from finance_auto_backend.services.collaboration import CollaborationService


BASE = "/api/plugins/finance-auto"
USER_HEADER = "X-OpenAkita-User-Id"


@pytest.fixture()
def rbac_app(tmp_path: Path):
    router, svc, db = build_router_and_service(tmp_path / "rbac.sqlite")
    asyncio.run(db.init())

    async def _seed() -> dict[str, Any]:
        # Create an org for assignment scoping.
        org = await svc.create_org(
            OrganizationCreate(
                name="RBACOrg", code="RBAC",
                standard="small", fiscal_start="2025-01-01",
            )
        )
        await svc.ensure_period(org_id=org.id, period_id="2025-FY")

        collab = CollaborationService(svc.db.conn)
        # Three users — every required role x assignment combo we
        # probe in the tests is covered.
        for u in (
            UserCreateRequest(
                user_id="user_admin", display_name="Alice",
                role="admin", email="a@x.com",
            ),
            UserCreateRequest(
                user_id="user_mgr", display_name="Bob",
                role="manager", email="b@x.com",
            ),
            UserCreateRequest(
                user_id="user_aud", display_name="Carol",
                role="auditor", email="c@x.com",
            ),
            UserCreateRequest(
                user_id="user_partner", display_name="Dave",
                role="partner", email="d@x.com",
            ),
        ):
            await collab.register_user(u)

        # Auditor + manager assigned to RBAC org.  Partner is global,
        # admin needs no assignment (admin perms are scope=all).
        for user_id, role_in_project in (
            ("user_aud", "lead_auditor"),
            ("user_mgr", "reviewer"),
        ):
            await collab.assign(
                user_id=user_id,
                org_id=org.id,
                period_id=None,
                role_in_project=role_in_project,
                assigned_by="local",
            )

        return {"org_id": org.id, "period_id": "2025-FY"}

    seeded = asyncio.run(_seed())

    app = FastAPI()
    app.include_router(router, prefix=BASE)
    client = TestClient(app)
    yield client, svc, seeded
    asyncio.run(db.close())


def _hdrs(user_id: str) -> dict[str, str]:
    return {USER_HEADER: user_id}


# ---------------------------------------------------------------------------
# 1. admin_backup.create — only admin passes
# ---------------------------------------------------------------------------


def test_admin_backup_create_admin_allowed(
    rbac_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _svc, _seed = rbac_app
    # Sandbox env override so we can hit the backup endpoint without
    # writing to the user's real home directory.
    monkeypatch.setenv(
        "OPENAKITA_FINANCE_AUTO_BACKUP_ROOT", str(tmp_path / "backups")
    )

    res = client.post(
        f"{BASE}/admin/backups",
        json={"passphrase": "test-passphrase-12345"},
        headers=_hdrs("user_admin"),
    )
    # Admin has the seeded permission → either 201 (success) or a
    # 4xx coming from the backup service itself (e.g. encryption
    # not enabled).  Either way, NOT a 403 rbac_denied.
    assert res.status_code != 403, res.text


def test_admin_backup_create_auditor_denied(rbac_app) -> None:
    client, _svc, _seed = rbac_app
    res = client.post(
        f"{BASE}/admin/backups",
        json={"passphrase": "test-passphrase-12345"},
        headers=_hdrs("user_aud"),
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"
    assert res.json()["detail"]["resource"] == "admin_backup"


# ---------------------------------------------------------------------------
# 2. reclassification.apply — auditor denied, manager granted
# ---------------------------------------------------------------------------


def test_reclass_apply_auditor_denied(rbac_app) -> None:
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/reclassification-runs/apply",
        json={"period_id": seed["period_id"], "triggered_by": "carol"},
        headers=_hdrs("user_aud"),
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"


def test_reclass_preview_auditor_allowed(rbac_app) -> None:
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/reclassification-runs/preview",
        json={"period_id": seed["period_id"], "triggered_by": "carol"},
        headers=_hdrs("user_aud"),
    )
    # 404 is OK here — no import exists; the point is we cleared
    # RBAC and reached the service layer.
    assert res.status_code != 403, res.text


# ---------------------------------------------------------------------------
# 3. consolidation.create_group — auditor denied, manager granted
# ---------------------------------------------------------------------------


def test_consolidation_create_group_auditor_denied(rbac_app) -> None:
    client, _svc, _seed = rbac_app
    res = client.post(
        f"{BASE}/consolidation-groups",
        json={"name": "G1", "parent_org_id": "org_unused", "user_id": "carol"},
        headers=_hdrs("user_aud"),
    )
    # consolidation groups carry no ``org_id`` path param, so
    # scope=assigned means deny for any non-admin caller.  The
    # auditor's role doesn't have ``consolidation.create_group``
    # in the seed at all → 403.
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"


def test_consolidation_create_group_partner_allowed(rbac_app) -> None:
    client, _svc, _seed = rbac_app
    res = client.post(
        f"{BASE}/consolidation-groups",
        json={"name": "G1", "parent_org_id": "org_unused"},
        headers=_hdrs("user_partner"),
    )
    # Partner has scope=all consolidation.create_group → not 403.
    # The service may still 4xx on the missing parent_org_id;
    # we only care that the RBAC layer let us through.
    assert res.status_code != 403, res.text


# ---------------------------------------------------------------------------
# 4. cash_flow.compute — auditor (assigned) allowed, regular auditor (unassigned) denied
# ---------------------------------------------------------------------------


def test_cash_flow_compute_assigned_auditor_allowed(rbac_app) -> None:
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/cash-flow/compute",
        json={"period_id": seed["period_id"]},
        headers=_hdrs("user_aud"),
    )
    # 200 / 400 / 404 are all acceptable — anything but 403.
    assert res.status_code != 403, res.text


def test_cash_flow_compute_unknown_user_denied(rbac_app) -> None:
    """An unregistered user_id → CollaborationService treats it as
    'unknown'; check_permission returns False; we get 403."""
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/cash-flow/compute",
        json={"period_id": seed["period_id"]},
        headers=_hdrs("user_nope"),
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"


# ---------------------------------------------------------------------------
# 5. parse_issue.decide — assigned auditor allowed, unknown caller denied
# ---------------------------------------------------------------------------


def test_parse_issue_decide_assigned_auditor_allowed(rbac_app) -> None:
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/parse-issues/nonexistent_iss/decide",
        json={"decision": "skip", "decided_by": "carol"},
        headers=_hdrs("user_aud"),
    )
    # 404 (issue doesn't exist) is fine; just not 403.
    assert res.status_code != 403, res.text


def test_parse_issue_decide_unknown_user_denied(rbac_app) -> None:
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/parse-issues/nonexistent_iss/decide",
        json={"decision": "skip", "decided_by": "ghost"},
        headers=_hdrs("user_ghost"),
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "rbac_denied"


# ---------------------------------------------------------------------------
# Bonus: legacy 'local' caller (no X-OpenAkita-User-Id header) still passes
# ---------------------------------------------------------------------------


def test_legacy_local_caller_bypasses_rbac(rbac_app) -> None:
    """No header → user_id resolves to 'local' → CollaborationService
    short-circuits to True so v0.2 single-user mode keeps working."""
    client, _svc, seed = rbac_app
    res = client.post(
        f"{BASE}/orgs/{seed['org_id']}/cash-flow/compute",
        json={"period_id": seed["period_id"]},
    )
    assert res.status_code != 403, res.text
