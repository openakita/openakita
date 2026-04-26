from __future__ import annotations

import pytest
from excel_executor import ExcelOperationExecutor, OperationExecutionError
from excel_formula import generate_formula
from excel_maker_inline.python_deps import PythonDepsManager, list_optional_groups
from excel_models import WorkbookPlan, WorkbookPlanOperation


def test_optional_groups_are_whitelisted() -> None:
    groups = list_optional_groups()

    assert set(groups) == {"table_core", "legacy_excel", "charting", "template_tools"}
    assert "openpyxl" in groups["table_core"]


def test_unknown_dependency_group_rejected(tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    with pytest.raises(ValueError):
        manager.status("requests")


def test_executor_rejects_code_params() -> None:
    plan = WorkbookPlan(
        title="Unsafe",
        operations=[
            WorkbookPlanOperation(
                op="derive_column",
                params={"code": "import os; os.remove('x')"},
            )
        ],
    )

    with pytest.raises(OperationExecutionError):
        ExcelOperationExecutor().apply_plan(plan)


def test_formula_generation_starts_with_equals() -> None:
    suggestion = generate_formula("sumifs", range_ref="B:B", criteria_ref="A:A", criteria="East")

    assert suggestion.formula.startswith("=")
    assert "SUMIFS" in suggestion.formula

