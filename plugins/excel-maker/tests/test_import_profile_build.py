from __future__ import annotations

from pathlib import Path

import pytest
from excel_auditor import WorkbookAuditor
from excel_importer import WorkbookImporter
from excel_plan import WorkbookPlanBuilder
from excel_profiler import WorkbookProfiler
from excel_workbook_builder import WorkbookBuilder


def test_csv_import_profile_build_and_audit(tmp_path) -> None:
    pytest.importorskip("openpyxl")

    source = tmp_path / "sales.csv"
    source.write_text("region,revenue\nEast,10\nWest,20\n", encoding="utf-8")

    imported = WorkbookImporter(tmp_path).import_file(source, "wb_test")
    profile = WorkbookProfiler().profile_import(imported.profile_path, tmp_path / "profile.json")
    plan = WorkbookPlanBuilder().build_default_plan(
        title="Sales Report",
        workbook_id="wb_test",
        profile=profile,
        brief={"goal": "Create a clean Excel report"},
    )
    output = WorkbookBuilder().build(
        plan=plan,
        profile=profile,
        preview=imported.preview,
        output_path=tmp_path / "report.xlsx",
    )
    audit = WorkbookAuditor().audit(output, tmp_path / "audit.json")

    assert imported.sheets[0]["name"] == "CSV_Data"
    assert imported.sheets[0]["data_range"] == "A1:B3"
    assert profile["sheets"][0]["candidate_metrics"] == ["revenue"]
    assert Path(output).is_file()
    assert audit["ok"] is True

    import openpyxl

    wb = openpyxl.load_workbook(output, data_only=False)
    summary = wb["Summary"]
    assert summary["C4"].value == "=SUM(Clean_Data!B2:B3)"

