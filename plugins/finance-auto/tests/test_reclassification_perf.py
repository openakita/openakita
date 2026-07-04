"""Performance regression tests for the reclassification engine.

EX-P2-3 from the extended audit: the per-item INSERT loop was a
classic N+1 (one SQLite round-trip per matched item).  After
switching to ``executemany`` a 1000-item batch should finish in
well under 1 second on commodity hardware.

The fixture seeds 1000 synthetic trial-balance rows and one rule
that matches every row, then asserts both correctness (run.items
count) and wall-clock.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.models import (
    OrganizationCreate,
    ReclassificationRuleCreateRequest,
    ReclassificationRunRequest,
)
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services.reclassification import ReclassificationService


N_ROWS = 1000
WALL_CLOCK_BUDGET_SEC = 1.0


@pytest.fixture()
def big_balance(tmp_path: Path):
    db_path = tmp_path / "reclass_perf.sqlite"
    db = FinanceAutoDB(db_path)
    asyncio.run(db.init())
    service = FinanceAutoService(db)

    async def _seed() -> tuple[str, str, str]:
        org = await service.create_org(
            OrganizationCreate(
                name="PerfOrg",
                code="PERF",
                standard="small",
                fiscal_start="2025-01-01",
            )
        )
        await service.ensure_period(org_id=org.id, period_id="2025-FY")
        imp = await service.insert_pending_import(
            org_id=org.id,
            period_id="2025-FY",
            source_file="perf.xlsx",
            file_size=1024,
            file_sha256="0" * 64,
        )
        rows = []
        for i in range(N_ROWS):
            # All rows live under prefix "1122" so a single rule matches.
            full_code = f"1122.{i:04d}"
            rows.append({
                "import_id": imp.id,
                "org_id": org.id,
                "period_id": "2025-FY",
                "row_index": i,
                "raw_code": full_code,
                "parent_code": "1122",
                "child_code": f"{i:04d}",
                "full_code": full_code,
                "account_name": f"应收-客户{i:04d}",
                "aux_text": "",
                "opening_debit": 0.0,
                "opening_credit": 0.0,
                "period_debit": 0.0,
                "period_credit": 0.0,
                "closing_debit": float(1000 + i),
                "closing_credit": 0.0,
            })
        # Insert directly through the service-private writer.  We
        # construct cheap ParsedRow-shaped namespaces inline.
        from types import SimpleNamespace

        await service.persist_rows(
            import_id=imp.id,
            org_id=org.id,
            period_id="2025-FY",
            rows=[SimpleNamespace(**r) for r in rows],
        )
        await service.finalise_import(
            import_id=imp.id, parser_used="perf", row_count=N_ROWS,
            status="ok", error_message=None,
        )
        return org.id, "2025-FY", imp.id

    org_id, period_id, import_id = asyncio.run(_seed())
    yield service, org_id, period_id, import_id
    asyncio.run(db.close())


def test_reclassification_apply_1000_items_under_one_second(big_balance) -> None:
    service, org_id, period_id, _import_id = big_balance
    rsvc = ReclassificationService(service.db.conn)

    async def _setup_rule_and_run() -> int:
        await rsvc.create_rule(
            org_id=org_id,
            payload=ReclassificationRuleCreateRequest(
                name="receivable-to-other",
                description="move every 1122 row to 1221 for the perf test",
                when_condition={
                    "account_code_starts": ["1122"],
                    "balance_direction": "debit",
                },
                action={
                    "move_to_account_code": "1221",
                    "reason": "perf bench",
                    "parse_issue_threshold": "1000000000",
                },
                active=True,
                priority=10,
            ),
        )
        t0 = time.perf_counter()
        run = await rsvc.run(
            org_id=org_id,
            payload=ReclassificationRunRequest(
                period_id=period_id, triggered_by="perf",
            ),
            mode="preview",
        )
        elapsed = time.perf_counter() - t0
        assert run.items_count == N_ROWS, (
            f"expected {N_ROWS} matched items, got {run.items_count}"
        )
        return elapsed

    elapsed = asyncio.run(_setup_rule_and_run())
    assert elapsed < WALL_CLOCK_BUDGET_SEC, (
        f"reclassification preview with {N_ROWS} items took "
        f"{elapsed:.3f}s; budget is {WALL_CLOCK_BUDGET_SEC}s"
    )
