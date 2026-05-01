from __future__ import annotations

import json
from pathlib import Path

import plugin
import pytest


class FakeAPI:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.routes = []
        self.tools = []
        self.config = {}
        self.logs = []
        self.spawned = []

    def get_data_dir(self) -> Path:
        return self.data_dir

    def register_api_routes(self, router) -> None:
        self.routes.append(router)

    def register_tools(self, definitions, handler) -> None:
        self.tools.extend(definitions)
        self.handler = handler

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append((level, message))

    def get_config(self) -> dict:
        return self.config

    def set_config(self, updates: dict) -> None:
        self.config.update(updates)

    def has_permission(self, name: str) -> bool:
        return name != "brain.access"

    def get_brain(self):
        return None

    def spawn_task(self, coro, *, name=None):
        import asyncio

        task = asyncio.create_task(coro, name=name)
        self.spawned.append(task)
        return task


@pytest.mark.asyncio
async def test_plugin_loads_routes_and_tools(tmp_path: Path) -> None:
    api = FakeAPI(tmp_path)
    instance = plugin.Plugin()

    instance.on_load(api)

    assert api.routes
    assert {tool["name"] for tool in api.tools} >= {"word_start_project", "word_list_projects"}
    assert (tmp_path / "word-maker" / "word-maker.db").parent.exists()

    response = json.loads(await instance._handle_tool("word_start_project", {"title": "周报"}))
    assert response["project_id"].startswith("doc_")

    projects = json.loads(await instance._handle_tool("word_list_projects", {}))
    assert len(projects["projects"]) == 1

    await instance.on_unload()


def test_tool_definitions_include_expected_names() -> None:
    tool_names = {item["name"] for item in plugin._tool_definitions()}

    assert "word_fill_template" in tool_names
    assert "word_cancel" in tool_names


@pytest.mark.asyncio
async def test_declared_tools_have_handlers(tmp_path: Path) -> None:
    api = FakeAPI(tmp_path)
    instance = plugin.Plugin()
    instance.on_load(api)
    project = json.loads(await instance._handle_tool("word_start_project", {"title": "工具覆盖"}))
    project_id = project["project_id"]

    source = tmp_path / "word-maker" / "uploads" / "notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# notes", encoding="utf-8")

    handled = {
        "word_list_projects": {},
        "word_ingest_sources": {"project_id": project_id, "paths": ["uploads/notes.md"]},
        "word_generate_outline": {"requirement": "生成报告", "doc_type": "research_report"},
        "word_confirm_outline": {"project_id": project_id, "outline": {"title": "报告", "sections": []}},
        "word_audit": {},
        "word_export": {"project_id": project_id},
        "word_cancel": {"project_id": project_id},
    }

    for tool_name, args in handled.items():
        response = json.loads(await instance._handle_tool(tool_name, args))
        assert "not yet implemented" not in json.dumps(response, ensure_ascii=False)

    await instance.on_unload()


def test_custom_data_dir_is_used_on_load(tmp_path: Path) -> None:
    custom = tmp_path / "custom-word-data"
    api = FakeAPI(tmp_path)
    api.config = {plugin.SETTINGS_KEY: {"custom_data_dir": str(custom)}}
    instance = plugin.Plugin()

    instance.on_load(api)

    assert instance._require_workspace() == custom

