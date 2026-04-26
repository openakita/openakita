"""excel-maker plugin entry point.

The plugin focuses on producing auditable XLSX report workbooks. LLM calls may
help clarify requirements and draft WorkbookPlan JSON, but binary Excel output
is generated only by deterministic Python code.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from excel_auditor import WorkbookAuditor
from excel_executor import ExcelOperationExecutor, OperationExecutionError
from excel_formula import generate_formula
from excel_importer import WorkbookImporter, WorkbookImportError
from excel_maker_inline.file_utils import (
    copy_into,
    ensure_child,
    export_dir,
    project_dir,
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


class ProjectCreateRequest(ProjectCreate):
    pass


class ImportWorkbookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    project_id: Optional[str] = None
    name: Optional[str] = None


class ProfileWorkbookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class ClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = None
    workbook_id: Optional[str] = None
    goal: str = ""
    profile: Optional[dict[str, Any]] = None


class ReportPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = None
    workbook_id: Optional[str] = None
    brief: dict[str, Any] = Field(default_factory=dict)
    profile: Optional[dict[str, Any]] = None


class FormulaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "sumifs"
    range_ref: str
    criteria_ref: str = ""
    criteria: str = ""


class OperationsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    profile: Optional[dict[str, Any]] = None


class BuildReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_id: Optional[str] = None
    plan: Optional[dict[str, Any]] = None


class TemplateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: Optional[str] = None


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Optional[str] = None
    export_dir: Optional[str] = None
    default_style: Optional[str] = None
    brand_color: Optional[str] = None
    font_family: Optional[str] = None
    number_format: Optional[str] = None


class StorageOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Optional[str] = None


class StorageMkdirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: Optional[str] = None
    name: str


class Plugin(PluginBase):
    """OpenAkita plugin entry for Excel report workbook generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._deps: PythonDepsManager | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = resolve_plugin_data_root(api.get_data_dir() or Path.cwd() / "data")
        self._data_dir = data_dir
        self._deps = PythonDepsManager(data_dir)
        router = APIRouter()
        register_upload_preview_routes(router, data_dir / "uploads", prefix="/uploads")

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
            return {"ok": True, "settings": self._settings().model_dump(mode="json")}

        @router.put("/settings")
        async def update_settings(payload: SettingsUpdateRequest) -> dict[str, Any]:
            settings = self._settings()
            values = settings.model_dump()
            for key, value in payload.model_dump(exclude_none=True).items():
                if key in {"data_dir", "export_dir"} and value:
                    write_probe(value)
                values[key] = value
            values["updated_at"] = __import__("time").time()
            self._save_settings(Settings(**values))
            return {"ok": True, "settings": values, "reload_recommended": "data_dir" in payload.model_dump(exclude_none=True)}

        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            return {"ok": True, "stats": collect_storage_stats(data_dir)}

        @router.post("/storage/open-folder")
        async def open_folder(payload: StorageOpenRequest) -> dict[str, Any]:
            target = Path(payload.path).expanduser().resolve() if payload.path else data_dir
            if not target.exists():
                raise HTTPException(status_code=404, detail="Folder not found")
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            return {"ok": True, "path": str(target)}

        @router.get("/storage/list-dir")
        async def list_dir(path: str | None = None) -> dict[str, Any]:
            target = ensure_child(data_dir, path or data_dir)
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
            parent = ensure_child(data_dir, payload.parent or data_dir)
            target = ensure_child(data_dir, parent / safe_name(payload.name, "folder"))
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}

        @router.post("/cleanup")
        async def cleanup() -> dict[str, Any]:
            cache = data_dir / "cache"
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
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "ok": True,
                "project": project.model_dump(mode="json"),
                "workbooks": [item.model_dump(mode="json") for item in workbooks],
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
                "audit_items": [item.model_dump(mode="json") for item in audit_items],
            }

        @router.delete("/projects/{project_id}")
        async def delete_project(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                deleted = await manager.delete_project(project_id)
            return {"ok": True, "deleted": deleted}

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
            target = unique_child(data_dir / "uploads", filename)
            content = await upload_file.read()
            target.write_bytes(content)
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.create_workbook(
                    project_id=project_id,
                    filename=filename,
                    original_path=str(target),
                    metadata={"size": len(content), "preview_url": f"/uploads/{target.name}"},
                )
            return {"ok": True, "workbook": workbook.model_dump(mode="json"), "preview_url": f"/uploads/{target.name}"}

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
                "workbook": workbook.model_dump(mode="json"),
                "sheets": [item.model_dump(mode="json") for item in sheets],
            }

        @router.get("/workbooks/{workbook_id}/preview")
        async def preview_workbook(workbook_id: str, sheet: str | None = None) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                workbook = await manager.get_workbook(workbook_id)
            if workbook is None or not workbook.profile_path:
                raise HTTPException(status_code=404, detail="Workbook preview not found")
            return {"ok": True, "preview": WorkbookImporter(data_dir).preview(workbook.profile_path, sheet)}

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
            plan = WorkbookPlanBuilder().build_default_plan(
                title=title,
                workbook_id=payload.workbook_id,
                profile=profile,
                brief=payload.brief,
            )
            if payload.project_id:
                plan_path = project_dir(data_dir, payload.project_id) / "workbook_plan.json"
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
            return {"ok": True, "result": result}

        @router.post("/reports/{project_id}/build")
        async def build_report(project_id: str, payload: BuildReportRequest) -> dict[str, Any]:
            return await self._build_report(data_dir, project_id, payload)

        @router.get("/reports/{project_id}/artifacts")
        async def report_artifacts(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifacts = await manager.list_artifacts(project_id)
            return {"ok": True, "artifacts": [item.model_dump(mode="json") for item in artifacts]}

        @router.post("/reports/{project_id}/audit")
        async def audit_report(project_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifacts = await manager.list_artifacts(project_id)
            workbook_artifact = next((item for item in artifacts if item.kind == ArtifactKind.WORKBOOK), None)
            if workbook_artifact is None:
                raise HTTPException(status_code=404, detail="Workbook artifact not found")
            return await self._audit_report(data_dir, project_id, workbook_artifact.path, workbook_artifact.id)

        @router.get("/artifacts/{artifact_id}/download")
        async def download_artifact(artifact_id: str) -> FileResponse:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                artifact = await manager.get_artifact(artifact_id)
            if artifact is None or not Path(artifact.path).is_file():
                raise HTTPException(status_code=404, detail="Artifact not found")
            return FileResponse(artifact.path, filename=Path(artifact.path).name)

        @router.get("/templates")
        async def list_templates() -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                templates = await manager.list_templates()
            return {"ok": True, "templates": [item.model_dump(mode="json") for item in templates]}

        @router.post("/templates")
        async def upload_template(payload: TemplateUploadRequest) -> dict[str, Any]:
            copied = copy_into(payload.path, data_dir / "templates", payload.name or Path(payload.path).name)
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                template = await manager.create_template(name=payload.name or copied.stem, original_path=str(copied))
            return {"ok": True, "template": template.model_dump(mode="json")}

        @router.post("/templates/{template_id}/diagnose")
        async def diagnose_template(template_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                template = await manager.get_template(template_id)
            if template is None:
                raise HTTPException(status_code=404, detail="Template not found")
            try:
                out_path = data_dir / "templates" / f"{template_id}_diagnostic.json"
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
            return {"ok": True, "template": updated.model_dump(mode="json") if updated else None, "diagnostic": diagnostic}

        @router.delete("/templates/{template_id}")
        async def delete_template(template_id: str) -> dict[str, Any]:
            async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
                deleted = await manager.delete_template(template_id)
            return {"ok": True, "deleted": deleted}

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _import_workbook(self, data_dir: Path, payload: ImportWorkbookRequest) -> dict[str, Any]:
        source_path = Path(payload.path).expanduser().resolve()
        if not source_path.is_file():
            raise HTTPException(status_code=404, detail="Workbook file not found")
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.create_workbook(
                project_id=payload.project_id,
                filename=payload.name or source_path.name,
                original_path=str(source_path),
                metadata={"imported_from": str(source_path)},
            )
        try:
            imported = WorkbookImporter(data_dir).import_file(source_path, workbook.id)
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
            "workbook": workbook.model_dump(mode="json") if workbook else None,
            "sheets": [item.model_dump(mode="json") for item in sheets],
            "preview": imported.preview,
            "warnings": imported.warnings,
        }

    async def _profile_workbook(self, data_dir: Path, workbook_id: str, *, force: bool = False) -> dict[str, Any]:
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.get_workbook(workbook_id)
        if workbook is None or not workbook.profile_path:
            raise HTTPException(status_code=404, detail="Workbook import profile not found")
        profile_path = data_dir / "workbooks" / workbook_id / "profile.json"
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
        return {"ok": True, "profile": profile, "profile_path": str(profile_path)}

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

    async def _load_profile_for_workbook(self, data_dir: Path, workbook_id: str | None) -> dict[str, Any] | None:
        if not workbook_id:
            return None
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            workbook = await manager.get_workbook(workbook_id)
        if workbook is None or not workbook.profile_path:
            return None
        path = Path(workbook.profile_path)
        if path.name == "import_profile.json":
            profile_path = data_dir / "workbooks" / workbook_id / "profile.json"
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
                    preview = WorkbookImporter(data_dir).preview(workbook.profile_path)
                except Exception:
                    preview = None
        plan = WorkbookPlan.model_validate(payload.plan) if payload.plan else WorkbookPlanBuilder().build_default_plan(
            title=project.title,
            workbook_id=workbook_id,
            profile=profile,
            brief=project.report_brief,
        )
        async with ExcelTaskManager(data_dir / "excel_maker.db") as manager:
            version = await manager.next_artifact_version(project_id, ArtifactKind.WORKBOOK)
        output_path = export_dir(data_dir, project_id) / f"report_v{version}.xlsx"
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
        audit = await self._audit_report(data_dir, project_id, str(output_path), artifact.id)
        await self._broadcast("workbook_generated", {"project_id": project_id, "artifact_id": artifact.id})
        return {"ok": True, "artifact": artifact.model_dump(mode="json"), "audit": audit.get("audit")}

    async def _audit_report(
        self, data_dir: Path, project_id: str, workbook_path: str, artifact_id: str | None = None
    ) -> dict[str, Any]:
        audit_path = project_dir(data_dir, project_id) / "audit.json"
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
        await self._broadcast("audit_ready", {"project_id": project_id, "ok": audit["ok"]})
        return {
            "ok": True,
            "audit": audit,
            "items": [item.model_dump(mode="json") for item in items],
            "artifact": audit_artifact.model_dump(mode="json"),
        }

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
                plan = WorkbookPlanBuilder().build_default_plan(
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
        path = self._settings_path()
        if not path.exists():
            return Settings(data_dir=str(self._data_dir), export_dir=str(self._data_dir / "exports"))
        data = json.loads(path.read_text(encoding="utf-8"))
        return Settings(**data)

    def _save_settings(self, settings: Settings) -> None:
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

