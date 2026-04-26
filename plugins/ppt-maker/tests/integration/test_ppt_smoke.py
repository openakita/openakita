from __future__ import annotations

import os

import pytest
from ppt_models import DeckMode, ProjectCreate
from ppt_pipeline import PptPipeline
from ppt_task_manager import PptTaskManager


@pytest.mark.skipif(
    os.environ.get("PPT_MAKER_RUN_INTEGRATION") != "1",
    reason="Set PPT_MAKER_RUN_INTEGRATION=1 to run the real PPT smoke test.",
)
@pytest.mark.asyncio
async def test_real_ppt_smoke(tmp_path) -> None:
    async with PptTaskManager(tmp_path / "ppt_maker.db") as manager:
        project = await manager.create_project(
            ProjectCreate(
                mode=DeckMode.TOPIC_TO_DECK,
                title="OpenAkita 插件生态路线图",
                prompt="8 pages, tech business style, executive audience",
                slide_count=8,
            )
        )

    result = await PptPipeline(data_root=tmp_path).run(project.id)

    assert result["audit_ok"] is True
    assert result["export_path"].endswith(".pptx")

