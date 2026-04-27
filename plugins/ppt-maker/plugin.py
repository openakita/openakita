"""ppt-maker plugin entry point.

Phase 0 only wires a minimal router and tool registry. Later phases add the
project store, pipeline, table analyzer, template manager, exporter, and UI
routes while preserving this self-contained plugin shape.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from ppt_activity_log import PptActivityLogger
from ppt_audit import PptAudit
from ppt_brain_adapter import PptBrainAdapter
from ppt_design import DesignBuilder
from ppt_exporter import PptxExporter, PptxExportError
from ppt_ir import SlideIrBuilder
from ppt_maker_inline.file_utils import (
    project_dir,
    resolve_plugin_data_root,
    safe_name,
    unique_child,
)
from ppt_maker_inline.python_deps import PythonDepsManager
from ppt_maker_inline.storage_stats import collect_storage_stats
from ppt_maker_inline.upload_preview import register_upload_preview_routes
from ppt_models import DeckMode, ProjectCreate, ProjectStatus, SourceStatus
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
    collection_name: str | None = None


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


class StorageOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = "root"
    path: str = ""


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: dict[str, str] = {}


class DeleteAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delete_files: bool = False


class Plugin(PluginBase):
    """OpenAkita plugin entry for guided PPT generation."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._deps: PythonDepsManager | None = None
        self._brain_adapter: PptBrainAdapter | None = None
        self._asset_provider: Any = None
        self._activity_logger: PptActivityLogger | None = None

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = resolve_plugin_data_root(api.get_data_dir() or Path.cwd() / "data")
        self._data_dir = data_dir
        self._deps = PythonDepsManager(data_dir)
        self._brain_adapter = PptBrainAdapter(api, data_root=data_dir)
        self._activity_logger = PptActivityLogger(data_root=data_dir)
        self._brain_adapter.bind_activity_logger(
            self._activity_logger,
            emit=lambda event: self._broadcast("ppt_activity", event),
        )
        try:
            from ppt_asset_provider import PptAssetProvider  # type: ignore

            self._asset_provider = PptAssetProvider(
                settings=_load_settings(data_dir),
                data_root=data_dir,
            )
        except Exception:  # noqa: BLE001
            self._asset_provider = None

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

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            settings = _load_settings(data_dir)
            return {"ok": True, "settings": settings, "resolved": _resolved_storage_paths(data_dir, settings)}

        @router.put("/settings")
        async def update_settings(payload: SettingsUpdateRequest) -> dict[str, Any]:
            settings = _load_settings(data_dir)
            allowed = set(_default_settings())
            for key, value in payload.updates.items():
                if key not in allowed:
                    raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
                settings[key] = str(value)
            _save_settings(data_dir, settings)
            return {"ok": True, "settings": settings, "resolved": _resolved_storage_paths(data_dir, settings)}

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

        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            folders = _storage_folders(data_dir, _load_settings(data_dir))
            stats = {}
            for key, path in folders.items():
                raw = collect_storage_stats(path)
                stats[key] = {
                    "path": str(path),
                    "bytes": raw["bytes"],
                    "size_mb": round(raw["bytes"] / 1024 / 1024, 2),
                    "file_count": raw["files"],
                    "dir_count": raw["dirs"],
                }
            return {"ok": True, "data_dir": str(data_dir), "stats": stats}

        @router.post("/storage/open-folder")
        async def open_storage_folder(payload: StorageOpenRequest) -> dict[str, Any]:
            folders = _storage_folders(data_dir, _load_settings(data_dir))
            path = Path(payload.path).expanduser() if payload.path else folders.get(payload.key)
            if path is None:
                raise HTTPException(status_code=400, detail=f"Unknown storage key: {payload.key}")
            path.mkdir(parents=True, exist_ok=True)
            try:
                _open_folder(path)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Cannot open folder: {exc}") from exc
            return {"ok": True, "path": str(path)}

        @router.get("/storage/list-dir")
        async def list_storage_dir(path: str = "") -> dict[str, Any]:
            return _list_dir_payload(path)

        @router.post("/storage/mkdir")
        async def make_storage_dir(body: dict[str, Any]) -> dict[str, Any]:
            parent = str(body.get("parent") or "").strip()
            name = str(body.get("name") or "").strip()
            if not parent or not name:
                raise HTTPException(status_code=400, detail="Missing parent or name")
            if "/" in name or "\\" in name or name in {".", ".."}:
                raise HTTPException(status_code=400, detail="Invalid folder name")
            parent_path = Path(parent).expanduser().resolve(strict=False)
            if not parent_path.is_dir():
                raise HTTPException(status_code=400, detail="Parent is not a directory")
            target = parent_path / safe_name(name, fallback="folder")
            try:
                target.mkdir(parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail="Folder already exists") from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"ok": True, "path": str(target)}

        @router.post("/projects")
        async def create_project(payload: ProjectCreateRequest) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.create_project(ProjectCreate(**payload.model_dump()))
            return {"ok": True, "project": project.model_dump(mode="json")}

        @router.get("/projects")
        async def list_projects() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                projects = await manager.list_projects()
                payload = []
                for project in projects:
                    item = project.model_dump(mode="json")
                    item["exports"] = await manager.list_exports(project.id)
                    payload.append(item)
            return {"ok": True, "projects": payload}

        @router.get("/projects/{project_id}")
        async def get_project(project_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                project = await manager.get_project(project_id)
                slides = await manager.list_slides(project_id)
                exports = await manager.list_exports(project_id)
                outline = await manager.latest_outline(project_id)
                design = await manager.latest_design_spec(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "ok": True,
                "project": project.model_dump(mode="json"),
                "outline": outline,
                "design": design,
                "slides": slides,
                "exports": exports,
            }

        @router.get("/projects/{project_id}/activity")
        async def get_project_activity(
            project_id: str, since: float | None = None, limit: int = 200
        ) -> dict[str, Any]:
            assert self._activity_logger is not None
            try:
                limit_clamped = max(1, min(int(limit), 1000))
            except (TypeError, ValueError):
                limit_clamped = 200
            events = self._activity_logger.read(
                project_id, since=since, limit=limit_clamped
            )
            return {
                "ok": True,
                "project_id": project_id,
                "events": events,
                "count": len(events),
                "latest_ts": events[-1]["ts"] if events else None,
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
            result = await self._make_pipeline(data_dir).run(project_id)
            return {"ok": True, "result": result}

        @router.post("/upload")
        async def upload(request: Request) -> dict[str, Any]:
            form = await request.form()
            upload = form.get("file")
            project_id = str(form.get("project_id") or "") or None
            collection_name = str(form.get("collection_name") or "").strip()
            if upload is None or not hasattr(upload, "filename") or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Missing upload field: file")

            settings = _load_settings(data_dir)
            filename = _format_upload_filename(str(upload.filename or "upload.bin"), settings)
            target_dir = _upload_target_dir(data_dir, settings, filename, collection_name=collection_name)
            target = unique_child(target_dir, filename)
            content = await upload.read()
            target.write_bytes(content)

            loader = SourceLoader()
            kind = loader.detect_kind(target)
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                source = await manager.create_source(
                    project_id=project_id,
                    kind=kind,
                    filename=target.name,
                    path=str(target),
                    metadata={
                        "size": len(content),
                        "original_filename": str(upload.filename or ""),
                        "collection": collection_name or target.parent.name,
                        "storage_dir": str(target.parent),
                        "preview_url": f"/uploads/{target.name}",
                    },
                )
            return {
                "ok": True,
                "source": source.model_dump(mode="json"),
                "preview_url": f"/uploads/{target.name}",
            }

        @router.get("/sources")
        async def list_sources() -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                sources = await manager.list_sources()
            return {"ok": True, "sources": [item.model_dump(mode="json") for item in sources]}

        @router.delete("/sources/{source_id}")
        async def delete_source(
            source_id: str,
            payload: DeleteAssetRequest | None = None,
        ) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                source = await manager.get_source(source_id)
                if source is None:
                    raise HTTPException(status_code=404, detail="Source not found")
                deleted = await manager.delete_source(source_id)
            metadata = source.metadata or {}
            paths_to_clean: list[str | None] = [
                source.path,
                metadata.get("parsed_path"),
                metadata.get("parsed_text_path"),
            ]
            file_result = _delete_stored_paths(
                data_dir,
                paths_to_clean,
                enabled=bool(payload and payload.delete_files),
            )
            return {"ok": True, "deleted": deleted, **file_result}

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

        @router.post("/sources/{source_id}/parse")
        async def parse_source_by_id(source_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                source = await manager.get_source(source_id)
                if source is None:
                    raise HTTPException(status_code=404, detail="Source not found")
                loader = SourceLoader()
                try:
                    parsed = await loader.parse(source.path, kind=source.kind)
                except MissingDependencyError as exc:
                    raise HTTPException(
                        status_code=424,
                        detail={"error": str(exc), "dependency_group": exc.dependency_group},
                    ) from exc
                except SourceParseError as exc:
                    await manager.update_source_safe(source_id, status=SourceStatus.FAILED)
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                settings = _load_settings(data_dir)
                analysis_dir = _analysis_dir(data_dir, settings, "sources_analysis", source_id)
                analysis_dir.mkdir(parents=True, exist_ok=True)
                full_text = parsed.text or ""
                parsed_payload = {
                    "kind": parsed.kind,
                    "title": parsed.title,
                    "text": full_text,
                    "metadata": parsed.metadata,
                    "source_id": source_id,
                    "source_path": source.path,
                }
                parsed_json_path = analysis_dir / "parsed.json"
                parsed_text_path = analysis_dir / "parsed_text.txt"
                parsed_json_path.write_text(
                    json.dumps(parsed_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                parsed_text_path.write_text(full_text, encoding="utf-8")
                merged_metadata = {
                    **(source.metadata or {}),
                    "parsed_path": str(parsed_json_path),
                    "parsed_text_path": str(parsed_text_path),
                    "parsed": {
                        "title": parsed.title,
                        "kind": parsed.kind,
                        "text_preview": full_text[:1200],
                        "text_length": len(full_text),
                        "metadata": parsed.metadata,
                    },
                }
                updated = await manager.update_source_safe(
                    source_id,
                    status=SourceStatus.PARSED,
                    metadata=merged_metadata,
                )
            return {
                "ok": True,
                "source": (updated or source).model_dump(mode="json"),
                "parsed": {
                    "kind": parsed.kind,
                    "title": parsed.title,
                    "text": parsed.text,
                    "metadata": parsed.metadata,
                    "parsed_path": str(parsed_json_path),
                    "parsed_text_path": str(parsed_text_path),
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
                    metadata={
                        "kind": SourceLoader().detect_kind(source_path),
                        "collection": payload.collection_name or payload.name or source_path.stem,
                    },
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

        @router.delete("/datasets/{dataset_id}")
        async def delete_dataset(
            dataset_id: str,
            payload: DeleteAssetRequest | None = None,
        ) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
                if dataset is None:
                    raise HTTPException(status_code=404, detail="Dataset not found")
                deleted = await manager.delete_dataset(dataset_id)
            file_result = _delete_stored_paths(
                data_dir,
                [
                    dataset.original_path,
                    dataset.profile_path,
                    dataset.insights_path,
                    dataset.chart_specs_path,
                ],
                enabled=bool(payload and payload.delete_files),
            )
            return {"ok": True, "deleted": deleted, **file_result}

        @router.get("/datasets/{dataset_id}/analysis")
        async def dataset_analysis(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            return {
                "ok": True,
                "dataset": dataset.model_dump(mode="json"),
                "profile": _read_json_if_exists(dataset.profile_path),
                "insights": _read_json_if_exists(dataset.insights_path),
                "chart_specs": _read_json_if_exists(dataset.chart_specs_path),
            }

        @router.post("/datasets/{dataset_id}/profile")
        async def profile_dataset(dataset_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                dataset = await manager.get_dataset(dataset_id)
                if dataset is None:
                    raise HTTPException(status_code=404, detail="Dataset not found")
                try:
                    settings = _load_settings(data_dir)
                    analysis = TableAnalyzer().analyze_to_files(
                        dataset.original_path,
                        _analysis_dir(data_dir, settings, "datasets", dataset_id),
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

        @router.get("/templates/{template_id}/diagnosis")
        async def get_template_diagnosis(template_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.get_template(template_id)
            if template is None:
                raise HTTPException(status_code=404, detail="Template not found")
            return {
                "ok": True,
                "template": template.model_dump(mode="json"),
                "template_profile": _read_json_if_exists(template.profile_path),
                "brand_tokens": _read_json_if_exists(template.brand_tokens_path),
                "layout_map": _read_json_if_exists(template.layout_map_path),
            }

        @router.post("/templates/{template_id}/diagnose")
        async def diagnose_template(template_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.get_template(template_id)
                if template is None or not template.original_path:
                    raise HTTPException(status_code=404, detail="Template not found")
                try:
                    settings = _load_settings(data_dir)
                    diagnosis = TemplateManager().diagnose_to_files(
                        template.original_path,
                        _analysis_dir(data_dir, settings, "templates", template_id),
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
            template_path = _analysis_dir(data_dir, _load_settings(data_dir), "templates", template_id)
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
        async def delete_template(
            template_id: str,
            payload: DeleteAssetRequest | None = None,
        ) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                template = await manager.get_template(template_id)
                if template is None:
                    raise HTTPException(status_code=404, detail="Template not found")
                deleted = await manager.delete_template(template_id)
            file_result = _delete_stored_paths(
                data_dir,
                [
                    template.original_path,
                    template.profile_path,
                    template.brand_tokens_path,
                    template.layout_map_path,
                ],
                enabled=bool(payload and payload.delete_files),
            )
            return {"ok": True, "deleted": deleted, **file_result}

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
            settings = _load_settings(data_dir)
            out_dir = _analysis_dir(data_dir, settings, "exports", project_id)
            output_name = _format_output_filename(project_id, "pptx", settings)
            output_path = out_dir / output_name
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

        @router.get("/exports/{export_id}/download", response_class=FileResponse)
        async def download_export(export_id: str):
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                export = await manager.get_export(export_id)
            if export is None or not Path(export["path"]).exists():
                raise HTTPException(status_code=404, detail="Export not found")
            return FileResponse(export["path"], filename=Path(export["path"]).name)

        @router.delete("/exports/{export_id}")
        async def delete_export(
            export_id: str,
            payload: DeleteAssetRequest | None = None,
        ) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                export = await manager.get_export(export_id)
                if export is None:
                    raise HTTPException(status_code=404, detail="Export not found")
                deleted = await manager.delete_export(export_id)
            file_result = _delete_stored_paths(
                data_dir,
                [export.get("path")],
                enabled=bool(payload and payload.delete_files),
            )
            return {"ok": True, "deleted": deleted, **file_result}

        @router.post("/exports/{export_id}/open-folder")
        async def open_export_folder(export_id: str) -> dict[str, Any]:
            async with PptTaskManager(data_dir / "ppt_maker.db") as manager:
                export = await manager.get_export(export_id)
            if export is None:
                raise HTTPException(status_code=404, detail="Export not found")
            path = Path(export["path"]).expanduser().resolve(strict=False)
            if not path.exists():
                raise HTTPException(status_code=404, detail="Export file not found")
            try:
                _open_folder(path.parent)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Cannot open folder: {exc}") from exc
            return {"ok": True, "path": str(path.parent), "file": path.name}

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
                payload = []
                for project in projects:
                    item = project.model_dump(mode="json")
                    item["exports"] = await manager.list_exports(project.id)
                    payload.append(item)
                return json.dumps(
                    {"ok": True, "projects": payload},
                    ensure_ascii=False,
                )
            if tool_name == "ppt_start_project":
                project = await manager.create_project(ProjectCreate(**arguments))
                return json.dumps({"ok": True, "project": project.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "ppt_ingest_sources":
                created = []
                for raw_path in arguments.get("paths", []):
                    path = Path(str(raw_path))
                    source = await manager.create_source(
                        project_id=arguments.get("project_id"),
                        kind=SourceLoader().detect_kind(path),
                        filename=path.name,
                        path=str(path),
                        metadata={"tool": tool_name},
                    )
                    created.append(source.model_dump(mode="json"))
                return json.dumps({"ok": True, "sources": created}, ensure_ascii=False)
            if tool_name == "ppt_ingest_table":
                dataset = await manager.create_dataset(
                    project_id=arguments.get("project_id"),
                    name=arguments.get("name") or Path(str(arguments["path"])).stem,
                    original_path=str(arguments["path"]),
                    metadata={"kind": SourceLoader().detect_kind(str(arguments["path"]))},
                )
                return json.dumps({"ok": True, "dataset": dataset.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "ppt_profile_table":
                result = await _profile_dataset_for_tool(manager, data_dir, str(arguments["dataset_id"]))
                return json.dumps({"ok": True, **result}, ensure_ascii=False)
            if tool_name == "ppt_generate_table_insights":
                result = await _dataset_file_payload(manager, str(arguments["dataset_id"]), "insights")
                return json.dumps({"ok": True, **result}, ensure_ascii=False)
            if tool_name == "ppt_upload_template":
                template = await manager.create_template(
                    name=arguments.get("name") or Path(str(arguments["path"])).stem,
                    category=arguments.get("category"),
                    original_path=str(arguments["path"]),
                )
                return json.dumps({"ok": True, "template": template.model_dump(mode="json")}, ensure_ascii=False)
            if tool_name == "ppt_diagnose_template":
                result = await _diagnose_template_for_tool(manager, data_dir, str(arguments["template_id"]))
                return json.dumps({"ok": True, **result}, ensure_ascii=False)
            if tool_name == "ppt_generate_outline":
                result = await _generate_outline_for_tool(manager, data_dir, str(arguments["project_id"]))
                return json.dumps({"ok": True, **result}, ensure_ascii=False)
            if tool_name == "ppt_confirm_outline":
                outline = OutlineBuilder().confirm(arguments["outline"])
                OutlineBuilder().save(outline, project_dir(data_dir, str(arguments["project_id"])))
                record = await manager.create_outline(
                    project_id=str(arguments["project_id"]),
                    outline=outline,
                    confirmed=True,
                )
                await manager.update_project_safe(str(arguments["project_id"]), status=ProjectStatus.OUTLINE_CONFIRMED)
                return json.dumps({"ok": True, "outline": outline, "record": record}, ensure_ascii=False)
            if tool_name == "ppt_generate_design":
                result = await _generate_design_for_tool(manager, data_dir, str(arguments["project_id"]))
                return json.dumps({"ok": True, **result}, ensure_ascii=False)
            if tool_name == "ppt_confirm_design":
                design = DesignBuilder().confirm(arguments["design"])
                paths = DesignBuilder().save(design, project_dir(data_dir, str(arguments["project_id"])))
                record = await manager.create_design_spec(
                    project_id=str(arguments["project_id"]),
                    design_markdown=design["design_spec_markdown"],
                    spec_lock=design["spec_lock"],
                    confirmed=True,
                )
                await manager.update_project_safe(str(arguments["project_id"]), status=ProjectStatus.DESIGN_CONFIRMED)
                return json.dumps({"ok": True, "design": design, "paths": paths, "record": record}, ensure_ascii=False)
            if tool_name == "ppt_revise_slide":
                updated = await manager.update_slide_safe(
                    str(arguments["project_id"]),
                    str(arguments["slide_id"]),
                    arguments["slide"],
                )
                return json.dumps({"ok": updated is not None, "slide": updated}, ensure_ascii=False)
            if tool_name == "ppt_audit":
                slides_ir = _project_json(data_dir, str(arguments["project_id"]), "slides_ir.json")
                if slides_ir is None:
                    return json.dumps({"ok": False, "error": "Generate slides first"}, ensure_ascii=False)
                report = PptAudit().run(slides_ir)
                path = PptAudit().save(report, project_dir(data_dir, str(arguments["project_id"])))
                return json.dumps({"ok": True, "audit": report, "path": str(path)}, ensure_ascii=False)
            if tool_name == "ppt_cancel":
                project_id = str(arguments.get("project_id") or "")
                count = await manager.cancel_project_tasks(project_id)
                await manager.update_project_safe(project_id, status=ProjectStatus.CANCELLED)
                return json.dumps({"ok": True, "cancelled_tasks": count}, ensure_ascii=False)
        if tool_name in {"ppt_generate_deck", "ppt_export"}:
            project_id = str(arguments.get("project_id") or "")
            result = await self._make_pipeline(data_dir).run(project_id)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"{tool_name} is registered but not wired until a later phase."},
            ensure_ascii=False,
        )

    def _make_pipeline(self, data_dir: Path) -> PptPipeline:
        settings = _load_settings(data_dir)
        if self._asset_provider is not None and hasattr(self._asset_provider, "update_settings"):
            try:
                self._asset_provider.update_settings(settings)
            except Exception:  # noqa: BLE001
                pass
        return PptPipeline(
            data_root=data_dir,
            emit=self._broadcast,
            brain_adapter=self._brain_adapter,
            asset_provider=self._asset_provider,
            settings=settings,
            activity_logger=self._activity_logger,
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


def _delete_stored_paths(
    data_dir: Path,
    raw_paths: list[str | None],
    *,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"files_deleted": [], "files_skipped": []}
    settings = _load_settings(data_dir)
    roots = [data_dir, *_storage_folders(data_dir, settings).values()]
    root_paths = [_resolve_for_compare(root) for root in roots]
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    for raw_path in raw_paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        comparable = _resolve_for_compare(path)
        if not any(_is_relative_to(comparable, root) for root in root_paths):
            skipped.append({"path": str(path), "reason": "不在插件存储目录内，已保留"})
            continue
        if not path.exists():
            skipped.append({"path": str(path), "reason": "文件不存在"})
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(str(path))
            _remove_empty_storage_parents(path.parent, root_paths)
        except OSError as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
    return {"files_deleted": deleted, "files_skipped": skipped}


def _resolve_for_compare(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _remove_empty_storage_parents(start: Path, root_paths: list[Path]) -> None:
    current = _resolve_for_compare(start)
    root_set = {str(root) for root in root_paths}
    while str(current) not in root_set and any(_is_relative_to(current, root) for root in root_paths):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


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


async def _profile_dataset_for_tool(
    manager: PptTaskManager,
    data_dir: Path,
    dataset_id: str,
) -> dict[str, Any]:
    dataset = await manager.get_dataset(dataset_id)
    if dataset is None:
        return {"dataset": None, "error": "Dataset not found"}
    analysis = TableAnalyzer().analyze_to_files(
        dataset.original_path,
        _analysis_dir(data_dir, _load_settings(data_dir), "datasets", dataset_id),
    )
    updated = await manager.update_dataset_safe(
        dataset_id,
        status="profiled",
        profile_path=analysis["paths"]["profile_path"],
        insights_path=analysis["paths"]["insights_path"],
        chart_specs_path=analysis["paths"]["chart_specs_path"],
    )
    return {
        "dataset": updated.model_dump(mode="json") if updated else None,
        "profile": analysis["profile"],
        "insights": analysis["insights"],
        "chart_specs": analysis["chart_specs"],
    }


async def _dataset_file_payload(
    manager: PptTaskManager,
    dataset_id: str,
    kind: str,
) -> dict[str, Any]:
    dataset = await manager.get_dataset(dataset_id)
    if dataset is None:
        return {"error": "Dataset not found"}
    path = dataset.insights_path if kind == "insights" else dataset.chart_specs_path
    return {kind: _read_json_if_exists(path)}


async def _diagnose_template_for_tool(
    manager: PptTaskManager,
    data_dir: Path,
    template_id: str,
) -> dict[str, Any]:
    template = await manager.get_template(template_id)
    if template is None or not template.original_path:
        return {"template": None, "error": "Template not found"}
    diagnosis = TemplateManager().diagnose_to_files(
        template.original_path,
        _analysis_dir(data_dir, _load_settings(data_dir), "templates", template_id),
    )
    updated = await manager.update_template_safe(
        template_id,
        status="diagnosed",
        profile_path=diagnosis["paths"]["profile_path"],
        brand_tokens_path=diagnosis["paths"]["brand_tokens_path"],
        layout_map_path=diagnosis["paths"]["layout_map_path"],
    )
    return {
        "template": updated.model_dump(mode="json") if updated else None,
        "profile": diagnosis["template_profile"],
        "brand_tokens": diagnosis["brand_tokens"],
        "layout_map": diagnosis["layout_map"],
    }


async def _generate_outline_for_tool(
    manager: PptTaskManager,
    data_dir: Path,
    project_id: str,
) -> dict[str, Any]:
    project = await manager.get_project(project_id)
    if project is None:
        return {"error": "Project not found"}
    dataset = await manager.get_dataset(project.dataset_id) if project.dataset_id else None
    template = await manager.get_template(project.template_id) if project.template_id else None
    outline = OutlineBuilder().build(
        mode=project.mode,
        title=project.title,
        slide_count=project.slide_count,
        audience=project.audience,
        requirements={"prompt": project.prompt, "style": project.style},
        table_insights=_read_json_if_exists(dataset.insights_path if dataset else None),
        template_profile=_read_json_if_exists(template.profile_path if template else None),
    )
    path = OutlineBuilder().save(outline, project_dir(data_dir, project_id))
    record = await manager.create_outline(project_id=project_id, outline=outline)
    await manager.update_project_safe(project_id, status=ProjectStatus.OUTLINE_READY)
    return {"outline": outline, "path": str(path), "record": record}


async def _generate_design_for_tool(
    manager: PptTaskManager,
    data_dir: Path,
    project_id: str,
) -> dict[str, Any]:
    project = await manager.get_project(project_id)
    if project is None:
        return {"error": "Project not found"}
    outline = await manager.latest_outline(project_id)
    if outline is None:
        return {"error": "Generate outline first"}
    brand_tokens, layout_map = await _template_design_inputs(manager, project.template_id)
    design = DesignBuilder().build(
        outline=outline["outline"],
        brand_tokens=brand_tokens,
        layout_map=layout_map,
    )
    paths = DesignBuilder().save(design, project_dir(data_dir, project_id))
    record = await manager.create_design_spec(
        project_id=project_id,
        design_markdown=design["design_spec_markdown"],
        spec_lock=design["spec_lock"],
    )
    await manager.update_project_safe(project_id, status=ProjectStatus.DESIGN_READY)
    return {"design": design, "paths": paths, "record": record}


def _default_settings() -> dict[str, str]:
    return {
        "uploads_dir": "",
        "datasets_dir": "",
        "templates_dir": "",
        "projects_dir": "",
        "exports_dir": "",
        "upload_subdir_mode": "type",
        "analysis_subdir_mode": "date",
        "upload_naming_rule": "{date}_{original}",
        "export_naming_rule": "{date}_{project_id}",
        # AI generation knobs (consumed by PptPipeline / PptBrainAdapter)
        "verbosity": "balanced",
        "tone": "professional",
        "language": "zh-CN",
        "single_shot_mode": "false",
        "web_search_enabled": "false",
        # Image / icon resolution (consumed by PptAssetProvider)
        "image_provider": "none",
        "pexels_api_key": "",
        "pixabay_api_key": "",
        "dashscope_api_key": "",
        "dashscope_image_model": "wanx-v1",
    }


def _settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def _load_settings(data_dir: Path) -> dict[str, str]:
    settings = _default_settings()
    path = _settings_path(data_dir)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings.update({key: str(value) for key, value in raw.items() if key in settings})
        except (OSError, ValueError, TypeError):
            pass
    return settings


def _save_settings(data_dir: Path, settings: dict[str, str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _settings_path(data_dir).write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolved_storage_paths(data_dir: Path, settings: dict[str, str]) -> dict[str, str]:
    return {key: str(path) for key, path in _storage_folders(data_dir, settings).items()}


def _setting_path(settings: dict[str, str], key: str, fallback: Path) -> Path:
    raw = (settings.get(key) or "").strip()
    return Path(raw).expanduser() if raw else fallback


def _storage_folders(data_dir: Path, settings: dict[str, str] | None = None) -> dict[str, Path]:
    settings = settings or _load_settings(data_dir)
    return {
        "root": data_dir,
        "uploads": _setting_path(settings, "uploads_dir", data_dir / "uploads"),
        "projects": _setting_path(settings, "projects_dir", data_dir / "projects"),
        "datasets": _setting_path(settings, "datasets_dir", data_dir / "datasets"),
        "templates": _setting_path(settings, "templates_dir", data_dir / "templates"),
        "exports": _setting_path(settings, "exports_dir", data_dir / "exports"),
        "sources_analysis": data_dir / "sources_analysis",
    }


def _today() -> str:
    import time

    return time.strftime("%Y%m%d")


def _format_upload_filename(original_name: str, settings: dict[str, str]) -> str:
    safe_original = safe_name(original_name)
    stem = Path(safe_original).stem
    suffix = Path(safe_original).suffix
    token_values = {
        "date": _today(),
        "original": stem,
        "kind": suffix.lstrip(".") or "file",
    }
    pattern = settings.get("upload_naming_rule") or "{date}_{original}"
    base = _apply_name_pattern(pattern, token_values)
    if base.endswith(suffix):
        return safe_name(base)
    return safe_name(base + suffix)


def _format_output_filename(project_id: str, suffix: str, settings: dict[str, str]) -> str:
    token_values = {"date": _today(), "project_id": project_id}
    pattern = settings.get("export_naming_rule") or "{date}_{project_id}"
    extension = "." + suffix.lstrip(".")
    base = _apply_name_pattern(pattern, token_values)
    if base.endswith(extension):
        return safe_name(base)
    return safe_name(base + extension)


def _apply_name_pattern(pattern: str, values: dict[str, str]) -> str:
    result = pattern
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def _upload_target_dir(
    data_dir: Path,
    settings: dict[str, str],
    filename: str,
    *,
    collection_name: str = "",
) -> Path:
    base = _storage_folders(data_dir, settings)["uploads"]
    collection = safe_name(collection_name, fallback="") if collection_name else ""
    if collection:
        return base / collection
    mode = settings.get("upload_subdir_mode") or "type"
    if mode == "date":
        return base / _today()
    if mode == "type":
        suffix = Path(filename).suffix.lower()
        if suffix in {".csv", ".tsv", ".xlsx"}:
            return base / "tables"
        if suffix in {".pptx", ".potx"}:
            return base / "templates"
        return base / "sources"
    return base


def _analysis_dir(data_dir: Path, settings: dict[str, str], key: str, item_id: str) -> Path:
    base = _storage_folders(data_dir, settings)[key]
    mode = settings.get("analysis_subdir_mode") or "date"
    if mode == "date":
        return base / _today() / safe_name(item_id)
    if mode == "flat":
        return base / safe_name(item_id)
    return base / safe_name(item_id)


def _list_dir_payload(path: str = "") -> dict[str, Any]:
    raw = (path or "").strip()
    if not raw:
        anchors: list[dict[str, Any]] = []
        home = Path.home()
        anchors.append({"name": "Home", "path": str(home), "is_dir": True, "kind": "home"})
        for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos"):
            candidate = home / sub
            if candidate.is_dir():
                anchors.append({
                    "name": sub,
                    "path": str(candidate),
                    "is_dir": True,
                    "kind": "shortcut",
                })
        if sys.platform.startswith("win"):
            import string

            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                try:
                    if drive.exists():
                        anchors.append({
                            "name": f"{letter}:",
                            "path": str(drive),
                            "is_dir": True,
                            "kind": "drive",
                        })
                except OSError:
                    continue
        else:
            anchors.append({"name": "/", "path": "/", "is_dir": True, "kind": "drive"})
        return {"ok": True, "path": "", "parent": None, "items": anchors, "is_anchor": True}

    target = Path(raw).expanduser().resolve(strict=False)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    items: list[dict[str, Any]] = []
    try:
        for entry in target.iterdir():
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    items.append({"name": entry.name, "path": str(entry), "is_dir": True})
            except (OSError, PermissionError):
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    items.sort(key=lambda item: item["name"].lower())
    parent = str(target.parent) if target.parent != target else None
    return {"ok": True, "path": str(target), "parent": parent, "items": items, "is_anchor": False}


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])

