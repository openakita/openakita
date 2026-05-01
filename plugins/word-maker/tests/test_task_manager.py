from __future__ import annotations

import time

import pytest
from word_models import ProjectSpec
from word_task_manager import WordTaskManager


@pytest.mark.asyncio
async def test_project_crud_and_dirs(tmp_path) -> None:
    manager = WordTaskManager(tmp_path / "word-maker.db", tmp_path / "projects")
    async with manager:
        project = await manager.create_project(
            ProjectSpec(
                title="项目验收报告",
                doc_type="acceptance_report",
                audience="甲方项目经理",
                requirements="基于验收材料生成正式报告",
            )
        )

        assert project["id"].startswith("doc_")
        assert project["status"] == "draft"
        assert manager.project_dir(project["id"]).exists()

        listed = await manager.list_projects()
        assert [item["id"] for item in listed] == [project["id"]]

        updated = await manager.update_project_safe(project["id"], status="outline_ready")
        assert updated is not None
        assert updated["status"] == "outline_ready"

        assert await manager.delete_project(project["id"]) is True
        assert await manager.get_project(project["id"]) is None
        assert not manager.project_dir(project["id"]).exists()


@pytest.mark.asyncio
async def test_update_project_safe_rejects_unknown_fields(tmp_path) -> None:
    manager = WordTaskManager(tmp_path / "word-maker.db", tmp_path / "projects")
    async with manager:
        project = await manager.create_project({"title": "周报", "doc_type": "weekly_report"})

        with pytest.raises(ValueError, match="Unsupported project update fields"):
            await manager.update_project_safe(project["id"], id="other")

        with pytest.raises(ValueError, match="Unsupported project status"):
            await manager.update_project_safe(project["id"], status="bad")


@pytest.mark.asyncio
async def test_sources_templates_and_versions(tmp_path) -> None:
    manager = WordTaskManager(tmp_path / "word-maker.db", tmp_path / "projects")
    async with manager:
        project = await manager.create_project({"title": "调研报告", "doc_type": "research_report"})
        source = await manager.add_source(
            project["id"],
            source_type="markdown",
            filename="notes.md",
            path=str(tmp_path / "notes.md"),
            text_preview="preview",
            parse_status="parsed",
        )
        template = await manager.add_template(
            project["id"],
            label="默认模板",
            path=str(tmp_path / "template.docx"),
            variables=["company", "items"],
            validation={"missing": []},
        )
        version = await manager.add_draft_version(
            project["id"],
            outline={"title": "调研报告"},
            fields={"company": "OpenAkita"},
            doc_markdown="# 调研报告",
            audit={"ok": True},
        )

        assert source["parse_status"] == "parsed"
        assert template["vars"] == ["company", "items"]
        assert version["version"] == 1
        assert version["outline"]["title"] == "调研报告"

        versions = await manager.list_versions(project["id"])
        assert len(versions) == 1
        assert (await manager.get_project(project["id"]))["current_version"] == 1


@pytest.mark.asyncio
async def test_cleanup_expired_removes_completed_projects(tmp_path) -> None:
    manager = WordTaskManager(tmp_path / "word-maker.db", tmp_path / "projects")
    async with manager:
        old = await manager.create_project({"title": "旧报告", "doc_type": "research_report"})
        fresh = await manager.create_project({"title": "新报告", "doc_type": "research_report"})
        await manager.update_project_safe(
            old["id"],
            status="succeeded",
            completed_at=time.time() - 40 * 86400,
        )
        await manager.update_project_safe(fresh["id"], status="succeeded", completed_at=time.time())

        assert await manager.cleanup_expired(retention_days=30) == 1
        assert await manager.get_project(old["id"]) is None
        assert await manager.get_project(fresh["id"]) is not None

