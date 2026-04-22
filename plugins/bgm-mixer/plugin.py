"""bgm-mixer — beat-aware ducking & mixing plugin entry point.

Wires :mod:`mixer_engine` and :mod:`task_manager` to the OpenAkita
plugin host (HTTP routes + brain tools + lifecycle).  Every "real"
mixing decision (beat detection, ducking envelope, ffmpeg filter
graph) lives in the engine — this file is just the glue.

Conventions copied from ``plugins/transcribe-archive/plugin.py``
(closest sibling — same async-worker pattern, same UIEventEmitter
streaming, same ErrorCoach error-rendering on every catch):

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* ``QualityGates.check_input_integrity`` validates every request body
  before queuing the job — fail fast, never half-create a task,
* the worker offloads ffmpeg to a thread (``asyncio.to_thread``) so
  the host loop is never blocked by a long mix.
"""

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
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    ffprobe_json_sync,
)

from mixer_engine import (
    DEFAULT_BGM_GAIN_DB,
    DEFAULT_DUCK_DB,
    DEFAULT_FADE_IN_SEC,
    DEFAULT_FADE_OUT_SEC,
    DEFAULT_VOICE_GAIN_DB,
    BeatTrackerProtocol,
    MadmomBeatTracker,
    Sentence,
    StubBeatTracker,
    build_ffmpeg_mix_command,
    detect_voice_sentences_from_words,
    ffmpeg_available,
    mix_tracks,
    plan_mix,
    to_verification,
)
from task_manager import MixerTaskManager

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    """POST /tasks payload.

    ``words`` is the optional transcript to drive ducking; if omitted,
    the plugin treats the whole voice track as a single sentence (so
    BGM ducks for the entire voice duration — works for narration but
    is overkill for podcasts).
    """

    voice_path: str = Field(..., min_length=1)
    bgm_path: str = Field(..., min_length=1)
    words: list[dict[str, Any]] | None = None  # output of transcribe-archive
    bpm_hint: float | None = None
    duck_db: float = DEFAULT_DUCK_DB
    fade_in_sec: float = DEFAULT_FADE_IN_SEC
    fade_out_sec: float = DEFAULT_FADE_OUT_SEC
    voice_gain_db: float = DEFAULT_VOICE_GAIN_DB
    bgm_gain_db: float = DEFAULT_BGM_GAIN_DB
    beat_tracker: str = "auto"  # auto | stub | madmom


class PreviewBody(BaseModel):
    """POST /preview payload — build a plan WITHOUT running ffmpeg.

    Lets the UI show the duck envelope, ffmpeg command and beat
    snapshot before committing — important when the user is dialling
    in fade / duck values."""

    voice_path: str = Field(..., min_length=1)
    bgm_path: str = Field(..., min_length=1)
    voice_duration_sec: float | None = None  # if omitted, ffprobe
    bgm_duration_sec: float | None = None
    words: list[dict[str, Any]] | None = None
    bpm_hint: float | None = None
    duck_db: float = DEFAULT_DUCK_DB


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = MixerTaskManager(data_dir / "bgm_mixer.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "bgm_mixer_create",
                 "description": "Mix a voice track with a BGM track (beat-aware ducking + cuts).",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "voice_path": {"type": "string"},
                         "bgm_path": {"type": "string"},
                     },
                     "required": ["voice_path", "bgm_path"],
                 }},
                {"name": "bgm_mixer_status",
                 "description": "Get the status of a mix job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "bgm_mixer_list",
                 "description": "List recent mix jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "bgm_mixer_cancel",
                 "description": "Cancel a running mix job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "bgm_mixer_preview",
                 "description": "Build a mix plan without running ffmpeg (preview).",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "voice_path": {"type": "string"},
                         "bgm_path": {"type": "string"},
                     },
                     "required": ["voice_path", "bgm_path"],
                 }},
            ],
            self._handle_tool_call,
        )
        api.log("bgm-mixer loaded")

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
                        f"bgm-mixer on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        """Brain-side adapter — every tool returns one short string.

        Long blobs (full plan, ffmpeg command, envelope) belong on the
        HTTP side; the brain only needs "what's the status / what to
        click on next"."""
        try:
            if tool_name == "bgm_mixer_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建混音任务 {tid}"
            if tool_name == "bgm_mixer_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "bgm_mixer_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} {Path(r.extra.get('output_path', '') or '').name}"
                    for r in rows
                )
            if tool_name == "bgm_mixer_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "bgm_mixer_preview":
                body = PreviewBody(**args)
                plan = await self._build_preview_plan(body)
                return (
                    f"计划：BGM × {plan.bgm_loop_count}，"
                    f"ducking {plan.duck_db:.0f}dB，"
                    f"{len(plan.beats)} 拍，{len(plan.sentences)} 句话"
                )
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
                "plugin": "bgm-mixer",
                "ffmpeg": ffmpeg_available(),
                "madmom": _madmom_available(),
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
            cmd = build_ffmpeg_mix_command(plan, output_path="<preview>.mp3")
            return {"plan": plan.to_dict(), "ffmpeg_cmd": cmd}

        @router.post("/tasks")
        async def create_task(body: CreateBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["voice_path", "bgm_path"],
                non_empty_strings=["voice_path", "bgm_path"],
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

        @router.get("/tasks/{task_id}/audio")
        async def serve_audio(task_id: str) -> FileResponse:
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.extra.get("output_path"):
                raise HTTPException(
                    status_code=404, detail={"problem": "no mix file"},
                )
            p = Path(rec.extra["output_path"])
            if not p.is_file():
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "mix file missing on disk"},
                )
            return FileResponse(p)

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={"voice_path": body.voice_path, "bgm_path": body.bgm_path},
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
        voice_dur = body.voice_duration_sec
        bgm_dur = body.bgm_duration_sec
        if voice_dur is None:
            voice_dur = await asyncio.to_thread(
                _safe_duration, body.voice_path, fallback=10.0,
            )
        if bgm_dur is None:
            bgm_dur = await asyncio.to_thread(
                _safe_duration, body.bgm_path, fallback=30.0,
            )
        sentences = (
            detect_voice_sentences_from_words(body.words)
            if body.words else []
        )
        tracker = self._select_tracker("auto", bpm_hint=body.bpm_hint)
        beats, bpm = await asyncio.to_thread(
            tracker.detect, Path(body.bgm_path), duration_sec=bgm_dur,
        )
        return plan_mix(
            voice_path=body.voice_path, bgm_path=body.bgm_path,
            voice_duration_sec=voice_dur, bgm_duration_sec=bgm_dur,
            sentences=sentences, beats=beats, bpm=bpm,
            duck_db=body.duck_db,
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

            voice_path = params["voice_path"]
            bgm_path = params["bgm_path"]
            voice_dur = await asyncio.to_thread(
                _safe_duration, voice_path, fallback=0.0,
            )
            bgm_dur = await asyncio.to_thread(
                _safe_duration, bgm_path, fallback=0.0,
            )
            if voice_dur <= 0:
                raise FileNotFoundError(
                    f"voice file missing or unreadable: {voice_path}"
                )
            if bgm_dur <= 0:
                raise FileNotFoundError(
                    f"bgm file missing or unreadable: {bgm_path}"
                )

            sentences = (
                detect_voice_sentences_from_words(params.get("words") or [])
            )
            tracker = self._select_tracker(
                params.get("beat_tracker", "auto"),
                bpm_hint=params.get("bpm_hint"),
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "beat-tracking",
            })
            beats, bpm = await asyncio.to_thread(
                tracker.detect, Path(bgm_path), duration_sec=bgm_dur,
            )
            plan = plan_mix(
                voice_path=voice_path, bgm_path=bgm_path,
                voice_duration_sec=voice_dur, bgm_duration_sec=bgm_dur,
                sentences=sentences, beats=beats, bpm=bpm,
                duck_db=float(params.get("duck_db", DEFAULT_DUCK_DB)),
                fade_in_sec=float(params.get("fade_in_sec", DEFAULT_FADE_IN_SEC)),
                fade_out_sec=float(params.get("fade_out_sec", DEFAULT_FADE_OUT_SEC)),
                voice_gain_db=float(params.get("voice_gain_db", DEFAULT_VOICE_GAIN_DB)),
                bgm_gain_db=float(params.get("bgm_gain_db", DEFAULT_BGM_GAIN_DB)),
            )

            output_dir = self._data_dir / "outputs" / task_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "mix.mp3"

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "mixing",
            })
            result = await asyncio.to_thread(
                mix_tracks, plan, output_path=output_path,
            )
            # Patch ``used_madmom`` based on the actual tracker we
            # selected (mix_tracks defaults to False because it has no
            # way to know what produced the beats).
            result.used_madmom = isinstance(tracker, MadmomBeatTracker)
            verification = to_verification(result)

            verification_dict = verification.to_dict()
            plan_dict = plan.to_dict()
            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                # ``result`` is the API-facing payload (surfaced in
                # GET /tasks/{id}); ``extra`` writes the same blobs to
                # dedicated columns so a future SQL query / migration
                # can read them without re-parsing JSON.  Storyboard
                # uses the same pattern.
                result={
                    "output_path": str(output_path),
                    "duration_sec": result.duration_sec,
                    "voice_active_ratio": result.voice_active_ratio,
                    "snap_max_distance_sec": result.snap_max_distance_sec,
                    "looped": result.looped,
                    "ffmpeg_cmd": result.ffmpeg_cmd,
                    "verification": verification_dict,
                    "plan": plan_dict,
                },
                extra={
                    "output_path": str(output_path),
                    "verification_json": json.dumps(verification_dict, ensure_ascii=False),
                    "plan_json": json.dumps(plan_dict, ensure_ascii=False),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "output_path": str(output_path),
                "verification": verification.to_dict(),
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
                f"bgm-mixer failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })

    # ── beat tracker selection ──────────────────────────────────────

    def _select_tracker(
        self, name: str, *, bpm_hint: float | None,
    ) -> BeatTrackerProtocol:
        """Return the right beat tracker.

        ``"auto"`` picks madmom when the package is importable and
        falls back to stub otherwise.  We never let a typo silently
        route to the wrong tracker — unknown names raise ValueError so
        tests catch the mistake before deployment.
        """
        normalised = (name or "").strip().lower() or "auto"
        if normalised == "auto":
            normalised = "madmom" if _madmom_available() else "stub"
        if normalised == "stub":
            return StubBeatTracker(bpm=float(bpm_hint or 120.0))
        if normalised == "madmom":
            if not _madmom_available():
                logger.info(
                    "bgm-mixer: madmom requested but unavailable; "
                    "falling back to stub"
                )
                return StubBeatTracker(bpm=float(bpm_hint or 120.0))
            return MadmomBeatTracker(fallback_bpm=float(bpm_hint or 120.0))
        raise ValueError(
            f"unknown beat_tracker {name!r}; allowed: auto / stub / madmom"
        )


# ── module helpers ────────────────────────────────────────────────────


def _madmom_available() -> bool:
    """Cheap import probe.  Lazy + cached on the function attribute so
    we don't pay the import-error overhead on every request."""
    if hasattr(_madmom_available, "_cached"):
        return _madmom_available._cached  # type: ignore[attr-defined]
    try:
        import madmom  # type: ignore  # noqa: F401
        ok = True
    except ImportError:
        ok = False
    _madmom_available._cached = ok  # type: ignore[attr-defined]
    return ok


def _safe_duration(media_path: str, *, fallback: float = 0.0) -> float:
    """Return the media duration via ffprobe, or ``fallback`` on any error.

    The plugin uses this for both the voice and BGM tracks; falling
    back rather than raising lets the worker emit a better-targeted
    error (FileNotFoundError) than a generic FFmpegError.
    """
    p = Path(media_path)
    if not p.is_file():
        return fallback
    try:
        info = ffprobe_json_sync(p, timeout_sec=10.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("bgm-mixer: ffprobe failed for %s: %s", p, e)
        return fallback
    fmt = info.get("format") or {}
    raw = fmt.get("duration")
    try:
        return float(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


__all__ = ["Plugin"]
