"""subtitle-maker — generate SRT/VTT subtitles, optional burn-in."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach, QualityGates, TaskStatus, UIEventEmitter,
)

from subtitle_engine import (
    burn_subtitles_command, to_srt, to_vtt, whisper_cpp_transcribe,
)
from task_manager import SubtitleTaskManager

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    source_path: str = Field(..., min_length=1)
    language: str = "auto"
    asr_model: str = "base"
    output_format: str = "srt"   # srt | vtt | both
    burn_into_video: bool = False


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = SubtitleTaskManager(data_dir / "subtitle.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "subtitle_maker_create",
                 "description": "Generate subtitles for a video/audio file.",
                 "input_schema": {"type": "object",
                                  "properties": {"source_path": {"type": "string"},
                                                 "language": {"type": "string"},
                                                 "burn_into_video": {"type": "boolean"}},
                                  "required": ["source_path"]}},
                {"name": "subtitle_maker_status",
                 "description": "Get task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "subtitle_maker_list",
                 "description": "List recent subtitle tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "subtitle_maker_cancel",
                 "description": "Cancel a running subtitle task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.log("subtitle-maker loaded")

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
                        f"subtitle-maker on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "subtitle_maker_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建任务 {tid}"
            if tool_name == "subtitle_maker_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "subtitle_maker_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "subtitle_maker_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "subtitle-maker"}

        @router.get("/config")
        async def get_config():
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/upload")
        async def upload(file: UploadFile = File(...)):
            data_dir = self._api.get_data_dir() / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / file.filename
            with target.open("wb") as fp:
                while chunk := await file.read(1024 * 1024):
                    fp.write(chunk)
            return {"path": str(target)}

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(), required=["source_path"], non_empty_strings=["source_path"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            try:
                tid = await self._create(body)
            except FileNotFoundError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=404, detail=rendered.to_dict())
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

        @router.get("/tasks/{task_id}/srt")
        async def serve_srt(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("srt_path"):
                raise HTTPException(status_code=404, detail={"problem": "no subtitle"})
            p = Path(rec.extra["srt_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "subtitle file missing"})
            return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/plain",
                                     headers={"Content-Disposition": f'attachment; filename="{task_id}.srt"'})

        @router.get("/tasks/{task_id}/vtt")
        async def serve_vtt(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("vtt_path"):
                raise HTTPException(status_code=404, detail={"problem": "no subtitle"})
            p = Path(rec.extra["vtt_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "subtitle file missing"})
            return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/vtt",
                                     headers={"Content-Disposition": f'attachment; filename="{task_id}.vtt"'})

        @router.get("/tasks/{task_id}/burned-video")
        async def serve_burned(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("burned_video_path"):
                raise HTTPException(status_code=404, detail={"problem": "no burned video"})
            p = Path(rec.extra["burned_video_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "video file missing"})
            return FileResponse(p)

    async def _create(self, body: CreateBody) -> str:
        source = Path(body.source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"源文件不存在: {source}")
        tid = await self._tm.create_task(
            prompt=body.source_path,
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"source_path": str(source), "language": body.language},
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
        source = Path(rec.extra.get("source_path") or "")
        if not source.exists():
            await self._fail(task_id, FileNotFoundError(f"source missing: {source}"))
            return

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "transcribe"})

            chunks = await whisper_cpp_transcribe(
                source,
                model=params.get("asr_model", "base"),
                language=params.get("language", "auto"),
                binary="whisper-cli",
            )
            if not chunks:
                # whisper.cpp not installed → friendly stub w/ user guidance
                from openakita_plugin_sdk.contrib import VendorError
                raise VendorError(
                    "whisper.cpp 未安装；安装后重试。"
                    "https://github.com/ggerganov/whisper.cpp/releases",
                    retryable=False,
                )

            output_dir = self._api.get_data_dir() / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            out_format = params.get("output_format", "srt")
            srt_path = output_dir / f"{task_id}.srt"
            vtt_path = output_dir / f"{task_id}.vtt"
            srt_path.write_text(to_srt(chunks), encoding="utf-8")
            vtt_path.write_text(to_vtt(chunks), encoding="utf-8")

            burned = ""
            if params.get("burn_into_video"):
                self._events.emit("task_updated", {"id": task_id, "status": "running",
                                                   "stage": "burn"})
                cfg = await self._tm.get_config()
                ffmpeg = cfg.get("ffmpeg_path") or "ffmpeg"
                burned_path = output_dir / f"{task_id}.mp4"
                cmd = burn_subtitles_command(
                    source_video=source, srt_file=srt_path, output=burned_path,
                    ffmpeg=ffmpeg,
                )
                await asyncio.to_thread(
                    subprocess.run, cmd, check=True, timeout=900, capture_output=True,
                )
                burned = str(burned_path)

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"srt_path": str(srt_path), "vtt_path": str(vtt_path),
                        "burned_video_path": burned,
                        "chunk_count": len(chunks)},
                extra={"srt_path": str(srt_path), "vtt_path": str(vtt_path),
                       "burned_video_path": burned},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "srt_path": str(srt_path),
                                               "burned_video_path": burned})
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
