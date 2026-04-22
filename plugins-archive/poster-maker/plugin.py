"""poster-maker — template + text + (optional AI background) → PNG poster."""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from _shared import (
    ErrorCoach, QualityGates, TaskStatus, UIEventEmitter,
    add_upload_preview_route, build_preview_url,
)

from poster_engine import render_poster, select_image_provider
from task_manager import PosterTaskManager
from templates import get_template, list_templates

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    template_id: str = "social-square"
    text_values: dict[str, str] = Field(default_factory=dict)
    background_image_path: str | None = None
    ai_enhance_prompt: str | None = None  # if set, run image-edit on background


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = PosterTaskManager(data_dir / "poster_maker.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "poster_maker_create",
                 "description": "Create a poster from template + text.",
                 "input_schema": {"type": "object",
                                  "properties": {"template_id": {"type": "string"},
                                                 "text_values": {"type": "object"}},
                                  "required": ["template_id", "text_values"]}},
                {"name": "poster_maker_status",
                 "description": "Get task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "poster_maker_list",
                 "description": "List recent tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "poster_maker_cancel",
                 "description": "Cancel a running task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "poster_maker_templates",
                 "description": "List all built-in templates.",
                 "input_schema": {"type": "object", "properties": {}}},
            ],
            self._handle_tool_call,
        )
        api.log("poster-maker loaded")

    async def on_unload(self) -> None:
        workers = [t for t in list(self._workers.values()) if not t.done()]
        for t in workers:
            t.cancel()
        if workers:
            results = await asyncio.gather(*workers, return_exceptions=True)
            for res in results:
                if isinstance(res, asyncio.CancelledError):
                    continue
                if isinstance(res, Exception):
                    self._api.log(
                        f"poster-maker on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "poster_maker_create":
                return f"已创建任务 {await self._create(CreateBody(**args))}"
            if tool_name == "poster_maker_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "poster_maker_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "poster_maker_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
            if tool_name == "poster_maker_templates":
                return "\n".join(f"{t['id']}: {t['name']}" for t in list_templates())
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        # Issue #479: serve previously uploaded background images so the UI
        # can render <img src="/api/plugins/poster-maker/uploads/<file>">.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "poster-maker"}

        @router.get("/templates")
        async def templates():
            return {"templates": list_templates()}

        @router.get("/config")
        async def get_config():
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/upload-background")
        async def upload_background(file: UploadFile = File(...)):
            data_dir = self._api.get_data_dir() / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(file.filename or "image.png").suffix or ".png"
            target = data_dir / f"{uuid.uuid4().hex[:12]}{ext}"
            with target.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            return {
                "path": str(target),
                "url": build_preview_url("poster-maker", target.name),
            }

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                {"template_id": body.template_id, "text_values": body.text_values},
                required=["template_id"], non_empty_strings=["template_id"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            try:
                get_template(body.template_id)
            except KeyError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from e
            tid = await self._create(body)
            return {"task_id": tid, "status": "queued"}

        @router.get("/tasks")
        async def list_tasks(status: str | None = None, limit: int = 50):
            rows = await self._tm.list_tasks(status=status, limit=limit)
            return [r.to_dict() for r in rows]

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None:
                rendered = self._coach.render(status=404, raw_message=f"task {task_id} not found")
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec.to_dict()

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str):
            out = await self._cancel(task_id)
            if not out:
                raise HTTPException(status_code=404, detail={"problem": "task not found"})
            return {"ok": True, "status": out.status}

        @router.get("/tasks/{task_id}/poster")
        async def serve_poster(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("output_path"):
                raise HTTPException(status_code=404, detail={"problem": "no poster"})
            p = Path(rec.extra["output_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "poster file missing"})
            return FileResponse(p, media_type="image/png", filename=p.name)

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.template_id, params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"template_id": body.template_id,
                   "background_image_path": body.background_image_path or ""},
        )
        worker = asyncio.create_task(self._run(tid))
        self._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: self._workers.pop(k, None))
        return tid

    async def _cancel(self, task_id: str):
        worker = self._workers.pop(task_id, None)
        if worker and not worker.done():
            worker.cancel()
        return await self._tm.cancel_task(task_id)

    async def _run(self, task_id: str) -> None:
        rec = await self._tm.get_task(task_id)
        if rec is None: return
        params = rec.params

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            output_dir = self._api.get_data_dir() / "outputs" / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            template = get_template(params.get("template_id", "social-square"))
            bg_str = params.get("background_image_path") or None
            bg_path = Path(bg_str) if bg_str else None

            # Optional AI background enhance — uses image-edit's provider
            enhance_prompt = (params.get("ai_enhance_prompt") or "").strip()
            if enhance_prompt and bg_path and bg_path.exists() and select_image_provider:
                try:
                    self._events.emit("task_updated",
                                      {"id": task_id, "status": "running", "stage": "ai-enhance"})
                    provider = select_image_provider("auto")
                    enhanced = await provider.edit(
                        image_path=bg_path, mask_path=None, prompt=enhance_prompt,
                        size=f"{template.width}x{template.height}", n=1,
                        output_dir=output_dir,
                    )
                    if enhanced.output_paths:
                        bg_path = enhanced.output_paths[0]
                except Exception as e:  # noqa: BLE001
                    logger.warning("AI enhance failed, fall back to original bg: %s", e)

            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "render"})
            output_path = output_dir / "poster.png"
            render_poster(
                template=template, text_values=params.get("text_values") or {},
                background_image=bg_path, output_path=output_path,
            )

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"output_path": str(output_path),
                        "template_id": template.id,
                        "size": [template.width, template.height]},
                extra={"output_path": str(output_path)},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "output_path": str(output_path)})
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        await self._tm.update_task(
            task_id, status=TaskStatus.FAILED.value,
            error_message=rendered.problem, result={"error": rendered.to_dict()},
        )
        self._events.emit("task_updated", {"id": task_id, "status": "failed",
                                           "error": rendered.to_dict()})
