from __future__ import annotations

import sqlite3

import pytest

from ppt_models import DeckMode, ProjectCreate, ProjectStatus, TaskCreate, TaskStatus
from ppt_task_manager import PptTaskManager


@pytest.mark.asyncio
async def test_project_crud_and_json_fields(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        created = await manager.create_project(
            ProjectCreate(
                mode=DeckMode.TOPIC_TO_DECK,
                title="OpenAkita roadmap",
                prompt="Make an executive deck",
                metadata={"source": "unit"},
            )
        )
        updated = await manager.update_project_safe(
            created.id,
            status=ProjectStatus.OUTLINE_READY,
            metadata={"outline": "ready"},
        )
        projects = await manager.list_projects()

    assert updated is not None
    assert created.id == updated.id
    assert updated.status == ProjectStatus.OUTLINE_READY
    assert updated.metadata == {"outline": "ready"}
    assert [item.id for item in projects] == [created.id]


@pytest.mark.asyncio
async def test_safe_update_rejects_non_writable_columns(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.OUTLINE_TO_DECK, title="Existing outline")
        )
        with pytest.raises(ValueError):
            await manager.update_project_safe(project.id, id="malicious")


@pytest.mark.asyncio
async def test_task_crud_status_and_completion(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TABLE_TO_DECK, title="KPI report")
        )
        task = await manager.create_task(
            TaskCreate(project_id=project.id, task_type="profile_table", params={"dataset": "x"})
        )
        updated = await manager.update_task_safe(
            task.id,
            status=TaskStatus.SUCCEEDED,
            progress=1,
            result={"profile_path": "profile.json"},
        )

    assert updated is not None
    assert updated.status == TaskStatus.SUCCEEDED
    assert updated.completed_at is not None
    assert updated.result == {"profile_path": "profile.json"}


@pytest.mark.asyncio
async def test_sources_datasets_templates_and_wal(tmp_path) -> None:
    db_path = tmp_path / "ppt_maker.db"
    async with PptTaskManager(db_path) as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TEMPLATE_DECK, title="Proposal")
        )
        source = await manager.create_source(
            project_id=project.id,
            kind="markdown",
            filename="brief.md",
            path="uploads/brief.md",
            metadata={"chars": 120},
        )
        dataset = await manager.create_dataset(
            project_id=project.id,
            name="Sales",
            original_path="datasets/raw.csv",
        )
        template = await manager.create_template(
            name="Brand",
            category="business",
            original_path="templates/original.pptx",
        )

    with sqlite3.connect(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert source.metadata == {"chars": 120}
    assert dataset.status == "created"
    assert template.category is not None
    assert journal_mode.lower() == "wal"

