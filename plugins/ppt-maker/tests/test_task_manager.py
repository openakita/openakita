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


@pytest.mark.asyncio
async def test_dataset_update_safe_records_analysis_paths(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        dataset = await manager.create_dataset(name="Sales", original_path="raw.csv")
        updated = await manager.update_dataset_safe(
            dataset.id,
            status="profiled",
            profile_path="profile.json",
            insights_path="insights.json",
            chart_specs_path="chart_specs.json",
            metadata={"rows": 10},
        )
        with pytest.raises(ValueError):
            await manager.update_dataset_safe(dataset.id, id="bad")

    assert updated is not None
    assert updated.status == "profiled"
    assert updated.profile_path == "profile.json"
    assert updated.metadata == {"rows": 10}


@pytest.mark.asyncio
async def test_template_update_and_delete(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        template = await manager.create_template(name="Brand", original_path="brand.pptx")
        updated = await manager.update_template_safe(
            template.id,
            status="diagnosed",
            profile_path="template_profile.json",
            brand_tokens_path="brand_tokens.json",
            layout_map_path="layout_map.json",
            metadata={"layouts": 2},
        )
        templates = await manager.list_templates()
        with pytest.raises(ValueError):
            await manager.update_template_safe(template.id, id="bad")
        deleted = await manager.delete_template(template.id)

    assert updated is not None
    assert updated.status == "diagnosed"
    assert updated.metadata == {"layouts": 2}
    assert [item.id for item in templates] == [template.id]
    assert deleted is True


@pytest.mark.asyncio
async def test_outline_and_design_versions(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap")
        )
        first = await manager.create_outline(project_id=project.id, outline={"slides": []})
        second = await manager.create_outline(
            project_id=project.id,
            outline={"slides": [{"title": "Intro"}]},
            confirmed=True,
        )
        latest_outline = await manager.latest_outline(project.id)
        design = await manager.create_design_spec(
            project_id=project.id,
            design_markdown="# Spec",
            spec_lock={"theme": "default"},
            confirmed=True,
        )
        latest_design = await manager.latest_design_spec(project.id)

    assert first["version"] == 1
    assert second["version"] == 2
    assert latest_outline is not None
    assert latest_outline["confirmed"] is True
    assert design["version"] == 1
    assert latest_design is not None
    assert latest_design["spec_lock"] == {"theme": "default"}


@pytest.mark.asyncio
async def test_replace_list_and_update_slides(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap")
        )
        records = await manager.replace_slides(
            project.id,
            [
                {"id": "slide_01", "slide_type": "cover", "title": "Cover"},
                {"id": "slide_02", "slide_type": "content", "title": "Body"},
            ],
        )
        listed = await manager.list_slides(project.id)
        updated = await manager.update_slide_safe(
            project.id,
            "slide_02",
            {"id": "slide_02", "slide_type": "summary", "title": "Summary"},
        )

    assert [record["id"] for record in records] == ["slide_01", "slide_02"]
    assert [record["id"] for record in listed] == ["slide_01", "slide_02"]
    assert updated is not None
    assert updated["slide_type"] == "summary"


@pytest.mark.asyncio
async def test_create_and_get_export(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap")
        )
        export = await manager.create_export(
            project_id=project.id,
            path="exports/roadmap.pptx",
            metadata={"audit_ok": True},
        )
        fetched = await manager.get_export(export["id"])
        exports = await manager.list_exports(project.id)

    assert fetched is not None
    assert fetched["metadata"] == {"audit_ok": True}
    assert [item["id"] for item in exports] == [export["id"]]

