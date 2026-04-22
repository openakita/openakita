"""ClipSense Video Editor — AI-powered video editing plugin.

Backend entry point providing REST API endpoints for the frontend UI.
Supports 4 editing modes: highlight extraction, silence removal,
topic splitting, and talking-head polish. Uses DashScope Paraformer
for ASR, Qwen for content analysis, and local ffmpeg for execution.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from clip_sense_inline.storage_stats import collect_storage_stats
from clip_sense_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)

from clip_asr_client import ClipAsrClient
from clip_ffmpeg_ops import FFmpegOps
from clip_models import (
    MODES,
    MODES_BY_ID,
    SILENCE_PRESETS,
    SILENCE_PRESETS_BY_ID,
    estimate_cost,
    get_error_hints,
    get_mode,
    mode_to_dict,
)
from clip_pipeline import ClipPipelineContext, run_pipeline
from clip_task_manager import TaskManager

logger = logging.getLogger(__name__)


# ── Request models ──

class CreateTaskBody(BaseModel):
    mode: str = "highlight_extract"
    source_video_path: str = ""
    source_url: str = ""
    flavor: str = ""
    target_count: int = 5
    target_duration: int = 30
    threshold_db: float = -40.0
    min_silence_sec: float = 0.5
    padding_sec: float = 0.1
    silence_preset: str = ""
    target_segment_duration: int = 180
    burn_subtitle: bool = False
    output_format: str = "mp4"


class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]


# ── Plugin entry ──

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._data_dir = data_dir
        self._tm = TaskManager(data_dir / "clip_sense.db")
        self._client: ClipAsrClient | None = None
        self._ffmpeg: FFmpegOps | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running_pipelines: dict[str, ClipPipelineContext] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "clip_sense_create",
                "description": "Create a video editing task (highlight extraction, silence removal, topic splitting, or talking-head polish)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["highlight_extract", "silence_clean", "topic_split", "talking_polish"]},
                        "source_video_path": {"type": "string", "description": "Path to the source video file"},
                        "flavor": {"type": "string", "description": "Highlight selection preference"},
                    },
                    "required": ["mode", "source_video_path"],
                },
            },
            {
                "name": "clip_sense_status",
                "description": "Check status of a clip-sense editing task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "clip_sense_list",
                "description": "List recent clip-sense editing tasks",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
            {
                "name": "clip_sense_transcribe",
                "description": "Transcribe a video file using Paraformer ASR",
                "input_schema": {
                    "type": "object",
                    "properties": {"source_video_path": {"type": "string"}},
                    "required": ["source_video_path"],
                },
            },
            {
                "name": "clip_sense_cancel",
                "description": "Cancel a running clip-sense task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
        ], handler=self._handle_tool)

        api.spawn_task(self._async_init(), name="clip-sense:init")
        api.log("ClipSense plugin loaded")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("dashscope_api_key")
        if api_key:
            self._client = ClipAsrClient(api_key)
        ffmpeg_path = await self._tm.get_config("ffmpeg_path") or ""
        self._ffmpeg = FFmpegOps(ffmpeg_path if ffmpeg_path else None)
        self._start_polling()

    async def on_unload(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("clip-sense poll task drain: %s", exc)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("clip-sense ASR client close: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("clip-sense task manager close: %s", exc)

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "clip_sense_create":
            task = await self._create_task_internal(args)
            return f"Task created: {task['id']} (mode: {task['mode']}, status: {task['status']})"
        elif tool_name == "clip_sense_status":
            task = await self._tm.get_task(args.get("task_id", ""))
            if not task:
                return "Task not found"
            return (
                f"Task {task['id']}: status={task['status']}, mode={task['mode']}, "
                f"step={task.get('pipeline_step', 'N/A')}"
            )
        elif tool_name == "clip_sense_list":
            result = await self._tm.list_tasks(limit=args.get("limit", 10))
            lines = [f"Total: {result['total']} tasks"]
            for t in result["tasks"][:10]:
                lines.append(f"  {t['id']}: {t['mode']} / {t['status']}")
            return "\n".join(lines)
        elif tool_name == "clip_sense_transcribe":
            return "Transcription via tool not yet implemented — use the UI."
        elif tool_name == "clip_sense_cancel":
            tid = args.get("task_id", "")
            ctx = self._running_pipelines.get(tid)
            if ctx:
                ctx.cancelled = True
                return f"Cancel requested for task {tid}"
            return f"Task {tid} not found in running pipelines"
        return f"Unknown tool: {tool_name}"

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:
        uploads_dir = self._data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        add_upload_preview_route(router, base_dir=uploads_dir)

        # 1. POST /tasks — create task
        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            d = body.model_dump() if hasattr(body, "model_dump") else body.dict()
            task = await self._create_task_internal(d)
            return task

        # 2. GET /tasks — list tasks
        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict[str, Any]:
            return await self._tm.list_tasks(
                status=status, mode=mode, offset=offset, limit=limit
            )

        # 3. GET /tasks/{task_id} — get task
        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return task

        # 4. DELETE /tasks/{task_id}
        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, str]:
            if not await self._tm.delete_task(task_id):
                raise HTTPException(404, "Task not found")
            return {"status": "deleted"}

        # 5. POST /tasks/{task_id}/cancel
        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, str]:
            ctx = self._running_pipelines.get(task_id)
            if ctx:
                ctx.cancelled = True
                return {"status": "cancel_requested"}
            await self._tm.update_task(task_id, status="cancelled")
            return {"status": "cancelled"}

        # 6. POST /tasks/{task_id}/retry
        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] not in ("failed", "cancelled"):
                raise HTTPException(400, "Can only retry failed or cancelled tasks")
            new_params = task.get("params") or {}
            new_task = await self._create_task_internal({
                "mode": task["mode"],
                "source_video_path": task.get("source_video_path", ""),
                **new_params,
            })
            return new_task

        # 7. GET /tasks/{task_id}/download
        @router.get("/tasks/{task_id}/download")
        async def download_output(task_id: str) -> Any:
            from fastapi.responses import FileResponse
            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_path"):
                raise HTTPException(404, "Output not found")
            p = Path(task["output_path"])
            if not p.exists():
                raise HTTPException(404, "Output file missing")
            return FileResponse(p, filename=p.name)

        # 8. GET /tasks/{task_id}/subtitle
        @router.get("/tasks/{task_id}/subtitle")
        async def download_subtitle(task_id: str) -> Any:
            from fastapi.responses import FileResponse
            task = await self._tm.get_task(task_id)
            if not task or not task.get("subtitle_path"):
                raise HTTPException(404, "Subtitle not found")
            p = Path(task["subtitle_path"])
            if not p.exists():
                raise HTTPException(404, "Subtitle file missing")
            return FileResponse(p, filename=p.name, media_type="text/plain")

        # 9. GET /tasks/{task_id}/transcript
        @router.get("/tasks/{task_id}/transcript")
        async def get_transcript(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task or not task.get("transcript_id"):
                raise HTTPException(404, "Transcript not found")
            tr = await self._tm.get_transcript(task["transcript_id"])
            if not tr:
                raise HTTPException(404, "Transcript record not found")
            return tr

        # 10. POST /upload
        @router.post("/upload")
        async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
            uploads_dir = self._data_dir / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename or 'video.mp4'}"
            dest = uploads_dir / safe_name
            with open(dest, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
            url = build_preview_url("clip-sense", safe_name)
            return {
                "path": str(dest),
                "filename": safe_name,
                "url": url,
                "size": dest.stat().st_size,
            }

        # 11. GET /library
        @router.get("/library")
        async def list_library(offset: int = 0, limit: int = 50) -> dict[str, Any]:
            return await self._tm.list_transcripts(offset=offset, limit=limit)

        # 12. POST /library/{tid}/transcribe
        @router.post("/library/{tid}/transcribe")
        async def transcribe_library(tid: str) -> dict[str, str]:
            tr = await self._tm.get_transcript(tid)
            if not tr:
                raise HTTPException(404, "Transcript not found")
            return {"status": "transcription_queued", "transcript_id": tid}

        # 13. DELETE /library/{tid}
        @router.delete("/library/{tid}")
        async def delete_library(tid: str) -> dict[str, str]:
            if not await self._tm.delete_transcript(tid):
                raise HTTPException(404, "Not found")
            return {"status": "deleted"}

        # 14. GET /settings
        @router.get("/settings")
        async def get_settings() -> dict[str, str]:
            return await self._tm.get_all_config()

        # 15. PUT /settings
        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict[str, str]:
            await self._tm.set_configs(body.updates)
            if "dashscope_api_key" in body.updates:
                key = body.updates["dashscope_api_key"]
                if key:
                    if self._client:
                        self._client.update_api_key(key)
                    else:
                        self._client = ClipAsrClient(key)
                else:
                    self._client = None
            if "ffmpeg_path" in body.updates:
                fp = body.updates["ffmpeg_path"]
                self._ffmpeg = FFmpegOps(fp if fp else None)
            return {"status": "ok"}

        # 16. GET /storage/stats
        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            roots = [
                self._data_dir / "uploads",
                self._data_dir / "tasks",
            ]
            stats = await collect_storage_stats([r for r in roots if r.exists()])
            return stats.to_dict()

        # 17. GET /ffmpeg/status
        @router.get("/ffmpeg/status")
        async def ffmpeg_status() -> dict[str, Any]:
            if self._ffmpeg:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, self._ffmpeg.detect)
            return {"available": False, "version": "", "path": ""}

        # 18. GET /modes
        @router.get("/modes")
        async def get_modes() -> list[dict[str, Any]]:
            return [mode_to_dict(m) for m in MODES]

    # ── Internal task creation ──

    async def _create_task_internal(self, args: dict[str, Any]) -> dict[str, Any]:
        mode_id = args.get("mode", "highlight_extract")
        mode_def = MODES_BY_ID.get(mode_id)
        if not mode_def:
            raise HTTPException(400, f"Unknown mode: {mode_id}")

        source_path = args.get("source_video_path", "")
        if not source_path:
            raise HTTPException(400, "source_video_path is required")

        preset_id = args.get("silence_preset", "")
        if preset_id and preset_id in SILENCE_PRESETS_BY_ID:
            preset = SILENCE_PRESETS_BY_ID[preset_id]
            args.setdefault("threshold_db", preset.threshold_db)
            args.setdefault("min_silence_sec", preset.min_silence_sec)
            args.setdefault("padding_sec", preset.padding_sec)

        params = {
            k: v for k, v in args.items()
            if k not in ("mode", "source_video_path", "source_url")
        }

        task = await self._tm.create_task(
            mode=mode_id,
            source_video_path=source_path,
            params=params,
        )

        source_url = args.get("source_url", "")
        if not source_url and Path(source_path).exists():
            rel = Path(source_path).name
            source_url = build_preview_url("clip-sense", rel)

        task_dir = self._data_dir / "tasks" / task["id"]
        ctx = ClipPipelineContext(
            task_id=task["id"],
            mode=mode_id,
            params=params,
            task_dir=task_dir,
            source_video_path=Path(source_path),
            source_url=source_url,
        )
        self._running_pipelines[task["id"]] = ctx
        self._api.spawn_task(
            self._run_task(ctx), name=f"clip-sense:task:{task['id']}"
        )

        return task

    async def _run_task(self, ctx: ClipPipelineContext) -> None:
        try:
            await run_pipeline(
                ctx, self._tm, self._client, self._ffmpeg, self._emit
            )
        except Exception as exc:
            logger.exception("clip-sense pipeline unexpected error: %s", exc)
        finally:
            self._running_pipelines.pop(ctx.task_id, None)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._api.broadcast_ui_event(event, data)
        except Exception:
            pass

    # ── Polling ──

    def _start_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Periodic check for stale running tasks."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    running = await self._tm.get_running_tasks()
                    for t in running:
                        tid = t["id"]
                        if tid not in self._running_pipelines:
                            await self._tm.update_task(
                                tid, status="failed",
                                error_kind="unknown",
                                error_message="Task found in running state but no pipeline context (likely server restart)",
                            )
                except Exception as exc:
                    logger.warning("clip-sense poll error: %s", exc)
        except asyncio.CancelledError:
            pass
