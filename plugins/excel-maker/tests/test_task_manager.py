from __future__ import annotations

import pytest
from excel_maker_inline.file_utils import ensure_child
from excel_models import ArtifactKind, ProjectCreate, ProjectStatus
from excel_task_manager import ExcelTaskManager


@pytest.mark.asyncio
async def test_project_workbook_artifact_crud(tmp_path) -> None:
    async with ExcelTaskManager(tmp_path / "excel_maker.db") as manager:
        project = await manager.create_project(ProjectCreate(title="Sales Report", goal="Make XLSX"))
        workbook = await manager.create_workbook(
            project_id=project.id,
            filename="sales.csv",
            original_path=str(tmp_path / "sales.csv"),
        )
        sheets = await manager.replace_sheets(
            workbook.id,
            [{"name": "CSV_Data", "row_count": 3, "column_count": 2, "header_row": 1}],
        )
        artifact = await manager.create_artifact(
            project_id=project.id,
            kind=ArtifactKind.WORKBOOK,
            path=str(tmp_path / "report.xlsx"),
        )
        updated = await manager.update_project_safe(project.id, status=ProjectStatus.GENERATED)

        assert project.id.startswith("proj_")
        assert workbook.id.startswith("wb_")
        assert sheets[0].name == "CSV_Data"
        assert artifact.version == 1
        assert updated is not None
        assert updated.status == ProjectStatus.GENERATED


@pytest.mark.asyncio
async def test_records_operations(tmp_path) -> None:
    async with ExcelTaskManager(tmp_path / "excel_maker.db") as manager:
        project = await manager.create_project(ProjectCreate(title="Sales Report"))
        count = await manager.record_operations(
            project.id,
            [{"op": "rename_column", "params": {"from": "old", "to": "new"}, "status": "accepted"}],
        )
        operations = await manager.list_operations(project.id)

        assert count == 1
        assert operations[0]["op"] == "rename_column"
        assert operations[0]["params"] == {"from": "old", "to": "new"}


def test_ensure_child_resolves_relative_paths_inside_root(tmp_path) -> None:
    target = ensure_child(tmp_path, "uploads")

    assert target == tmp_path.resolve() / "uploads"


@pytest.mark.asyncio
async def test_rejects_unknown_project_update(tmp_path) -> None:
    async with ExcelTaskManager(tmp_path / "excel_maker.db") as manager:
        project = await manager.create_project(ProjectCreate(title="Sales Report"))

        with pytest.raises(ValueError):
            await manager.update_project_safe(project.id, arbitrary="nope")

