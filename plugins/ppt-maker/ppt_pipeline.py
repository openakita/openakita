"""10-step ppt-maker pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Awaitable

from ppt_audit import PptAudit
from ppt_design import DesignBuilder
from ppt_exporter import PptxExporter
from ppt_ir import SlideIrBuilder
from ppt_maker_inline.file_utils import project_dir
from ppt_models import ErrorKind, ProjectStatus, TaskCreate, TaskStatus
from ppt_outline import OutlineBuilder
from ppt_task_manager import PptTaskManager


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


class PptPipeline:
    """Linear orchestration for the MVP deck generation path."""

    def __init__(self, *, data_root: str | Path, emit: Emit | None = None) -> None:
        self._data_root = Path(data_root)
        self._emit = emit

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
                raise
            await manager.update_task_safe(
                task.id,
                status=TaskStatus.SUCCEEDED,
                progress=1,
                result=result,
            )
            await self._emit_update(task.id, "succeeded", 1, result)
            return {"task_id": task.id, **result}

    async def _run_steps(
        self,
        manager: PptTaskManager,
        project_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        project = await manager.get_project(project_id)
        if project is None:
            raise ValueError("Project not found")
        root = project_dir(self._data_root, project_id)
        await self._step(manager, task_id, "setup", 0.1)
        await self._step(manager, task_id, "ingest", 0.2)
        await self._step(manager, task_id, "table_profile", 0.3)
        await self._step(manager, task_id, "template_diagnose", 0.4)
        await self._step(manager, task_id, "requirements", 0.5)

        outline = await manager.latest_outline(project_id)
        if outline is None:
            outline_data = OutlineBuilder().build(
                mode=project.mode,
                title=project.title,
                slide_count=project.slide_count,
                audience=project.audience,
                requirements={"prompt": project.prompt, "style": project.style},
            )
            OutlineBuilder().save(outline_data, root)
            outline = await manager.create_outline(project_id=project_id, outline=outline_data)
        await self._step(manager, task_id, "outline", 0.6)

        design = await manager.latest_design_spec(project_id)
        if design is None:
            design_data = DesignBuilder().build(outline=outline["outline"])
            DesignBuilder().save(design_data, root)
            design = await manager.create_design_spec(
                project_id=project_id,
                design_markdown=design_data["design_spec_markdown"],
                spec_lock=design_data["spec_lock"],
            )
        await self._step(manager, task_id, "design", 0.7)

        slides_ir = SlideIrBuilder().build(
            outline=outline["outline"],
            spec_lock=design["spec_lock"],
            template_id=project.template_id,
        )
        SlideIrBuilder().save(slides_ir, root)
        await manager.replace_slides(project_id, slides_ir["slides"])
        await self._step(manager, task_id, "ir", 0.8)

        export_path = PptxExporter().export(slides_ir, root / "exports" / f"{project_id}.pptx")
        export = await manager.create_export(
            project_id=project_id,
            path=str(export_path),
            metadata={"slide_count": len(slides_ir["slides"])},
        )
        await self._step(manager, task_id, "export", 0.9)

        audit = PptAudit().run(slides_ir, export_path)
        audit_path = PptAudit().save(audit, root)
        await manager.update_project_safe(project_id, status=ProjectStatus.READY)
        await self._step(manager, task_id, "audit_finalize", 0.98)
        return {
            "project_id": project_id,
            "export_id": export["id"],
            "export_path": str(export_path),
            "audit_path": str(audit_path),
            "audit_ok": audit["ok"],
        }

    async def _step(
        self,
        manager: PptTaskManager,
        task_id: str,
        step: str,
        progress: float,
    ) -> None:
        await manager.update_task_safe(task_id, status=TaskStatus.RUNNING, progress=progress)
        await self._emit_update(task_id, step, progress, {"step": step})

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


def pipeline_summary(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))

