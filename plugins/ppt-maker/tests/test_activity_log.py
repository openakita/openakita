"""Tests for the per-project activity log + pipeline integration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ppt_activity_log import PptActivityLogger
from ppt_models import DeckMode, ProjectCreate, ProjectStatus
from ppt_pipeline import PptPipeline
from ppt_task_manager import PptTaskManager


class _StubAPI:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.router = None

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router) -> None:
        self.router = router

    def register_tools(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log(self, *args: Any, **kwargs: Any) -> None:
        return None


# ── Logger primitives ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_logger_appends_and_reads(tmp_path) -> None:
    logger = PptActivityLogger(data_root=tmp_path)
    project_id = "ppt_logtest"

    first = await logger.append(
        project_id=project_id,
        stage="outline",
        status="start",
        message="开始大纲",
    )
    second = await logger.append(
        project_id=project_id,
        stage="outline",
        status="success",
        message="大纲完成",
        details={"slides": 7},
    )

    events = logger.read(project_id)
    assert len(events) == 2
    assert events[0]["message"] == "开始大纲"
    assert events[1]["details"] == {"slides": 7}
    assert events[0]["ts"] <= events[1]["ts"]
    assert first["iso"] and second["iso"]

    # ``since`` filters strict-greater-than, so passing the first ts only
    # returns the second event.
    only_second = logger.read(project_id, since=events[0]["ts"])
    assert len(only_second) == 1
    assert only_second[0]["status"] == "success"


@pytest.mark.asyncio
async def test_activity_logger_handles_missing_file(tmp_path) -> None:
    logger = PptActivityLogger(data_root=tmp_path)
    assert logger.read("never_written") == []
    assert logger.latest_ts("never_written") is None


@pytest.mark.asyncio
async def test_activity_logger_caps_to_limit(tmp_path) -> None:
    logger = PptActivityLogger(data_root=tmp_path)
    for index in range(8):
        await logger.append(
            project_id="ppt_cap",
            stage="bench",
            status="info",
            message=f"event {index}",
        )
    capped = logger.read("ppt_cap", limit=3)
    assert len(capped) == 3
    assert capped[-1]["message"] == "event 7"


@pytest.mark.asyncio
async def test_activity_logger_concurrent_safety(tmp_path) -> None:
    logger = PptActivityLogger(data_root=tmp_path)

    async def writer(idx: int) -> None:
        await logger.append(
            project_id="ppt_par",
            stage="bench",
            status="info",
            message=f"row {idx}",
        )

    await asyncio.gather(*[writer(i) for i in range(20)])
    events = logger.read("ppt_par")
    assert len(events) == 20
    # File contents should be valid JSONL even under parallel writes.
    raw_path = tmp_path / "projects" / "ppt_par" / "logs" / "activity.jsonl"
    parsed = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(parsed) == 20


# ── Pipeline integration ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_emits_activity_events(tmp_path) -> None:
    """Running the deterministic fallback pipeline should leave a JSONL trail
    and broadcast ``ppt_activity`` events."""
    broadcast_events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        broadcast_events.append((name, payload))

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="活动日志测试", slide_count=3)
        )

    activity = PptActivityLogger(data_root=tmp_path)
    pipeline = PptPipeline(
        data_root=tmp_path,
        emit=emit,
        activity_logger=activity,
    )
    result = await pipeline.run(project.id)

    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        updated = await manager.get_project(project.id)

    assert updated is not None
    assert updated.status == ProjectStatus.OUTLINE_READY
    assert result["needs_confirmation"] == "outline"

    events = activity.read(project.id)
    stages = {ev["stage"] for ev in events}
    # Pipeline-level milestones we always expect on the outline gate path.
    for required in ("run", "setup", "ingest", "outline"):
        assert required in stages, f"missing activity stage {required}"

    # ``run`` event should mark the start with start status.
    run_events = [ev for ev in events if ev["stage"] == "run"]
    assert any(ev["status"] == "start" for ev in run_events)

    # Each activity append should also be broadcast as ``ppt_activity``.
    activity_payloads = [payload for name, payload in broadcast_events if name == "ppt_activity"]
    assert len(activity_payloads) >= len(events)
    assert all(payload["project_id"] == project.id for payload in activity_payloads)


@pytest.mark.asyncio
async def test_pipeline_records_failure(tmp_path) -> None:
    """When a step blows up, the pipeline should record an ``error`` event."""
    activity = PptActivityLogger(data_root=tmp_path)
    pipeline = PptPipeline(data_root=tmp_path, activity_logger=activity)

    # Project ID does not exist → ``_run_steps`` raises ValueError after
    # entering the run guard, but importantly *before* recording any "start"
    # event because the project lookup happens first. We instead force a
    # failure by patching ``_table_inputs``.
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(mode=DeckMode.TOPIC_TO_DECK, title="失败用例", slide_count=2)
        )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    pipeline._table_inputs = boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        await pipeline.run(project.id)

    events = activity.read(project.id)
    error_events = [ev for ev in events if ev["status"] == "error"]
    assert error_events, "expected an error activity event"
    assert error_events[-1]["details"]["error_class"] == "RuntimeError"


# ── HTTP route ────────────────────────────────────────────────────────


def test_activity_route_returns_logged_events(tmp_path: Path) -> None:
    """The new ``GET /projects/{id}/activity`` endpoint serves the JSONL feed."""
    import plugin

    instance = plugin.Plugin()
    stub_api = _StubAPI(tmp_path)
    instance.on_load(stub_api)
    assert stub_api.router is not None
    assert instance._activity_logger is not None

    project_id = "ppt_route_demo"

    # Pre-populate the JSONL log so the route has something to return.
    asyncio.run(
        instance._activity_logger.append(
            project_id=project_id,
            stage="run",
            status="start",
            message="开始",
        )
    )
    asyncio.run(
        instance._activity_logger.append(
            project_id=project_id,
            stage="brain.outline",
            status="success",
            message="Brain 大纲完成",
            details={"slides": 5},
        )
    )

    app = FastAPI()
    app.include_router(stub_api.router)
    client = TestClient(app)

    response = client.get(f"/projects/{project_id}/activity")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["events"][0]["stage"] == "run"
    assert body["events"][1]["details"] == {"slides": 5}

    # ``since`` filter should drop earlier events.
    cutoff = body["events"][0]["ts"]
    response = client.get(
        f"/projects/{project_id}/activity", params={"since": cutoff}
    )
    assert response.status_code == 200
    filtered = response.json()
    assert filtered["count"] == 1
    assert filtered["events"][0]["stage"] == "brain.outline"

    # Unknown project should still 200 with empty list (matches UI expectation).
    response = client.get("/projects/never/activity")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "project_id": "never",
        "events": [],
        "count": 0,
        "latest_ts": None,
    }

