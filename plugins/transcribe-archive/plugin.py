"""transcribe-archive — chunked, cache-friendly ASR with subtitle export.

What this module is responsible for (and what it ISN'T):

* It IS the wiring layer that exposes :mod:`transcribe_engine` and
  :mod:`task_manager` over the OpenAkita Plugin API: HTTP routes,
  brain tools, lifecycle (``on_load`` / ``on_unload``), background
  worker management, and progress events.  Every "real" transcription
  decision (chunking, caching, merging, rendering) lives in the
  engine, not here.

* It is NOT the place to add new ASR providers — drop a new
  ``TranscribeProvider`` implementation into ``transcribe_engine.py``
  and wire it through :func:`Plugin._select_provider`.

Conventions copied from ``plugins/tts-studio/plugin.py`` (the closest
relative pattern-wise — long-running media job, single async worker
per task, FileResponse for downloads):

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can cancel cleanly.
* every long-running step emits ``task_updated`` via
  :class:`UIEventEmitter` so the host UI streams progress.
* every brain tool call funnels through :meth:`_handle_tool_call`
  which catches and renders exceptions through :class:`ErrorCoach`
  (D2.14 three-segment shape).
* ``QualityGates.check_input_integrity`` validates the request body
  before the job is queued — fail fast, never half-create a task.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)

from task_manager import DEFAULT_CONFIG, TranscribeTaskManager
from transcribe_engine import (
    DEFAULT_CHUNK_DURATION_SEC,
    DEFAULT_CHUNK_OVERLAP_SEC,
    StubProvider,
    TranscribeProvider,
    ffmpeg_available,
    plan_chunks,
    probe_duration_seconds,
    stub_transcribe_offline,
    to_archive_bundle,
    to_verification,
    transcribe_file,
)

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    """POST /tasks payload.

    ``audio_path`` is a server-side path the host has already accepted
    (the plugin doesn't multipart-upload — the upload-preview helper
    in the SDK handles that and hands us a path).
    """

    audio_path: str = Field(..., min_length=1)
    language: str = "zh"
    provider: str = "auto"
    chunk_duration_sec: float = DEFAULT_CHUNK_DURATION_SEC
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC


class PreviewBody(BaseModel):
    """POST /preview payload — chunk-plan a hypothetical job WITHOUT
    running ffmpeg (uses a duration the caller supplies).  Lets the UI
    show "this 3-hour podcast → 180 chunks → ~30 s ASR per chunk"
    before the user commits."""

    duration_sec: float = Field(..., gt=0)
    chunk_duration_sec: float = DEFAULT_CHUNK_DURATION_SEC
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC
    use_stub: bool = False


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = TranscribeTaskManager(data_dir / "transcribe.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}
        self._init_lock = asyncio.Lock()
        self._initialized = False

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "transcribe_archive_create",
                 "description": "Create a new transcription job from an audio/video file path.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "audio_path": {"type": "string"},
                         "language": {"type": "string"},
                         "provider": {"type": "string"},
                     },
                     "required": ["audio_path"],
                 }},
                {"name": "transcribe_archive_status",
                 "description": "Get the status (and result if finished) of a transcription task.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "transcribe_archive_list",
                 "description": "List recent transcription tasks.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "transcribe_archive_cancel",
                 "description": "Cancel a running transcription task.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "transcribe_archive_preview",
                 "description": "Plan chunks for a duration without running ASR (cost preview).",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "duration_sec": {"type": "number"},
                         "chunk_duration_sec": {"type": "number"},
                         "overlap_sec": {"type": "number"},
                     },
                     "required": ["duration_sec"],
                 }},
            ],
            self._handle_tool_call,
        )
        api.log("transcribe-archive loaded")

    async def on_unload(self) -> None:
        # Cancel any in-flight workers first so the DB close below
        # doesn't race with a worker writing its terminal status.
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
                        f"transcribe-archive on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()
        if self._initialized:
            try:
                await self._tm.close()
            except Exception as e:  # noqa: BLE001 — close must never raise out of unload
                self._api.log(
                    f"transcribe-archive on_unload db close error: {e!r}",
                    level="warning",
                )
            self._initialized = False

    # ── lazy DB init ─────────────────────────────────────────────────

    async def _ensure_db(self) -> None:
        """``TranscribeTaskManager.init()`` is async; ``on_load`` is sync.

        Lazy-init under a lock so the very first request opens the
        connection but every subsequent request just sees a no-op.
        Mirrors the seedance-video pattern (sync ``on_load`` is a
        Plugin API constraint we cannot change from a plugin).
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._tm.init()
            self._initialized = True

    # ── brain tool dispatcher ────────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        """Brain-side adapter — every tool returns one short string.

        Long blobs (full transcripts, chunk lists) belong on the HTTP
        side; the brain only needs to know "what's the status / what's
        the next thing to click on".
        """
        try:
            await self._ensure_db()
            if tool_name == "transcribe_archive_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建转写任务 {tid}"
            if tool_name == "transcribe_archive_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = f"{rec['status']}"
                if rec.get("error_message"):
                    msg += f"：{rec['error_message']}"
                if rec.get("chunks_total"):
                    msg += f" ({rec.get('chunks_done', 0)}/{rec['chunks_total']} 段)"
                return msg
            if tool_name == "transcribe_archive_list":
                rows, _ = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r['id']} {r['status']} {Path(r.get('audio_path', '')).name}"
                    for r in rows
                )
            if tool_name == "transcribe_archive_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "transcribe_archive_preview":
                body = PreviewBody(**args)
                chunks = plan_chunks(
                    body.duration_sec,
                    chunk_duration_sec=body.chunk_duration_sec,
                    overlap_sec=body.overlap_sec,
                )
                return f"将切成 {len(chunks)} 段，估计每段 {body.chunk_duration_sec:.0f}s"
        except Exception as e:  # noqa: BLE001 — translate to user-friendly text
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ───────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": "transcribe-archive",
                "ffmpeg": ffmpeg_available(),
            }

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            await self._ensure_db()
            return await self._tm.get_all_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._ensure_db()
            # Whitelist + cast to str — the config table is TEXT-only.
            allowed = set(DEFAULT_CONFIG.keys())
            payload = {
                k: str(v) for k, v in updates.items() if k in allowed
            }
            if not payload:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "problem": "no recognised keys",
                        "next_step": (
                            "请使用以下 key 之一："
                            + ", ".join(sorted(allowed))
                        ),
                    },
                )
            await self._tm.set_configs(payload)
            return await self._tm.get_all_config()

        @router.post("/preview")
        async def preview(body: PreviewBody) -> dict[str, Any]:
            """Plan-only preview — does NOT touch ffmpeg or any provider."""
            chunks = plan_chunks(
                body.duration_sec,
                chunk_duration_sec=body.chunk_duration_sec,
                overlap_sec=body.overlap_sec,
            )
            stub_result = None
            if body.use_stub:
                # Render a deterministic placeholder transcript so the
                # UI can show the SRT/VTT shape before a real ASR call.
                tr = stub_transcribe_offline(
                    duration_sec=body.duration_sec,
                    chunk_duration_sec=body.chunk_duration_sec,
                    overlap_sec=body.overlap_sec,
                )
                bundle = to_archive_bundle(tr)
                stub_result = {
                    "result": tr.to_dict(),
                    "verification": to_verification(tr).to_dict(),
                    "preview_srt": bundle.srt[:2000],
                }
            return {
                "chunks": [c.to_dict() for c in chunks],
                "chunk_count": len(chunks),
                "stub": stub_result,
            }

        @router.post("/tasks")
        async def create_task(body: CreateBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["audio_path"],
                non_empty_strings=["audio_path"],
            )
            if gate.blocking:
                rendered = self._coach.render(
                    ValueError(gate.message), raw_message=gate.message,
                )
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            await self._ensure_db()
            tid = await self._create(body)
            return {"task_id": tid, "status": "pending"}

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = Query(default=None),
            limit: int = Query(default=50, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
            await self._ensure_db()
            rows, total = await self._tm.list_tasks(
                status=status, limit=limit, offset=offset,
            )
            return {"items": rows, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            await self._ensure_db()
            rec = await self._tm.get_task(task_id)
            if rec is None:
                rendered = self._coach.render(
                    status=404, raw_message=f"task {task_id} not found",
                )
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str) -> dict[str, Any]:
            await self._ensure_db()
            ok = await self._cancel(task_id)
            if not ok:
                raise HTTPException(
                    status_code=404, detail={"problem": "task not found or already done"},
                )
            return {"ok": True}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, Any]:
            await self._ensure_db()
            ok = await self._tm.delete_task(task_id)
            if not ok:
                raise HTTPException(
                    status_code=404, detail={"problem": "task not found"},
                )
            return {"ok": True}

        # Subtitle / archive download routes — one route per format so a
        # browser <a download> can pick the right MIME.

        @router.get("/tasks/{task_id}/archive.json")
        async def archive_json(task_id: str) -> dict[str, Any]:
            bundle = await self._build_bundle(task_id)
            return {"json": bundle.json, "txt": bundle.txt,
                    "srt": bundle.srt, "vtt": bundle.vtt}

        @router.get("/tasks/{task_id}/archive.srt")
        async def archive_srt(task_id: str) -> PlainTextResponse:
            bundle = await self._build_bundle(task_id)
            return PlainTextResponse(
                bundle.srt,
                media_type="application/x-subrip; charset=utf-8",
                headers={"Content-Disposition":
                         f'attachment; filename="{task_id}.srt"'},
            )

        @router.get("/tasks/{task_id}/archive.vtt")
        async def archive_vtt(task_id: str) -> PlainTextResponse:
            bundle = await self._build_bundle(task_id)
            return PlainTextResponse(
                bundle.vtt,
                media_type="text/vtt; charset=utf-8",
                headers={"Content-Disposition":
                         f'attachment; filename="{task_id}.vtt"'},
            )

        @router.get("/tasks/{task_id}/archive.txt")
        async def archive_txt(task_id: str) -> PlainTextResponse:
            bundle = await self._build_bundle(task_id)
            return PlainTextResponse(
                bundle.txt,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition":
                         f'attachment; filename="{task_id}.txt"'},
            )

        @router.get("/tasks/{task_id}/audio")
        async def serve_audio(task_id: str) -> FileResponse:
            await self._ensure_db()
            rec = await self._tm.get_task(task_id)
            if rec is None:
                raise HTTPException(
                    status_code=404, detail={"problem": "task not found"},
                )
            p = Path(rec.get("audio_path", ""))
            if not p.is_file():
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "audio file missing on disk"},
                )
            return FileResponse(p)

    # ── lifecycle helpers ────────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        rec = await self._tm.create_task(
            audio_path=body.audio_path,
            language=body.language,
            provider_id=self._normalize_provider(body.provider),
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
        )
        tid = rec["id"]
        worker = asyncio.create_task(self._run(tid))
        self._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: self._workers.pop(k, None))
        return tid

    async def _cancel(self, task_id: str) -> bool:
        rec = await self._tm.get_task(task_id)
        if rec is None:
            return False
        if TaskStatus.is_terminal(rec["status"]):
            # Already finished — return False so the caller doesn't
            # mislead the user into thinking we just stopped a job.
            return False
        worker = self._workers.pop(task_id, None)
        if worker and not worker.done():
            worker.cancel()
        await self._tm.update_task(
            task_id, status=TaskStatus.CANCELLED.value,
        )
        return True

    async def _build_bundle(self, task_id: str):
        await self._ensure_db()
        rec = await self._tm.get_task(task_id)
        if rec is None:
            raise HTTPException(
                status_code=404, detail={"problem": "task not found"},
            )
        result = rec.get("result")
        if not result or not result.get("words"):
            raise HTTPException(
                status_code=409,
                detail={"problem": "task has no transcript yet",
                        "next_step": "等任务跑完再来下载字幕。"},
            )
        # Reconstruct a TranscriptResult from the stored dict so we
        # don't duplicate the renderers.  Only the fields used by the
        # renderers + verification are required.
        from transcribe_engine import TranscriptResult, Word
        words = [
            Word(text=str(w["text"]),
                 start=float(w["start"]),
                 end=float(w["end"]),
                 confidence=float(w.get("confidence", 1.0)))
            for w in result.get("words", [])
        ]
        tr = TranscriptResult(
            words=words,
            duration_sec=float(result.get("duration_sec", 0.0)),
            language=str(result.get("language", "zh")),
            chunks_total=int(result.get("chunks_total", 0)),
            chunks_from_cache=int(result.get("chunks_from_cache", 0)),
            chunks_failed=int(result.get("chunks_failed", 0)),
            provider_id=str(result.get("provider_id", "stub")),
            failed_chunk_indexes=list(result.get("failed_chunk_indexes", [])),
            notes=str(result.get("notes", "")),
        )
        return to_archive_bundle(tr)

    # ── worker ───────────────────────────────────────────────────────

    async def _run(self, task_id: str) -> None:
        """Background worker — one per task.

        The engine call (:func:`transcribe_file`) is synchronous (uses
        ffmpeg + provider HTTP calls); we offload to a thread so the
        host loop is never blocked.  Stub jobs (no API key configured)
        run inline because they're fast and pure-Python.
        """
        try:
            await self._ensure_db()
            rec = await self._tm.get_task(task_id)
            if rec is None:
                return
            params = rec.get("params") or {}
            await self._tm.update_task(
                task_id, status=TaskStatus.RUNNING.value,
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "starting",
            })

            provider_id = self._normalize_provider(params.get("provider", "auto"))
            cache_dir = Path(
                (await self._tm.get_config("cache_dir"))
                or self._data_dir / "transcribe_cache"
            )

            if provider_id == "stub" or not ffmpeg_available():
                # Offline / smoke path — fast, deterministic, no audio
                # required.  We use a 60-second placeholder duration
                # if there's no audio; otherwise probe the real file.
                if (
                    ffmpeg_available()
                    and Path(params.get("audio_path", "")).is_file()
                ):
                    duration = await asyncio.to_thread(
                        probe_duration_seconds, params["audio_path"]
                    )
                else:
                    duration = 60.0
                tr = await asyncio.to_thread(
                    stub_transcribe_offline,
                    duration_sec=duration,
                    chunk_duration_sec=float(params.get("chunk_duration_sec", DEFAULT_CHUNK_DURATION_SEC)),
                    overlap_sec=float(params.get("overlap_sec", DEFAULT_CHUNK_OVERLAP_SEC)),
                    language=str(params.get("language", "zh")),
                )
            else:
                provider = self._build_provider(provider_id, params)
                audio_path = params.get("audio_path", "")
                if not Path(audio_path).is_file():
                    raise FileNotFoundError(
                        f"audio file does not exist: {audio_path}"
                    )

                loop = asyncio.get_running_loop()

                def _progress(done: int, total: int) -> None:
                    # ``call_soon_threadsafe`` is the only safe way to
                    # touch the host loop from the worker thread.
                    loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._on_progress(task_id, done, total),
                    )

                tr = await asyncio.to_thread(
                    transcribe_file,
                    audio_path,
                    provider=provider,
                    cache_dir=cache_dir,
                    language=str(params.get("language", "zh")),
                    chunk_duration_sec=float(params.get("chunk_duration_sec", DEFAULT_CHUNK_DURATION_SEC)),
                    overlap_sec=float(params.get("overlap_sec", DEFAULT_CHUNK_OVERLAP_SEC)),
                    progress_cb=_progress,
                )

            verification = to_verification(tr)
            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result=tr.to_dict(),
                verification=verification.to_dict(),
                chunks_total=tr.chunks_total,
                chunks_done=tr.chunks_total - tr.chunks_failed,
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "chunks_total": tr.chunks_total,
                "chunks_failed": tr.chunks_failed,
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
        except Exception as e:  # noqa: BLE001 — turn into a coached error
            await self._fail(task_id, e)

    async def _on_progress(self, task_id: str, done: int, total: int) -> None:
        try:
            await self._tm.update_task(
                task_id, chunks_total=total, chunks_done=done,
            )
        finally:
            self._events.emit("task_updated", {
                "id": task_id, "status": "running",
                "chunks_done": done, "chunks_total": total,
                "stage": f"transcribing {done}/{total}",
            })

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
                f"transcribe-archive failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })

    # ── provider wiring ──────────────────────────────────────────────

    def _normalize_provider(self, name: str) -> str:
        name = (name or "").strip().lower()
        if name in ("", "auto"):
            return "stub"  # safe default until a provider is configured
        return name

    def _build_provider(self, provider_id: str, params: dict) -> TranscribeProvider:
        """Hand back the right provider instance for ``provider_id``.

        Currently only :class:`StubProvider` is bundled — real adapters
        (whisper / scribe / paraformer) land in subsequent sprints
        and plug in here.  Mapping lives in code (not config) so a
        typo in config can never silently route to the wrong provider.
        """
        if provider_id == "stub":
            return StubProvider()
        # Hooks for upcoming sprints: whisper / scribe / paraformer.
        # When we add them, every adapter gets its own ``elif`` branch
        # here.  Keeping the dispatch explicit (no plugin-defined
        # registries) means an operator can grep for ``provider_id ==``
        # to see exactly which providers exist.
        raise ValueError(
            f"provider {provider_id!r} not yet bundled with transcribe-archive; "
            "set 'default_provider' to 'stub' in config or wait for the "
            "real adapter sprint."
        )


__all__ = ["Plugin"]
