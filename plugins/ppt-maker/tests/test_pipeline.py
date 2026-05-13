from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from ppt_brain_adapter import PptBrainAdapter
from ppt_design import DesignBuilder
from ppt_models import DeckMode, ProjectCreate, ProjectStatus, TaskCreate
from ppt_outline import OutlineBuilder
from ppt_pipeline import PIPELINE_STEPS, PptPipeline
from ppt_task_manager import PptTaskManager


async def collect_event(events, event_name, payload):
    events.append((event_name, payload))


@pytest.mark.asyncio
async def test_pipeline_stops_at_outline_gate(tmp_path) -> None:
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

    assert len(PIPELINE_STEPS) == 10
    assert updated is not None
    assert updated.status == ProjectStatus.OUTLINE_READY
    assert result["needs_confirmation"] == "outline"
    assert any(event[1]["status"] == "succeeded" for event in events)


@pytest.mark.asyncio
async def test_pipeline_generates_export_after_confirmed_gates(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
        )
        outline = OutlineBuilder().confirm(
            OutlineBuilder().build(mode=project.mode, title=project.title, slide_count=project.slide_count)
        )
        await manager.create_outline(project_id=project.id, outline=outline, confirmed=True)
        design = DesignBuilder().confirm(DesignBuilder().build(outline=outline))
        await manager.create_design_spec(
            project_id=project.id,
            design_markdown=design["design_spec_markdown"],
            spec_lock=design["spec_lock"],
            confirmed=True,
        )

    result = await PptPipeline(data_root=tmp_path).run(project.id)

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        updated = await manager.get_project(project.id)
        exports = await manager.list_exports(project.id)
        slides = await manager.list_slides(project.id)

    assert result["export_id"] == exports[0]["id"]
    assert updated is not None
    assert updated.status == ProjectStatus.READY
    assert len(slides) == 3
    assert (tmp_path / "projects" / project.id / "brief.json").exists()
    assert (tmp_path / "projects" / project.id / "render_model.json").exists()
    assert (tmp_path / "projects" / project.id / "repair_plan.json").exists()


@dataclass
class _FakeResp:
    content: str


class _ScriptedBrain:
    """Minimal Brain that returns a different scripted JSON for each call."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    async def think(self, prompt, *, system, max_tokens):
        self.calls.append({"prompt": prompt, "system": system})
        if not self._payloads:
            raise RuntimeError("no scripted response left")
        return _FakeResp(json.dumps(self._payloads.pop(0), ensure_ascii=False))


class _FakeApi:
    def __init__(self, brain) -> None:
        self.brain = brain

    def has_permission(self, name: str) -> bool:
        return name == "brain.access"

    def get_brain(self):
        return self.brain


@pytest.mark.asyncio
async def test_pipeline_uses_brain_outline_when_adapter_available(tmp_path) -> None:
    brain = _ScriptedBrain(
        payloads=[
            {  # generate_outline
                "title": "Brain Roadmap",
                "mode": "topic_to_deck",
                "audience": "engineers",
                "storyline": ["Cover", "Body", "Wrap"],
                "slides": [
                    {
                        "index": 1,
                        "title": "Brain Cover",
                        "purpose": "intro",
                        "slide_type": "cover",
                        "key_points": [],
                        "body": "Brain authored cover body.",
                        "speaker_note": "Welcome.",
                        "image_query": None,
                        "icon_query": None,
                    },
                    {
                        "index": 2,
                        "title": "Brain Body",
                        "purpose": "core",
                        "slide_type": "content",
                        "key_points": ["one", "two", "three"],
                        "body": "Brain body sentence.",
                        "speaker_note": "Talk track.",
                        "image_query": None,
                        "icon_query": "rocket",
                    },
                    {
                        "index": 3,
                        "title": "Brain Wrap",
                        "purpose": "outro",
                        "slide_type": "summary",
                        "key_points": ["next"],
                        "body": "Brain summary.",
                        "speaker_note": "End.",
                        "image_query": None,
                        "icon_query": None,
                    },
                ],
                "confirmation_questions": ["页数 OK 吗？"],
            },
            {  # select_layout_per_slide
                "slides": [
                    {"index": 1, "slide_type": "cover"},
                    {"index": 2, "slide_type": "content"},
                    {"index": 3, "slide_type": "summary"},
                ]
            },
        ]
    )
    adapter = PptBrainAdapter(_FakeApi(brain), data_root=tmp_path)

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
        )

    pipeline = PptPipeline(
        data_root=tmp_path,
        brain_adapter=adapter,
        settings={
            "verbosity": "balanced",
            "tone": "professional",
            "language": "zh-CN",
            "web_search_enabled": "false",
        },
    )
    result = await pipeline.run(project.id)

    assert result["needs_confirmation"] == "outline"
    outline = result["outline"]
    assert outline["title"] == "Brain Roadmap"
    titles = [s["title"] for s in outline["slides"]]
    assert "Brain Cover" in titles
    # The Brain made at least the outline call (layout selection may also have run).
    assert brain.calls, "Brain.think should have been invoked"


@pytest.mark.asyncio
async def test_pipeline_falls_back_when_brain_outline_raises(tmp_path) -> None:
    class ExplodingBrain:
        async def think(self, prompt, *, system, max_tokens):
            raise RuntimeError("brain offline")

    adapter = PptBrainAdapter(_FakeApi(ExplodingBrain()), data_root=tmp_path)

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="Fallback", slide_count=3)
        )

    pipeline = PptPipeline(data_root=tmp_path, brain_adapter=adapter)
    result = await pipeline.run(project.id)

    assert result["needs_confirmation"] == "outline"
    # Fallback produced by OutlineBuilder uses the project's title as cover slide title.
    titles = [s["title"] for s in result["outline"]["slides"]]
    assert titles[0] == "Fallback"


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

