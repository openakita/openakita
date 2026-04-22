"""avatar-speaker — beginner-friendly TTS + (future) talking-head avatar."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openakita_plugin_sdk.contrib import (
    CostEstimator,
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    VendorError,
    add_upload_preview_route,
    build_preview_url,
)
from providers import (
    PRESET_VOICES_ZH,
    configure_credentials,
    select_avatar,
    select_tts_provider,
)
from pydantic import BaseModel, Field
from task_manager import AvatarSpeakerTaskManager

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    provider: str = "auto"
    avatar_provider: str = "none"
    portrait_path: str | None = None


class CostBody(BaseModel):
    text: str
    provider: str = "auto"


_REDACT_KEYS = ("api_key", "api_token", "secret", "access_key")


def _redacted_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Mask API-key-like values when echoing config back to the UI."""
    out: dict[str, Any] = {}
    for k, v in (cfg or {}).items():
        if any(token in k.lower() for token in _REDACT_KEYS) and v:
            sv = str(v)
            out[k] = sv[:4] + "***" + sv[-2:] if len(sv) > 6 else "***"
        else:
            out[k] = v
    return out


_PRICE_TABLE = {
    "edge":              {"per_1k_chars": 0.0,   "currency": "CNY"},
    "qwen3_tts_flash":   {"per_1k_chars": 0.04,  "currency": "CNY"},
    "cosyvoice":         {"per_1k_chars": 0.05,  "currency": "CNY"},
    "openai":            {"per_1k_chars": 0.015, "currency": "USD"},
    "stub-silent":       {"per_1k_chars": 0.0,   "currency": "CNY"},
    "edge-tts":            {"per_1k_chars": 0.0,   "currency": "CNY"},
    "dashscope-cosyvoice": {"per_1k_chars": 0.05,  "currency": "CNY"},
    "openai-tts":          {"per_1k_chars": 0.015, "currency": "USD"},
}


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = AvatarSpeakerTaskManager(data_dir / "avatar_speaker.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        # Wire credentials from per-plugin config first; ``configure_credentials``
        # is idempotent so ``POST /settings`` can re-call it without restart.
        # ``_load_credentials`` is async so it runs as a fire-and-forget task —
        # the first request after boot will see the keys (or fall back to env).
        api.spawn_task(self._load_credentials())

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "avatar_speaker_synthesize",
                 "description": "Synthesize speech from text. Optional digital-human avatar (scaffolded).",
                 "input_schema": {"type": "object",
                                  "properties": {"text": {"type": "string"},
                                                 "voice": {"type": "string"}},
                                  "required": ["text"]}},
                {"name": "avatar_speaker_status",
                 "description": "Get task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "avatar_speaker_list",
                 "description": "List recent TTS tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "avatar_speaker_cancel",
                 "description": "Cancel a running task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.log("avatar-speaker loaded")

    async def _load_credentials(self) -> None:
        """Read API keys from plugin config (with env bootstrap fallback)
        and push them into the providers module.

        Mirrors the tongyi-image pattern: config first, env only as
        bootstrap when no key has been saved yet. Tests can stub this by
        calling :func:`configure_credentials` directly.
        """
        import os as _os

        try:
            cfg = await self._tm.get_config()
        except Exception:  # noqa: BLE001 — never let creds break boot
            cfg = {}
        dashscope = cfg.get("dashscope_api_key") or _os.environ.get("DASHSCOPE_API_KEY")
        openai = cfg.get("openai_api_key") or _os.environ.get("OPENAI_API_KEY")
        configure_credentials(
            dashscope_api_key=dashscope or "",
            openai_api_key=openai or "",
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
                        f"avatar-speaker on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "avatar_speaker_synthesize":
                tid = await self._create(CreateBody(**args))
                return f"已创建任务 {tid}"
            if tool_name == "avatar_speaker_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "avatar_speaker_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "avatar_speaker_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        # Issue #479: serve previously uploaded portrait images so the UI can
        # render <img src="/api/plugins/avatar-speaker/uploads/<file>">.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "avatar-speaker"}

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

        # Section 7 (overhaul template): every plugin must expose
        # ``GET/POST /settings`` so the host-wide settings panel can wire
        # in. Aliases of /config; ``POST /settings`` triggers credential
        # hot-reload identically.
        @router.get("/settings")
        async def get_settings():
            return {"ok": True, "config": _redacted_config(await self._tm.get_config())}

        @router.post("/settings")
        async def set_settings(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            await self._load_credentials()
            return {"ok": True, "config": _redacted_config(await self._tm.get_config())}

        @router.get("/providers")
        async def providers():
            try:
                p = select_tts_provider("auto")
                return {"available": [p.name], "active": p.name}
            except VendorError as e:
                return {"available": [], "active": None, "error": str(e)}

        @router.post("/cost")
        async def cost(body: CostBody):
            try:
                p = select_tts_provider(body.provider)
            except VendorError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            tariff = _PRICE_TABLE.get(p.name, _PRICE_TABLE["stub-silent"])
            chars = max(1, len(body.text))
            est = CostEstimator(currency=tariff["currency"])
            est.add(f"{p.name} {chars}字",
                    units=chars / 1000.0, unit_label="千字",
                    unit_price=float(tariff["per_1k_chars"]))
            est.note(f"使用 provider: {p.name}")
            return est.build(confidence="high",
                             sample_label=f"{p.name} {chars}字").to_dict()

        @router.post("/upload-portrait")
        async def upload_portrait(file: UploadFile = File(...)):
            data_dir = self._api.get_data_dir() / "uploads" / "portrait"
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / file.filename
            with target.open("wb") as fp:
                while chunk := await file.read(1024 * 1024):
                    fp.write(chunk)
            rel = target.relative_to(self._api.get_data_dir() / "uploads")
            return {
                "path": str(target),
                "url": build_preview_url("avatar-speaker", rel),
            }

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(), required=["text"], non_empty_strings=["text"],
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

        @router.get("/audio/{task_id}")
        async def serve_audio(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("audio_path"):
                raise HTTPException(status_code=404, detail={"problem": "no audio"})
            p = Path(rec.extra["audio_path"])
            if not p.exists():
                raise HTTPException(status_code=404, detail={"problem": "audio file missing"})
            return FileResponse(p)

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.text[:200],
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"text_input": body.text, "voice": body.voice, "provider": body.provider},
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
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "tts"})
            output_dir = self._api.get_data_dir() / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)

            tts_prov = select_tts_provider(params.get("provider", "auto"))
            await self._tm.update_task(task_id, extra={"provider": tts_prov.name})
            tts_res = await tts_prov.synthesize(
                text=params.get("text", ""),
                voice=params.get("voice", "zh-CN-XiaoxiaoNeural"),
                rate=params.get("rate", "+0%"),
                pitch=params.get("pitch", "+0Hz"),
                output_dir=output_dir,
            )

            avatar_video_path = ""
            avatar_pref = params.get("avatar_provider", "none")
            portrait = params.get("portrait_path") or ""
            if avatar_pref not in ("none", "off", "") and portrait:
                self._events.emit("task_updated", {"id": task_id, "status": "running",
                                                   "stage": "avatar"})
                avatar = select_avatar(avatar_pref)
                if avatar is not None:
                    out = await avatar.render(audio_path=tts_res.audio_path,
                                              portrait_path=Path(portrait),
                                              output_dir=output_dir)
                    avatar_video_path = str(out)

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"audio_path": str(tts_res.audio_path),
                        "duration_sec": tts_res.duration_sec,
                        "voice": tts_res.voice,
                        "provider": tts_res.provider,
                        "avatar_video_path": avatar_video_path},
                extra={"audio_path": str(tts_res.audio_path),
                       "avatar_video_path": avatar_video_path,
                       "voice": tts_res.voice},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "audio_path": str(tts_res.audio_path),
                                               "duration_sec": tts_res.duration_sec})
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
