"""Unit tests for NotesGenerator real-data context builders (P1-C fix).

The M3 audit (`_finance_plugin_audit_report.md` §2.1 / §6) flagged two
note sections — ``NOTE_ACCOUNTS_PAYABLE_CONCENTRATION`` and
``NOTE_RELATED_PARTY_TRANSACTIONS`` — as returning hard-coded stub data
(``母公司 12 万 / 兄弟公司 6 万`` and ``主要供应商 A 40%, B 25%, 其他 35%``)
regardless of the underlying trial balance.  These tests prove the
replacement implementations actually consume ``trial_balance_rows`` and
respond to the seeded payload.

Each test seeds a minimal trial balance via the public service API so
the persistence path is exercised end-to-end, then calls the two
private context builders directly.  Going through the builders (instead
of the full ``generate(...)`` flow) keeps the assertions tight and
avoids depending on the report-cells render path which is covered by
the M3 notes acceptance script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.models import OrganizationCreate
from finance_auto_backend.parsers.xls_parser import ParsedRow
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services.notes_generator import (
    _ctx_accounts_payable,
    _ctx_related_party,
)


def _row(
    *,
    row_index: int,
    parent_code: str,
    account_name: str,
    aux_text: str | None = None,
    closing_credit: float = 0.0,
    closing_debit: float = 0.0,
    period_credit: float = 0.0,
    period_debit: float = 0.0,
) -> ParsedRow:
    return ParsedRow(
        row_index=row_index,
        raw_code=parent_code,
        parent_code=parent_code,
        child_code=None,
        full_code=parent_code,
        account_name=account_name,
        aux_text=aux_text,
        opening_debit=0.0,
        opening_credit=0.0,
        period_debit=period_debit,
        period_credit=period_credit,
        closing_debit=closing_debit,
        closing_credit=closing_credit,
    )


async def _seed_org_with_rows(
    tmp_path: Path, rows: list[ParsedRow]
) -> tuple[FinanceAutoDB, FinanceAutoService, str, str]:
    db_path = tmp_path / "notes.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    service = FinanceAutoService(db)
    org = await service.create_org(
        OrganizationCreate(name="附注真实数据测试", code="NOTES-REAL-001")
    )
    period_id = "2025-FY"
    await service.ensure_period(org_id=org.id, period_id=period_id)
    imp = await service.insert_pending_import(
        org_id=org.id, period_id=period_id,
        source_file="seed.xlsx", file_size=0, file_sha256=None,
    )
    await service.persist_rows(
        import_id=imp.id, org_id=org.id, period_id=period_id, rows=rows,
    )
    await service.finalise_import(
        import_id=imp.id, parser_used="seed", row_count=len(rows),
        status="ok", error_message=None,
    )
    return db, service, org.id, period_id


@pytest.mark.asyncio
async def test_accounts_payable_aggregates_real_rows(tmp_path):
    """Three suppliers seeded; top-5 cutoff leaves all visible; totals
    and percentages must reflect the actual closing_credit sums."""
    rows = [
        _row(row_index=1, parent_code="2202", account_name="应付账款",
             aux_text="苏州某机械有限公司", closing_credit=120_000.0,
             period_credit=120_000.0),
        _row(row_index=2, parent_code="2202", account_name="应付账款",
             aux_text="上海某软件有限公司", closing_credit=80_000.0,
             period_credit=80_000.0),
        _row(row_index=3, parent_code="2202", account_name="应付账款",
             aux_text="深圳某物流有限公司", closing_credit=50_000.0,
             period_credit=50_000.0),
        # 1001 should be ignored entirely.
        _row(row_index=4, parent_code="1001", account_name="库存现金",
             closing_debit=10_000.0, period_debit=10_000.0),
    ]
    db, service, org_id, period_id = await _seed_org_with_rows(tmp_path, rows)
    try:
        ctx = await _ctx_accounts_payable(
            service, org_id=org_id, period_id=period_id,
        )
        assert ctx["top_n"] == 3
        labels = [s["label"] for s in ctx["suppliers"]]
        assert "苏州某机械有限公司" in labels
        assert "上海某软件有限公司" in labels
        assert "深圳某物流有限公司" in labels
        # Total 120k + 80k + 50k = 250k.
        assert ctx["total_end"] == "250,000.00"
        # Top-N amount equals total (only 3 suppliers).
        assert ctx["top_n_amount"] == "250,000.00"
        # 100% concentration since rest is empty.
        assert ctx["top_n_pct"] == "100.00"
        # Largest first — must be the 苏州 supplier (120k of 250k = 48%).
        first = ctx["suppliers"][0]
        assert first["label"] == "苏州某机械有限公司"
        assert first["end"] == "120,000.00"
        assert first["pct"] == "48.00"
        # No "其他供应商" rollup because total suppliers <= top_n.
        assert "其他供应商" not in labels
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_accounts_payable_empty_when_no_2202_rows(tmp_path):
    """No 2202 family rows present → empty suppliers + zero totals."""
    rows = [
        _row(row_index=1, parent_code="1001", account_name="库存现金",
             closing_debit=1_000.0, period_debit=1_000.0),
    ]
    db, service, org_id, period_id = await _seed_org_with_rows(tmp_path, rows)
    try:
        ctx = await _ctx_accounts_payable(
            service, org_id=org_id, period_id=period_id,
        )
        assert ctx["suppliers"] == []
        assert ctx["total_end"] == "0.00"
        assert ctx["top_n"] == 0
        assert "未发现" in ctx["narrative_seed"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_accounts_payable_top_n_rollup(tmp_path):
    """7 suppliers seeded → top-5 + 其他供应商 (2 rolled together)."""
    rows = []
    for i, (aux, amount) in enumerate(
        [
            ("供应商A", 100_000.0),
            ("供应商B", 80_000.0),
            ("供应商C", 60_000.0),
            ("供应商D", 40_000.0),
            ("供应商E", 30_000.0),
            ("供应商F", 20_000.0),
            ("供应商G", 10_000.0),
        ],
        start=1,
    ):
        rows.append(
            _row(row_index=i, parent_code="2202", account_name="应付账款",
                 aux_text=aux, closing_credit=amount, period_credit=amount)
        )
    db, service, org_id, period_id = await _seed_org_with_rows(tmp_path, rows)
    try:
        ctx = await _ctx_accounts_payable(
            service, org_id=org_id, period_id=period_id,
        )
        assert ctx["top_n"] == 5
        labels = [s["label"] for s in ctx["suppliers"]]
        # Top-5 + "其他供应商" rollup of F + G (20k + 10k = 30k).
        assert labels[-1] == "其他供应商"
        other = ctx["suppliers"][-1]
        assert other["end"] == "30,000.00"
        # Total = 340k.  Top-5 = 310k.  Concentration = 91.18%.
        assert ctx["total_end"] == "340,000.00"
        assert ctx["top_n_amount"] == "310,000.00"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_related_party_detects_keyword_rows(tmp_path):
    """aux_text containing 母公司 / 兄弟公司 / 控股 → surfaces in output."""
    rows = [
        _row(row_index=1, parent_code="1122", account_name="应收账款",
             aux_text="母公司北京XX集团", closing_debit=50_000.0,
             period_debit=120_000.0),
        _row(row_index=2, parent_code="2202", account_name="应付账款",
             aux_text="兄弟公司南京YY科技", closing_credit=30_000.0,
             period_credit=80_000.0),
        _row(row_index=3, parent_code="1131", account_name="应收股利",
             aux_text="控股股东张某某", closing_debit=10_000.0,
             period_debit=10_000.0),
        # Unrelated supplier — must NOT show up.
        _row(row_index=4, parent_code="2202", account_name="应付账款",
             aux_text="普通供应商无关键字", closing_credit=5_000.0,
             period_credit=5_000.0),
    ]
    db, service, org_id, period_id = await _seed_org_with_rows(tmp_path, rows)
    try:
        ctx = await _ctx_related_party(
            service, org_id=org_id, period_id=period_id,
        )
        assert ctx["party_count"] == 3
        names = {p["name"] for p in ctx["related_parties"]}
        assert "母公司北京XX集团" in names
        assert "兄弟公司南京YY科技" in names
        assert "控股股东张某某" in names
        assert "普通供应商无关键字" not in names
        # Relation label is derived from parent_code.
        parent = next(p for p in ctx["related_parties"]
                      if p["name"] == "母公司北京XX集团")
        assert "应收" in parent["relation"], parent
        sibling = next(p for p in ctx["related_parties"]
                       if p["name"] == "兄弟公司南京YY科技")
        assert "应付" in sibling["relation"], sibling
        assert "narrative_seed" in ctx and "3" in ctx["narrative_seed"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_related_party_empty_when_no_keyword_rows(tmp_path):
    """No aux_text matches → empty list + guidance message."""
    rows = [
        _row(row_index=1, parent_code="1001", account_name="库存现金",
             closing_debit=1_000.0, period_debit=1_000.0),
        _row(row_index=2, parent_code="2202", account_name="应付账款",
             aux_text="普通供应商X", closing_credit=5_000.0,
             period_credit=5_000.0),
    ]
    db, service, org_id, period_id = await _seed_org_with_rows(tmp_path, rows)
    try:
        ctx = await _ctx_related_party(
            service, org_id=org_id, period_id=period_id,
        )
        assert ctx["related_parties"] == []
        assert ctx["party_count"] == 0
        assert ctx["total_amount"] == "0.00"
        assert "未检索到" in ctx["narrative_seed"]
    finally:
        await db.close()
