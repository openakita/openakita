"""Integration smoke test for clip-sense ASR pipeline.

Requires:
- DASHSCOPE_API_KEY env var set
- ffmpeg installed
- A test video file

Run: pytest tests/integration/test_asr_smoke.py -m integration
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def api_key():
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        pytest.skip("DASHSCOPE_API_KEY not set")
    return key


def test_ffmpeg_available():
    """Smoke test: ffmpeg is detected."""
    from clip_ffmpeg_ops import FFmpegOps

    ops = FFmpegOps()
    info = ops.detect()
    if not info["available"]:
        pytest.skip("ffmpeg not available")
    assert info["version"]


def test_models_sanity():
    """Smoke test: all 4 modes load correctly."""
    from clip_models import MODES, MODES_BY_ID, estimate_cost

    assert len(MODES) == 4
    for m in MODES:
        assert m.id in MODES_BY_ID
        cost = estimate_cost(m.id, 60.0)
        assert cost.total_cny >= 0


def test_task_manager_lifecycle(tmp_path: Path):
    """Smoke test: full task manager lifecycle."""
    from clip_task_manager import TaskManager

    tm = TaskManager(tmp_path / "test.db")

    async def _run():
        await tm.init()
        task = await tm.create_task(mode="silence_clean", source_video_path="/tmp/v.mp4")
        assert task["status"] == "pending"

        await tm.update_task(task["id"], status="running", pipeline_step="execute")
        updated = await tm.get_task(task["id"])
        assert updated["status"] == "running"

        tr = await tm.create_transcript(source_hash="smoke_test_hash")
        assert tr["status"] == "pending"

        result = await tm.list_tasks()
        assert result["total"] == 1

        await tm.close()

    asyncio.get_event_loop().run_until_complete(_run())


def test_pipeline_context():
    """Smoke test: pipeline context creation."""
    from clip_pipeline import ClipPipelineContext

    ctx = ClipPipelineContext(
        task_id="smoke",
        mode="silence_clean",
        params={"threshold_db": -40},
        task_dir=Path("/tmp/clip_sense_test"),
        source_video_path=Path("/tmp/test.mp4"),
    )
    assert ctx.mode == "silence_clean"
    assert ctx.params["threshold_db"] == -40
