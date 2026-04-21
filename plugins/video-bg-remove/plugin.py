"""video-bg-remove — plugin entry point.

Wires :mod:`matting_engine` and :mod:`task_manager` to the OpenAkita
plugin host (HTTP routes + brain tools + lifecycle).  All matting
math (RVM session, recurrent state, alpha compositing) lives in
:mod:`matting_engine`; this file is purely glue.

Conventions copied verbatim from
``plugins/video-color-grade/plugin.py``:

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* :class:`QualityGates` validates every request body before queuing,
* the worker offloads RVM + ffmpeg to a thread (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    add_upload_preview_route,
    build_preview_url,
)

from matting_engine import (
    DEFAULT_DOWNSAMPLE_RATIO,
    DEFAULT_MODEL_FILENAME,
    DEFAULT_RENDER_TIMEOUT_SEC,
    Background,
    ffmpeg_available,
    model_available,
    onnxruntime_available,
    plan_matting,
    run_matting,
    to_verification,
)
from task_manager import MattingTaskManager

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    """POST /tasks payload."""

    input_path: str = Field(..., min_length=1)
    output_path: str | None = None
    # background.kind ∈ {"color", "image", "transparent"}
    background: dict[str, Any] | None = None
    downsample_ratio: float = DEFAULT_DOWNSAMPLE_RATIO
    crf: int = 18
    libx264_preset: str = "fast"


class PreviewBody(BaseModel):
    """POST /preview — build a plan WITHOUT running matting."""

    input_path: str = Field(..., min_length=1)
    output_path: str | None = None
    background: dict[str, Any] | None = None
    downsample_ratio: float = DEFAULT_DOWNSAMPLE_RATIO


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = MattingTaskManager(data_dir / "video_bg_remove.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "video_bg_remove_create",
                 "description": "Remove the background from a video and composite onto color/image/transparency.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "input_path": {"type": "string"},
                         "output_path": {"type": "string"},
                         "background": {"type": "object"},
                     },
                     "required": ["input_path"],
                 }},
                {"name": "video_bg_remove_status",
                 "description": "Get the status of a matting job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "video_bg_remove_list",
                 "description": "List recent matting jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "video_bg_remove_cancel",
                 "description": "Cancel a running matting job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "video_bg_remove_check_deps",
                 "description": "Check whether onnxruntime + RVM model + ffmpeg are ready.",
                 "input_schema": {"type": "object", "properties": {}}},
            ],
            self._handle_tool_call,
        )
        api.log("video-bg-remove loaded")

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
                        f"video-bg-remove on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── helpers ────────────────────────────────────────────────────

    def _model_path(self) -> Path:
        return self._data_dir / "models" / DEFAULT_MODEL_FILENAME

    def _check_deps(self) -> dict[str, Any]:
        mp = self._model_path()
        return {
            "onnxruntime": onnxruntime_available(),
            "ffmpeg": ffmpeg_available(),
            "model_path": str(mp),
            "model_present": model_available(mp),
            "model_download_hint": (
                "Download rvm_mobilenetv3_fp32.onnx from "
                "https://github.com/PeterL1n/RobustVideoMatting/releases "
                f"and drop it at {mp}"
            ) if not model_available(mp) else None,
        }

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "video_bg_remove_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建抠像任务 {tid}"
            if tool_name == "video_bg_remove_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "video_bg_remove_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} {Path(r.extra.get('output_path', '') or '').name}"
                    for r in rows
                )
            if tool_name == "video_bg_remove_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "video_bg_remove_check_deps":
                deps = self._check_deps()
                lines = [
                    f"onnxruntime: {'✓' if deps['onnxruntime'] else '✗'}",
                    f"ffmpeg: {'✓' if deps['ffmpeg'] else '✗'}",
                    f"model: {'✓' if deps['model_present'] else '✗'} ({deps['model_path']})",
                ]
                if deps["model_download_hint"]:
                    lines.append(deps["model_download_hint"])
                return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        add_upload_preview_route(
            router, base_dir=self._data_dir / "uploads",
        )

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            deps = self._check_deps()
            return {
                "ok": True,
                "plugin": "video-bg-remove",
                "deps": deps,
            }

        @router.get("/check-deps")
        async def check_deps() -> dict[str, Any]:
            return self._check_deps()

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/upload-background")
        async def upload_background(file: UploadFile = File(...)) -> dict[str, str]:
            data_dir = self._data_dir / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(file.filename or "image.png").suffix or ".png"
            target = data_dir / f"{uuid.uuid4().hex[:12]}{ext}"
            with target.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            return {
                "path": str(target),
                "url": build_preview_url("video-bg-remove", target.name),
            }

        @router.post("/preview")
        async def preview(body: PreviewBody) -> dict[str, Any]:
            try:
                plan = await asyncio.to_thread(
                    plan_matting,
                    input_path=body.input_path,
                    output_path=body.output_path or "<preview>.mp4",
                    background=body.background,
                    model_path=str(self._model_path()),
                    downsample_ratio=body.downsample_ratio,
                )
            except (ValueError, TypeError) as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from e
            return {"plan": plan.to_dict(), "deps": self._check_deps()}

        @router.post("/tasks")
        async def create_task(body: CreateBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["input_path"],
                non_empty_strings=["input_path"],
            )
            if gate.blocking:
                rendered = self._coach.render(
                    ValueError(gate.message), raw_message=gate.message,
                )
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            tid = await self._create(body)
            return {"task_id": tid, "status": "pending"}

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = Query(default=None),
            limit: int = Query(default=50, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
            rows = await self._tm.list_tasks(
                status=status, limit=limit, offset=offset,
            )
            return {"items": [r.to_dict() for r in rows], "total": len(rows)}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                rendered = self._coach.render(
                    status=404, raw_message=f"task {task_id} not found",
                )
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec.to_dict()

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str) -> dict[str, Any]:
            ok = await self._cancel(task_id)
            if not ok:
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "task not found or already done"},
                )
            return {"ok": True}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_task(task_id)
            if not ok:
                raise HTTPException(
                    status_code=404, detail={"problem": "task not found"},
                )
            return {"ok": True}

        @router.get("/tasks/{task_id}/video")
        async def serve_video(task_id: str) -> FileResponse:
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("output_path"):
                raise HTTPException(
                    status_code=404, detail={"problem": "no output file"},
                )
            p = Path(rec.extra["output_path"])
            if not p.is_file():
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "output file missing on disk"},
                )
            return FileResponse(p)

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        bg = (body.background or {})
        bg_kind = (bg.get("kind") if isinstance(bg, dict) else None) or "color"
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={
                "input_path": body.input_path,
                "background_kind": bg_kind,
                **({"output_path": body.output_path} if body.output_path else {}),
            },
        )
        worker = asyncio.create_task(self._run(tid))
        self._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: self._workers.pop(k, None))
        return tid

    async def _cancel(self, task_id: str) -> bool:
        rec = await self._tm.get_task(task_id)
        if rec is None:
            return False
        if TaskStatus.is_terminal(rec.status):
            return False
        worker = self._workers.pop(task_id, None)
        if worker and not worker.done():
            worker.cancel()
        await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
        return True

    # ── worker ──────────────────────────────────────────────────────

    async def _run(self, task_id: str) -> None:
        try:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                return
            params = rec.params
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "planning",
            })

            input_path = params["input_path"]
            output_path = params.get("output_path")
            if not output_path:
                output_dir = self._data_dir / "outputs" / task_id
                output_dir.mkdir(parents=True, exist_ok=True)
                ext = ".mov" if (params.get("background") or {}).get("kind") == "transparent" else ".mp4"
                output_path = str(output_dir / f"matted{ext}")

            plan = await asyncio.to_thread(
                plan_matting,
                input_path=input_path,
                output_path=output_path,
                background=params.get("background"),
                model_path=str(self._model_path()),
                downsample_ratio=float(params.get("downsample_ratio", DEFAULT_DOWNSAMPLE_RATIO)),
                crf=int(params.get("crf", 18)),
                libx264_preset=str(params.get("libx264_preset", "fast")),
            )

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "matting",
                "fps": plan.fps, "duration_sec": plan.duration_sec,
            })

            def _on_progress(done: int, total: int | None) -> None:
                payload = {
                    "id": task_id, "status": "running", "stage": "matting",
                    "frames_done": done,
                }
                if total:
                    payload["frames_total"] = total
                    payload["progress"] = round(done / total, 3)
                self._events.emit("task_updated", payload)

            result = await asyncio.to_thread(
                run_matting, plan, on_progress=_on_progress,
            )

            verification = to_verification(result)
            verification_dict = verification.to_dict()
            plan_dict = plan.to_dict()

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={
                    "input_path": input_path,
                    "output_path": output_path,
                    "frame_count": result.frame_count,
                    "elapsed_sec": result.elapsed_sec,
                    "output_size_bytes": result.output_size_bytes,
                    "mean_alpha": result.mean_alpha,
                    "verification": verification_dict,
                    "plan": plan_dict,
                },
                extra={
                    "input_path": input_path,
                    "output_path": output_path,
                    "background_kind": plan.background.kind,
                    "verification_json": json.dumps(verification_dict, ensure_ascii=False),
                    "plan_json": json.dumps(plan_dict, ensure_ascii=False),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "output_path": output_path,
                "verification": verification_dict,
            })
        except asyncio.CancelledError:
            await self._tm.update_task(
                task_id, status=TaskStatus.CANCELLED.value,
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "cancelled",
            })
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        try:
            await self._tm.update_task(
                task_id,
                status=TaskStatus.FAILED.value,
                error_message=rendered.problem,
                result={"error": rendered.to_dict()},
            )
        except Exception as inner:  # noqa: BLE001
            self._api.log(
                f"video-bg-remove failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
