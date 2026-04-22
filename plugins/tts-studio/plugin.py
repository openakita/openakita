"""tts-studio — multi-segment / multi-speaker TTS, auto-merged."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)
from pydantic import BaseModel, Field
from studio_engine import (
    PRESET_VOICES_ZH,
    concat_audio_command,
    configure_credentials,
    parse_dialogue_script,
    select_tts_provider,
)
from task_manager import StudioTaskManager

from openakita.plugins.api import PluginAPI, PluginBase

_SENSITIVE_CONFIG_KEYS = {"dashscope_api_key", "openai_api_key"}


def _redacted_config(cfg: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in cfg.items():
        if k in _SENSITIVE_CONFIG_KEYS and v:
            out[k] = f"***{v[-4:]}" if len(v) > 4 else "***"
        else:
            out[k] = v
    return out

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    script: str = Field(..., min_length=1)
    title: str = "未命名"
    default_voice: str = "Cherry"
    voice_map: dict[str, str] = Field(default_factory=dict)   # {"A": "voiceId", ...}
    provider: str = "auto"


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = StudioTaskManager(data_dir / "tts_studio.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "tts_studio_create",
                 "description": "Synthesize multi-segment dialogue/script into one merged audio.",
                 "input_schema": {"type": "object",
                                  "properties": {"script": {"type": "string"}},
                                  "required": ["script"]}},
                {"name": "tts_studio_status",
                 "description": "Get task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "tts_studio_list",
                 "description": "List recent tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "tts_studio_cancel",
                 "description": "Cancel a running task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.spawn_task(self._load_credentials())
        api.log("tts-studio loaded")

    async def _load_credentials(self) -> None:
        cfg = await self._tm.get_config()
        configure_credentials(
            dashscope_api_key=cfg.get("dashscope_api_key") or os.environ.get("DASHSCOPE_API_KEY", ""),
            openai_api_key=cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", ""),
        )

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
                        f"tts-studio on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "tts_studio_create":
                return f"已创建任务 {await self._create(CreateBody(**args))}"
            if tool_name == "tts_studio_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "tts_studio_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "tts_studio_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "tts-studio"}

        @router.get("/voices")
        async def voices():
            return {"presets": PRESET_VOICES_ZH}

        @router.get("/config")
        async def get_config():
            return _redacted_config(await self._tm.get_config())

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            await self._load_credentials()
            return _redacted_config(await self._tm.get_config())

        @router.get("/settings")
        async def get_settings():
            return _redacted_config(await self._tm.get_config())

        @router.post("/settings")
        async def set_settings(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            await self._load_credentials()
            return _redacted_config(await self._tm.get_config())

        @router.post("/preview")
        async def preview(body: CreateBody):
            """Parse the script & return planned segments without TTSing."""
            script = parse_dialogue_script(
                body.script, default_voice=body.default_voice,
                voice_map=body.voice_map, title=body.title,
            )
            return {"title": script.title,
                    "segments": [{"index": s.index, "speaker": s.speaker,
                                  "voice": s.voice, "text": s.text} for s in script.segments]}

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(), required=["script"], non_empty_strings=["script"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
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

        @router.get("/tasks/{task_id}/audio")
        async def serve_audio(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("merged_audio_path"):
                raise HTTPException(status_code=404, detail={"problem": "no audio"})
            p = Path(rec.extra["merged_audio_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "audio file missing"})
            return FileResponse(p)

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.title, params=body.model_dump(),
            status=TaskStatus.QUEUED.value, extra={"script_text": body.script},
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
        if rec is None:
            return
        params = rec.params

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            output_dir = self._api.get_data_dir() / "outputs" / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            script = parse_dialogue_script(
                params.get("script", ""),
                default_voice=params.get("default_voice", "Cherry"),
                voice_map=params.get("voice_map") or {},
                title=params.get("title", "未命名"),
            )
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "tts", "total": len(script.segments)})

            tts_prov = select_tts_provider(params.get("provider", "auto"))
            parts: list[Path] = []
            for i, seg in enumerate(script.segments):
                self._events.emit("task_updated", {"id": task_id, "status": "running",
                                                   "stage": f"tts {i+1}/{len(script.segments)}"})
                res = await tts_prov.synthesize(
                    text=seg.text, voice=seg.voice,
                    rate=seg.rate, pitch=seg.pitch, output_dir=output_dir,
                )
                parts.append(res.audio_path)

            # Concat
            merged_path = output_dir / "merged.mp3"
            ffmpeg_path = (await self._tm.get_config()).get("ffmpeg_path") or "ffmpeg"
            from shutil import which
            if which(ffmpeg_path):
                self._events.emit("task_updated", {"id": task_id, "status": "running",
                                                   "stage": "concat"})
                cmd = concat_audio_command(parts=parts, list_file=output_dir / "_list.txt",
                                            output=merged_path, ffmpeg=ffmpeg_path)
                await asyncio.to_thread(
                    subprocess.run, cmd, check=True, timeout=600, capture_output=True,
                )
            else:
                # No ffmpeg → keep the first part as the "merged" output (degraded mode)
                merged_path = parts[0] if parts else merged_path

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"merged_audio_path": str(merged_path),
                        "segment_count": len(script.segments),
                        "parts": [str(p) for p in parts]},
                extra={"merged_audio_path": str(merged_path),
                       "segment_count": len(script.segments)},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "merged_audio_path": str(merged_path)})
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
