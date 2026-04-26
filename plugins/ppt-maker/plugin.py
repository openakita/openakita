"""ppt-maker plugin entry point.

Phase 0 only wires a minimal router and tool registry. Later phases add the
project store, pipeline, table analyzer, template manager, exporter, and UI
routes while preserving this self-contained plugin shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from ppt_audit import PptAudit
from ppt_design import DesignBuilder
from ppt_exporter import PptxExporter, PptxExportError
from ppt_ir import SlideIrBuilder
from ppt_maker_inline.file_utils import (
    dataset_dir,
    project_dir,
    resolve_plugin_data_root,
    safe_name,
    template_dir,
    unique_child,
)
from ppt_maker_inline.python_deps import PythonDepsManager
from ppt_maker_inline.upload_preview import register_upload_preview_routes
from ppt_models import DeckMode, ProjectCreate, ProjectStatus
from ppt_outline import OutlineBuilder
from ppt_pipeline import PptPipeline
from ppt_source_loader import MissingDependencyError, SourceLoader, SourceParseError
from ppt_table_analyzer import TableAnalyzer
from ppt_task_manager import PptTaskManager
from ppt_template_manager import TemplateDiagnosticError, TemplateManager
from pydantic import BaseModel, ConfigDict

from openakita.plugins.api import PluginAPI, PluginBase

PLUGIN_ID = "ppt-maker"


class ParseSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str | None = None


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: DeckMode = DeckMode.TOPIC_TO_DECK
    title: str
    prompt: str = ""
    audience: str = ""
    style: str = "tech_business"
    slide_count: int = 8
    template_id: str | None = None
    dataset_id: str | None = None
    metadata: dict[str, Any] = {}


class DatasetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None
    project_id: str | None = None


class TemplateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None
    category: str | None = None


class TemplateBrandUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_tokens: dict[str, Any]


class OutlineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline: dict[str, Any]
    confirmed: bool = True


class DesignConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: dict[str, Any] | None = None
    confirmed: bool = True


class SlideUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide: dict[str, Any]


class Plugin(PluginBase):
    """OpenAkita plugin entry for guided PPT generation."""

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
                "phase": 1,
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "ppt_maker.db"),
                "pipeline_steps": 10,
            }

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
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.create_project(ProjectCreate(**payload.model_dump()))
            return {"ok": True, "project": project.model_dump(mode="json")}

        @router.get("/projects")
        async def list_projects() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                projects = await manager.list_projects()
            return {"ok": True, "projects": [item.model_dump(mode="json") for item in projects]}

        @router.get("/projects/{project_id}")
        async def get_project(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.get_project(project_id)
                slides = await manager.list_slides(project_id)
                exports = await manager.list_exports(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "ok": True,
                "project": project.model_dump(mode="json"),
                "slides": slides,
                "exports": exports,
            }

        @router.delete("/projects/{project_id}")
        async def delete_project(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                deleted = await manager.delete_project(project_id)
            return {"ok": True, "deleted": deleted}

        @router.post("/projects/{project_id}/cancel")
        async def cancel_project(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                count = await manager.cancel_project_tasks(project_id)
                await manager.update_project_safe(project_id, status=ProjectStatus.CANCELLED)
            await self._broadcast("task_update", {"project_id": project_id, "status": "cancelled"})
            return {"ok": True, "cancelled_tasks": count}

        @router.post("/projects/{project_id}/retry")
        async def retry_project(project_id: str) -> dict[str, Any]:
            result = await PptPipeline(data_root=data_dir, emit=self._broadcast).run(project_id)
            return {"ok": True, "result": result}

        @router.post("/upload")
        async def upload(request: Request) -> dict[str, Any]:
            form = await request.form()
            upload = form.get("file")
            project_id = str(form.get("project_id") or "") or None
            if upload is None or not hasattr(upload, "filename") or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Missing upload field: file")

            filename = safe_name(str(upload.filename or "upload.bin"))
            target = unique_child(data_dir / "uploads", filename)
            content = await upload.read()
            target.write_bytes(content)

            loader = SourceLoader()
            kind = loader.detect_kind(target)
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                source = await manager.create_source(
                    project_id=project_id,
                    kind=kind,
                    filename=filename,
                    path=str(target),
                    metadata={"size": len(content), "preview_url": f"/uploads/{target.name}"},
                )
            return {
                "ok": True,
                "source": source.model_dump(mode="json"),
                "preview_url": f"/uploads/{target.name}",
            }

        @router.post("/sources/parse")
        async def parse_source(payload: ParseSourceRequest) -> dict[str, Any]:
            loader = SourceLoader()
            try:
                parsed = await loader.parse(payload.path, kind=payload.kind)
            except MissingDependencyError as exc:
                raise HTTPException(
                    status_code=424,
                    detail={"error": str(exc), "dependency_group": exc.dependency_group},
                ) from exc
            except SourceParseError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "ok": True,
                "source": {
                    "kind": parsed.kind,
                    "title": parsed.title,
                    "text": parsed.text,
                    "metadata": parsed.metadata,
                },
            }

        @router.post("/datasets")
        async def create_dataset(payload: DatasetCreateRequest) -> dict[str, Any]:
            source_path = Path(payload.path)
            if not source_path.exists() or not source_path.is_file():
                raise HTTPException(status_code=404, detail="Dataset file not found")
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.create_dataset(
                    project_id=payload.project_id,
                    name=payload.name or source_path.stem,
                    original_path=str(source_path),
                    metadata={"kind": SourceLoader().detect_kind(source_path)},
                )
            return {"ok": True, "dataset": dataset.model_dump(mode="json")}

        @router.get("/datasets")
        async def list_datasets() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                datasets = await manager.list_datasets()
            return {"ok": True, "datasets": [item.model_dump(mode="json") for item in datasets]}

        @router.get("/datasets/{dataset_id}")
        async def get_dataset(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            return {"ok": True, "dataset": dataset.model_dump(mode="json")}

        @router.post("/datasets/{dataset_id}/profile")
        async def profile_dataset(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
                if dataset is None:
                    raise HTTPException(status_code=404, detail="Dataset not found")
                try:
                    analysis = TableAnalyzer().analyze_to_files(
                        dataset.original_path,
                        dataset_dir(data_dir, dataset_id),
                    )
                except MissingDependencyError as exc:
                    raise HTTPException(
                        status_code=424,
                        detail={"error": str(exc), "dependency_group": exc.dependency_group},
                    ) from exc
                except SourceParseError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                dataset = await manager.update_dataset_safe(
                    dataset_id,
                    status="profiled",
                    profile_path=analysis["paths"]["profile_path"],
                    insights_path=analysis["paths"]["insights_path"],
                    chart_specs_path=analysis["paths"]["chart_specs_path"],
                )
            return {
                "ok": True,
                "dataset": dataset.model_dump(mode="json") if dataset else None,
                "profile": analysis["profile"],
            }

        @router.post("/datasets/{dataset_id}/insights")
        async def dataset_insights(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None or not dataset.insights_path:
                raise HTTPException(status_code=404, detail="Dataset insights not found")
            insights = json.loads(Path(dataset.insights_path).read_text(encoding="utf-8"))
            return {"ok": True, "insights": insights}

        @router.post("/datasets/{dataset_id}/chart-specs")
        async def dataset_chart_specs(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None or not dataset.chart_specs_path:
                raise HTTPException(status_code=404, detail="Dataset chart specs not found")
            chart_specs = json.loads(Path(dataset.chart_specs_path).read_text(encoding="utf-8"))
            return {"ok": True, "chart_specs": chart_specs}

        @router.post("/templates/upload")
        async def upload_template(payload: TemplateUploadRequest) -> dict[str, Any]:
            source_path = Path(payload.path)
            if not source_path.exists() or not source_path.is_file():
                raise HTTPException(status_code=404, detail="Template file not found")
            if source_path.suffix.lower() != ".pptx":
                raise HTTPException(status_code=400, detail="Template must be a .pptx file")
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.create_template(
                    name=payload.name or source_path.stem,
                    category=payload.category,
                    original_path=str(source_path),
                )
            return {"ok": True, "template": template.model_dump(mode="json")}

        @router.get("/templates")
        async def list_templates() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                templates = await manager.list_templates()
            return {
                "ok": True,
                "builtin": TemplateManager().builtin_templates(),
                "templates": [item.model_dump(mode="json") for item in templates],
            }

        @router.get("/templates/{template_id}")
        async def get_template(template_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.get_template(template_id)
            if template is None:
                raise HTTPException(status_code=404, detail="Template not found")
            return {"ok": True, "template": template.model_dump(mode="json")}

        @router.post("/templates/{template_id}/diagnose")
        async def diagnose_template(template_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.get_template(template_id)
                if template is None or not template.original_path:
                    raise HTTPException(status_code=404, detail="Template not found")
                try:
                    diagnosis = TemplateManager().diagnose_to_files(
                        template.original_path,
                        template_dir(data_dir, template_id),
                    )
                except TemplateDiagnosticError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                template = await manager.update_template_safe(
                    template_id,
                    status="diagnosed",
                    profile_path=diagnosis["paths"]["profile_path"],
                    brand_tokens_path=diagnosis["paths"]["brand_tokens_path"],
                    layout_map_path=diagnosis["paths"]["layout_map_path"],
                )
            return {
                "ok": True,
                "template": template.model_dump(mode="json") if template else None,
                "template_profile": diagnosis["template_profile"],
                "brand_tokens": diagnosis["brand_tokens"],
                "layout_map": diagnosis["layout_map"],
            }

        @router.put("/templates/{template_id}/brand")
        async def update_template_brand(
            template_id: str,
            payload: TemplateBrandUpdateRequest,
        ) -> dict[str, Any]:
            template_path = template_dir(data_dir, template_id)
            brand_path = template_path / "brand_tokens.json"
            brand_path.write_text(
                json.dumps(payload.brand_tokens, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.update_template_safe(
                    template_id,
                    brand_tokens_path=str(brand_path),
                )
            if template is None:
                raise HTTPException(status_code=404, detail="Template not found")
            return {"ok": True, "template": template.model_dump(mode="json")}

        @router.delete("/templates/{template_id}")
        async def delete_template(template_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                deleted = await manager.delete_template(template_id)
            return {"ok": True, "deleted": deleted}

        @router.post("/projects/{project_id}/outline")
        async def generate_outline(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.get_project(project_id)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")
                dataset = await manager.get_dataset(project.dataset_id) if project.dataset_id else None
                template = await manager.get_template(project.template_id) if project.template_id else None
                table_insights = _read_json_if_exists(dataset.insights_path if dataset else None)
                template_profile = _read_json_if_exists(template.profile_path if template else None)
                outline = OutlineBuilder().build(
                    mode=project.mode,
                    title=project.title,
                    slide_count=project.slide_count,
                    audience=project.audience,
                    requirements={"prompt": project.prompt, "style": project.style},
                    table_insights=table_insights,
                    template_profile=template_profile,
                )
                OutlineBuilder().save(outline, project_dir(data_dir, project_id))
                stored = await manager.create_outline(project_id=project_id, outline=outline)
                await manager.update_project_safe(project_id, status="outline_ready")
            return {"ok": True, "outline": outline, "record": stored}

        @router.put("/projects/{project_id}/outline")
        async def confirm_outline(project_id: str, payload: OutlineUpdateRequest) -> dict[str, Any]:
            outline = OutlineBuilder().confirm(payload.outline) if payload.confirmed else payload.outline
            OutlineBuilder().save(outline, project_dir(data_dir, project_id))
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                stored = await manager.create_outline(
                    project_id=project_id,
                    outline=outline,
                    confirmed=payload.confirmed,
                )
                await manager.update_project_safe(
                    project_id,
                    status="outline_confirmed" if payload.confirmed else "outline_ready",
                )
            return {"ok": True, "outline": outline, "record": stored}

        @router.post("/projects/{project_id}/design")
        async def generate_design(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.get_project(project_id)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")
                latest_outline = await manager.latest_outline(project_id)
                if latest_outline is None:
                    raise HTTPException(status_code=409, detail="Generate outline first")
                brand_tokens, layout_map = await _template_design_inputs(manager, project.template_id)
                design = DesignBuilder().build(
                    outline=latest_outline["outline"],
                    brand_tokens=brand_tokens,
                    layout_map=layout_map,
                )
                paths = DesignBuilder().save(design, project_dir(data_dir, project_id))
                stored = await manager.create_design_spec(
                    project_id=project_id,
                    design_markdown=design["design_spec_markdown"],
                    spec_lock=design["spec_lock"],
                )
                await manager.update_project_safe(project_id, status="design_ready")
            return {"ok": True, "design": design, "paths": paths, "record": stored}

        @router.put("/projects/{project_id}/design/confirm")
        async def confirm_design(project_id: str, payload: DesignConfirmRequest) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                current = _normalize_design(payload.design or await manager.latest_design_spec(project_id))
                if current is None:
                    raise HTTPException(status_code=409, detail="Generate design first")
                design = DesignBuilder().confirm(current) if payload.confirmed else current
                paths = DesignBuilder().save(design, project_dir(data_dir, project_id))
                stored = await manager.create_design_spec(
                    project_id=project_id,
                    design_markdown=design["design_spec_markdown"],
                    spec_lock=design["spec_lock"],
                    confirmed=payload.confirmed,
                )
                await manager.update_project_safe(
                    project_id,
                    status="design_confirmed" if payload.confirmed else "design_ready",
                )
            return {"ok": True, "design": design, "paths": paths, "record": stored}

        @router.post("/projects/{project_id}/slides")
        async def generate_slides(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.get_project(project_id)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")
                outline = await manager.latest_outline(project_id)
                design = _normalize_design(await manager.latest_design_spec(project_id))
                if outline is None or design is None:
                    raise HTTPException(status_code=409, detail="Confirm outline and design first")
                dataset = await manager.get_dataset(project.dataset_id) if project.dataset_id else None
                template = await manager.get_template(project.template_id) if project.template_id else None
                table_insights = _read_json_if_exists(dataset.insights_path if dataset else None)
                chart_specs = _read_json_if_exists(dataset.chart_specs_path if dataset else None) or []
                layout_map = _read_json_if_exists(template.layout_map_path if template else None)
                ir = SlideIrBuilder().build(
                    outline=outline["outline"],
                    spec_lock=design["spec_lock"],
                    table_insights=table_insights,
                    chart_specs=chart_specs,
                    template_id=project.template_id,
                    layout_map=layout_map,
                )
                path = SlideIrBuilder().save(ir, project_dir(data_dir, project_id))
                slides = await manager.replace_slides(project_id, ir["slides"])
            return {"ok": True, "slides_ir": ir, "path": str(path), "slides": slides}

        @router.put("/projects/{project_id}/slides/{slide_id}")
        async def update_slide(
            project_id: str,
            slide_id: str,
            payload: SlideUpdateRequest,
        ) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                updated = await manager.update_slide_safe(project_id, slide_id, payload.slide)
            if updated is None:
                raise HTTPException(status_code=404, detail="Slide not found")
            return {"ok": True, "slide": updated}

        @router.post("/projects/{project_id}/audit")
        async def audit_project(project_id: str) -> dict[str, Any]:
            slides_ir = _project_json(data_dir, project_id, "slides_ir.json")
            if slides_ir is None:
                raise HTTPException(status_code=409, detail="Generate slides first")
            report = PptAudit().run(slides_ir)
            path = PptAudit().save(report, project_dir(data_dir, project_id))
            return {"ok": True, "audit": report, "path": str(path)}

        @router.get("/projects/{project_id}/audit")
        async def get_audit(project_id: str) -> dict[str, Any]:
            report = _project_json(data_dir, project_id, "audit_report.json")
            if report is None:
                raise HTTPException(status_code=404, detail="Audit report not found")
            return {"ok": True, "audit": report}

        @router.post("/projects/{project_id}/export")
        async def export_project(project_id: str) -> dict[str, Any]:
            slides_ir = _project_json(data_dir, project_id, "slides_ir.json")
            if slides_ir is None:
                raise HTTPException(status_code=409, detail="Generate slides first")
            out_dir = project_dir(data_dir, project_id) / "exports"
            output_path = out_dir / f"{project_id}.pptx"
            try:
                export_path = PptxExporter().export(slides_ir, output_path)
            except PptxExportError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            audit = PptAudit().run(slides_ir, export_path)
            PptAudit().save(audit, project_dir(data_dir, project_id))
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                export = await manager.create_export(
                    project_id=project_id,
                    path=str(export_path),
                    metadata={"audit_ok": audit["ok"], "slide_count": len(slides_ir.get("slides", []))},
                )
                await manager.update_project_safe(project_id, status="ready")
            return {"ok": True, "export": export, "audit": audit}

        @router.get("/exports/{export_id}/download")
        async def download_export(export_id: str) -> FileResponse:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                export = await manager.get_export(export_id)
            if export is None or not Path(export["path"]).exists():
                raise HTTPException(status_code=404, detail="Export not found")
            return FileResponse(export["path"], filename=Path(export["path"]).name)

        api.register_api_routes(router)
        api.register_tools(_tool_definitions(), self._handle_tool)
        api.log(f"{PLUGIN_ID}: loaded")

    async def _handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if self._data_dir is None:
            return json.dumps({"ok": False, "error": "ppt-maker is not loaded"}, ensure_ascii=False)
        data_dir = self._data_dir
        async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
            if tool_name == "ppt_list_projects":
                projects = await manager.list_projects()
                return json.dumps(
                    {"ok": True, "projects": [item.model_dump(mode="json") for item in projects]},
                    ensure_ascii=False,
                )
            if tool_name == "ppt_start_project":
                project = await manager.create_project(ProjectCreate(**arguments))
                return json.dumps({"ok": True, "project": project.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "ppt_cancel":
                project_id = str(arguments.get("project_id") or "")
                count = await manager.cancel_project_tasks(project_id)
                await manager.update_project_safe(project_id, status=ProjectStatus.CANCELLED)
                return json.dumps({"ok": True, "cancelled_tasks": count}, ensure_ascii=False)
        if tool_name in {"ppt_generate_deck", "ppt_export"}:
            project_id = str(arguments.get("project_id") or "")
            result = await PptPipeline(data_root=data_dir, emit=self._broadcast).run(project_id)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"{tool_name} is registered but not wired until a later phase."},
            ensure_ascii=False,
        )

    async def _broadcast(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._api is None:
            return
        broadcast = getattr(self._api, "broadcast_ui_event", None)
        if callable(broadcast):
            result = broadcast(event_name, payload)
            if hasattr(result, "__await__"):
                await result

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


def _read_json_if_exists(path: str | None) -> Any:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


def _project_json(data_dir: Path, project_id: str, filename: str) -> Any:
    return _read_json_if_exists(str(project_dir(data_dir, project_id) / filename))


def _normalize_design(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if "design_markdown" in value and "design_spec_markdown" not in value:
        value = dict(value)
        value["design_spec_markdown"] = value.pop("design_markdown")
    return value


async def _template_design_inputs(
    manager: PptTaskManager,
    template_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not template_id:
        return None, None
    template = await manager.get_template(template_id)
    if template is None:
        return None, None
    brand_tokens = _read_json_if_exists(template.brand_tokens_path)
    layout_map = _read_json_if_exists(template.layout_map_path)
    return brand_tokens, layout_map

