"""End-to-end tests for the M2 Biz Stage 3 reclassification routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.routes import build_router_and_service


BASE = "/api/plugins/finance-auto"


@pytest.fixture()
def harness(tmp_path: Path):
    db_path = tmp_path / "reclass.sqlite"
    router, service, db = build_router_and_service(db_path)
    app = FastAPI(title="finance-auto reclass test")
    app.include_router(router, prefix=BASE)
    asyncio.run(db.init())
    yield app, service
    asyncio.run(db.close())


def _seed_org_and_balance(client: TestClient, service) -> tuple[str, str, str]:
    """Insert one org + one import + a handful of TB rows for the engine
    to chew on.  Returns (org_id, period_id, import_id)."""
    r = client.post(f"{BASE}/orgs", json={
        "name": "Reclass Org", "code": "RECLS_ORG", "standard": "small",
    })
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    period_id = "2025-FY"
    import_id = f"imp_test_{org_id[-4:]}"

    async def _go() -> None:
        # Period (`id` is the PK; period_id is a label).
        await service.db.conn.execute(
            "INSERT OR IGNORE INTO accounting_periods(id, org_id, period_id, "
            "period_kind, start_date, end_date, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"per_{org_id[-4:]}", org_id, period_id, "year",
             "2025-01-01", "2025-12-31", "2026-05-23T18:00:00Z"),
        )
        # Import header (must be 'ok' for _resolve_import_id to find it).
        await service.db.conn.execute(
            "INSERT INTO trial_balance_imports(id, org_id, period_id, "
            "source_file, file_size, parser_used, row_count, status, "
            "error_message, uploaded_at, parsed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (import_id, org_id, period_id, "test.xlsx", 1024, "stub", 4,
             "ok", None, "2026-05-23T18:00:00Z", "2026-05-23T18:00:00Z"),
        )
        # TB rows: row 1 = 应收账款 with negative (credit) closing
        #          row 2 = 应付账款 normal credit
        #          row 3 = 应付账款 with negative (debit) closing → reclassify
        #          row 4 = 预收账款 small
        for idx, (full, name, debit, credit) in enumerate([
            ("1122.01", "应收账款-客户A",   0.0,    250000.00),  # 重分类候选
            ("2202.01", "应付账款-供应商A", 0.0,    180000.00),
            ("2202.02", "应付账款-供应商B", 75000.0, 0.0),       # 重分类候选
            ("2203.01", "预收账款-客户B",   0.0,    5000.00),
        ], start=1):
            await service.db.conn.execute(
                "INSERT INTO trial_balance_rows(id, import_id, org_id, period_id, "
                "row_index, raw_code, parent_code, child_code, full_code, "
                "account_name, aux_text, opening_debit, opening_credit, "
                "period_debit, period_credit, closing_debit, closing_credit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"row_{import_id}_{idx}", import_id, org_id, period_id, idx,
                    full, full.split(".")[0], full.split(".")[1] if "." in full else None,
                    full, name, None, 0.0, 0.0, 0.0, 0.0, debit, credit,
                ),
            )
        await service.db.conn.commit()
    asyncio.run(_go())
    return org_id, period_id, import_id


def test_create_and_list_rules(harness) -> None:
    app, service = harness
    client = TestClient(app)
    org_id, _, _ = _seed_org_and_balance(client, service)

    r = client.post(f"{BASE}/orgs/{org_id}/reclassification-rules", json={
        "name": "应收负余额重分类",
        "description": "应收变负 → 应付",
        "when_condition": {
            "account_code_starts": ["1122"],
            "balance_direction": "credit",
            "threshold": "0.01",
        },
        "action": {
            "move_to_account_code": "2202",
            "reason": "应收负余额按谨慎性重分类",
            "parse_issue_severity": "warning",
            "parse_issue_threshold": "100000",
        },
        "priority": 10,
    })
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["name"] == "应收负余额重分类"
    assert rule["priority"] == 10
    assert rule["active"] is True
    assert rule["version"] == 1

    # List back.
    r2 = client.get(f"{BASE}/orgs/{org_id}/reclassification-rules")
    assert r2.status_code == 200
    body = r2.json()
    assert body["total"] >= 1
    names = [x["name"] for x in body["rules"]]
    assert "应收负余额重分类" in names


def test_preview_then_apply(harness) -> None:
    app, service = harness
    client = TestClient(app)
    org_id, period_id, _ = _seed_org_and_balance(client, service)

    # Two rules at priority 10 / 11.
    client.post(f"{BASE}/orgs/{org_id}/reclassification-rules", json={
        "name": "应收→应付",
        "when_condition": {
            "account_code_starts": ["1122"], "balance_direction": "credit",
        },
        "action": {
            "move_to_account_code": "2202",
            "reason": "应收负余额→应付",
            "parse_issue_severity": "warning",
            "parse_issue_threshold": "100000",
        },
        "priority": 10,
    })
    client.post(f"{BASE}/orgs/{org_id}/reclassification-rules", json={
        "name": "应付→应收",
        "when_condition": {
            "account_code_starts": ["2202"], "balance_direction": "debit",
        },
        "action": {
            "move_to_account_code": "1122",
            "reason": "应付负余额→应收",
            "parse_issue_severity": "warning",
            "parse_issue_threshold": "100000",
        },
        "priority": 11,
    })

    # Preview.
    r = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/preview",
        json={"period_id": period_id},
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["mode"] == "preview"
    assert run["items_count"] >= 2, run
    assert run["rules_count"] == 2
    # Should NOT have generated parse_issue ids in preview mode.
    assert run["parse_issue_ids"] == []
    # Check at least one item matches each rule.
    src_accounts = {it["source_account"] for it in run["items"]}
    assert any(a.startswith("1122") for a in src_accounts)
    assert any(a.startswith("2202") for a in src_accounts)

    # Apply.
    r2 = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/apply",
        json={"period_id": period_id},
    )
    assert r2.status_code == 201, r2.text
    run2 = r2.json()
    assert run2["mode"] == "apply"
    # 250k > 100k threshold → 应收 rule should emit a ParseIssue.
    # 75k < 100k threshold → 应付 rule should NOT.
    assert len(run2["parse_issue_ids"]) >= 1, run2

    # List runs.
    r3 = client.get(f"{BASE}/orgs/{org_id}/reclassification-runs",
                    params={"period_id": period_id})
    assert r3.status_code == 200
    runs = r3.json()
    assert len(runs) == 2
    # Most-recent first.
    assert runs[0]["mode"] == "apply"
    assert runs[1]["mode"] == "preview"


def test_preview_with_no_matching_rules(harness) -> None:
    app, service = harness
    client = TestClient(app)
    org_id, period_id, _ = _seed_org_and_balance(client, service)

    # Rule that matches nothing (5xxx accounts only — TB has none).
    client.post(f"{BASE}/orgs/{org_id}/reclassification-rules", json={
        "name": "5xxx Filter",
        "when_condition": {"account_code_starts": ["5001"], "balance_direction": "debit"},
        "action": {"move_to_account_code": "9999", "reason": "n/a"},
        "priority": 99,
    })
    r = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/preview",
        json={"period_id": period_id},
    )
    assert r.status_code == 201, r.text
    assert r.json()["items_count"] == 0


def test_preview_unknown_period_404(harness) -> None:
    app, service = harness
    client = TestClient(app)
    org_id, _, _ = _seed_org_and_balance(client, service)
    client.post(f"{BASE}/orgs/{org_id}/reclassification-rules", json={
        "name": "any",
        "when_condition": {"account_code_starts": ["1122"], "balance_direction": "credit"},
        "action": {"move_to_account_code": "2202", "reason": "x"},
    })
    r = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/preview",
        json={"period_id": "9999-FY"},
    )
    assert r.status_code == 404


def test_global_rule_visible_to_org(harness) -> None:
    """A rule with org_id=NULL should appear for every org's list_rules."""
    app, service = harness

    async def _insert_global() -> None:
        await service.db.conn.execute(
            "INSERT INTO reclassification_rules(org_id, name, when_condition, "
            "action, active, priority, created_at, updated_at) "
            "VALUES (NULL,?,?,?,1,5,?,?)",
            (
                "全局规则", '{"account_code_starts":["1122"]}',
                '{"move_to_account_code":"2202"}',
                "2026-05-23T18:00:00Z", "2026-05-23T18:00:00Z",
            ),
        )
        await service.db.conn.commit()
    asyncio.run(_insert_global())

    client = TestClient(app)
    org_id, _, _ = _seed_org_and_balance(client, service)
    r = client.get(f"{BASE}/orgs/{org_id}/reclassification-rules")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["rules"]]
    assert "全局规则" in names
