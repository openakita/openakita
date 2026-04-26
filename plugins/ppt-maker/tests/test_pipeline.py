from __future__ import annotations

import pytest
from ppt_models import DeckMode, ProjectCreate, ProjectStatus, TaskCreate
from ppt_pipeline import PIPELINE_STEPS, PptPipeline
from ppt_task_manager import PptTaskManager


async def collect_event(events, event_name, payload):
    events.append((event_name, payload))


@pytest.mark.asyncio
async def test_pipeline_generates_export_audit_and_events(tmp_path) -> None:
    events = []
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
        )

    result = await PptPipeline(
        data_root=tmp_path,
        emit=lambda name, payload: collect_event(events, name, payload),
    ).run(project.id)

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        updated = await manager.get_project(project.id)
        exports = await manager.list_exports(project.id)
        slides = await manager.list_slides(project.id)

    assert len(PIPELINE_STEPS) == 10
    assert result["export_id"] == exports[0]["id"]
    assert updated is not None
    assert updated.status == ProjectStatus.READY
    assert len(slides) == 3
    assert any(event[1]["status"] == "succeeded" for event in events)


@pytest.mark.asyncio
async def test_cancel_and_delete_project_helpers(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap")
        )
        task = await manager.create_task(
            TaskCreate(project_id=project.id, task_type="generate_deck")
        )
        cancelled = await manager.cancel_project_tasks(project.id)
        deleted = await manager.delete_project(project.id)
        fetched = await manager.get_project(project.id)

    assert task.id
    assert cancelled == 1
    assert deleted is True
    assert fetched is None

