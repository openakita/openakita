"""ppt-maker plugin entry point.

Phase 0 only wires a minimal router and tool registry. Later phases add the
project store, pipeline, table analyzer, template manager, exporter, and UI
routes while preserving this self-contained plugin shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from openakita.plugins.api import PluginAPI, PluginBase

from ppt_maker_inline.file_utils import resolve_plugin_data_root


PLUGIN_ID = "ppt-maker"


class Plugin(PluginBase):
    """OpenAkita plugin entry for guided PPT generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = resolve_plugin_data_root(api.get_data_dir() or Path.cwd() / "data")
        self._data_dir = data_dir

        router = APIRouter()

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 1,
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "ppt_maker.db"),
            }

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "ppt_list_projects":
            return "ppt-maker project storage is available. Routes are wired in Phase 9."
        return f"{tool_name} is registered; implementation is added in later phases."

    async def on_unload(self) -> None:
        if self._api:
            self._api.log(f"{PLUGIN_ID}: unloaded")


def _tool_definitions() -> list[dict[str, Any]]:
    names = [
        ("ppt_start_project", "Start a guided PPT project."),
        ("ppt_ingest_sources", "Attach source documents to a PPT project."),
        ("ppt_ingest_table", "Attach CSV/XLSX/table data to a PPT project."),
        ("ppt_profile_table", "Profile an ingested table dataset."),
        ("ppt_generate_table_insights", "Generate table insights for a PPT project."),
        ("ppt_upload_template", "Upload a PPTX enterprise template."),
        ("ppt_diagnose_template", "Diagnose a PPTX template for brand/layout tokens."),
        ("ppt_generate_outline", "Generate a presentation outline."),
        ("ppt_confirm_outline", "Confirm or update a generated outline."),
        ("ppt_generate_design", "Generate design_spec and spec_lock."),
        ("ppt_confirm_design", "Confirm or update design settings."),
        ("ppt_generate_deck", "Generate slide IR and export a PPT deck."),
        ("ppt_revise_slide", "Revise one slide or part of a PPT project."),
        ("ppt_audit", "Audit a generated PPT project."),
        ("ppt_export", "Export a PPT project."),
        ("ppt_list_projects", "List PPT projects."),
        ("ppt_cancel", "Cancel a running PPT task."),
    ]
    return [
        {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        }
        for name, desc in names
    ]

