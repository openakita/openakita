"""word-maker plugin entry point."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from word_brain_helper import WordBrainHelper
from word_maker_inline.file_utils import safe_name, unique_child
from word_maker_inline.python_deps import check_optional_deps
from word_maker_inline.storage_stats import collect_storage_stats
from word_maker_inline.upload_preview import add_upload_preview_route
from word_models import build_catalog
from word_pipeline import WordPipelineContext, audit_output, build_ppt_asset_metadata, run_pipeline
from word_source_loader import load_source
from word_task_manager import WordTaskManager
from word_template_engine import extract_template_vars, render_template

from openakita.plugins.api import PluginAPI, PluginBase

PLUGIN_ID = "word-maker"
SETTINGS_KEY = "word_maker_settings"


def _read_settings(api: PluginAPI | None) -> dict[str, Any]:
    config = api.get_config() if api else {}
    settings = config.get(SETTINGS_KEY, {}) if isinstance(config, dict) else {}
    return {
        "custom_data_dir": str(settings.get("custom_data_dir") or "").strip(),
        "default_language": settings.get("default_language", "zh-CN"),
        "default_tone": settings.get("default_tone", "professional"),
        "retention_days": int(settings.get("retention_days", 30)),
    }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreateRequest(StrictModel):
    title: str = Field(default="未命名文档")
    doc_type: str = Field(default="research_report")
    audience: str = ""
    tone: str = "professional"
    language: str = "zh-CN"
    requirements: str = ""


class RenderRequest(StrictModel):
    template_path: str | None = None
    source_paths: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)
    outline: dict[str, Any] = Field(default_factory=dict)


class OutlineRequest(StrictModel):
    requirement: str = ""
    doc_type: str = "research_report"
    sources_text: str = ""


class ConfirmOutlineRequest(StrictModel):
    outline: dict[str, Any]


class RewriteSectionRequest(StrictModel):
    section_markdown: str
    instruction: str
    tone: str = "professional"


class SettingsUpdateRequest(StrictModel):
    custom_data_dir: str | None = None
    default_language: str = "zh-CN"
    default_tone: str = "professional"
    retention_days: int = 30


class Plugin(PluginBase):
    """OpenAkita plugin entry for guided Word document generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._workspace_dir: Path | None = None
        self._manager: WordTaskManager | None = None
        self._brain_helper: WordBrainHelper | None = None
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        settings = _read_settings(api)
        data_dir = api.get_data_dir() or Path.cwd() / "data" / PLUGIN_ID
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        self._workspace_dir = (
            Path(settings["custom_data_dir"]).expanduser()
            if settings.get("custom_data_dir")
            else data_dir / PLUGIN_ID
        )
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        self._manager = WordTaskManager(
            self._workspace_dir / "word-maker.db",
            self._workspace_dir / "projects",
        )
        self._brain_helper = WordBrainHelper(api)

        router = APIRouter()
        add_upload_preview_route(router, base_dir=self._workspace_dir)
        self._register_routes(router)

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        manager = self._require_manager()
        if tool_name == "word_list_projects":
            return json.dumps({"projects": await manager.list_projects()}, ensure_ascii=False)
        if tool_name == "word_start_project":
            project = await manager.create_project(arguments or {"title": "未命名文档"})
            return json.dumps(
                {
                    "ok": True,
                    "project_id": project["id"],
                    "status": project["status"],
                    "next_action": "ingest_sources_or_upload_template",
                },
                ensure_ascii=False,
            )
        if tool_name == "word_ingest_sources":
            return json.dumps(await self._tool_ingest_sources(arguments), ensure_ascii=False)
        if tool_name == "word_upload_template":
            return json.dumps(await self._tool_upload_template(arguments), ensure_ascii=False)
        if tool_name == "word_extract_template_vars":
            result = extract_template_vars(
                self._resolve_workspace_path(arguments.get("template_path", "")),
                context=arguments.get("context", {}),
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        if tool_name == "word_generate_outline":
            helper = self._require_brain_helper()
            result = await helper.generate_outline(
                requirement=arguments.get("requirement", ""),
                doc_type=arguments.get("doc_type", "research_report"),
                sources_text=arguments.get("sources_text", ""),
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        if tool_name == "word_confirm_outline":
            project_id = arguments.get("project_id", "")
            version = await manager.add_draft_version(project_id, outline=arguments.get("outline", {}))
            project = await manager.update_project_safe(project_id, status="outline_ready")
            return json.dumps(
                {"ok": True, "project": project, "version": version, "next_action": "fill_template_or_render"},
                ensure_ascii=False,
            )
        if tool_name == "word_fill_template":
            return json.dumps(await self._tool_fill_template(arguments), ensure_ascii=False)
        if tool_name == "word_rewrite_section":
            result = await self._require_brain_helper().rewrite_section(
                section_markdown=arguments.get("section_markdown", ""),
                instruction=arguments.get("instruction", ""),
                tone=arguments.get("tone", "professional"),
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        if tool_name == "word_audit":
            output_path = Path(arguments.get("output_path", "")) if arguments.get("output_path") else None
            return json.dumps(audit_output(output_path), ensure_ascii=False)
        if tool_name == "word_export":
            project = await manager.get_project(arguments.get("project_id", ""))
            asset_id = None
            versions = await manager.list_versions(project["id"]) if project else []
            latest = versions[0] if versions else {}
            if project and arguments.get("publish_for_ppt") and self._api and self._api.has_permission("assets.publish"):
                asset_id = await self._api.publish_asset(
                    asset_kind="word_document_brief",
                    source_path=project.get("output_path"),
                    metadata=build_ppt_asset_metadata(
                        project=project,
                        outline=latest.get("outline"),
                        doc_markdown=latest.get("doc_markdown", ""),
                    ),
                    shared_with=["ppt-maker"],
                    ttl_seconds=7 * 86400,
                )
            return json.dumps(
                {
                    "project_id": arguments.get("project_id"),
                    "status": project.get("status") if project else "not_found",
                    "output_path": project.get("output_path") if project else None,
                    "asset_id": asset_id,
                    "next_action": "download_or_publish_for_ppt" if project else "check_project_id",
                },
                ensure_ascii=False,
            )
        if tool_name == "word_cancel":
            return json.dumps(await self._cancel_project(arguments.get("project_id", "")), ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"Unknown or not yet implemented tool: {tool_name}"}, ensure_ascii=False)

    async def on_unload(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
        if self._manager:
            await self._manager.close()
        if self._api:
            self._api.log(f"{PLUGIN_ID}: unloaded")

    def _require_manager(self) -> WordTaskManager:
        if self._manager is None:
            raise RuntimeError("word-maker manager is not initialized")
        return self._manager

    def _require_brain_helper(self) -> WordBrainHelper:
        if self._brain_helper is None:
            raise RuntimeError("word-maker brain helper is not initialized")
        return self._brain_helper

    def _require_workspace(self) -> Path:
        if self._workspace_dir is None:
            raise RuntimeError("word-maker workspace is not initialized")
        return self._workspace_dir

    def _resolve_workspace_path(self, value: str | Path) -> Path:
        raw = Path(value)
        if raw.is_absolute():
            return raw
        candidate = (self._require_workspace() / raw).resolve()
        try:
            candidate.relative_to(self._require_workspace().resolve())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Path escapes plugin workspace") from exc
        return candidate

    def _file_url(self, path: Path) -> str:
        rel = path.resolve().relative_to(self._require_workspace().resolve())
        return f"/api/plugins/{PLUGIN_ID}/files/{rel.as_posix()}"

    def _settings(self) -> dict[str, Any]:
        settings = _read_settings(self._api)
        return {
            "custom_data_dir": settings.get("custom_data_dir", ""),
            "default_language": settings.get("default_language", "zh-CN"),
            "default_tone": settings.get("default_tone", "professional"),
            "retention_days": int(settings.get("retention_days", 30)),
            "data_dir_active": str(self._require_workspace()),
            "brain_available": bool(self._brain_helper and self._brain_helper.is_available()),
            "deps": check_optional_deps(),
        }

    async def _tool_ingest_sources(self, arguments: dict[str, Any]) -> dict[str, Any]:
        manager = self._require_manager()
        project_id = arguments.get("project_id", "")
        raw_paths = arguments.get("paths") or arguments.get("source_paths") or []
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        sources = []
        for raw_path in raw_paths:
            path = self._resolve_workspace_path(raw_path)
            result = load_source(path)
            source = await manager.add_source(
                project_id,
                source_type=result.source_type,
                filename=path.name,
                path=str(path),
                text_preview=result.text[:1200],
                parse_status="parsed" if result.ok else "failed",
                error_message=result.error or None,
            )
            sources.append({"source": source, "load": result.to_dict()})
        return {
            "ok": all(item["load"]["ok"] for item in sources),
            "project_id": project_id,
            "sources": sources,
            "next_action": "generate_outline_or_upload_template",
        }

    async def _tool_upload_template(self, arguments: dict[str, Any]) -> dict[str, Any]:
        manager = self._require_manager()
        project_id = arguments.get("project_id", "")
        template_path = self._resolve_workspace_path(arguments.get("template_path", ""))
        inspection = extract_template_vars(template_path, context=arguments.get("context", {}))
        template = await manager.add_template(
            project_id,
            label=template_path.name,
            path=str(template_path),
            variables=inspection.variables,
            validation=inspection.to_dict(),
        )
        project = await manager.update_project_safe(project_id, status="template_ready")
        return {
            "ok": inspection.error == "",
            "project": project,
            "template": template,
            "inspection": inspection.to_dict(),
            "next_action": "fill_missing_fields" if inspection.missing else "render_docx",
        }

    async def _tool_fill_template(self, arguments: dict[str, Any]) -> dict[str, Any]:
        manager = self._require_manager()
        project_id = arguments.get("project_id", "")
        template_path = self._resolve_workspace_path(arguments.get("template_path", ""))
        output_arg = arguments.get("output_path")
        output_path = (
            self._resolve_workspace_path(output_arg)
            if output_arg
            else manager.project_dir(project_id) / "exports" / "document.docx"
        )
        fields = arguments.get("fields", {})
        result = render_template(template_path, output_path, fields)
        audit = audit_output(output_path if result.ok else None, missing=result.missing)
        project = None
        if project_id:
            await manager.add_draft_version(
                project_id,
                fields=fields,
                export_path=str(output_path) if result.ok else None,
                audit=audit,
            )
            project = await manager.update_project_safe(
                project_id,
                status="succeeded" if result.ok and audit.get("ok") else "failed",
                output_path=str(output_path) if result.ok else None,
                error_kind=None if result.ok else "template_render_failed",
                error_message="" if result.ok else result.error,
            )
        return {
            **result.to_dict(),
            "project": project,
            "audit": audit,
            "download_url": self._file_url(output_path) if result.ok else None,
            "next_action": "download_or_audit" if result.ok else "fill_missing_fields",
        }

    async def _cancel_project(self, project_id: str) -> dict[str, Any]:
        task = self._tasks.pop(project_id, None)
        if task and not task.done():
            task.cancel()
        project = await self._require_manager().update_project_safe(project_id, status="cancelled")
        return {"ok": project is not None, "project": project, "cancelled_task": bool(task)}

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 4,
                "data_dir": str(self._require_workspace()),
                "brain_available": bool(self._brain_helper and self._brain_helper.is_available()),
            }

        @router.get("/catalog")
        async def catalog() -> dict[str, Any]:
            return build_catalog()

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            return self._settings()

        @router.put("/settings")
        async def put_settings(body: SettingsUpdateRequest) -> dict[str, Any]:
            if self._api:
                self._api.set_config({SETTINGS_KEY: body.model_dump()})
            return self._settings()

        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            stats = await collect_storage_stats(self._require_workspace())
            return stats.to_dict()

        @router.post("/storage/open-folder")
        async def open_folder() -> dict[str, Any]:
            return {
                "ok": True,
                "path": str(self._require_workspace()),
                "note": "Open-folder is handled by the UI host when available.",
            }

        @router.get("/storage/list-dir")
        async def list_dir(path: str | None = None) -> dict[str, Any]:
            root = Path(path).resolve() if path else self._require_workspace()
            if not root.exists() or not root.is_dir():
                raise HTTPException(status_code=404, detail="Directory not found")
            entries = [
                {"name": item.name, "path": str(item), "is_dir": item.is_dir()}
                for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            ]
            return {"path": str(root), "entries": entries}

        @router.post("/storage/mkdir")
        async def mkdir(body: dict[str, Any]) -> dict[str, Any]:
            parent = Path(body.get("parent") or self._require_workspace()).resolve()
            name = safe_name(str(body.get("name") or "folder"), fallback="folder")
            target = parent / name
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}

        @router.post("/upload")
        async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
            uploads = self._require_workspace() / "uploads"
            target = unique_child(uploads, file.filename or "upload.bin")
            content = await file.read()
            target.write_bytes(content)
            rel = target.relative_to(self._require_workspace())
            return {"ok": True, "rel_path": rel.as_posix(), "url": self._file_url(target), "filename": target.name}

        @router.get("/projects")
        async def list_projects(status: str | None = None) -> dict[str, Any]:
            return {"projects": await self._require_manager().list_projects(status=status)}

        @router.post("/projects")
        async def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
            project = await self._require_manager().create_project(body.model_dump())
            return {"project": project}

        @router.get("/projects/{project_id}")
        async def get_project(project_id: str) -> dict[str, Any]:
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "project": project,
                "sources": await self._require_manager().list_sources(project_id),
                "versions": await self._require_manager().list_versions(project_id),
            }

        @router.delete("/projects/{project_id}")
        async def delete_project(project_id: str) -> dict[str, Any]:
            return {"ok": await self._require_manager().delete_project(project_id)}

        @router.post("/projects/{project_id}/sources")
        async def add_source(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
            result = load_source(self._resolve_workspace_path(body.get("path") or body.get("rel_path", "")))
            source = await self._require_manager().add_source(
                project_id,
                source_type=result.source_type,
                filename=Path(result.path).name,
                path=result.path,
                text_preview=result.text[:1200],
                parse_status="parsed" if result.ok else "failed",
                error_message=result.error or None,
            )
            return {"source": source, "load": result.to_dict()}

        @router.post("/projects/{project_id}/template")
        async def add_template(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
            template_path = self._resolve_workspace_path(body.get("template_path") or body.get("rel_path", ""))
            inspection = extract_template_vars(template_path, context=body.get("context", {}))
            template = await self._require_manager().add_template(
                project_id,
                label=template_path.name,
                path=inspection.template_path,
                variables=inspection.variables,
                validation=inspection.to_dict(),
            )
            return {"template": template, "inspection": inspection.to_dict()}

        @router.post("/projects/{project_id}/outline/generate")
        async def generate_outline(project_id: str, body: OutlineRequest) -> dict[str, Any]:
            result = await self._require_brain_helper().generate_outline(
                requirement=body.requirement,
                doc_type=body.doc_type,
                sources_text=body.sources_text,
            )
            await self._require_manager().update_project_safe(project_id, status="outline_ready")
            return result.to_dict()

        @router.post("/projects/{project_id}/outline/confirm")
        async def confirm_outline(project_id: str, body: ConfirmOutlineRequest) -> dict[str, Any]:
            version = await self._require_manager().add_draft_version(project_id, outline=body.outline)
            await self._require_manager().update_project_safe(project_id, status="outline_ready")
            return {"version": version}

        @router.post("/projects/{project_id}/render")
        async def render(project_id: str, body: RenderRequest) -> dict[str, Any]:
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            ctx = WordPipelineContext(
                project_id=project_id,
                task_dir=self._require_manager().project_dir(project_id),
                requirement=project.get("requirements", ""),
                doc_type=project.get("doc_type", "research_report"),
                template_path=self._resolve_workspace_path(body.template_path) if body.template_path else None,
                source_paths=[self._resolve_workspace_path(item) for item in body.source_paths],
                fields=body.fields,
                outline=body.outline,
            )
            coro = run_pipeline(ctx, manager=self._require_manager(), brain_helper=self._brain_helper)
            task = self._api.spawn_task(coro, name=f"word-maker:{project_id}") if self._api else asyncio.create_task(coro)
            self._tasks[project_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(project_id, None))
            return {"ok": True, "project_id": project_id, "status": "rendering"}

        @router.post("/projects/{project_id}/cancel")
        async def cancel(project_id: str) -> dict[str, Any]:
            return await self._cancel_project(project_id)

        @router.post("/projects/{project_id}/sections/rewrite")
        async def rewrite_section(project_id: str, body: RewriteSectionRequest) -> dict[str, Any]:
            _ = project_id
            return (
                await self._require_brain_helper().rewrite_section(
                    section_markdown=body.section_markdown,
                    instruction=body.instruction,
                    tone=body.tone,
                )
            ).to_dict()

        @router.get("/projects/{project_id}/exports/{filename}")
        async def export(project_id: str, filename: str):
            path = self._require_manager().project_dir(project_id) / "exports" / safe_name(filename)
            if not path.exists():
                raise HTTPException(status_code=404, detail="Export not found")
            if self._api:
                return self._api.create_file_response(path, filename=path.name, as_download=True)
            raise HTTPException(status_code=500, detail="Plugin API unavailable")

        @router.post("/deps/check")
        async def deps_check() -> dict[str, Any]:
            return {"deps": check_optional_deps()}


def _tool_definitions() -> list[dict[str, Any]]:
    names = [
        ("word_start_project", "Start a guided Word document project."),
        ("word_ingest_sources", "Attach source files or notes to a Word document project."),
        ("word_upload_template", "Upload a DOCX template for a Word document project."),
        ("word_extract_template_vars", "Extract variables from a DOCX template."),
        ("word_generate_outline", "Generate a document outline from requirements and sources."),
        ("word_confirm_outline", "Confirm or update a generated document outline."),
        ("word_fill_template", "Fill a DOCX template with structured field data."),
        ("word_rewrite_section", "Rewrite one section of a Word document project."),
        ("word_audit", "Audit a generated Word document project."),
        ("word_export", "Export a Word document project."),
        ("word_list_projects", "List Word document projects."),
        ("word_cancel", "Cancel a running Word document task."),
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

