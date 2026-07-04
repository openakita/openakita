"""Tests for M2 Biz Stage 6 — consolidation routes + engine."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.routes import build_router_and_service


BASE = "/api/plugins/finance-auto"


@pytest.fixture()
def harness(tmp_path: Path):
    db_path = tmp_path / "consol.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    yield app, service
    asyncio.run(db.close())


def _seed_org_with_bs(service, *, name: str, code: str, cells: dict[str, float],
                       period_id: str = "2025-FY") -> str:
    """Create an org + a balance_sheet report + report_cells."""
    import uuid
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    report_id = f"rep_bs_{org_id[-4:]}"
    org_code = code  # capture in closure to avoid name shadowing below.

    async def _go() -> None:
        await service.db.conn.execute(
            "INSERT INTO organizations(id, name, code, standard, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (org_id, name, org_code, "cas",
             "2026-05-23T18:00:00Z", "2026-05-23T18:00:00Z"),
        )
        await service.db.conn.execute(
            "INSERT INTO accounting_periods(id, org_id, period_id, period_kind, "
            "start_date, end_date, created_at) VALUES (?,?,?,?,?,?,?)",
            (f"per_{org_id[-4:]}", org_id, period_id, "year",
             "2025-01-01", "2025-12-31", "2026-05-23T18:00:00Z"),
        )
        await service.db.conn.execute(
            "INSERT INTO reports(id, org_id, period_id, sheet_kind, "
            "accounting_standard, template_id, template_version, status, "
            "cell_count, warnings_json, source_import_id, backend_used, "
            "output_path, generated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, org_id, period_id, "balance_sheet",
             "general_enterprise", "bs_ge_v1", 1, "ok",
             len(cells), "[]", None, "inline", None,
             "2026-05-23T18:00:00Z"),
        )
        for ref_code, val in cells.items():
            await service.db.conn.execute(
                "INSERT INTO report_cells(id, report_id, reference_code, "
                "target_line_no, target_label, indent_level, data_source, "
                "value, sign, is_total, is_tbd) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"cel_{ref_code}_{report_id[-6:]}", report_id, ref_code, 99,
                 ref_code, 0, "account", float(val), 1, 0, 0),
            )
        await service.db.conn.commit()
    asyncio.run(_go())
    return org_id


def test_create_group_and_auto_parent_member(harness) -> None:
    app, service = harness
    client = TestClient(app)
    parent = _seed_org_with_bs(service, name="母公司", code="HOLD_001",
                                cells={"BS_1122": 100000, "BS_2202": 80000})

    r = client.post(f"{BASE}/consolidation-groups", json={
        "name": "Skyforge集团", "parent_org_id": parent,
        "description": "测试集团",
    })
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["name"] == "Skyforge集团"
    assert g["parent_org_id"] == parent

    # Auto-added the parent as the first member.
    r2 = client.get(f"{BASE}/consolidation-groups/{g['group_id']}/members")
    assert r2.status_code == 200
    members = r2.json()
    assert len(members) == 1
    assert members[0]["is_parent"] is True
    assert members[0]["subsidiary_org_id"] == parent


def test_add_subsidiary_and_eliminate(harness) -> None:
    app, service = harness
    client = TestClient(app)
    # Parent: AR 100k, AP 80k
    parent = _seed_org_with_bs(service, name="母", code="HOLD",
                                cells={"BS_1122": 100000, "BS_2202": 80000,
                                       "BS_TOTAL_OWNERS_EQUITY": 50000,
                                       "BS_1501": 60000})
    # Subsidiary: AR 50k, AP 30k (of which 40k AR / 30k AP is intra-group)
    sub = _seed_org_with_bs(service, name="子", code="SUB",
                             cells={"BS_1122": 50000, "BS_2202": 30000,
                                    "BS_TOTAL_OWNERS_EQUITY": 30000})

    r = client.post(f"{BASE}/consolidation-groups", json={
        "name": "TestGroup", "parent_org_id": parent,
    })
    gid = r.json()["group_id"]

    # Add subsidiary at 80% ownership using full method.
    r2 = client.post(f"{BASE}/consolidation-groups/{gid}/members", json={
        "subsidiary_org_id": sub, "ownership_pct": 80.0, "join_method": "full",
    })
    assert r2.status_code == 201, r2.text

    # Add elimination: 40k intra-group AR ↔ AP.
    r3 = client.post(f"{BASE}/consolidation-groups/{gid}/eliminations", json={
        "period_id": "2025-FY", "kind": "inter_ar_ap",
        "debit_target": "BS_2202", "credit_target": "BS_1122",
        "amount": "40000", "rationale": "intra-group AR/AP",
    })
    assert r3.status_code == 201, r3.text

    # Run consolidation.
    r4 = client.post(f"{BASE}/consolidation-groups/{gid}/runs", json={
        "period_id": "2025-FY", "kind": "balance_sheet",
    })
    assert r4.status_code == 201, r4.text
    rep = r4.json()
    assert rep["status"] == "ok"

    # Build a dict for assertions.
    cells = {c["reference_code"]: Decimal(c["value"]) for c in rep["cells"]}
    # AR = 100k + 50k - 40k(elim) = 110k.
    assert cells["BS_1122"] == Decimal("110000")
    # AP = 80k + 30k - 40k(elim) = 70k.
    assert cells["BS_2202"] == Decimal("70000")
    # Minority interest = (1 - 0.8) * subsidiary equity (30k) = 6k.
    assert Decimal(rep["minority_interest"]) == Decimal("6000")
    # Elimination IDs captured.
    assert len(rep["elimination_ids"]) == 1
    # Member snapshot has both.
    assert len(rep["member_orgs_snapshot"]) == 2


def test_run_with_no_eliminations(harness) -> None:
    app, service = harness
    client = TestClient(app)
    parent = _seed_org_with_bs(service, name="P", code="P",
                                cells={"BS_1122": 1000, "BS_1001": 500})
    r = client.post(f"{BASE}/consolidation-groups", json={
        "name": "NoElim", "parent_org_id": parent,
    })
    gid = r.json()["group_id"]
    r2 = client.post(f"{BASE}/consolidation-groups/{gid}/runs", json={
        "period_id": "2025-FY", "kind": "balance_sheet",
    })
    assert r2.status_code == 201, r2.text
    rep = r2.json()
    cells = {c["reference_code"]: Decimal(c["value"]) for c in rep["cells"]}
    assert cells["BS_1122"] == Decimal("1000")
    assert cells["BS_1001"] == Decimal("500")
    assert len(rep["elimination_ids"]) == 0


def test_run_group_missing_returns_404(harness) -> None:
    app, _ = harness
    client = TestClient(app)
    r = client.post(f"{BASE}/consolidation-groups/9999/runs", json={
        "period_id": "2025-FY", "kind": "balance_sheet",
    })
    assert r.status_code == 404


def test_list_groups_and_reports(harness) -> None:
    app, service = harness
    client = TestClient(app)
    parent = _seed_org_with_bs(service, name="P", code="P",
                                cells={"BS_1001": 9999})
    r = client.post(f"{BASE}/consolidation-groups", json={
        "name": "ListGroup", "parent_org_id": parent,
    })
    gid = r.json()["group_id"]
    client.post(f"{BASE}/consolidation-groups/{gid}/runs", json={
        "period_id": "2025-FY", "kind": "balance_sheet",
    })
    # Use a different period to avoid the (group, period, kind, generated_at)
    # UNIQUE collision when the second run lands in the same wall-clock second.
    client.post(f"{BASE}/consolidation-groups/{gid}/runs", json={
        "period_id": "2024-FY", "kind": "balance_sheet",
    })
    r2 = client.get(f"{BASE}/consolidation-groups")
    assert r2.status_code == 200
    assert any(g["group_id"] == gid for g in r2.json()["groups"])
    r3 = client.get(f"{BASE}/consolidation-groups/{gid}/reports")
    assert r3.status_code == 200
    assert r3.json()["total"] == 2
