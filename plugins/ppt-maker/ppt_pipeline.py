"""10-step ppt-maker pipeline (Brain-first, deterministic fallback)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ppt_activity_log import PptActivityLogger
from ppt_audit import PptAudit
from ppt_brain_adapter import (
    BrainAccessError,
    LayoutPlan,
    OutlineDraft,
    PptBrainAdapter,
)
from ppt_design import DesignBuilder
from ppt_exporter import PptxExporter
from ppt_ir import SlideIrBuilder
from ppt_maker_inline.file_utils import ensure_dir, safe_name
from ppt_models import ErrorKind, ProjectStatus, SlideType, TaskCreate, TaskStatus
from ppt_outline import OutlineBuilder
from ppt_table_analyzer import TableAnalyzer
from ppt_task_manager import PptTaskManager
from ppt_template_manager import TemplateManager

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


PIPELINE_STEPS = [
    "setup",
    "ingest",
    "table_profile",
    "template_diagnose",
    "requirements",
    "outline",
    "design",
    "ir",
    "export",
    "audit_finalize",
]

_DEFAULT_STEP_MESSAGES: dict[str, str] = {
    "setup": "初始化项目工作目录",
    "ingest": "解析参考资料/数据",
    "table_profile": "完成表格画像与图表建议",
    "template_diagnose": "完成模板诊断",
    "requirements": "完成需求复核",
    "outline": "大纲阶段",
    "design": "设计方案阶段",
    "ir": "页面 IR 渲染",
    "export": "导出 PPTX",
    "audit_finalize": "审计与归档",
}


class PptPipeline:
    """Linear orchestration for the MVP deck generation path."""

    def __init__(
        self,
        *,
        data_root: str | Path,
        emit: Emit | None = None,
        brain_adapter: PptBrainAdapter | None = None,
        asset_provider: Any = None,
        settings: dict[str, str] | None = None,
        activity_logger: PptActivityLogger | None = None,
    ) -> None:
        self._data_root = Path(data_root)
        self._emit = emit
        self._brain_adapter = brain_adapter
        self._asset_provider = asset_provider
        self._settings = settings or {}
        self._activity = activity_logger or PptActivityLogger(data_root=data_root)
        # Brain adapter shares the same activity logger so its LLM calls appear
        # in the same timeline as the pipeline steps.
        if self._brain_adapter is not None and not getattr(
            self._brain_adapter, "_activity", None
        ):
            self._brain_adapter.bind_activity_logger(
                self._activity, emit=self._record_emit
            )

    # ── Public API ─────────────────────────────────────────────────────

    async def run(self, project_id: str) -> dict[str, Any]:
        async with PptTaskManager(self._data_root / "ppt_maker.db") as manager:
            task = await manager.create_task(
                TaskCreate(project_id=project_id, task_type="generate_deck", params={})
            )
            try:
                result = await self._run_steps(manager, project_id, task.id)
            except Exception as exc:  # noqa: BLE001
                error_kind = self._classify_error(exc)
                await manager.update_task_safe(
                    task.id,
                    status=TaskStatus.FAILED,
                    error_kind=error_kind.value,
                    error_message=str(exc),
                    error_hints=[],
                )
                await manager.update_project_safe(project_id, status=ProjectStatus.FAILED)
                await self._emit_update(task.id, "failed", 1, {"error": str(exc)})
                await self._record(
                    project_id=project_id,
                    stage="run",
                    status="error",
                    level="error",
                    message=f"项目生成失败：{exc}",
                    details={
                        "error_kind": error_kind.value,
                        "error_class": exc.__class__.__name__,
                    },
                )
                raise
            await manager.update_task_safe(
                task.id,
                status=TaskStatus.SUCCEEDED,
                progress=1,
                result=result,
            )
            await self._emit_update(task.id, "succeeded", 1, result)
            return {"task_id": task.id, **result}

    # ── Steps ──────────────────────────────────────────────────────────

    async def _run_steps(
        self,
        manager: PptTaskManager,
        project_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        project = await manager.get_project(project_id)
        if project is None:
            raise ValueError("Project not found")
        settings = _load_settings(self._data_root)
        root = _project_work_dir(self._data_root, settings, project_id)

        await self._record(
            project_id=project_id,
            stage="run",
            status="start",
            message=f"开始生成项目 ({project.mode.value} · {project.slide_count or '?'} 页)",
            details={
                "title": project.title,
                "audience": project.audience,
                "style": project.style,
                "brain_available": bool(
                    self._brain_adapter and self._brain_adapter.has_brain_access()
                ),
            },
        )
        await self._step(manager, task_id, "setup", 0.05, project_id=project_id)
        await self._step(manager, task_id, "ingest", 0.15, project_id=project_id)

        table_insights, chart_specs = await self._table_inputs(manager, project)
        await self._step(
            manager,
            task_id,
            "table_profile",
            0.25,
            project_id=project_id,
            details={
                "has_table": bool(table_insights),
                "chart_specs": len(chart_specs or []),
            },
        )
        brand_tokens, layout_map = await self._template_inputs(manager, project)
        await self._step(
            manager,
            task_id,
            "template_diagnose",
            0.35,
            project_id=project_id,
            details={"has_brand": bool(brand_tokens), "layouts": len(layout_map or {})},
        )
        await self._step(manager, task_id, "requirements", 0.45, project_id=project_id)

        # ── Outline ────────────────────────────────────────────────────
        outline = await manager.latest_outline(project_id)
        if outline is None:
            outline_data = await self._build_outline(
                manager=manager,
                project=project,
                table_insights=table_insights,
            )
            OutlineBuilder().save(outline_data, root)
            outline = await manager.create_outline(project_id=project_id, outline=outline_data)
            await manager.update_project_safe(project_id, status=ProjectStatus.OUTLINE_READY)
            await self._step(
                manager,
                task_id,
                "outline",
                0.55,
                project_id=project_id,
                message=f"大纲生成完成 ({len(outline_data.get('slides', []))} 页)",
                details={"slides": len(outline_data.get("slides", []))},
            )
            return self._gate_result(project_id, "outline", outline["outline"])
        if not outline.get("confirmed"):
            await self._step(
                manager,
                task_id,
                "outline",
                0.55,
                project_id=project_id,
                message="等待用户确认大纲",
            )
            return self._gate_result(project_id, "outline", outline["outline"])
        await self._step(
            manager,
            task_id,
            "outline",
            0.55,
            project_id=project_id,
            message="大纲已确认，进入设计阶段",
        )

        # ── Design ─────────────────────────────────────────────────────
        design = await manager.latest_design_spec(project_id)
        if design is None:
            design_data = DesignBuilder().build(
                outline=outline["outline"],
                brand_tokens=brand_tokens,
                layout_map=layout_map,
                style=getattr(project, "style", None),
            )
            DesignBuilder().save(design_data, root)
            design = await manager.create_design_spec(
                project_id=project_id,
                design_markdown=design_data["design_spec_markdown"],
                spec_lock=design_data["spec_lock"],
            )
            await manager.update_project_safe(project_id, status=ProjectStatus.DESIGN_READY)
            await self._step(
                manager,
                task_id,
                "design",
                0.65,
                project_id=project_id,
                message="设计方案生成完成",
                details={"theme": design_data.get("spec_lock", {}).get("theme", {})},
            )
            return self._gate_result(project_id, "design", design_data)
        if not design.get("confirmed"):
            await self._step(
                manager,
                task_id,
                "design",
                0.65,
                project_id=project_id,
                message="等待用户确认设计",
            )
            return self._gate_result(
                project_id,
                "design",
                {
                    "design_spec_markdown": design["design_markdown"],
                    "spec_lock": design["spec_lock"],
                },
            )
        await self._step(
            manager,
            task_id,
            "design",
            0.65,
            project_id=project_id,
            message="设计已确认，进入页面渲染",
        )

        # ── IR (brain per-slide content + assets) ──────────────────────
        slide_contents = await self._maybe_build_slide_contents(
            manager=manager,
            project=project,
            outline=outline["outline"],
        )

        slides_ir = SlideIrBuilder().build(
            outline=outline["outline"],
            spec_lock=design["spec_lock"],
            table_insights=table_insights,
            chart_specs=chart_specs,
            template_id=project.template_id,
            layout_map=layout_map,
            slide_contents=slide_contents,
        )
        slides_ir = await self._resolve_assets(slides_ir, project_id)
        SlideIrBuilder().save(slides_ir, root)
        await manager.replace_slides(project_id, slides_ir["slides"])
        await self._step(
            manager,
            task_id,
            "ir",
            0.8,
            project_id=project_id,
            message=f"IR 渲染完成 ({len(slides_ir.get('slides', []))} 页)",
            details={
                "slides": len(slides_ir.get("slides", [])),
                "ai_filled": len(slide_contents or {}),
            },
        )

        # ── Export ─────────────────────────────────────────────────────
        export_dir = _analysis_dir(self._data_root, settings, "exports", project_id)
        export_path = PptxExporter().export(
            slides_ir,
            export_dir / _format_output_filename(project_id, "pptx", settings),
        )
        export = await manager.create_export(
            project_id=project_id,
            path=str(export_path),
            metadata={"slide_count": len(slides_ir["slides"])},
        )
        await self._step(
            manager,
            task_id,
            "export",
            0.92,
            project_id=project_id,
            message=f"PPTX 已写入 {export_path.name}",
            details={"path": str(export_path)},
        )

        # ── Audit ──────────────────────────────────────────────────────
        audit = PptAudit().run(slides_ir, export_path)
        audit_path = PptAudit().save(audit, root)
        await manager.update_project_safe(project_id, status=ProjectStatus.READY)
        await self._step(
            manager,
            task_id,
            "audit_finalize",
            0.98,
            project_id=project_id,
            message=f"审计完成 (ok={audit.get('ok', False)})",
            details={"audit_ok": audit.get("ok", False)},
        )
        await self._record(
            project_id=project_id,
            stage="run",
            status="success",
            message="项目生成完成",
            details={"export_id": export["id"], "export_path": str(export_path)},
        )
        return {
            "project_id": project_id,
            "export_id": export["id"],
            "export_path": str(export_path),
            "audit_path": str(audit_path),
            "audit_ok": audit.get("ok", False),
        }

    # ── Outline (Brain or fallback) ────────────────────────────────────

    async def _build_outline(
        self,
        *,
        manager: PptTaskManager,
        project: Any,
        table_insights: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self._brain_adapter is None or not self._brain_adapter.has_brain_access():
            await self._record(
                project_id=project.id,
                stage="outline",
                status="fallback",
                level="warn",
                message="Brain 不可用，使用本地兜底大纲",
            )
            return self._fallback_outline(project, table_insights)

        try:
            additional_context = await self._brain_adapter.compose_additional_context(
                manager=manager,
                project=project,
                web_search_enabled=self._setting_bool("web_search_enabled", False),
            )
            await self._record(
                project_id=project.id,
                stage="context",
                status="success",
                message="补充上下文已构建",
                details={
                    "context_chars": len(additional_context or ""),
                    "web_search": self._setting_bool("web_search_enabled", False),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("ppt-maker: compose_additional_context failed: %s", exc)
            additional_context = ""
            await self._record(
                project_id=project.id,
                stage="context",
                status="error",
                level="warn",
                message=f"补充上下文构建失败：{exc}",
            )

        verbosity = self._settings.get("verbosity") or "balanced"
        tone = self._settings.get("tone") or "professional"
        language = self._settings.get("language") or "zh-CN"
        requirements = {
            "title": project.title,
            "prompt": project.prompt,
            "audience": project.audience,
            "style": project.style,
            "slide_count": project.slide_count,
        }
        try:
            draft: OutlineDraft = await self._brain_adapter.generate_outline(
                mode=project.mode,
                requirements=requirements,
                context=additional_context,
                project_id=project.id,
                verbosity=verbosity,
                tone=tone,
                language=language,
            )
        except (BrainAccessError, Exception) as exc:  # noqa: BLE001
            logger.warning("ppt-maker: brain outline failed, falling back: %s", exc)
            await self._record(
                project_id=project.id,
                stage="outline",
                status="fallback",
                level="warn",
                message=f"Brain 大纲调用失败，回退到本地兜底：{exc}",
                details={"error_class": exc.__class__.__name__},
            )
            return self._fallback_outline(project, table_insights)

        outline_dump = draft.model_dump(mode="json")
        await self._record(
            project_id=project.id,
            stage="outline",
            status="success",
            message=f"Brain 已生成 {len(outline_dump.get('slides', []))} 页大纲",
            details={
                "slides": len(outline_dump.get("slides", [])),
                "verbosity": verbosity,
                "tone": tone,
                "language": language,
            },
        )

        try:
            plan: LayoutPlan = await self._brain_adapter.select_layout_per_slide(
                outline=outline_dump,
                project_id=project.id,
            )
            if plan.slides:
                index_to_type = {choice.index: choice.slide_type for choice in plan.slides}
                for slide in outline_dump["slides"]:
                    chosen = index_to_type.get(slide.get("index"))
                    if chosen is not None:
                        slide["slide_type"] = chosen.value
                await self._record(
                    project_id=project.id,
                    stage="layout_plan",
                    status="success",
                    message=f"已为 {len(plan.slides)} 页挑选版式",
                    details={
                        "types": [choice.slide_type.value for choice in plan.slides],
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.info("ppt-maker: layout selection failed (keeping outline types): %s", exc)
            await self._record(
                project_id=project.id,
                stage="layout_plan",
                status="fallback",
                level="warn",
                message=f"版式自动挑选失败，沿用大纲版式：{exc}",
            )

        # Stash context for later stages so we don't double-pay web search
        outline_dump["__brain_context__"] = additional_context
        outline_dump["needs_confirmation"] = True
        outline_dump["confirmation_questions"] = draft.confirmation_questions or [
            "页数是否合适？",
            "受众和汇报语气是否准确？",
            "是否需要调整章节顺序？",
        ]
        outline_dump["requirements"] = requirements
        # Make sure each slide has a stable id even after Brain rewrites
        for index, slide in enumerate(outline_dump.get("slides", []), start=1):
            slide.setdefault("id", f"slide_{index:02d}")
            slide["index"] = index
        return outline_dump

    def _fallback_outline(
        self,
        project: Any,
        table_insights: dict[str, Any] | None,
    ) -> dict[str, Any]:
        outline_data = OutlineBuilder().build(
            mode=project.mode,
            title=project.title,
            slide_count=project.slide_count,
            audience=project.audience,
            style=getattr(project, "style", "tech_business"),
            requirements={"prompt": project.prompt, "style": project.style},
            table_insights=table_insights,
        )
        return outline_data

    # ── Per-slide content (Brain) ──────────────────────────────────────

    async def _maybe_build_slide_contents(
        self,
        *,
        manager: PptTaskManager,
        project: Any,
        outline: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        if self._brain_adapter is None or not self._brain_adapter.has_brain_access():
            return {}
        slides = outline.get("slides", [])
        if not slides:
            return {}

        context = outline.get("__brain_context__")
        if context is None:
            try:
                context = await self._brain_adapter.compose_additional_context(
                    manager=manager,
                    project=project,
                    web_search_enabled=self._setting_bool("web_search_enabled", False),
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("ppt-maker: per-slide context fetch failed: %s", exc)
                context = ""

        verbosity = self._settings.get("verbosity") or "balanced"
        tone = self._settings.get("tone") or "professional"
        language = self._settings.get("language") or "zh-CN"
        deck_title = outline.get("title") or project.title

        await self._record(
            project_id=project.id,
            stage="slide_content",
            status="start",
            message=f"开始为 {len(slides)} 页生成内容",
            details={"single_shot": self._setting_bool("single_shot_mode", False)},
        )

        async def _one(slide: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
            try:
                slide_type = SlideType(slide.get("slide_type") or SlideType.CONTENT.value)
            except ValueError:
                slide_type = SlideType.CONTENT
            try:
                content = await self._brain_adapter.generate_slide_content_per_slide(
                    slide_outline=slide,
                    slide_type=slide_type,
                    deck_title=deck_title,
                    verbosity=verbosity,
                    tone=tone,
                    language=language,
                    context=context,
                    project_id=project.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "ppt-maker: brain slide content failed for slide %s (%s): %s",
                    slide.get("index"),
                    slide_type.value,
                    exc,
                )
                await self._record(
                    project_id=project.id,
                    stage="slide_content",
                    status="fallback",
                    level="warn",
                    message=f"第 {slide.get('index')} 页 ({slide_type.value}) 内容生成失败：{exc}",
                    details={
                        "slide_index": slide.get("index"),
                        "slide_type": slide_type.value,
                    },
                )
                return None
            return slide.get("index"), content

        semaphore = asyncio.Semaphore(3)

        async def _bounded(slide: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
            async with semaphore:
                return await _one(slide)

        results = await asyncio.gather(*[_bounded(slide) for slide in slides])
        out: dict[int, dict[str, Any]] = {}
        for item in results:
            if item is None:
                continue
            idx, content = item
            if idx is None:
                continue
            out[int(idx)] = content
        await self._record(
            project_id=project.id,
            stage="slide_content",
            status="success",
            message=f"页面内容生成完成 ({len(out)}/{len(slides)})",
            details={"filled": len(out), "total": len(slides)},
        )
        return out

    # ── Asset resolution ───────────────────────────────────────────────

    async def _resolve_assets(
        self,
        slides_ir: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        if self._asset_provider is None:
            return slides_ir
        image_hits = 0
        icon_hits = 0
        image_misses = 0
        for slide in slides_ir.get("slides", []):
            content = slide.get("content") or {}
            assets: dict[str, Any] = {}
            image_query = content.get("image_query")
            if image_query and hasattr(self._asset_provider, "resolve_image"):
                try:
                    image_path = await self._asset_provider.resolve_image(
                        query=image_query, project_id=project_id
                    )
                    if image_path:
                        assets["image_path"] = image_path
                        image_hits += 1
                    else:
                        image_misses += 1
                except Exception as exc:  # noqa: BLE001
                    logger.info("ppt-maker: image resolve failed (%s): %s", image_query, exc)
                    image_misses += 1
            icon_query = content.get("icon_query")
            if icon_query and hasattr(self._asset_provider, "resolve_icon"):
                try:
                    icon = self._asset_provider.resolve_icon(icon_query)
                    if icon:
                        assets["icon"] = icon
                        icon_hits += 1
                except Exception as exc:  # noqa: BLE001
                    logger.info("ppt-maker: icon resolve failed (%s): %s", icon_query, exc)
            if assets:
                slide["assets"] = assets
        if image_hits or icon_hits or image_misses:
            await self._record(
                project_id=project_id,
                stage="assets",
                status="success" if (image_hits or icon_hits) else "fallback",
                level="info" if (image_hits or icon_hits) else "warn",
                message=(
                    f"配图 {image_hits} 张 / 图标 {icon_hits} 个"
                    + (f"（{image_misses} 张未命中）" if image_misses else "")
                ),
                details={
                    "image_hits": image_hits,
                    "icon_hits": icon_hits,
                    "image_misses": image_misses,
                    "provider": (self._settings or {}).get("image_provider", "none"),
                },
            )
        return slides_ir

    # ── Existing helpers (unchanged) ───────────────────────────────────

    async def _table_inputs(
        self, manager: PptTaskManager, project: Any
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        if not project.dataset_id:
            return None, []
        dataset = await manager.get_dataset(project.dataset_id)
        if dataset is None:
            return None, []
        if not dataset.profile_path or not dataset.insights_path or not dataset.chart_specs_path:
            analysis = TableAnalyzer().analyze_to_files(
                dataset.original_path,
                _analysis_dir(self._data_root, _load_settings(self._data_root), "datasets", dataset.id),
            )
            dataset = await manager.update_dataset_safe(
                dataset.id,
                status="profiled",
                profile_path=analysis["paths"]["profile_path"],
                insights_path=analysis["paths"]["insights_path"],
                chart_specs_path=analysis["paths"]["chart_specs_path"],
            )
        if dataset is None:
            return None, []
        return _read_json(dataset.insights_path), _read_json(dataset.chart_specs_path) or []

    async def _template_inputs(
        self, manager: PptTaskManager, project: Any
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not project.template_id:
            return None, None
        template = await manager.get_template(project.template_id)
        if template is None:
            return None, None
        if template.original_path and (not template.brand_tokens_path or not template.layout_map_path):
            diagnosis = TemplateManager().diagnose_to_files(
                template.original_path,
                _analysis_dir(self._data_root, _load_settings(self._data_root), "templates", template.id),
            )
            template = await manager.update_template_safe(
                template.id,
                status="diagnosed",
                profile_path=diagnosis["paths"]["profile_path"],
                brand_tokens_path=diagnosis["paths"]["brand_tokens_path"],
                layout_map_path=diagnosis["paths"]["layout_map_path"],
            )
        if template is None:
            return None, None
        return _read_json(template.brand_tokens_path), _read_json(template.layout_map_path)

    def _gate_result(self, project_id: str, gate: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Strip transient keys before returning to clients
        if isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if not k.startswith("__")}
        return {
            "project_id": project_id,
            "needs_confirmation": gate,
            gate: payload,
            "message": f"Confirm {gate} before continuing to export.",
        }

    async def _step(
        self,
        manager: PptTaskManager,
        task_id: str,
        step: str,
        progress: float,
        *,
        project_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await manager.update_task_safe(task_id, status=TaskStatus.RUNNING, progress=progress)
        await self._emit_update(task_id, step, progress, {"step": step})
        if project_id is not None:
            await self._record(
                project_id=project_id,
                stage=step,
                status="success",
                message=message or _DEFAULT_STEP_MESSAGES.get(step, f"{step} 完成"),
                details=details or {"progress": round(progress, 2)},
            )

    async def _record(
        self,
        *,
        project_id: str,
        stage: str,
        status: str,
        message: str = "",
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = await self._activity.append(
            project_id=project_id,
            stage=stage,
            status=status,
            level=level,
            message=message,
            details=details,
        )
        await self._record_emit(event)
        return event

    async def _record_emit(self, event: dict[str, Any]) -> None:
        if self._emit is None:
            return
        try:
            await self._emit("ppt_activity", event)
        except Exception:  # noqa: BLE001
            logger.debug("ppt-maker: activity broadcast failed", exc_info=True)

    async def _emit_update(
        self,
        task_id: str,
        status: str,
        progress: float,
        payload: dict[str, Any],
    ) -> None:
        if self._emit is None:
            return
        await self._emit(
            "task_update",
            {"task_id": task_id, "status": status, "progress": progress, **payload},
        )

    def _classify_error(self, exc: Exception) -> ErrorKind:
        if isinstance(exc, BrainAccessError):
            return ErrorKind.BRAIN
        text = str(exc).lower()
        if "dependency" in text:
            return ErrorKind.DEPENDENCY
        if "template" in text:
            return ErrorKind.TEMPLATE
        if "export" in text or "pptx" in text:
            return ErrorKind.EXPORT
        if "audit" in text:
            return ErrorKind.AUDIT
        if "parse" in text:
            return ErrorKind.SOURCE_PARSE
        return ErrorKind.UNKNOWN

    def _setting_bool(self, key: str, default: bool) -> bool:
        raw = self._settings.get(key)
        if raw is None:
            return default
        return str(raw).lower() in {"1", "true", "yes", "on", "y"}


# ── Module-level helpers ───────────────────────────────────────────────


def pipeline_summary(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _default_settings() -> dict[str, str]:
    return {
        "datasets_dir": "",
        "templates_dir": "",
        "projects_dir": "",
        "exports_dir": "",
        "analysis_subdir_mode": "date",
        "export_naming_rule": "{date}_{project_id}",
    }


def _load_settings(data_root: Path) -> dict[str, str]:
    settings = _default_settings()
    path = data_root / "settings.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings.update({key: str(value) for key, value in raw.items() if key in settings})
        except (OSError, ValueError, TypeError):
            pass
    return settings


def _setting_path(settings: dict[str, str], key: str, fallback: Path) -> Path:
    raw = (settings.get(key) or "").strip()
    return Path(raw).expanduser() if raw else fallback


def _storage_folders(data_root: Path, settings: dict[str, str]) -> dict[str, Path]:
    return {
        "projects": _setting_path(settings, "projects_dir", data_root / "projects"),
        "datasets": _setting_path(settings, "datasets_dir", data_root / "datasets"),
        "templates": _setting_path(settings, "templates_dir", data_root / "templates"),
        "exports": _setting_path(settings, "exports_dir", data_root / "exports"),
    }


def _today() -> str:
    return time.strftime("%Y%m%d")


def _analysis_dir(data_root: Path, settings: dict[str, str], key: str, item_id: str) -> Path:
    base = _storage_folders(data_root, settings)[key]
    mode = settings.get("analysis_subdir_mode") or "date"
    if mode == "date":
        return ensure_dir(base / _today() / safe_name(item_id))
    return ensure_dir(base / safe_name(item_id))


def _project_work_dir(data_root: Path, settings: dict[str, str], project_id: str) -> Path:
    base = _storage_folders(data_root, settings)["projects"]
    return ensure_dir(base / safe_name(project_id))


def _format_output_filename(project_id: str, suffix: str, settings: dict[str, str]) -> str:
    extension = "." + suffix.lstrip(".")
    pattern = settings.get("export_naming_rule") or "{date}_{project_id}"
    base = pattern.replace("{date}", _today()).replace("{project_id}", project_id)
    if base.endswith(extension):
        return safe_name(base)
    return safe_name(base + extension)


def _read_json(path: str | None) -> Any:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


# Public so plugin.py can fetch the same merged map
def load_settings(data_root: Path) -> dict[str, str]:
    return _load_settings(data_root)
