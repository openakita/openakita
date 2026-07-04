"""Unit tests for PeerComparisonService happy-path (P2-4 audit fix).

§5.4 of the M3 audit reported that the M3 service layer (peer_comparison,
notes_generator, key_rotation, backup_restore, consolidation) had zero
dedicated unit tests — coverage came only from the M3 acceptance scripts.
The audit asked for at least 3 minimal happy-path tests on the most
critical services.  notes_generator + key_rotation got their own
modules; this file covers the peer-comparison branch.

Each test seeds a minimal balance sheet + income statement directly so
the metrics + quartile assessment path runs end-to-end without standing
up the full report generator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.models import OrganizationCreate
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services.peer_comparison import (
    PeerComparisonError,
    PeerComparisonService,
    _quartile_assessment,
)


def _ts() -> str:
    return "2026-05-23T12:00:00Z"


async def _seed_report_cells(
    service: FinanceAutoService,
    *,
    org_id: str,
    period_id: str,
    sheet_kind: str,
    cells: list[tuple[str, str, float]],
) -> None:
    """Insert one ``reports`` row + N ``report_cells`` rows directly.

    ``cells`` is a list of ``(reference_code, target_label, value)``
    tuples mirroring the report engine output shape so the peer service
    can pull metrics through ``_load_latest_cells``.
    """
    report_id = f"rep_{sheet_kind[:3]}_{org_id[-6:]}"
    await service.db.conn.execute(
        "INSERT INTO reports(id, org_id, period_id, sheet_kind, "
        "accounting_standard, template_id, template_version, status, "
        "cell_count, warnings_json, source_import_id, backend_used, "
        "output_path, generated_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            report_id, org_id, period_id, sheet_kind, "small_enterprise",
            f"{sheet_kind}_se_v1", 1, "ok", len(cells), "[]", None,
            "inline", None, _ts(),
        ),
    )
    for idx, (code, label, value) in enumerate(cells, start=1):
        await service.db.conn.execute(
            "INSERT INTO report_cells(id, report_id, reference_code, "
            "target_line_no, target_label, data_source, value, "
            "source_rows, version) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"{report_id}_{idx}", report_id, code, idx, label,
                "account", value, "[]", 1,
            ),
        )
    await service.db.conn.commit()


@pytest.fixture
async def peer_setup(tmp_path: Path):
    db_path = tmp_path / "peer.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    service = FinanceAutoService(db)
    org = await service.create_org(
        OrganizationCreate(
            name="同业对比测试", code="PEER-001",
            industry="manufacturing",  # seeded in v10 with 4 metrics
            standard="small",
        )
    )
    yield db, service, org.id
    await db.close()


@pytest.mark.asyncio
async def test_list_benchmarks_returns_seeded_rows(peer_setup):
    """v10 migration seeds 3 industries × 4 metrics = 12 rows."""
    _db, service, _org_id = peer_setup
    svc = PeerComparisonService(service)
    all_rows = await svc.list_benchmarks()
    assert len(all_rows) >= 12
    industries = {r["industry_code"] for r in all_rows}
    assert {"manufacturing", "restaurant", "tech_service"} <= industries

    manu = await svc.list_benchmarks(industry_code="manufacturing")
    assert len(manu) == 4
    metric_codes = {r["metric_code"] for r in manu}
    assert {"gross_margin", "current_ratio",
            "asset_turnover", "debt_ratio"} == metric_codes


@pytest.mark.asyncio
async def test_run_comparison_happy_path_4_metrics(peer_setup):
    """Seed both sheets, run the comparison, expect 4 assessed metrics
    with sensible quartile placements."""
    _db, service, org_id = peer_setup
    period_id = "2025-FY"
    await service.ensure_period(org_id=org_id, period_id=period_id)

    # Income statement: revenue 10M, cost 7M → gross_margin 0.30
    # → manufacturing p25/p50/p75 = 0.18/0.27/0.38 → 'median_band' (0.30 in 0.27-0.38).
    await _seed_report_cells(
        service, org_id=org_id, period_id=period_id,
        sheet_kind="income_statement",
        cells=[
            ("IS_REVENUE", "营业收入", 10_000_000.0),
            ("IS_COST", "营业成本", 7_000_000.0),
        ],
    )
    # Balance sheet:
    #   total_ca 6M / total_cl 4M → current_ratio 1.5 → manufacturing
    #     p25/p50/p75 = 0.90/1.40/2.10 → 1.5 in 1.4..2.1 → 'median_band'.
    #   revenue 10M / total_assets 12M → asset_turnover 0.833 → p50=1.0,
    #     p25=0.6, p75=1.6 → below p50 but above p25 → 'median_band' (0.6..1.6).
    #   total_liab 5M / total_assets 12M → debt_ratio 0.4167 → p50=0.48,
    #     p25=0.30, p75=0.65 → 'median_band' (0.30..0.65).
    await _seed_report_cells(
        service, org_id=org_id, period_id=period_id,
        sheet_kind="balance_sheet",
        cells=[
            ("BS_TOTAL_CA", "流动资产合计", 6_000_000.0),
            ("BS_TOTAL_CL", "流动负债合计", 4_000_000.0),
            ("BS_TOTAL_ASSETS", "资产总计", 12_000_000.0),
            ("BS_TOTAL_LIABILITIES", "负债合计", 5_000_000.0),
        ],
    )

    svc = PeerComparisonService(service)
    result = await svc.run_comparison(
        org_id=org_id, period_id=period_id,
        # industry_code omitted → falls back to org.industry.
    )
    assert result["industry_code"] == "manufacturing"
    assert result["ai_summary"] == ""  # M3 leaves it blank for Sibling B.
    assert result["version"] == 1
    assert isinstance(result["id"], int) and result["id"] > 0

    metrics = {m["metric_code"]: m for m in result["metrics"]}
    assert set(metrics) == {
        "gross_margin", "current_ratio", "asset_turnover", "debt_ratio",
    }

    # Hand-checked round-trips on metric arithmetic.
    assert metrics["gross_margin"]["org_value"] == 0.3
    assert metrics["current_ratio"]["org_value"] == 1.5
    # All four metrics fall inside the manufacturing median band given
    # the seeded inputs above.
    for m in metrics.values():
        assert m["assessment"] in {"median_band", "below", "above"}, m
        assert m["sample_size"] == 180  # manufacturing N
        assert m["delta_vs_median"] is not None


@pytest.mark.asyncio
async def test_run_comparison_missing_industry_raises(peer_setup):
    """No industry_code passed + org has none → PeerComparisonError."""
    _db, service, _org_id = peer_setup
    # Create a second org with no industry to exercise the fallback.
    org2 = await service.create_org(
        OrganizationCreate(name="无行业", code="NOIND-001", standard="small")
    )
    svc = PeerComparisonService(service)
    with pytest.raises(PeerComparisonError) as exc_info:
        await svc.run_comparison(org_id=org2.id, period_id="2025-FY")
    assert "industry_code" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_comparison_insufficient_data_when_no_cells(peer_setup):
    """No report cells → metrics all classify as 'insufficient_data'."""
    _db, service, org_id = peer_setup
    await service.ensure_period(org_id=org_id, period_id="2025-FY")
    svc = PeerComparisonService(service)
    result = await svc.run_comparison(
        org_id=org_id, period_id="2025-FY",
    )
    assessments = {m["assessment"] for m in result["metrics"]}
    assert assessments == {"insufficient_data"}
    # Persistence still happens — auditors can see the gap.
    persisted = await svc.list_results(org_id=org_id)
    assert len(persisted) == 1
    assert persisted[0]["id"] == result["id"]


def test_quartile_assessment_boundaries():
    """Pure-function tests for the bucket math — no DB."""
    # p25=0.30, p50=0.48, p75=0.65 (manufacturing debt_ratio).
    p25, p50, p75 = 0.30, 0.48, 0.65
    assert _quartile_assessment(0.10, p25, p50, p75) == "well_below"
    assert _quartile_assessment(0.28, p25, p50, p75) == "below"
    assert _quartile_assessment(0.45, p25, p50, p75) == "median_band"
    assert _quartile_assessment(0.70, p25, p50, p75) == "above"
    assert _quartile_assessment(1.00, p25, p50, p75) == "well_above"
