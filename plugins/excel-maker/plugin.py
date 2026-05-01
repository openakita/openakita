"""excel-maker plugin entry point.

The plugin focuses on producing auditable XLSX report workbooks. LLM calls may
help clarify requirements and draft WorkbookPlan JSON, but binary Excel output
is generated only by deterministic Python code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from excel_auditor import WorkbookAuditor
from excel_executor import ExcelOperationExecutor, OperationExecutionError
from excel_formula import generate_formula
from excel_importer import WorkbookImporter, WorkbookImportError
from excel_maker_inline.file_utils import (
    copy_into,
    resolve_plugin_data_root,
    safe_name,
    unique_child,
    write_probe,
)
from excel_maker_inline.llm_json_parser import parse_json_object
from excel_maker_inline.python_deps import PythonDepsManager
from excel_maker_inline.storage_stats import collect_storage_stats
from excel_maker_inline.upload_preview import register_upload_preview_routes
from excel_models import (
    ArtifactKind,
    ProjectCreate,
    ProjectStatus,
    Settings,
    TemplateStatus,
    WorkbookPlan,
    WorkbookStatus,
)
from excel_plan import WorkbookPlanBuilder
from excel_profiler import WorkbookProfiler
from excel_task_manager import ExcelTaskManager
from excel_template_manager import TemplateDiagnosticError, TemplateManager
from excel_workbook_builder import WorkbookBuilder, WorkbookBuildError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase

PLUGIN_ID = "excel-maker"
SETTINGS_KEY = "excel_maker_settings"


class ProjectCreateRequest(ProjectCreate):
    pass


class ImportWorkbookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    workbook_id: str | None = None
    project_id: str | None = None
    name: str | None = None


class ProfileWorkbookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class ClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    workbook_id: str | None = None
    goal: str = ""
    profile: dict[str, Any] | None = None


class ReportPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    workbook_id: str | None = None
    brief: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] | None = None


class FormulaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "sumifs"
    range_ref: str
    criteria_ref: str = ""
    criteria: str = ""


class OperationsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    project_id: str | None = None
    profile: dict[str, Any] | None = None


class BuildReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_id: str | None = None
    plan: dict[str, Any] | None = None


class TemplateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: str | None = None
    export_dir: str | None = None
    default_style: str | None = None
    brand_color: str | None = None
    font_family: str | None = None
    number_format: str | None = None


class StorageOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None


class StorageMkdirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: str | None = None
    name: str


class Plugin(PluginBase):
    """OpenAkita plugin entry for Excel report workbook generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._deps: PythonDepsManager | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        config = api.get_config() if hasattr(api, "get_config") else {}
        configured = config.get(SETTINGS_KEY, {}) if isinstance(config, dict) else {}
        configured_data_dir = configured.get("data_dir") if isinstance(configured, dict) else None
        data_root = api.get_data_dir() or Path.cwd() / "data"
        if configured_data_dir:
            try:
                data_root = write_probe(configured_data_dir)
            except OSError as exc:
                api.log(f"{PLUGIN_ID}: invalid configured data_dir, falling back: {exc}")
        data_dir = resolve_plugin_data_root(data_root)
        self._data_dir = data_dir
        self._deps = PythonDepsManager(data_dir)
        router = APIRouter()
        register_upload_preview_routes(router, self._storage_paths()["uploads"], prefix="/uploads")

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 9,
                "primary_artifact": "xlsx",
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "excel_maker.db"),
            }

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            settings = self._settings()
            return {
                "ok": True,
                "settings": settings.model_dump(mode="json"),
                "resolved": {key: str(path) for key, path in self._storage_paths(settings).items()},
            }

        @router.put("/settings")
        async def update_settings(payload: SettingsUpdateRequest) -> dict[str, Any]:
            settings = self._settings()
            values = settings.model_dump()
            path_keys = {
                "data_dir",
                "uploads_dir",
                "workbooks_dir",
                "projects_dir",
                "export_dir",
                "templates_dir",
                "cache_dir",
            }
            for key, value in payload.model_dump(exclude_none=True).items():
                if key in path_keys and value:
                    write_probe(value)
                values[key] = value
            values["updated_at"] = __import__("time").time()
            self._save_settings(Settings(**values))
            return {
                "ok": True,
                "settings": values,
                "resolved": {key: str(path) for key, path in self._storage_paths(Settings(**values)).items()},
                "reload_recommended": "data_dir" in payload.model_dump(exclude_none=True),
            }

        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            return {"ok": True, "stats": collect_storage_stats(data_dir, self._storage_paths())}

        @router.post("/storage/open-folder")
        async def open_folder(payload: StorageOpenRequest) -> dict[str, Any]:
            try:
                target = self._safe_open_path(payload.path)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.exists():
                raise HTTPException(status_code=404, detail="Folder not found")
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return {"ok": True, "path": str(target)}

        @router.get("/storage/list-dir")
        async def list_dir(path: str | None = None) -> dict[str, Any]:
            try:
                target = self._safe_open_path(path)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Path is not a directory")
            return {
                "ok": True,
                "path": str(target),
                "entries": [
                    {
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else 0,
                    }
                    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                ],
            }

        @router.post("/storage/mkdir")
        async def mkdir(payload: StorageMkdirRequest) -> dict[str, Any]:
            try:
                parent = self._safe_open_path(payload.parent)
                target = parent / safe_name(payload.name, "folder")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}

        @router.post("/cleanup")
        async def cleanup() -> dict[str, Any]:
            cache = self._storage_paths()["cache"]
            removed = 0
            if cache.exists():
                for item in cache.iterdir():
                    removed += 1
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink(missing_ok=True)
            cache.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "removed": removed}

        @router.get("/system/python-deps")
        async def list_python_deps() -> dict[str, Any]:
            assert self._deps is not None
            return {"ok": True, "groups": self._deps.list_groups()}

        @router.get("/system/python-deps/{dep_id}/status")
        async def python_dep_status(dep_id: str) -> dict[str, Any]:
            assert self._deps is not None
            try:
                return {"ok": True, "dependency": self._deps.status(dep_id)}
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @router.post("/system/python-deps/{dep_id}/install")
        async def install_python_dep(dep_id: str) -> dict[str, Any]:
            assert self._deps is not None
            try:
                return {"ok": True, "dependency": await self._deps.start_install(dep_id)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @router.post("/system/python-deps/{dep_id}/uninstall")
        async def uninstall_python_dep(dep_id: str) -> dict[str, Any]:
            assert self._deps is not None
            try:
                return {"ok": True, "dependency": await self._deps.start_uninstall(dep_id)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @router.post("/projects")
        async def create_project(payload: ProjectCreateRequest) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                project = await manager.create_project(ProjectCreate(**payload.model_dump()))
            await self._broadcast("project_update", {"project_id": project.id, "status": project.status})
            return {"ok": True, "project": project.model_dump(mode="json")}

        @router.get("/projects")
        async def list_projects() -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                projects = await manager.list_projects()
            return {"ok": True, "projects": [item.model_dump(mode="json") for item in projects]}

        @router.get("/projects/{project_id}")
        async def get_project(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                project = await manager.get_project(project_id)
                workbooks = await manager.list_workbooks(project_id)
                artifacts = await manager.list_artifacts(project_id)
                audit_items = await manager.list_audit_items(project_id)
                operations = await manager.list_operations(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "ok": True,
                "project": project.model_dump(mode="json"),
                "workbooks": [self._public_workbook(item) for item in workbooks],
                "artifacts": [self._public_artifact(item) for item in artifacts],
                "audit_items": [item.model_dump(mode="json") for item in audit_items],
                "operations": operations,
            }

        @router.delete("/projects/{project_id}")
        async def delete_project(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbooks = await manager.list_workbooks(project_id)
                deleted = await manager.delete_project(project_id)
            removed_dirs = self._cleanup_project_files(data_dir, project_id, [item.id for item in workbooks])
            return {"ok": True, "deleted": deleted, "removed_dirs": removed_dirs}

        @router.post("/projects/{project_id}/cancel")
        async def cancel_project(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                await manager.update_project_safe(project_id, status=ProjectStatus.CANCELLED)
            await self._broadcast("project_update", {"project_id": project_id, "status": "cancelled"})
            return {"ok": True}

        @router.post("/projects/{project_id}/retry")
        async def retry_project(project_id: str) -> dict[str, Any]:
            return await build_report(project_id, BuildReportRequest())

        @router.post("/upload")
        async def upload(request: Request) -> dict[str, Any]:
            form = await request.form()
            upload_file = form.get("file")
            project_id = str(form.get("project_id") or "") or None
            if upload_file is None or not hasattr(upload_file, "filename") or not hasattr(upload_file, "read"):
                raise HTTPException(status_code=400, detail="Missing upload field: file")
            filename = safe_name(str(upload_file.filename or "upload.xlsx"))
            target = unique_child(self._storage_paths()["uploads"], filename)
            content = await upload_file.read()
            target.write_bytes(content)
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.create_workbook(
                    project_id=project_id,
                    filename=filename,
                    original_path=str(target),
                    metadata={"size": len(content), "preview_url": f"/uploads/{target.name}"},
                )
            return {"ok": True, "workbook": self._public_workbook(workbook), "preview_url": f"/uploads/{target.name}"}

        @router.post("/workbooks/import")
        async def import_workbook(payload: ImportWorkbookRequest) -> dict[str, Any]:
            return await self._import_workbook(data_dir, payload)

        @router.get("/workbooks/{workbook_id}")
        async def get_workbook(workbook_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.get_workbook(workbook_id)
                sheets = await manager.list_sheets(workbook_id)
            if workbook is None:
                raise HTTPException(status_code=404, detail="Workbook not found")
            return {
                "ok": True,
                "workbook": self._public_workbook(workbook),
                "sheets": [item.model_dump(mode="json") for item in sheets],
            }

        @router.get("/workbooks/{workbook_id}/preview")
        async def preview_workbook(workbook_id: str, sheet: str | None = None) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.get_workbook(workbook_id)
            if workbook is None or not workbook.profile_path:
                raise HTTPException(status_code=404, detail="Workbook preview not found")
            return {
                "ok": True,
                "preview": WorkbookImporter(
                    data_dir,
                    workbooks_root=self._storage_paths()["workbooks"],
                ).preview(workbook.profile_path, sheet),
            }

        @router.post("/workbooks/{workbook_id}/profile")
        async def profile_workbook(workbook_id: str, payload: ProfileWorkbookRequest) -> dict[str, Any]:
            return await self._profile_workbook(data_dir, workbook_id, force=payload.force)

        @router.post("/ai/clarify")
        async def clarify(payload: ClarifyRequest) -> dict[str, Any]:
            questions = await self._clarify_questions(payload)
            if payload.project_id:
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    await manager.update_project_safe(
                        payload.project_id,
                        report_brief={"goal": payload.goal, "questions": questions},
                    )
            return {"ok": True, "questions": questions}

        @router.post("/ai/report-plan")
        async def report_plan(payload: ReportPlanRequest) -> dict[str, Any]:
            profile = payload.profile or await self._load_profile_for_workbook(data_dir, payload.workbook_id)
            title = "Excel Report"
            if payload.project_id:
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    project = await manager.get_project(payload.project_id)
                    if project:
                        title = project.title
            plan = await self._generate_report_plan(
                title=title,
                workbook_id=payload.workbook_id,
                profile=profile,
                brief=payload.brief,
            )
            if payload.project_id:
                plan_path = self._project_dir(payload.project_id) / "workbook_plan.json"
                plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    await manager.create_artifact(project_id=payload.project_id, kind=ArtifactKind.PLAN, path=str(plan_path))
                    await manager.update_project_safe(payload.project_id, status=ProjectStatus.PLANNED)
            return {"ok": True, "plan": plan.model_dump(mode="json")}

        @router.post("/ai/formula")
        async def formula(payload: FormulaRequest) -> dict[str, Any]:
            suggestion = generate_formula(
                payload.kind,
                range_ref=payload.range_ref,
                criteria_ref=payload.criteria_ref,
                criteria=payload.criteria,
            )
            return {"ok": True, "formula": suggestion.model_dump(mode="json")}

        @router.post("/operations/plan")
        async def operations_plan(payload: ReportPlanRequest) -> dict[str, Any]:
            result = await report_plan(payload)
            plan = result["plan"]
            return {"ok": True, "operations": plan.get("operations", []), "plan": plan}

        @router.post("/operations/apply")
        async def operations_apply(payload: OperationsApplyRequest) -> dict[str, Any]:
            try:
                plan = WorkbookPlan.model_validate(payload.plan)
                result = ExcelOperationExecutor().apply_plan(plan, payload.profile)
            except (ValueError, OperationExecutionError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if payload.project_id:
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    await manager.record_operations(payload.project_id, result.get("applied", []))
            return {"ok": True, "result": result}

        @router.post("/reports/{project_id}/build")
        async def build_report(project_id: str, payload: BuildReportRequest) -> dict[str, Any]:
            return await self._build_report(data_dir, project_id, payload)

        @router.get("/reports/{project_id}/artifacts")
        async def report_artifacts(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifacts = await manager.list_artifacts(project_id)
            return {"ok": True, "artifacts": [self._public_artifact(item) for item in artifacts]}

        @router.post("/reports/{project_id}/audit")
        async def audit_report(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifacts = await manager.list_artifacts(project_id)
            workbook_artifact = next((item for item in artifacts if item.kind == ArtifactKind.WORKBOOK), None)
            if workbook_artifact is None:
                raise HTTPException(status_code=404, detail="Workbook artifact not found")
            return await self._audit_report(
                data_dir,
                project_id,
                workbook_artifact.path,
                workbook_artifact.id,
                plan=self._load_saved_plan(data_dir, project_id),
            )

        @router.get("/artifacts/{artifact_id}/download", response_class=FileResponse)
        async def download_artifact(artifact_id: str):
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifact = await manager.get_artifact(artifact_id)
            if artifact is None or not Path(artifact.path).is_file():
                raise HTTPException(status_code=404, detail="Artifact not found")
            return FileResponse(artifact.path, filename=Path(artifact.path).name)

        @router.get("/templates")
        async def list_templates() -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                templates = await manager.list_templates()
            return {"ok": True, "templates": [self._public_template(item) for item in templates]}

        @router.post("/templates")
        async def upload_template(payload: TemplateUploadRequest) -> dict[str, Any]:
            templates_dir = self._storage_paths()["templates"]
            copied = copy_into(payload.path, templates_dir, payload.name or Path(payload.path).name)
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                template = await manager.create_template(name=payload.name or copied.stem, original_path=str(copied))
            return {"ok": True, "template": self._public_template(template)}

        @router.post("/templates/{template_id}/diagnose")
        async def diagnose_template(template_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                template = await manager.get_template(template_id)
            if template is None:
                raise HTTPException(status_code=404, detail="Template not found")
            try:
                out_path = self._storage_paths()["templates"] / f"{template_id}_diagnostic.json"
                diagnostic = TemplateManager().diagnose(template.original_path, out_path)
            except TemplateDiagnosticError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                updated = await manager.update_template_safe(
                    template_id,
                    diagnostic_path=str(out_path),
                    status=TemplateStatus.DIAGNOSED,
                    metadata={"diagnostic": diagnostic},
                )
            return {"ok": True, "template": self._public_template(updated) if updated else None, "diagnostic": diagnostic}

        @router.delete("/templates/{template_id}")
        async def delete_template(template_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                template = await manager.get_template(template_id)
                deleted = await manager.delete_template(template_id)
            removed_files = self._cleanup_template_files(template)
            return {"ok": True, "deleted": deleted, "removed_files": removed_files}

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _import_workbook(self, data_dir: Path, payload: ImportWorkbookRequest) -> dict[str, Any]:
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.get_workbook(payload.workbook_id) if payload.workbook_id else None
            if workbook is None:
                source_path = Path(payload.path).expanduser().resolve()
                if not source_path.is_file():
                    raise HTTPException(status_code=404, detail="Workbook file not found")
                workbook = await manager.create_workbook(
                    project_id=payload.project_id,
                    filename=payload.name or source_path.name,
                    original_path=str(source_path),
                    metadata={"imported_from": str(source_path)},
                )
            else:
                source_path = Path(workbook.original_path)
        try:
            imported = WorkbookImporter(
                data_dir,
                workbooks_root=self._storage_paths()["workbooks"],
            ).import_file(source_path, workbook.id)
        except WorkbookImportError as exc:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                await manager.update_workbook_safe(workbook.id, status=WorkbookStatus.FAILED, metadata={"error": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.update_workbook_safe(
                workbook.id,
                imported_path=str(imported.imported_path),
                profile_path=str(imported.profile_path),
                status=WorkbookStatus.IMPORTED,
                metadata={"warnings": imported.warnings},
            )
            sheets = await manager.replace_sheets(workbook.id, imported.sheets) if workbook else []
            if payload.project_id:
                await manager.update_project_safe(payload.project_id, status=ProjectStatus.IMPORTED)
        await self._broadcast("dataset_profiled", {"workbook_id": workbook.id if workbook else None})
        return {
            "ok": True,
            "workbook": self._public_workbook(workbook) if workbook else None,
            "sheets": [item.model_dump(mode="json") for item in sheets],
            "preview": imported.preview,
            "warnings": imported.warnings,
        }

    async def _profile_workbook(self, data_dir: Path, workbook_id: str, *, force: bool = False) -> dict[str, Any]:
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.get_workbook(workbook_id)
        if workbook is None or not workbook.profile_path:
            raise HTTPException(status_code=404, detail="Workbook import profile not found")
        profile_path = self._storage_paths()["workbooks"] / workbook_id / "profile.json"
        if profile_path.exists() and not force:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        else:
            profile = WorkbookProfiler().profile_import(workbook.profile_path, profile_path)
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.update_workbook_safe(
                workbook_id,
                profile_path=str(profile_path),
                status=WorkbookStatus.PROFILED,
                metadata={**workbook.metadata, "profiled": True},
            )
            if workbook and workbook.project_id:
                await manager.update_project_safe(workbook.project_id, status=ProjectStatus.PROFILED)
        return {"ok": True, "profile": profile}

    async def _clarify_questions(self, payload: ClarifyRequest) -> list[str]:
        profile = payload.profile
        if profile is None and payload.workbook_id and self._data_dir:
            profile = await self._load_profile_for_workbook(self._data_dir, payload.workbook_id)
        fallback = [
            "这份报表的主要读者是谁？管理层、业务运营还是财务审计？",
            "核心指标有哪些，是否需要同比、环比或达成率？",
            "报表周期是什么，数据需要按日、周、月还是季度汇总？",
            "是否需要保留 Raw_Data 明细，以及哪些字段属于敏感字段？",
            "最终交付样式是否有品牌色、字体或模板要求？",
        ]
        brain = getattr(self._api, "brain", None) if self._api else None
        if brain is None:
            return fallback
        try:
            prompt = (
                "基于以下 Excel profile 和用户目标，生成 5 个用于完善 Excel 报表需求的中文追问。"
                "只返回 JSON：{\"questions\":[...]}\n"
                f"目标：{payload.goal}\nProfile：{json.dumps(profile or {}, ensure_ascii=False)[:6000]}"
            )
            access = getattr(brain, "access", None)
            if not callable(access):
                return fallback
            response = access(prompt)
            if hasattr(response, "__await__"):
                response = await response
            parsed = parse_json_object(str(response))
            questions = parsed.get("questions")
            if isinstance(questions, list) and questions:
                return [str(item) for item in questions[:8]]
        except Exception:
            return fallback
        return fallback

    async def _generate_report_plan(
        self,
        *,
        title: str,
        workbook_id: str | None,
        profile: dict[str, Any] | None,
        brief: dict[str, Any] | None,
    ) -> WorkbookPlan:
        builder = WorkbookPlanBuilder()
        fallback = builder.build_default_plan(
            title=title,
            workbook_id=workbook_id,
            profile=profile,
            brief=brief,
        )
        brain = getattr(self._api, "brain", None) if self._api else None
        access = getattr(brain, "access", None)
        if not callable(access):
            return fallback
        try:
            prompt = (
                "你是 Excel 报表方案设计助手。请基于 brief 和 profile 生成 WorkbookPlan JSON。"
                "只能返回 JSON 对象，不要 markdown。字段必须包含 title, purpose, source_workbook_id,"
                "sheets, operations, formulas, style, audit_expectations。"
                "operations 只能使用 rename_column/cast_type/fill_missing/drop_duplicates/derive_column/"
                "groupby/pivot/sort/filter/write_formula；公式必须以 = 开头。\n"
                f"title: {title}\nworkbook_id: {workbook_id}\n"
                f"brief: {json.dumps(brief or {}, ensure_ascii=False)[:3000]}\n"
                f"profile: {json.dumps(profile or {}, ensure_ascii=False)[:7000]}"
            )
            response = access(prompt)
            if hasattr(response, "__await__"):
                response = await response
            candidate = parse_json_object(str(response))
            candidate.setdefault("title", title)
            candidate.setdefault("source_workbook_id", workbook_id)
            return builder.validate_plan(candidate)
        except Exception:
            return fallback

    async def _load_profile_for_workbook(self, data_dir: Path, workbook_id: str | None) -> dict[str, Any] | None:
        if not workbook_id:
            return None
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.get_workbook(workbook_id)
        if workbook is None or not workbook.profile_path:
            return None
        path = Path(workbook.profile_path)
        if path.name == "import_profile.json":
            profile_path = self._storage_paths()["workbooks"] / workbook_id / "profile.json"
            if profile_path.exists():
                path = profile_path
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    async def _build_report(self, data_dir: Path, project_id: str, payload: BuildReportRequest) -> dict[str, Any]:
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            project = await manager.get_project(project_id)
            workbooks = await manager.list_workbooks(project_id)
            await manager.update_project_safe(project_id, status=ProjectStatus.BUILDING)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        workbook_id = payload.workbook_id or (workbooks[0].id if workbooks else None)
        profile = await self._load_profile_for_workbook(data_dir, workbook_id)
        preview = None
        if workbook_id:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.get_workbook(workbook_id)
            if workbook and workbook.profile_path:
                try:
                    preview = WorkbookImporter(
                        data_dir,
                        workbooks_root=self._storage_paths()["workbooks"],
                    ).preview(workbook.profile_path)
                except Exception:
                    preview = None
        plan = self._load_saved_plan(data_dir, project_id) if payload.plan is None else None
        if plan is None:
            plan = (
                WorkbookPlan.model_validate(payload.plan)
                if payload.plan
                else WorkbookPlanBuilder().build_default_plan(
                    title=project.title,
                    workbook_id=workbook_id,
                    profile=profile,
                    brief=project.report_brief,
                )
            )
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            version = await manager.next_artifact_version(project_id, ArtifactKind.WORKBOOK)
        output_path = self._storage_paths()["exports"] / safe_name(project_id) / f"report_v{version}.xlsx"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            WorkbookBuilder().build(plan=plan, profile=profile, preview=preview, output_path=output_path)
        except WorkbookBuildError as exc:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                await manager.update_project_safe(project_id, status=ProjectStatus.FAILED, metadata={"error": str(exc)})
            raise HTTPException(status_code=424, detail=str(exc)) from exc
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            artifact = await manager.create_artifact(
                project_id=project_id,
                kind=ArtifactKind.WORKBOOK,
                path=str(output_path),
                metadata={"workbook_id": workbook_id, "title": plan.title},
            )
            await manager.update_project_safe(project_id, status=ProjectStatus.GENERATED)
        audit = await self._audit_report(data_dir, project_id, str(output_path), artifact.id, plan=plan)
        await self._broadcast("workbook_generated", {"project_id": project_id, "artifact_id": artifact.id})
        return {"ok": True, "artifact": self._public_artifact(artifact), "audit": audit.get("audit")}

    async def _audit_report(
        self,
        data_dir: Path,
        project_id: str,
        workbook_path: str,
        artifact_id: str | None = None,
        plan: WorkbookPlan | None = None,
    ) -> dict[str, Any]:
        audit_path = self._project_dir(project_id) / "audit.json"
        audit = WorkbookAuditor().audit(workbook_path, audit_path)
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            items = await manager.replace_audit_items(project_id, audit["items"], artifact_id=artifact_id)
            audit_artifact = await manager.create_artifact(
                project_id=project_id,
                kind=ArtifactKind.AUDIT,
                path=str(audit_path),
                metadata={"ok": audit["ok"]},
            )
            await manager.update_project_safe(project_id, status=ProjectStatus.AUDITED)
        if plan is not None:
            WorkbookBuilder().update_audit_log(
                workbook_path=workbook_path,
                plan=plan,
                audit_items=[item.model_dump(mode="json") for item in items],
            )
        await self._broadcast("audit_ready", {"project_id": project_id, "ok": audit["ok"]})
        return {
            "ok": True,
            "audit": self._public_audit(audit),
            "items": [item.model_dump(mode="json") for item in items],
            "artifact": self._public_artifact(audit_artifact),
        }

    def _public_workbook(self, workbook: Any) -> dict[str, Any]:
        data = workbook.model_dump(mode="json")
        for key in ("original_path", "imported_path", "profile_path"):
            data.pop(key, None)
        return data

    def _public_artifact(self, artifact: Any) -> dict[str, Any]:
        data = artifact.model_dump(mode="json")
        path = Path(data.pop("path", ""))
        data["filename"] = path.name
        data["download_url"] = f"/artifacts/{artifact.id}/download"
        return data

    def _public_template(self, template: Any) -> dict[str, Any]:
        data = template.model_dump(mode="json")
        original = Path(data.pop("original_path", ""))
        diagnostic = data.pop("diagnostic_path", None)
        data["filename"] = original.name
        data["has_diagnostic"] = bool(diagnostic)
        return data

    def _public_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        data = dict(audit)
        workbook_path = Path(str(data.pop("workbook_path", "")))
        data["workbook_filename"] = workbook_path.name
        return data

    def _cleanup_project_files(self, data_dir: Path, project_id: str, workbook_ids: list[str]) -> list[str]:
        storage = self._storage_paths()
        candidates = [
            storage["projects"] / safe_name(project_id),
            storage["exports"] / safe_name(project_id),
            *[storage["workbooks"] / safe_name(workbook_id) for workbook_id in workbook_ids],
        ]
        removed: list[str] = []
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                shutil.rmtree(candidate)
                removed.append(str(candidate))
        return removed

    def _project_dir(self, project_id: str) -> Path:
        path = self._storage_paths()["projects"] / safe_name(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _storage_paths(self, settings: Settings | None = None) -> dict[str, Path]:
        assert self._data_dir is not None
        current = settings or self._settings()
        root = Path(current.data_dir or self._data_dir).expanduser()
        paths = {
            "uploads": current.uploads_dir or root / "uploads",
            "workbooks": current.workbooks_dir or root / "workbooks",
            "projects": current.projects_dir or root / "projects",
            "exports": current.export_dir or root / "exports",
            "templates": current.templates_dir or root / "templates",
            "cache": current.cache_dir or root / "cache",
        }
        resolved = {key: Path(path).expanduser().resolve() for key, path in paths.items()}
        for path in resolved.values():
            path.mkdir(parents=True, exist_ok=True)
        return resolved

    def _safe_open_path(self, path: str | None = None) -> Path:
        assert self._data_dir is not None
        if not path:
            return self._storage_paths()["uploads"].parent
        target = Path(path).expanduser().resolve()
        roots = [self._data_dir.resolve(), *self._storage_paths().values()]
        if not any(root == target or root in target.parents for root in roots):
            raise ValueError("Path is outside configured excel-maker directories")
        return target

    def _cleanup_template_files(self, template: Any | None) -> list[str]:
        if template is None:
            return []
        removed: list[str] = []
        for value in (template.original_path, template.diagnostic_path):
            if not value:
                continue
            path = Path(value)
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(str(path))
        return removed

    def _load_saved_plan(self, data_dir: Path, project_id: str) -> WorkbookPlan | None:
        plan_path = self._project_dir(project_id) / "workbook_plan.json"
        if not plan_path.exists():
            return None
        try:
            return WorkbookPlan.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    async def _handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if self._data_dir is None:
            return json.dumps({"ok": False, "error": "excel-maker is not loaded"}, ensure_ascii=False)
        data_dir = self._data_dir
        try:
            if tool_name == "excel_list_projects":
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    projects = await manager.list_projects()
                return json.dumps({"ok": True, "projects": [p.model_dump(mode="json") for p in projects]}, ensure_ascii=False)
            if tool_name == "excel_start_project":
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    project = await manager.create_project(ProjectCreate(**arguments))
                return json.dumps({"ok": True, "project": project.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "excel_import_workbook":
                result = await self._import_workbook(data_dir, ImportWorkbookRequest(**arguments))
                return json.dumps(result, ensure_ascii=False)
            if tool_name == "excel_profile_workbook":
                result = await self._profile_workbook(data_dir, str(arguments.get("workbook_id") or ""))
                return json.dumps(result, ensure_ascii=False)
            if tool_name == "excel_clarify_requirements":
                result = {"ok": True, "questions": await self._clarify_questions(ClarifyRequest(**arguments))}
                return json.dumps(result, ensure_ascii=False)
            if tool_name == "excel_generate_report_plan":
                profile = await self._load_profile_for_workbook(data_dir, arguments.get("workbook_id"))
                plan = await self._generate_report_plan(
                    title=str(arguments.get("title") or "Excel Report"),
                    workbook_id=arguments.get("workbook_id"),
                    profile=profile,
                    brief=arguments.get("brief") or {},
                )
                return json.dumps({"ok": True, "plan": plan.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "excel_generate_formula":
                suggestion = generate_formula(**arguments)
                return json.dumps({"ok": True, "formula": suggestion.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name in {"excel_plan_cleanup", "excel_apply_operations"}:
                plan = WorkbookPlan.model_validate(arguments.get("plan") or arguments)
                result = ExcelOperationExecutor().apply_plan(plan, arguments.get("profile"))
                return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
            if tool_name == "excel_build_workbook":
                build_args = dict(arguments)
                project_id = str(build_args.pop("project_id", "") or "")
                result = await self._build_report(data_dir, project_id, BuildReportRequest(**build_args))
                return json.dumps(result, ensure_ascii=False)
            if tool_name == "excel_audit_workbook":
                result = await self._audit_report(
                    data_dir,
                    str(arguments.get("project_id") or ""),
                    str(arguments.get("workbook_path") or ""),
                    arguments.get("artifact_id"),
                )
                return json.dumps(result, ensure_ascii=False)
            if tool_name == "excel_export_workbook":
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    artifacts = await manager.list_artifacts(str(arguments.get("project_id") or ""))
                return json.dumps({"ok": True, "artifacts": [a.model_dump(mode="json") for a in artifacts]}, ensure_ascii=False)
            if tool_name == "excel_cancel":
                async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                    await manager.update_project_safe(str(arguments.get("project_id") or ""), status=ProjectStatus.CANCELLED)
                return json.dumps({"ok": True}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

    async def _broadcast(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._api is None:
            return
        broadcast = getattr(self._api, "broadcast_ui_event", None)
        if callable(broadcast):
            result = broadcast(event_name, payload)
            if hasattr(result, "__await__"):
                await result

    def _settings_path(self) -> Path:
        assert self._data_dir is not None
        return self._data_dir / "settings.json"

    def _settings(self) -> Settings:
        if self._data_dir is None:
            return Settings()
        if self._api is not None:
            config = self._api.get_config()
            if isinstance(config, dict) and isinstance(config.get(SETTINGS_KEY), dict):
                return Settings(**config[SETTINGS_KEY])
        path = self._settings_path()
        if not path.exists():
            return Settings(
                data_dir=str(self._data_dir),
                uploads_dir=str(self._data_dir / "uploads"),
                workbooks_dir=str(self._data_dir / "workbooks"),
                projects_dir=str(self._data_dir / "projects"),
                export_dir=str(self._data_dir / "exports"),
                templates_dir=str(self._data_dir / "templates"),
                cache_dir=str(self._data_dir / "cache"),
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return Settings(**data)

    def _save_settings(self, settings: Settings) -> None:
        data = settings.model_dump(mode="json")
        if self._api is not None:
            self._api.set_config({SETTINGS_KEY: data})
        self._settings_path().write_text(settings.model_dump_json(indent=2), encoding="utf-8")

    async def on_unload(self) -> None:
        if self._api:
            self._api.log(f"{PLUGIN_ID}: unloaded")


def _tool_definitions() -> list[dict[str, Any]]:
    names = [
        ("excel_start_project", "Create an Excel report workbook project."),
        ("excel_import_workbook", "Import an Excel/CSV workbook for analysis."),
        ("excel_profile_workbook", "Profile workbook sheets, columns, quality, and samples."),
        ("excel_clarify_requirements", "Generate requirement clarification questions from workbook profile."),
        ("excel_generate_report_plan", "Generate a controlled WorkbookPlan for an XLSX report."),
        ("excel_generate_formula", "Generate and explain a common Excel formula."),
        ("excel_plan_cleanup", "Create or validate safe cleanup operations."),
        ("excel_apply_operations", "Apply whitelisted operations without executing arbitrary code."),
        ("excel_build_workbook", "Build a formatted .xlsx report workbook."),
        ("excel_audit_workbook", "Audit workbook formulas, sheets, and quality."),
        ("excel_export_workbook", "List exported workbook artifacts and download metadata."),
        ("excel_list_projects", "List Excel report projects."),
        ("excel_cancel", "Cancel a report project."),
    ]
    return [
        {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
        }
        for name, desc in names
    ]

