"""word-maker plugin entry point."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from word_brain_helper import WordBrainHelper
from word_maker_inline.file_utils import safe_name, unique_child
from word_maker_inline.python_deps import build_dependency_report, check_optional_deps
from word_maker_inline.storage_stats import collect_storage_stats
from word_maker_inline.upload_preview import add_upload_preview_route
from word_models import build_catalog, default_starter_doc_type
from word_pipeline import WordPipelineContext, audit_output, build_ppt_asset_metadata, run_pipeline
from word_source_loader import load_source
from word_task_manager import WordTaskManager
from word_template_convert import convert_template
from word_template_engine import extract_template_vars, render_template
from word_field_prepare import prepare_template_fields
from word_outline_sync import (
    build_outline_from_sources,
    merge_outline_into_fields,
)
from word_template_starters import (
    ensure_starter_files,
    list_starter_catalog,
    resolve_template_for_project,
    starter_path,
    starters_dir,
)
from word_workflow import build_workflow_state, collect_project_sources_text, load_project_draft

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


class ProjectUpdateRequest(StrictModel):
    title: str | None = None
    doc_type: str | None = None
    audience: str | None = None
    tone: str | None = None
    language: str | None = None
    requirements: str | None = None


class DefaultTemplateRequest(StrictModel):
    doc_type: str | None = None


class RenderRequest(StrictModel):
    template_path: str | None = None
    source_paths: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)
    outline: dict[str, Any] = Field(default_factory=dict)


class OutlineRequest(StrictModel):
    requirement: str = ""
    doc_type: str = "research_report"
    sources_text: str = ""
    source_paths: list[str] = Field(default_factory=list)
    template_path: str | None = None


class ConfirmOutlineRequest(StrictModel):
    outline: dict[str, Any]


class SyncFieldsRequest(StrictModel):
    outline: dict[str, Any]
    fields: dict[str, Any] = Field(default_factory=dict)
    template_path: str | None = None


class RewriteSectionRequest(StrictModel):
    section_markdown: str
    instruction: str
    tone: str = "professional"


class SettingsUpdateRequest(StrictModel):
    custom_data_dir: str | None = None
    default_language: str = "zh-CN"
    default_tone: str = "professional"
    retention_days: int = 30


class ClarifyRequest(StrictModel):
    requirement: str = ""
    doc_type: str = "research_report"


class ExtractFieldsRequest(StrictModel):
    template_path: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    source_paths: list[str] = Field(default_factory=list)


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
        if tool_name == "word_remove_template":
            return json.dumps(
                await self._remove_project_template(
                    arguments.get("project_id", ""),
                    template_path=arguments.get("template_path"),
                    delete_file=bool(arguments.get("delete_file", False)),
                ),
                ensure_ascii=False,
            )
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
        if tool_name == "word_clarify_requirements":
            project = await manager.get_project(arguments.get("project_id", ""))
            result = await self._require_brain_helper().clarify_requirements(
                requirement=arguments.get("requirement") or (project or {}).get("requirements", ""),
                doc_type=arguments.get("doc_type") or (project or {}).get("doc_type"),
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        if tool_name == "word_extract_fields":
            return json.dumps(await self._tool_extract_fields(arguments), ensure_ascii=False)
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

    def _uploads_dir(self) -> Path:
        path = self._require_workspace() / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_project_template(
        self,
        doc_type: str,
        template_path: str | None,
    ) -> tuple[Path | None, str, str | None]:
        """Resolve user or default starter template. Returns abs path, source, rel path."""
        abs_arg: str | None = None
        if template_path and str(template_path).strip():
            abs_arg = str(self._resolve_workspace_path(template_path))
        resolved, source = resolve_template_for_project(
            doc_type,
            abs_arg,
            uploads_dir=self._uploads_dir(),
        )
        if resolved is None:
            return None, source, None
        try:
            rel = resolved.relative_to(self._require_workspace().resolve()).as_posix()
        except ValueError:
            rel = str(resolved)
        return resolved, source, rel

    def _brain_status(self) -> dict[str, Any]:
        if self._brain_helper is None:
            return {
                "available": False,
                "permission_granted": False,
                "brain_injected": False,
                "reason": "helper_uninitialized",
                "message": "word-maker brain helper is not initialized",
            }
        return self._brain_helper.brain_status()

    def _settings(self) -> dict[str, Any]:
        settings = _read_settings(self._api)
        brain_status = self._brain_status()
        brain_available = bool(brain_status["available"])
        return {
            "custom_data_dir": settings.get("custom_data_dir", ""),
            "default_language": settings.get("default_language", "zh-CN"),
            "default_tone": settings.get("default_tone", "professional"),
            "retention_days": int(settings.get("retention_days", 30)),
            "data_dir_active": str(self._require_workspace()),
            "brain_available": brain_available,
            "brain_status": brain_status,
            "deps": check_optional_deps(),
            "dependency_report": build_dependency_report(brain_available=brain_available),
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

    async def _tool_extract_fields(self, arguments: dict[str, Any]) -> dict[str, Any]:
        manager = self._require_manager()
        project_id = arguments.get("project_id", "")
        project = await manager.get_project(project_id)
        if project is None:
            return {"ok": False, "error": f"Unknown project: {project_id}", "next_action": "check_project_id"}

        doc_type = str(project.get("doc_type") or "research_report")
        template_path_arg = arguments.get("template_path")
        resolved_tpl, template_source, template_rel = self._resolve_project_template(
            doc_type,
            template_path_arg,
        )
        if resolved_tpl is None:
            return {
                "ok": False,
                "error": "未找到模板，请上传模板或使用支持默认模板的文档类型",
                "project_id": project_id,
                "next_action": "upload_template_or_use_default",
            }

        fields = dict(arguments.get("fields") or {})
        source_paths = arguments.get("source_paths") or []
        if isinstance(source_paths, str):
            source_paths = [source_paths]

        path_candidates = list(source_paths)
        for item in await manager.list_sources(project_id):
            stored = str(item.get("path") or "").strip()
            if stored and stored not in path_candidates:
                path_candidates.append(stored)
        sources_text, _sources_meta = await collect_project_sources_text(
            manager,
            project_id,
            extra_paths=path_candidates,
            resolve_path=self._resolve_workspace_path,
        )
        requirement = arguments.get("requirement") or project.get("requirements", "")
        outline = arguments.get("outline")
        if not isinstance(outline, dict):
            versions = await manager.list_versions(project_id)
            outline = versions[0].get("outline") if versions else {}
        if not isinstance(outline, dict):
            outline = {}

        prepared = await prepare_template_fields(
            doc_type=doc_type,
            template_path=resolved_tpl,
            sources_text=sources_text,
            requirement=requirement,
            outline=outline,
            fields=fields,
            brain_helper=self._brain_helper,
        )
        merged = prepared.fields
        missing = prepared.missing

        return {
            "ok": True,
            "data": {"fields": merged, "missing": missing, "confidence": "medium" if prepared.used_brain else "low"},
            "error": prepared.error,
            "used_brain": prepared.used_brain,
            "project_id": project_id,
            "fields": merged,
            "field_sources": prepared.field_sources,
            "missing": missing,
            "confidence": "medium" if prepared.used_brain else "low",
            "template_path": template_rel,
            "template_source": template_source,
            "next_action": "confirm_fields_or_render",
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

    async def _generate_outline_for_project(
        self,
        project_id: str,
        body: OutlineRequest,
    ) -> dict[str, Any]:
        manager = self._require_manager()
        project = await manager.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        sources_text = (body.sources_text or "").strip()
        sources_meta: list[dict[str, Any]] = []
        if not sources_text:
            path_candidates = list(body.source_paths)
            for source in await manager.list_sources(project_id):
                stored = str(source.get("path") or "").strip()
                if stored and stored not in path_candidates:
                    path_candidates.append(stored)
            sources_text, sources_meta = await collect_project_sources_text(
                manager,
                project_id,
                extra_paths=path_candidates,
                resolve_path=self._resolve_workspace_path,
            )

        if not sources_text.strip():
            template_path = body.template_path
            if not template_path:
                template = await manager.latest_template(project_id)
                template_path = str(template.get("path") or "") if template else ""
            if template_path:
                tmpl_result = load_source(self._resolve_workspace_path(template_path))
                if tmpl_result.ok and tmpl_result.text.strip():
                    sources_text = f"--- 模板内容参考 ---\n{tmpl_result.text[:2000]}"
                    sources_meta.append(
                        {
                            "path": template_path,
                            "filename": Path(template_path).name,
                            "loaded": True,
                            "chars": len(tmpl_result.text),
                            "error": "",
                            "parse_status": "template",
                            "from_preview": False,
                        }
                    )

        requirement = body.requirement or project.get("requirements", "")
        if not sources_text.strip() and requirement.strip():
            sources_text = f"--- 需求描述 ---\n{requirement.strip()[:3000]}"
            sources_meta.append(
                {
                    "path": "(requirements)",
                    "filename": "需求描述",
                    "loaded": True,
                    "chars": len(requirement.strip()),
                    "error": "",
                    "parse_status": "requirements",
                    "from_preview": False,
                }
            )
        doc_type = body.doc_type or project.get("doc_type", "research_report")
        title = project.get("title", "")

        used_brain = False
        error = ""
        if self._brain_helper and self._brain_helper.is_available():
            result = await self._brain_helper.generate_outline(
                requirement=requirement,
                doc_type=doc_type,
                sources_text=sources_text,
            )
            if result.ok:
                outline_data = result.data
                used_brain = result.used_brain
            else:
                outline_data = build_outline_from_sources(sources_text, requirement, title=title)
                error = result.error
        else:
            outline_data = build_outline_from_sources(sources_text, requirement, title=title)
            if self._brain_helper:
                error = str(self._brain_helper.brain_status().get("message") or "")

        version = await manager.add_draft_version(project_id, outline=outline_data)
        await manager.update_project_safe(project_id, status="outline_ready")
        return {
            "ok": True,
            "data": outline_data,
            "error": error,
            "used_brain": used_brain,
            "sources_chars": len(sources_text),
            "sources_meta": sources_meta,
            "version": version,
            "next_action": "confirm_outline_and_fields",
        }

    async def _cancel_project(self, project_id: str) -> dict[str, Any]:
        task = self._tasks.pop(project_id, None)
        if task and not task.done():
            task.cancel()
        project = await self._require_manager().update_project_safe(project_id, status="cancelled")
        return {"ok": project is not None, "project": project, "cancelled_task": bool(task)}

    def _is_protected_starter_file(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(starters_dir().resolve())
        except ValueError:
            return False
        return True

    async def _remove_project_template(
        self,
        project_id: str,
        *,
        template_path: str | None = None,
        delete_file: bool = False,
    ) -> dict[str, Any]:
        manager = self._require_manager()
        project = await manager.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        deleted = await manager.delete_project_template(
            project_id,
            template_path=template_path,
        )
        if not deleted:
            return {
                "ok": False,
                "error": "未找到可删除的模板记录",
                "project_id": project_id,
            }

        removed_files: list[str] = []
        if delete_file:
            for row in deleted:
                try:
                    abs_path = self._resolve_workspace_path(str(row.get("path") or ""))
                except HTTPException:
                    continue
                if abs_path.exists() and not self._is_protected_starter_file(abs_path):
                    abs_path.unlink()
                    removed_files.append(str(row.get("path") or ""))

        doc_type = str(project.get("doc_type") or "")
        still_has = await manager.latest_template(project_id) is not None
        return {
            "ok": True,
            "project_id": project_id,
            "deleted_count": len(deleted),
            "deleted": [{"id": item.get("id"), "path": item.get("path"), "label": item.get("label")} for item in deleted],
            "removed_files": removed_files,
            "can_use_default_template": bool(default_starter_doc_type(doc_type)) and not still_has,
            "next_action": "upload_template_or_use_default",
        }

    async def _delete_project(self, project_id: str) -> dict[str, Any]:
        task = self._tasks.pop(project_id, None)
        if task and not task.done():
            task.cancel()
        ok = await self._require_manager().delete_project(project_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"ok": True, "project_id": project_id}

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 4,
                "data_dir": str(self._require_workspace()),
                "brain_available": bool(self._brain_status()["available"]),
                "brain_status": self._brain_status(),
            }

        @router.get("/catalog")
        async def catalog() -> dict[str, Any]:
            return build_catalog()

        @router.get("/templates/starters")
        async def list_starters() -> dict[str, Any]:
            return {"starters": list_starter_catalog(plugin_id=PLUGIN_ID)}

        @router.get("/templates/starters/{doc_type}/download")
        async def download_starter(doc_type: str) -> FileResponse:
            ensure_starter_files()
            path = starter_path(doc_type)
            if path is None or not path.exists():
                raise HTTPException(status_code=404, detail=f"Starter not found: {doc_type}")
            return FileResponse(
                path=str(path),
                filename=path.name,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

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

        @router.put("/projects/{project_id}")
        async def update_project(project_id: str, body: ProjectUpdateRequest) -> dict[str, Any]:
            updates = body.model_dump(exclude_unset=True)
            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")
            project = await self._require_manager().update_project_safe(project_id, **updates)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
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
            return await self._delete_project(project_id)

        @router.post("/projects/{project_id}/delete")
        async def delete_project_post(project_id: str) -> dict[str, Any]:
            return await self._delete_project(project_id)

        @router.post("/projects/{project_id}/sources")
        async def add_source(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
            resolved = self._resolve_workspace_path(body.get("path") or body.get("rel_path", ""))
            result = load_source(resolved)
            workspace = self._require_workspace().resolve()
            try:
                stored_path = resolved.resolve().relative_to(workspace).as_posix()
            except ValueError:
                stored_path = str(resolved)
            source = await self._require_manager().add_source(
                project_id,
                source_type=result.source_type,
                filename=Path(result.path).name,
                path=stored_path,
                text_preview=result.text[:1200],
                parse_status="parsed" if result.ok else "failed",
                error_message=result.error or None,
            )
            return {"source": source, "load": result.to_dict()}

        @router.post("/projects/{project_id}/template/default")
        async def use_default_template(
            project_id: str,
            body: DefaultTemplateRequest | None = None,
        ) -> dict[str, Any]:
            payload = body or DefaultTemplateRequest()
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            doc_type = payload.doc_type or str(project.get("doc_type") or "research_report")
            if payload.doc_type and payload.doc_type != project.get("doc_type"):
                await self._require_manager().update_project_safe(project_id, doc_type=payload.doc_type)
            resolved, source, rel = self._resolve_project_template(doc_type, None)
            if resolved is None or source != "default":
                return {
                    "ok": False,
                    "error": "当前文档类型没有可用的系统默认模板",
                    "template_source": source,
                }
            versions = await self._require_manager().list_versions(project_id)
            fields: dict[str, Any] = {}
            if versions and isinstance(versions[0].get("fields"), dict):
                fields = dict(versions[0]["fields"])
            inspection = extract_template_vars(resolved, context=fields)
            template = await self._require_manager().add_template(
                project_id,
                label=resolved.name,
                path=rel or str(resolved),
                variables=inspection.variables,
                validation=inspection.to_dict(),
            )
            return {
                "ok": True,
                "template_path": rel,
                "template_source": source,
                "template": template,
                "inspection": inspection.to_dict(),
            }

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

        @router.delete("/projects/{project_id}/template")
        async def delete_template(
            project_id: str,
            template_path: str | None = None,
            delete_file: bool = False,
        ) -> dict[str, Any]:
            return await self._remove_project_template(
                project_id,
                template_path=template_path,
                delete_file=delete_file,
            )

        @router.post("/projects/{project_id}/template/delete")
        async def delete_template_post(project_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            payload = body or {}
            return await self._remove_project_template(
                project_id,
                template_path=payload.get("template_path"),
                delete_file=bool(payload.get("delete_file", False)),
            )

        @router.post("/projects/{project_id}/template/convert")
        async def convert_template_route(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
            manager = self._require_manager()
            project = await manager.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            raw_path = body.get("template_path") or ""
            source_path = self._resolve_workspace_path(raw_path)
            uploads = self._require_workspace() / "uploads"
            output_name = source_path.stem + "-upgraded.docx"
            output_path = unique_child(uploads, output_name)
            result = convert_template(source_path, output_path)
            if not result.ok:
                return {"ok": False, "error": result.error, "placeholder_count": result.placeholder_count}
            rel = output_path.relative_to(self._require_workspace())
            convert_hints = {var: zh for zh, var in result.mapping.items()}
            inspection = extract_template_vars(
                output_path,
                context=body.get("context", {}),
                extra_hints=convert_hints,
            )
            await manager.add_template(
                project_id,
                label=output_path.name,
                path=str(output_path),
                variables=inspection.variables,
                validation=inspection.to_dict(),
            )
            return {
                "ok": True,
                "upgraded_path": rel.as_posix(),
                "variables": inspection.variables,
                "mapping": result.mapping,
                "placeholder_count": result.placeholder_count,
                "inspection": inspection.to_dict(),
            }

        @router.post("/projects/{project_id}/requirements/clarify")
        async def clarify_requirements(project_id: str, body: ClarifyRequest) -> dict[str, Any]:
            manager = self._require_manager()
            project = await manager.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            sources = await manager.list_sources(project_id)
            result = await self._require_brain_helper().clarify_requirements(
                requirement=body.requirement or project.get("requirements", ""),
                doc_type=body.doc_type or project.get("doc_type"),
                sources=sources,
            )
            if result.ok and result.data.get("doc_type"):
                await manager.update_project_safe(project_id, doc_type=result.data["doc_type"])
            return result.to_dict()

        @router.post("/projects/{project_id}/fields/extract")
        async def extract_fields(project_id: str, body: ExtractFieldsRequest) -> dict[str, Any]:
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return await self._tool_extract_fields(
                {
                    "project_id": project_id,
                    "template_path": body.template_path,
                    "fields": body.fields,
                    "source_paths": body.source_paths,
                }
            )

        @router.get("/projects/{project_id}/workflow")
        async def get_workflow(project_id: str, template_path: str | None = None) -> dict[str, Any]:
            manager = self._require_manager()
            project = await manager.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            draft = await load_project_draft(manager, project_id)
            if template_path:
                draft["template_path"] = template_path
            if not draft.get("template_path"):
                _resolved, source, rel = self._resolve_project_template(
                    str(project.get("doc_type") or "research_report"),
                    None,
                )
                if rel and source == "default":
                    draft["template_path"] = rel
                    draft["template_source"] = source
            return await build_workflow_state(
                manager,
                project_id,
                draft=draft,
                workspace=self._require_workspace(),
            )

        @router.post("/projects/{project_id}/outline/generate")
        async def generate_outline(project_id: str, body: OutlineRequest) -> dict[str, Any]:
            return await self._generate_outline_for_project(project_id, body)

        @router.post("/projects/{project_id}/outline/confirm")
        async def confirm_outline(project_id: str, body: ConfirmOutlineRequest) -> dict[str, Any]:
            version = await self._require_manager().add_draft_version(project_id, outline=body.outline)
            await self._require_manager().update_project_safe(project_id, status="outline_ready")
            return {"version": version}

        @router.post("/projects/{project_id}/outline/sync-fields")
        async def sync_outline_fields(project_id: str, body: SyncFieldsRequest) -> dict[str, Any]:
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            template_variables: list[str] = []
            var_contexts: dict[str, str] = {}
            resolved_tpl, _source, _rel = self._resolve_project_template(
                str(project.get("doc_type") or "research_report"),
                body.template_path,
            )
            if resolved_tpl is not None:
                inspection = extract_template_vars(resolved_tpl, context=body.fields)
                template_variables = inspection.variables
                var_contexts = inspection.var_contexts or {}
            merged = merge_outline_into_fields(
                body.outline,
                body.fields,
                template_variables=template_variables,
                var_contexts=var_contexts,
            )
            return {"project_id": project_id, "fields": merged, "template_path": _rel}

        @router.post("/projects/{project_id}/render")
        async def render(project_id: str, body: RenderRequest) -> dict[str, Any]:
            project = await self._require_manager().get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            outline = body.outline if isinstance(body.outline, dict) else {}
            fields = dict(body.fields or {})
            resolved_tpl, template_source, template_rel = self._resolve_project_template(
                str(project.get("doc_type") or "research_report"),
                body.template_path,
            )
            template_path = resolved_tpl
            if outline.get("sections") and template_path is not None:
                inspection = extract_template_vars(template_path, context=fields)
                fields = merge_outline_into_fields(
                    outline,
                    fields,
                    template_variables=inspection.variables,
                    var_contexts=inspection.var_contexts or {},
                )
                await self._require_manager().add_draft_version(
                    project_id,
                    outline=outline,
                    fields=fields,
                )
            ctx = WordPipelineContext(
                project_id=project_id,
                task_dir=self._require_manager().project_dir(project_id),
                requirement=project.get("requirements", ""),
                doc_type=project.get("doc_type", "research_report"),
                template_path=template_path,
                template_source=template_source,
                source_paths=[self._resolve_workspace_path(item) for item in body.source_paths],
                fields=fields,
                outline=outline,
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
            brain_status = self._brain_status()
            brain_available = bool(brain_status["available"])
            return {
                "deps": check_optional_deps(),
                "brain_status": brain_status,
                "dependency_report": build_dependency_report(brain_available=brain_available),
            }


def _tool_definitions() -> list[dict[str, Any]]:
    names = [
        ("word_start_project", "Start a guided Word document project."),
        ("word_ingest_sources", "Attach source files or notes to a Word document project."),
        ("word_upload_template", "Upload a DOCX template for a Word document project."),
        ("word_remove_template", "Remove the selected DOCX template from a project."),
        ("word_extract_template_vars", "Extract variables from a DOCX template."),
        ("word_generate_outline", "Generate a document outline from requirements and sources."),
        ("word_clarify_requirements", "Clarify document requirements and suggest follow-up questions."),
        ("word_extract_fields", "Extract template field values from requirements and sources."),
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

