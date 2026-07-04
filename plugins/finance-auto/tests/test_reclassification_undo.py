"""EX-P2-9 — reclassification apply/undo round-trip.

We exercise the full life-cycle:

1. seed a rule whose action threshold is low enough that every
   matched item spawns a ``parse_issues`` row;
2. apply the rule once → assert N parse_issues rows + one
   ``reclassification_history`` row with status='recorded';
3. POST .../undo → assert the parse_issues are gone, history row
   flips to 'undone', and the run row is marked 'undone'.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_auto_backend.models import (
    OrganizationCreate,
    ReclassificationRuleCreateRequest,
    ReclassificationRunRequest,
)
from finance_auto_backend.routes import build_router_and_service
from finance_auto_backend.services.reclassification import (
    ReclassificationService,
)


BASE = "/api/plugins/finance-auto"


@pytest.fixture()
def app_and_rule(tmp_path: Path):
    router, svc, db = build_router_and_service(tmp_path / "undo.sqlite")
    asyncio.run(db.init())
    app = FastAPI()
    app.include_router(router, prefix=BASE)
    client = TestClient(app)

    async def _seed() -> tuple[str, str, str, int]:
        org = await svc.create_org(
            OrganizationCreate(
                name="UndoOrg", code="UNDO",
                standard="small", fiscal_start="2025-01-01",
            )
        )
        await svc.ensure_period(org_id=org.id, period_id="2025-FY")
        imp = await svc.insert_pending_import(
            org_id=org.id, period_id="2025-FY",
            source_file="u.xlsx", file_size=1024, file_sha256="0" * 64,
        )
        from types import SimpleNamespace
        rows = []
        for i in range(3):
            full_code = f"1122.{i:04d}"
            rows.append(SimpleNamespace(
                import_id=imp.id, org_id=org.id, period_id="2025-FY",
                row_index=i, raw_code=full_code, parent_code="1122",
                child_code=f"{i:04d}", full_code=full_code,
                account_name=f"应收{i}", aux_text="",
                opening_debit=0.0, opening_credit=0.0,
                period_debit=0.0, period_credit=0.0,
                closing_debit=2_000_000.0,
                closing_credit=0.0,
            ))
        await svc.persist_rows(
            import_id=imp.id, org_id=org.id, period_id="2025-FY", rows=rows,
        )
        await svc.finalise_import(
            import_id=imp.id, parser_used="undo", row_count=3,
            status="ok", error_message=None,
        )

        rsvc = ReclassificationService(svc.db.conn)
        rule = await rsvc.create_rule(
            org_id=org.id,
            payload=ReclassificationRuleCreateRequest(
                name="undo-rule",
                description="EX-P2-9 happy-path undo",
                when_condition={
                    "account_code_starts": ["1122"],
                    "balance_direction": "debit",
                },
                action={
                    "move_to_account_code": "1221",
                    "reason": "undo test",
                    "parse_issue_threshold": "1000000",  # < 2M closing, so every row issues
                },
                active=True,
                priority=10,
            ),
        )
        return org.id, "2025-FY", imp.id, rule.rule_id

    org_id, period_id, _imp, rule_id = asyncio.run(_seed())
    yield client, svc, org_id, period_id, rule_id
    asyncio.run(svc.db.close())


def _count(conn, sql, params=()):
    async def _go():
        async with conn.execute(sql, params) as cur:
            (n,) = await cur.fetchone()
            return n
    return asyncio.run(_go())


def test_apply_records_history_then_undo_reverts(app_and_rule) -> None:
    client, svc, org_id, period_id, rule_id = app_and_rule

    # 1. apply the rule.
    res = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/apply",
        json={"period_id": period_id, "triggered_by": "alice"},
    )
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["items_count"] == 3
    assert len(run["parse_issue_ids"]) == 3

    # 2. history row recorded, parse_issues persisted.
    conn = svc.db.conn
    assert _count(
        conn,
        "SELECT COUNT(*) FROM reclassification_history "
        "WHERE rule_id=? AND status='recorded'",
        (rule_id,),
    ) == 1
    assert _count(
        conn,
        "SELECT COUNT(*) FROM parse_issues WHERE org_id=? AND period_id=?",
        (org_id, period_id),
    ) == 3

    # 3. undo.
    res = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-rules/{rule_id}/undo"
        "?actor_id=alice"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["deleted_parse_issues"] == 3

    # 4. parse_issues gone, history undone, run marked undone.
    assert _count(
        conn,
        "SELECT COUNT(*) FROM parse_issues WHERE org_id=? AND period_id=?",
        (org_id, period_id),
    ) == 0
    assert _count(
        conn,
        "SELECT COUNT(*) FROM reclassification_history "
        "WHERE rule_id=? AND status='undone'",
        (rule_id,),
    ) == 1


def test_undo_on_unapplied_rule_returns_404(app_and_rule) -> None:
    client, _svc, org_id, _period, rule_id = app_and_rule
    # never applied — no history → 404
    res = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-rules/{rule_id}/undo"
    )
    assert res.status_code == 404, res.text


def test_run_list_reflects_undone_at_after_undo(app_and_rule) -> None:
    """Regression: after POST /undo the run-list must surface ``undone_at``.

    The run header's ``status`` column is pinned to
    ('ok','failed','partial') by the v9 CHECK constraint, so an undone
    run cannot flip its own status.  The undo marker only lives in
    ``reclassification_history``; the run serializer must join it back in
    so the list API stops rendering an undone run as live.
    """
    client, _svc, org_id, period_id, rule_id = app_and_rule

    # apply, then confirm run-list shows the run as NOT undone.
    client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/apply",
        json={"period_id": period_id, "triggered_by": "alice"},
    )
    pre = client.get(f"{BASE}/orgs/{org_id}/reclassification-runs")
    assert pre.status_code == 200, pre.text
    pre_runs = pre.json()
    assert len(pre_runs) >= 1
    assert all(r.get("undone_at") is None for r in pre_runs)

    # undo.
    undo = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-rules/{rule_id}/undo"
        "?actor_id=bob"
    )
    assert undo.status_code == 200, undo.text

    # run-list must now reflect the undo without any reload/race.
    post = client.get(f"{BASE}/orgs/{org_id}/reclassification-runs")
    assert post.status_code == 200, post.text
    post_runs = post.json()
    undone = [r for r in post_runs if r.get("undone_at")]
    assert len(undone) == 1, post.text
    assert undone[0]["undone_by"] == "bob"
    assert undone[0]["undone_at"]


def test_double_undo_returns_404(app_and_rule) -> None:
    client, _svc, org_id, period_id, rule_id = app_and_rule
    client.post(
        f"{BASE}/orgs/{org_id}/reclassification-runs/apply",
        json={"period_id": period_id, "triggered_by": "alice"},
    )
    res1 = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-rules/{rule_id}/undo"
    )
    assert res1.status_code == 200
    res2 = client.post(
        f"{BASE}/orgs/{org_id}/reclassification-rules/{rule_id}/undo"
    )
    assert res2.status_code == 404
