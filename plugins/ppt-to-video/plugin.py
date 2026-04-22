"""ppt-to-video — plugin entry point.

Wires :mod:`slide_engine` and :mod:`task_manager` to the OpenAkita
plugin host (HTTP routes + brain tools + lifecycle).  All slide-deck
math (LibreOffice export, python-pptx notes extraction, ffmpeg
clip / concat) lives in :mod:`slide_engine`; this file is purely glue.

Conventions copied verbatim from
``plugins/video-bg-remove/plugin.py``:

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* :class:`QualityGates` validates every request body before queuing,
* the worker offloads soffice + ffmpeg + TTS to a thread
  (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import shutil
import sys
import subprocess
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    add_upload_preview_route,
)

from slide_engine import (
    DEFAULT_FPS,
    DEFAULT_SILENT_SLIDE_SEC,
    DEFAULT_TTS_PROVIDER,
    DEFAULT_VOICE,
    SUPPORTED_INPUT_EXTENSIONS,
    ffmpeg_available,
    libreoffice_available,
    plan_video,
    pptx_available,
    resolve_libreoffice,
    run_pipeline,
    to_verification,
)
from task_manager import PptVideoTaskManager

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    input_path: str = Field(..., min_length=1)
    output_path: str | None = None
    voice: str = DEFAULT_VOICE
    tts_provider: str = DEFAULT_TTS_PROVIDER
    silent_slide_sec: float = DEFAULT_SILENT_SLIDE_SEC
    fps: int = DEFAULT_FPS
    crf: int = 20
    libx264_preset: str = "fast"


class PreviewBody(BaseModel):
    input_path: str = Field(..., min_length=1)
    output_path: str | None = None
    voice: str = DEFAULT_VOICE
    tts_provider: str = DEFAULT_TTS_PROVIDER
    silent_slide_sec: float = DEFAULT_SILENT_SLIDE_SEC


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = PptVideoTaskManager(data_dir / "ppt_to_video.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "ppt_to_video_create",
                 "description": "Convert a .pptx to a narrated MP4. Each slide becomes one image clip with a TTS voice-over of the speaker notes.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "input_path": {"type": "string"},
                         "output_path": {"type": "string"},
                         "voice": {"type": "string"},
                         "tts_provider": {"type": "string"},
                     },
                     "required": ["input_path"],
                 }},
                {"name": "ppt_to_video_status",
                 "description": "Get the status of a PPT→video job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "ppt_to_video_list",
                 "description": "List recent PPT→video jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "ppt_to_video_cancel",
                 "description": "Cancel a running PPT→video job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "ppt_to_video_check_deps",
                 "description": "Check whether LibreOffice (soffice) + python-pptx + ffmpeg are ready.",
                 "input_schema": {"type": "object", "properties": {}}},
            ],
            self._handle_tool_call,
        )
        api.log("ppt-to-video loaded")

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
                        f"ppt-to-video on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── helpers ────────────────────────────────────────────────────

    def _check_deps(self) -> dict[str, Any]:
        soffice_path = resolve_libreoffice()
        return {
            "soffice": soffice_path is not None,
            "soffice_path": soffice_path,
            "pptx": pptx_available(),
            "ffmpeg": ffmpeg_available(),
            "soffice_install_hint": (
                "Install LibreOffice (https://www.libreoffice.org) — "
                "the headless `soffice --convert-to png` is what we use "
                "to turn slides into images."
            ) if soffice_path is None else None,
            "pptx_install_hint": (
                "Install python-pptx with `pip install python-pptx` to enable "
                "speaker-notes extraction."
            ) if not pptx_available() else None,
        }

    def _load_avatar_speaker_providers(self):
        """Lazy-load avatar-speaker's TTS providers via sibling import.

        Returns ``None`` when avatar-speaker is missing so the plugin
        can still render silent slideshows (driven only by
        ``silent_slide_sec``).  We deliberately swallow ImportErrors
        here — the worker will surface "TTS unavailable" through
        ``tts_fallbacks`` which the verification envelope will flag.
        """
        try:
            src = Path(__file__).resolve().parent.parent / "avatar-speaker" / "providers.py"
            if not src.is_file():
                return None
            alias = "_oa_avp_for_ppt2video"
            if alias in sys.modules:
                return sys.modules[alias]
            spec = importlib.util.spec_from_file_location(alias, src)
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[alias] = mod
            spec.loader.exec_module(mod)
            return mod
        except Exception as exc:  # noqa: BLE001
            self._api.log(
                f"ppt-to-video: failed to load avatar-speaker providers: {exc!r}",
                level="warning",
            )
            return None

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "ppt_to_video_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建 PPT→视频 任务 {tid}"
            if tool_name == "ppt_to_video_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "ppt_to_video_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} {Path(r.extra.get('output_path', '') or '').name}"
                    for r in rows
                )
            if tool_name == "ppt_to_video_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "ppt_to_video_check_deps":
                deps = self._check_deps()
                lines = [
                    f"soffice: {'✓' if deps['soffice'] else '✗'} ({deps['soffice_path'] or '未找到'})",
                    f"python-pptx: {'✓' if deps['pptx'] else '✗'}",
                    f"ffmpeg: {'✓' if deps['ffmpeg'] else '✗'}",
                ]
                if deps["soffice_install_hint"]:
                    lines.append(deps["soffice_install_hint"])
                if deps["pptx_install_hint"]:
                    lines.append(deps["pptx_install_hint"])
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
            return {
                "ok": True,
                "plugin": "ppt-to-video",
                "deps": self._check_deps(),
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

        @router.post("/preview")
        async def preview(body: PreviewBody) -> dict[str, Any]:
            try:
                # Build a minimal plan WITHOUT actually invoking soffice
                # (preview mustn't require soffice on the host).  We
                # surface the input path + chosen voice + extension
                # checks so the user can iterate quickly.
                ext = Path(body.input_path).suffix.lower()
                if ext not in SUPPORTED_INPUT_EXTENSIONS:
                    raise ValueError(
                        f"unsupported input extension {ext!r}; "
                        f"supported: {SUPPORTED_INPUT_EXTENSIONS}",
                    )
                if not Path(body.input_path).is_file():
                    raise FileNotFoundError(
                        f"input file not found: {body.input_path}",
                    )
                preview_payload = {
                    "input_path": body.input_path,
                    "output_path": body.output_path or self._default_output_path(body.input_path),
                    "voice": body.voice,
                    "tts_provider": body.tts_provider,
                    "silent_slide_sec": body.silent_slide_sec,
                }
            except (ValueError, TypeError, FileNotFoundError) as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from e
            return {"plan": preview_payload, "deps": self._check_deps()}

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
            return FileResponse(p, media_type="video/mp4", filename=p.name)

    def _default_output_path(self, input_path: str) -> str:
        stem = Path(input_path).stem or "presentation"
        return str(self._data_dir / "outputs" / f"{stem}.mp4")

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={
                "input_path": body.input_path,
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
            output_path = params.get("output_path") or self._default_output_path(input_path)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            work_dir = self._data_dir / "work" / task_id
            work_dir.mkdir(parents=True, exist_ok=True)

            plan = await asyncio.to_thread(
                plan_video,
                input_path=input_path,
                output_path=output_path,
                work_dir=str(work_dir),
                voice=str(params.get("voice", DEFAULT_VOICE)),
                tts_provider=str(params.get("tts_provider", DEFAULT_TTS_PROVIDER)),
                silent_slide_sec=float(params.get("silent_slide_sec", DEFAULT_SILENT_SLIDE_SEC)),
                fps=int(params.get("fps", DEFAULT_FPS)),
                crf=int(params.get("crf", 20)),
                libx264_preset=str(params.get("libx264_preset", "fast")),
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "rendering",
                "slide_count": plan.slide_count,
            })

            tts_synth = self._build_tts_synth(plan)

            def _on_progress(stage: str, done: int, total: int) -> None:
                self._events.emit("task_updated", {
                    "id": task_id, "status": "running", "stage": stage,
                    "done": done, "total": total,
                    "progress": round(done / total, 3) if total else None,
                })

            result = await asyncio.to_thread(
                run_pipeline, plan,
                tts_synth=tts_synth, on_progress=_on_progress,
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
                    "slide_count": result.slide_count,
                    "audio_total_sec": result.audio_total_sec,
                    "elapsed_sec": result.elapsed_sec,
                    "output_size_bytes": result.output_size_bytes,
                    "tts_provider_used": result.tts_provider_used,
                    "tts_fallbacks": result.tts_fallbacks,
                    "verification": verification_dict,
                    "plan": plan_dict,
                },
                extra={
                    "input_path": input_path,
                    "output_path": output_path,
                    "slide_count": result.slide_count,
                    "notes_total_chars": plan.notes_total_chars,
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

    def _build_tts_synth(self, plan):
        """Return a sync ``(text, voice) -> (audio_path, duration_sec) | None`` callable.

        Wraps avatar-speaker's async ``synthesize`` into a thread-safe
        sync call so the worker (already inside ``asyncio.to_thread``)
        can drive it deterministically.  When avatar-speaker isn't
        importable or the chosen provider isn't available, returns
        ``None`` so :func:`run_pipeline` falls back to silent slides.
        """
        avp = self._load_avatar_speaker_providers()
        if avp is None:
            return None
        try:
            provider = avp.select_tts_provider(plan.tts_provider)
        except Exception as exc:  # noqa: BLE001
            self._api.log(
                f"ppt-to-video: TTS provider unavailable ({plan.tts_provider}): {exc!r}",
                level="warning",
            )
            return None

        audio_dir = Path(plan.work_dir) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        def _synth(text: str, voice: str):
            try:
                coro = provider.synthesize(
                    text=text, voice=voice, output_dir=audio_dir,
                )
                # Each call gets its own event loop so we never collide
                # with the outer asyncio thread.
                result = asyncio.run(coro)
            except Exception as exc:  # noqa: BLE001
                self._api.log(
                    f"ppt-to-video: TTS synthesize failed: {exc!r}",
                    level="warning",
                )
                return None
            return Path(result.audio_path), float(result.duration_sec)

        return _synth

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
                f"ppt-to-video failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
