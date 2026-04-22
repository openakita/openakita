"""video-color-grade — plugin entry point.

Wires :mod:`grade_engine` and :mod:`task_manager` to the OpenAkita
plugin host (HTTP routes + brain tools + lifecycle).  All of the actual
grading math (signalstats sampling, \u00b18% clamp, eq filter emission)
lives in :mod:`openakita_plugin_sdk.contrib.ffmpeg` — this file is
purely glue.

Conventions copied verbatim from ``plugins/bgm-mixer/plugin.py``:

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* :class:`QualityGates` validates every request body before queuing,
* the worker offloads ffmpeg to a thread (``asyncio.to_thread``).
"""
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
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from _shared import (
    DEFAULT_GRADE_CLAMP_PCT,
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)

from grade_engine import (
    DEFAULT_PROBE_DURATION_SEC,
    DEFAULT_RENDER_TIMEOUT_SEC,
    DEFAULT_SAMPLE_FRAMES,
    MODE_AUTO,
    apply_grade,
    build_grade_command,
    ffmpeg_available,
    list_modes,
    plan_grade,
    probe_video_duration_sec,
    to_verification,
)
from task_manager import GradeTaskManager

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    """POST /tasks payload."""

    input_path: str = Field(..., min_length=1)
    output_path: str | None = None  # default = data_dir/outputs/<id>/graded.mp4
    mode: str = MODE_AUTO            # "auto" | "preset:<name>"
    clamp_pct: float = DEFAULT_GRADE_CLAMP_PCT
    sample_window_sec: float = DEFAULT_PROBE_DURATION_SEC
    sample_frames: int = DEFAULT_SAMPLE_FRAMES
    crf: int = 18
    libx264_preset: str = "fast"


class PreviewBody(BaseModel):
    """POST /preview payload — build a plan WITHOUT running ffmpeg.

    Lets the UI show the auto-grade analysis (stats + filter string)
    before committing — important when the user is dialing in a
    custom clamp or window.
    """

    input_path: str = Field(..., min_length=1)
    mode: str = MODE_AUTO
    clamp_pct: float = DEFAULT_GRADE_CLAMP_PCT
    sample_window_sec: float = DEFAULT_PROBE_DURATION_SEC
    sample_frames: int = DEFAULT_SAMPLE_FRAMES


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = GradeTaskManager(data_dir / "video_color_grade.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "video_color_grade_create",
                 "description": "Apply a subtle (\u00b18% clamp) auto color grade to a video, or pick a named preset.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "input_path": {"type": "string"},
                         "mode": {"type": "string"},
                         "output_path": {"type": "string"},
                     },
                     "required": ["input_path"],
                 }},
                {"name": "video_color_grade_status",
                 "description": "Get the status of a grade job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "video_color_grade_list",
                 "description": "List recent grade jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "video_color_grade_cancel",
                 "description": "Cancel a running grade job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "video_color_grade_preview",
                 "description": "Analyze a clip and return the eq filter that would be applied (no render).",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "input_path": {"type": "string"},
                         "mode": {"type": "string"},
                     },
                     "required": ["input_path"],
                 }},
            ],
            self._handle_tool_call,
        )
        api.log("video-color-grade loaded")

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
                        f"video-color-grade on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "video_color_grade_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建调色任务 {tid}"
            if tool_name == "video_color_grade_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "video_color_grade_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} {Path(r.extra.get('output_path', '') or '').name}"
                    for r in rows
                )
            if tool_name == "video_color_grade_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "video_color_grade_preview":
                body = PreviewBody(**args)
                plan = await self._build_preview_plan(body)
                if plan.filter_string:
                    return f"模式 {plan.mode} → {plan.filter_string}"
                return f"模式 {plan.mode} → 无须调整 (原片已平衡)"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": "video-color-grade",
                "ffmpeg": ffmpeg_available(),
                "modes": list_modes(),
            }

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/preview")
        async def preview(body: PreviewBody) -> dict[str, Any]:
            plan = await self._build_preview_plan(body)
            cmd = build_grade_command(plan)
            return {
                "plan": plan.to_dict(),
                "ffmpeg_cmd": cmd,
                "available_modes": list_modes(),
            }

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
                    status_code=404, detail={"problem": "task not found or already done"},
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
                    status_code=404, detail={"problem": "no graded file"},
                )
            p = Path(rec.extra["output_path"])
            if not p.is_file():
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "graded file missing on disk"},
                )
            return FileResponse(p)

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        out_path = body.output_path  # may be None — resolved in worker
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={
                "input_path": body.input_path,
                "mode": body.mode,
                **({"output_path": out_path} if out_path else {}),
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

    async def _build_preview_plan(self, body: PreviewBody):
        return await asyncio.to_thread(
            plan_grade,
            input_path=body.input_path,
            output_path="<preview>.mp4",
            mode=body.mode,
            clamp_pct=body.clamp_pct,
            sample_window_sec=body.sample_window_sec,
            sample_frames=body.sample_frames,
        )

    # ── worker ──────────────────────────────────────────────────────

    async def _run(self, task_id: str) -> None:
        try:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                return
            params = rec.params
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "probing",
            })

            input_path = params["input_path"]
            duration = await asyncio.to_thread(probe_video_duration_sec, input_path)
            if duration <= 0 and not Path(input_path).is_file():
                raise FileNotFoundError(
                    f"input file missing or unreadable: {input_path}"
                )

            output_path = params.get("output_path")
            if not output_path:
                output_dir = self._data_dir / "outputs" / task_id
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = str(output_dir / "graded.mp4")

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "analyzing",
            })
            plan = await asyncio.to_thread(
                plan_grade,
                input_path=input_path,
                output_path=output_path,
                mode=params.get("mode", MODE_AUTO),
                clamp_pct=float(params.get("clamp_pct", DEFAULT_GRADE_CLAMP_PCT)),
                sample_window_sec=float(
                    params.get("sample_window_sec", DEFAULT_PROBE_DURATION_SEC),
                ),
                sample_frames=int(
                    params.get("sample_frames", DEFAULT_SAMPLE_FRAMES),
                ),
            )

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "rendering",
                "filter_string": plan.filter_string,
            })
            timeout_sec = float(
                (await self._tm.get_config()).get(
                    "render_timeout_sec",
                    str(DEFAULT_RENDER_TIMEOUT_SEC),
                ),
            )
            result = await asyncio.to_thread(
                apply_grade, plan,
                timeout_sec=timeout_sec,
                crf=int(params.get("crf", 18)),
                preset=str(params.get("libx264_preset", "fast")),
            )

            verification = to_verification(result)
            verification_dict = verification.to_dict()
            plan_dict = plan.to_dict()
            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                # Mirrors the bgm-mixer / storyboard dual-storage pattern:
                # ``result`` is the API-facing payload (``GET /tasks/{id}``)
                # while ``extra`` writes the same blobs into dedicated
                # columns so a future SQL query / migration can read them
                # without having to re-parse JSON.
                result={
                    "input_path": input_path,
                    "output_path": output_path,
                    "duration_sec": result.duration_sec,
                    "elapsed_sec": result.elapsed_sec,
                    "output_size_bytes": result.output_size_bytes,
                    "ffmpeg_cmd": result.ffmpeg_cmd,
                    "verification": verification_dict,
                    "plan": plan_dict,
                },
                extra={
                    "input_path": input_path,
                    "output_path": output_path,
                    "mode": plan.mode,
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
                f"video-color-grade failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
