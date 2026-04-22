"""dub-it — plugin entry point.

Wires :mod:`dub_engine` to the OpenAkita plugin host.  Three
extension points are exposed via :meth:`Plugin.set_transcriber`,
:meth:`Plugin.set_translator`, and :meth:`Plugin.set_synthesizer`
so the host can plug in real ASR / LLM / TTS backends without
touching engine code.

Defaults are deterministic stubs so the plugin is usable out of
the box for HTTP smoke tests and CI.
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
import shutil
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from _shared import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)

from dub_engine import (
    ALLOWED_TARGET_LANGUAGES,
    DEFAULT_DUCK_DB,
    DEFAULT_OUTPUT_FORMAT,
    DubPlan,
    DubResult,
    DubSegment,
    default_translator,
    humanise_segment_summary,
    plan_dub,
    preflight_review,
    run_dub,
    safe_workdir_name,
    to_verification,
)

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateDubBody(BaseModel):
    source_video: str = Field(..., min_length=1)
    target_language: str = Field(..., min_length=2)
    output_path: str | None = None
    output_format: str = DEFAULT_OUTPUT_FORMAT
    duck_db: int = DEFAULT_DUCK_DB
    keep_original_audio: bool = True
    source_language_hint: str = ""


class ReviewBody(BaseModel):
    source_video: str = Field(..., min_length=1)


# ── default backends (stubs) ──────────────────────────────────────────


async def _default_transcribe(audio_path: Path, _hint: str) -> list[DubSegment]:
    """Stub transcriber: one segment whose text is the file's basename.

    Lets the plugin smoke-test end-to-end without pulling whisper.
    Real integrations call :meth:`Plugin.set_transcriber`.
    """
    return [
        DubSegment(
            index=0, start_sec=0.0, end_sec=3.0,
            text=f"[stub transcribe] {audio_path.name}",
        ),
    ]


async def _default_synthesize(
    segments: list[DubSegment], _target_language: str, out_path: Path,
) -> Path:
    """Stub synthesizer: writes a small WAV header + silence to ``out_path``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal RIFF/WAVE header for 0.1s of mono 16 kHz PCM silence (3200 bytes).
    sample_rate = 16000
    n_samples = int(sample_rate * 0.1)
    data_size = n_samples * 2
    with out_path.open("wb") as f:
        f.write(b"RIFF")
        f.write((36 + data_size).to_bytes(4, "little"))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write((16).to_bytes(4, "little"))
        f.write((1).to_bytes(2, "little"))     # PCM
        f.write((1).to_bytes(2, "little"))     # mono
        f.write(sample_rate.to_bytes(4, "little"))
        f.write((sample_rate * 2).to_bytes(4, "little"))
        f.write((2).to_bytes(2, "little"))     # block align
        f.write((16).to_bytes(2, "little"))    # bits/sample
        f.write(b"data")
        f.write(data_size.to_bytes(4, "little"))
        f.write(b"\x00\x00" * n_samples)
    return out_path


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        from task_manager import DubItTaskManager
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = DubItTaskManager(data_dir / "dub_it.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        self._transcribe = _default_transcribe
        self._translate = default_translator
        self._synthesize = _default_synthesize

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "dub_it_create",
                 "description": "Create a dubbing job. Pre-flights with source_review (D2.3); rejects bad inputs before spending API quota.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "source_video": {"type": "string"},
                         "target_language": {"type": "string"},
                         "output_path": {"type": "string"},
                     },
                     "required": ["source_video", "target_language"],
                 }},
                {"name": "dub_it_status",
                 "description": "Get dubbing job status / segment summary / verification.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "dub_it_list",
                 "description": "List recent dubbing jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "dub_it_cancel",
                 "description": "Cancel a running dubbing job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "dub_it_review_source",
                 "description": "Run source_review on a video WITHOUT starting a dub. Use to triage 'will this even work?' before spending quota.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"source_video": {"type": "string"}},
                     "required": ["source_video"],
                 }},
                {"name": "dub_it_check_deps",
                 "description": "Check whether ffmpeg / ffprobe are installed and on PATH.",
                 "input_schema": {"type": "object", "properties": {}}},
            ],
            self._handle_tool_call,
        )
        api.log("dub-it loaded")

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
                        f"dub-it on_unload drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── extension points ───────────────────────────────────────────

    def set_transcriber(self, fn) -> None:
        self._transcribe = fn

    def set_translator(self, fn) -> None:
        self._translate = fn

    def set_synthesizer(self, fn) -> None:
        self._synthesize = fn

    # ── brain tools ─────────────────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "dub_it_create":
                tid = await self._create(CreateDubBody(**args))
                return f"已创建 dub-it 任务 {tid}"
            if tool_name == "dub_it_status":
                rec = await self._tm.get_task(args["task_id"])
                if rec is None:
                    return "未找到该任务"
                summary = (rec.extra or {}).get("segment_count", 0)
                line = f"{rec.status} · {summary} 段"
                if rec.error_message:
                    line += f"：{rec.error_message}"
                return line
            if tool_name == "dub_it_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} → {r.extra.get('output_video', '?')}"
                    for r in rows
                )
            if tool_name == "dub_it_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "dub_it_review_source":
                src = Path(args["source_video"])
                report = await asyncio.to_thread(preflight_review, src)
                if report.passed and not report.warnings:
                    return f"✅ 源文件通过体检：{report.kind} {report.metadata}"
                return (
                    f"{'✅' if report.passed else '❌'} kind={report.kind} "
                    f"errors={[i.code for i in report.errors]} "
                    f"warnings={[i.code for i in report.warnings]}"
                )
            if tool_name == "dub_it_check_deps":
                missing = []
                for b in ("ffmpeg", "ffprobe"):
                    if shutil.which(b) is None:
                        missing.append(b)
                if missing:
                    return f"缺少：{', '.join(missing)}（请安装 ffmpeg）"
                return "✅ ffmpeg / ffprobe 已就绪"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True, "plugin": "dub-it",
                "allowed_target_languages": list(ALLOWED_TARGET_LANGUAGES),
                "default_output_format": DEFAULT_OUTPUT_FORMAT,
                "default_duck_db": DEFAULT_DUCK_DB,
            }

        @router.get("/check-deps")
        async def check_deps() -> dict[str, Any]:
            present = {b: shutil.which(b) is not None for b in ("ffmpeg", "ffprobe")}
            missing = [b for b, ok in present.items() if not ok]
            return {"present": present, "missing": missing,
                     "ok": not missing}

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/review")
        async def review(body: ReviewBody) -> dict[str, Any]:
            try:
                report = await asyncio.to_thread(preflight_review,
                                                   Path(body.source_video))
            except Exception as exc:  # noqa: BLE001
                rendered = self._coach.render(exc, raw_message=str(exc))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from exc
            return report.to_dict()

        @router.post("/tasks")
        async def create_task(body: CreateDubBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["source_video", "target_language"],
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
            rows = await self._tm.list_tasks(status=status, limit=limit, offset=offset)
            return {"items": [r.to_dict() for r in rows], "total": len(rows)}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                raise HTTPException(status_code=404,
                                     detail={"problem": "task not found"})
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

        @router.get("/tasks/{task_id}/output")
        async def serve_output(task_id: str) -> FileResponse:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                raise HTTPException(status_code=404,
                                     detail={"problem": "no task"})
            path_str = (rec.result or {}).get("output_video_path") \
                or (rec.extra or {}).get("output_video")
            if not path_str:
                raise HTTPException(status_code=404,
                                     detail={"problem": "no output yet"})
            p = Path(path_str)
            if not p.is_file():
                raise HTTPException(status_code=404,
                                     detail={"problem": "output missing on disk"})
            return FileResponse(p, media_type="video/mp4", filename=p.name)

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateDubBody) -> str:
        out_path = body.output_path or str(
            self._data_dir / "outputs" / f"{Path(body.source_video).stem}.dub.mp4",
        )
        params = body.model_dump()
        params["output_path"] = out_path
        tid = await self._tm.create_task(
            params=params,
            status=TaskStatus.PENDING.value,
            extra={
                "source_video": body.source_video,
                "target_language": body.target_language,
                "output_video": out_path,
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
                "id": task_id, "status": "running", "stage": "review",
            })

            plan: DubPlan = await asyncio.to_thread(
                plan_dub,
                source_video=params["source_video"],
                target_language=params["target_language"],
                output_path=params["output_path"],
                output_format=params.get("output_format", DEFAULT_OUTPUT_FORMAT),
                duck_db=int(params.get("duck_db", DEFAULT_DUCK_DB)),
                keep_original_audio=bool(params.get("keep_original_audio", True)),
            )

            workdir = self._data_dir / "work" / safe_workdir_name(
                Path(params["source_video"]),
            )

            def _on_stage(stage: str) -> None:
                self._events.emit("task_updated", {
                    "id": task_id, "status": "running", "stage": stage,
                })

            result: DubResult = await run_dub(
                plan,
                transcribe=self._transcribe,
                translate=self._translate,
                synthesize=self._synthesize,
                workdir=workdir,
                on_stage=_on_stage,
                source_language_hint=params.get("source_language_hint", ""),
            )

            verification_dict = to_verification(result).to_dict()
            await self._tm.update_task(
                task_id,
                status=(
                    TaskStatus.SUCCEEDED.value if result.succeeded
                    else TaskStatus.FAILED.value
                ),
                result={**result.to_dict(), "verification": verification_dict},
                error_message=result.error,
                extra={
                    "segment_count": len(result.segments),
                    "bytes_output": result.bytes_output,
                    "review_passed": 1 if plan.review.passed else 0,
                    "verification_json": json.dumps(verification_dict, ensure_ascii=False),
                    "plan_json": json.dumps(plan.to_dict(), ensure_ascii=False),
                    "segments_json": json.dumps(
                        [s.to_dict() for s in result.segments], ensure_ascii=False,
                    ),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id,
                "status": "succeeded" if result.succeeded else "failed",
                "summary": humanise_segment_summary(result.segments),
                "verification": verification_dict,
            })
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            self._events.emit("task_updated", {"id": task_id, "status": "cancelled"})
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
                f"dub-it failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed", "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
