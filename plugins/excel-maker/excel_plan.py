"""Workbook plan generation and validation."""

from __future__ import annotations

from typing import Any

from excel_models import WorkbookPlan, WorkbookPlanSheet

DEFAULT_SHEETS = [
    WorkbookPlanSheet(name="README", role="readme", description="Report introduction and assumptions"),
    WorkbookPlanSheet(name="Raw_Data", role="raw_data", description="Original source data copy"),
    WorkbookPlanSheet(name="Clean_Data", role="clean_data", description="Cleaned tabular data"),
    WorkbookPlanSheet(name="Summary", role="summary", description="Core metric summary"),
    WorkbookPlanSheet(name="Charts", role="charts", description="Chart source data and charts"),
    WorkbookPlanSheet(name="Formula_Check", role="formula_check", description="Formula explanations"),
    WorkbookPlanSheet(name="Audit_Log", role="audit_log", description="Quality and version audit"),
]


class WorkbookPlanBuilder:
    def build_default_plan(
        self,
        *,
        title: str,
        workbook_id: str | None,
        profile: dict[str, Any] | None = None,
        brief: dict[str, Any] | None = None,
    ) -> WorkbookPlan:
        source_sheet = None
        metrics: list[str] = []
        dimensions: list[str] = []
        if profile and profile.get("sheets"):
            first = profile["sheets"][0]
            source_sheet = first.get("name")
            metrics = list(first.get("candidate_metrics") or [])
            dimensions = list(first.get("candidate_dimensions") or [])
        sheets = [sheet.model_copy(update={"source_sheet": source_sheet}) for sheet in DEFAULT_SHEETS]
        purpose = ""
        if brief:
            purpose = str(brief.get("goal") or brief.get("purpose") or "")
        return WorkbookPlan(
            title=title or "Excel Report",
            purpose=purpose,
            source_workbook_id=workbook_id,
            sheets=sheets,
            operations=[],
            formulas=[],
            style={"theme": "business", "brand_color": "#2563eb"},
            audit_expectations=[
                "Workbook contains required sheets",
                "Formula cells are explained",
                "Generated artifacts do not overwrite source files",
                f"Candidate metrics: {', '.join(metrics[:8])}" if metrics else "Candidate metrics reviewed",
                f"Candidate dimensions: {', '.join(dimensions[:8])}"
                if dimensions
                else "Candidate dimensions reviewed",
            ],
        )

    def validate_plan(self, value: dict[str, Any]) -> WorkbookPlan:
        return WorkbookPlan.model_validate(value)

