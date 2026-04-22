"""video-translator — orchestrate ASR + translate + TTS + mux."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach, QualityGates, TaskStatus, UIEventEmitter, VendorError,
)

from translator_engine import (
    SUPPORTED_LANGS, build_extract_audio_cmd, build_mux_cmd,
    concat_audio_chunks_cmd, select_tts_provider, to_srt,
    translate_chunks, translate_chunks_offline, whisper_cpp_transcribe,
)
from task_manager import TranslatorTaskManager

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    source_video_path: str
    target_language: str = "en"
    voice: str = "en-US-AriaNeural"
    burn_subtitles: bool = False
    keep_original_audio_volume: float = 0.15
    tts_provider: str = "auto"


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = TranslatorTaskManager(data_dir / "video_translator.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}
        self._brain = None

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "video_translator_create",
                 "description": "Translate a video: ASR + translate + TTS + mux.",
                 "input_schema": {"type": "object",
                                  "properties": {"source_video_path": {"type": "string"},
                                                 "target_language": {"type": "string"}},
                                  "required": ["source_video_path", "target_language"]}},
                {"name": "video_translator_status",
                 "description": "Get task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "video_translator_list",
                 "description": "List recent tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "video_translator_cancel",
                 "description": "Cancel a running task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.log("video-translator loaded")

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
                        f"video-translator on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "video_translator_create":
                return f"已创建任务 {await self._create(CreateBody(**args))}"
            if tool_name == "video_translator_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "video_translator_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "video_translator_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "video-translator",
                    "ffmpeg": bool(shutil.which("ffmpeg"))}

        @router.get("/languages")
        async def languages():
            return {"languages": [{"code": k, "name": v} for k, v in SUPPORTED_LANGS.items()]}

        @router.get("/config")
        async def get_config():
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/upload-video")
        async def upload_video(file: UploadFile = File(...)):
            data_dir = self._api.get_data_dir() / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(file.filename or "video.mp4").suffix or ".mp4"
            target = data_dir / f"{uuid.uuid4().hex[:12]}{ext}"
            with target.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            return {"path": str(target), "name": file.filename}

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["source_video_path", "target_language"],
                non_empty_strings=["source_video_path", "target_language"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            if not Path(body.source_video_path).exists():
                rendered = self._coach.render(FileNotFoundError(body.source_video_path),
                                              raw_message=f"file not found: {body.source_video_path}")
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

        @router.get("/tasks/{task_id}/video")
        async def serve_video(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("output_video_path"):
                raise HTTPException(status_code=404, detail={"problem": "no video"})
            p = Path(rec.extra["output_video_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "video file missing"})
            return FileResponse(p, media_type="video/mp4", filename=p.name)

        @router.get("/tasks/{task_id}/srt")
        async def serve_srt(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("translated_srt_path"):
                raise HTTPException(status_code=404, detail={"problem": "no srt"})
            p = Path(rec.extra["translated_srt_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "srt file missing"})
            return FileResponse(p, media_type="text/plain", filename=p.name)

    # ── llm wiring ─────────────────────────────────────────────

    def _llm_call(self):
        try:
            self._brain = self._brain or self._api.get_brain()
        except Exception:
            self._brain = None
        if not self._brain:
            return None
        brain = self._brain

        async def call(prompt: str, max_tokens: int = 2000, **_):
            think = getattr(brain, "think_lightweight", None)
            if not callable(think):
                return ""
            resp = await think(prompt=prompt, max_tokens=max_tokens)
            text = getattr(resp, "text", None) or getattr(resp, "content", None) or ""
            if not isinstance(text, str):
                try: text = "".join(getattr(b, "text", "") for b in text)
                except TypeError: text = str(text)
            return text
        return call

    # ── task plumbing ──────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.target_language, params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"source_video_path": body.source_video_path,
                   "target_language": body.target_language},
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
        cfg = await self._tm.get_config()

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            output_dir = self._api.get_data_dir() / "outputs" / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            source = Path(params["source_video_path"])
            ffmpeg_path = cfg.get("ffmpeg_path") or "ffmpeg"
            if not shutil.which(ffmpeg_path):
                raise VendorError("没找到 ffmpeg。请先安装 FFmpeg 并加入 PATH。",
                                  retryable=False)

            # 1) extract audio
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "extract"})
            wav = output_dir / "src.wav"
            await asyncio.to_thread(
                subprocess.run,
                build_extract_audio_cmd(source=source, output_audio=wav, ffmpeg=ffmpeg_path),
                check=True, timeout=600, capture_output=True,
            )

            # 2) ASR
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "transcribe"})
            chunks = await whisper_cpp_transcribe(wav, model=cfg.get("asr_model", "base"))
            if not chunks:
                raise VendorError(
                    "未能识别出语音。可能没装 whisper-cli，或音频太安静。",
                    retryable=False,
                )

            # 3) translate (LLM, with offline fallback)
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "translate"})
            llm = self._llm_call()
            if llm:
                translated = await translate_chunks(
                    chunks, target_lang=params["target_language"], llm_call=llm,
                )
            else:
                translated = translate_chunks_offline(chunks)

            # 4) write SRTs
            srt_path = output_dir / "original.srt"
            translated_srt = output_dir / f"{params['target_language']}.srt"
            srt_path.write_text(to_srt(chunks), encoding="utf-8")
            translated_srt.write_text(to_srt(translated), encoding="utf-8")

            # 5) TTS each translated chunk into its own file
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "tts"})
            tts_prov = select_tts_provider(params.get("tts_provider", "auto"))
            parts: list[Path] = []
            for i, c in enumerate(translated):
                self._events.emit("task_updated",
                                  {"id": task_id, "status": "running",
                                   "stage": f"tts {i+1}/{len(translated)}"})
                res = await tts_prov.synthesize(
                    text=c.text, voice=params.get("voice", "en-US-AriaNeural"),
                    output_dir=output_dir,
                )
                parts.append(res.audio_path)

            # 6) concat dubbed audio
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "concat"})
            dubbed = output_dir / "dubbed.m4a"
            await asyncio.to_thread(
                subprocess.run,
                concat_audio_chunks_cmd(parts=parts, list_file=output_dir / "_list.txt",
                                         output_audio=dubbed, ffmpeg=ffmpeg_path),
                check=True, timeout=600, capture_output=True,
            )

            # 7) mux
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "mux"})
            output_video = output_dir / "translated.mp4"
            await asyncio.to_thread(
                subprocess.run,
                build_mux_cmd(
                    source_video=source, dubbed_audio=dubbed, srt_file=translated_srt,
                    output_video=output_video, ffmpeg=ffmpeg_path,
                    burn_subtitles=bool(params.get("burn_subtitles")),
                    keep_original_audio_volume=float(params.get("keep_original_audio_volume", 0.0)),
                ),
                check=True, timeout=900, capture_output=True,
            )

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"output_video_path": str(output_video),
                        "srt_path": str(srt_path),
                        "translated_srt_path": str(translated_srt),
                        "dubbed_audio_path": str(dubbed)},
                extra={"output_video_path": str(output_video),
                       "srt_path": str(srt_path),
                       "translated_srt_path": str(translated_srt),
                       "dubbed_audio_path": str(dubbed)},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "output_video_path": str(output_video)})
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode("utf-8", errors="ignore")[-400:]
            await self._fail(task_id, VendorError(f"FFmpeg failed: {stderr}", retryable=False))
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
