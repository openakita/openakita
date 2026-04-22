"""highlight-cutter — pick out the highlights from a long video, automatically.

Beginner-friendly flow:
  upload → "verify intent" preview → cost preview → run → 庆祝 + open folder.

Backed by openakita_plugin_sdk.contrib (BaseTaskManager / ErrorCoach /
CostEstimator / IntentVerifier / RenderPipeline).
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
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from _shared import (
    CostEstimator,
    ErrorCoach,
    IntentVerifier,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    collect_storage_stats,
)

from highlight_engine import (
    HighlightSegment,
    configure_credentials,
    keyword_score,
    pick_segments,
    render_highlights,
    transcribe_with_contrib_asr,
)
from task_manager import HighlightTaskManager

_SENSITIVE_CONFIG_KEYS = {"dashscope_api_key"}


def _redacted_config(cfg: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in cfg.items():
        if k in _SENSITIVE_CONFIG_KEYS and v:
            out[k] = f"***{v[-4:]}" if len(v) > 4 else "***"
        else:
            out[k] = v
    return out

logger = logging.getLogger(__name__)


# ── request / response models ──


class CreateBody(BaseModel):
    source_path: str = Field(..., description="本地视频文件绝对路径")
    target_count: int = 5
    min_segment_sec: float = 3.0
    max_segment_sec: float = 20.0
    auto_open: bool = False
    intent_hint: str = ""


class IntentBody(BaseModel):
    hint: str = ""
    source_summary: str = ""


class CostBody(BaseModel):
    source_duration_sec: float
    target_count: int = 5
    avg_segment_sec: float = 8.0


# ── plugin ──


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = HighlightTaskManager(data_dir / "highlight.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._brain = None
        self._verifier: IntentVerifier | None = None
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {
                    "name": "highlight_cutter_create",
                    "description": "Create a highlight-cut task from a long video file path.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "source_path": {"type": "string"},
                            "target_count": {"type": "integer", "default": 5},
                        },
                        "required": ["source_path"],
                    },
                },
                {
                    "name": "highlight_cutter_status",
                    "description": "Get the status of a highlight task.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
                {
                    "name": "highlight_cutter_list",
                    "description": "List recent highlight tasks.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "highlight_cutter_cancel",
                    "description": "Cancel a running highlight task.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            ],
            self._handle_tool_call,
        )

        api.spawn_task(self._load_credentials())
        api.log("highlight-cutter loaded")

    async def _load_credentials(self) -> None:
        cfg = await self._tm.get_config()
        configure_credentials(
            dashscope_api_key=cfg.get("dashscope_api_key")
            or os.environ.get("DASHSCOPE_API_KEY", ""),
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
                        f"highlight-cutter on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── tool dispatcher ──

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "highlight_cutter_create":
                body = CreateBody(**args)
                task_id = await self._create(body)
                return f"已创建任务 {task_id}"
            if tool_name == "highlight_cutter_status":
                rec = await self._tm.get_task(args["task_id"])
                if rec is None:
                    return f"未找到任务 {args['task_id']}"
                return f"{rec.status}: {rec.error_message or ''}"
            if tool_name == "highlight_cutter_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "highlight_cutter_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到或已结束"
        except Exception as e:  # noqa: BLE001
            rendered = self._coach.render(e)
            return f"[{rendered.cause_category}] {rendered.problem} → {rendered.next_step}"
        return f"unknown tool: {tool_name}"

    # ── REST routes ──

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "highlight-cutter"}

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

        @router.post("/intent")
        async def intent(body: IntentBody):
            v = self._get_verifier()
            if not v:
                return {"summary": body.hint or "(未配置 LLM)", "confidence": "low",
                        "clarifying_questions": [], "risks": ["未配置 LLM 大脑，跳过意图复核"]}
            res = await v.verify(body.hint or "我想从这段视频里挑出几个精彩段落",
                                 attachments_summary=body.source_summary)
            return res.to_dict()

        @router.post("/cost")
        async def cost(body: CostBody):
            est = CostEstimator(currency="CNY")
            est.add(
                f"本地 ffmpeg 渲染 {body.target_count}*{body.avg_segment_sec:.0f}s",
                units=body.target_count * body.avg_segment_sec,
                unit_label="s",
                unit_price=0.0,
            )
            est.add(
                "whisper.cpp 转写",
                units=max(1.0, body.source_duration_sec / 60.0),
                unit_label="min",
                unit_price=0.0,
            )
            est.note("本插件全部在本地运行，无 API 调用费用。")
            return est.build(confidence="high",
                             sample_label=f"本地 ffmpeg 渲染 {body.target_count}*{body.avg_segment_sec:.0f}s").to_dict()

        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["source_path"], non_empty_strings=["source_path"],
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
                rendered = self._coach.render(status=404,
                                              raw_message=f"task {task_id} not found")
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec.to_dict()

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str):
            out = await self._cancel(task_id)
            if not out:
                raise HTTPException(status_code=404, detail={"problem": "task not found"})
            return {"ok": True, "status": out.status}

        @router.post("/upload")
        async def upload(file: UploadFile = File(...)):
            data_dir = self._api.get_data_dir() / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / file.filename
            with target.open("wb") as fp:
                while chunk := await file.read(1024 * 1024):
                    fp.write(chunk)
            return {"path": str(target), "size": target.stat().st_size}

        @router.get("/storage-stats")
        async def storage_stats():
            data_dir = self._api.get_data_dir()
            stats = await collect_storage_stats(
                [data_dir / "uploads", data_dir / "outputs"],
                max_files=2000,
            )
            return stats.to_dict()

    # ── core ──

    def _get_verifier(self) -> IntentVerifier | None:
        if self._verifier is not None:
            return self._verifier
        try:
            self._brain = self._api.get_brain()
        except Exception:  # noqa: BLE001
            self._brain = None
        if not self._brain:
            return None
        brain = self._brain

        async def llm_call(messages, max_tokens: int = 500, **_kw):
            sys = "\n".join(m["content"] for m in messages if m.get("role") == "system")
            usr = "\n".join(m["content"] for m in messages if m.get("role") == "user")
            think = getattr(brain, "think_lightweight", None)
            if not callable(think):
                return ""
            resp = await think(prompt=usr, system=sys or None, max_tokens=max_tokens)
            text = getattr(resp, "text", None) or getattr(resp, "content", None) or ""
            if not isinstance(text, str):
                try:
                    text = "".join(getattr(b, "text", "") for b in text)
                except TypeError:
                    text = str(text)
            return text

        self._verifier = IntentVerifier(
            llm_call=llm_call,
            plugin_specific_context=(
                "当前插件: highlight-cutter；用途: 从一段长视频里挑出 3-10 段精彩瞬间并自动剪好，"
                "纯本地处理，不调用云端付费 API。"
            ),
        )
        return self._verifier

    async def _create(self, body: CreateBody) -> str:
        source = Path(body.source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"源视频不存在: {source}")

        tid = await self._tm.create_task(
            prompt=body.intent_hint or "auto-highlight",
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"source_video_path": str(source)},
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
        source = Path(rec.extra.get("source_video_path") or "")
        if not source.exists():
            await self._fail(task_id, FileNotFoundError(f"source missing: {source}"))
            return

        try:
            # Stage 1: transcribe
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "transcribe"})
            cfg = await self._tm.get_config()
            chunks = await transcribe_with_contrib_asr(
                source,
                provider_id=cfg.get("asr_provider", "auto"),
                region=cfg.get("asr_region", "cn"),
                language=cfg.get("asr_language", "auto"),
                model=cfg.get("asr_model", "base"),
                binary=cfg.get("asr_binary", "whisper-cli"),
                allow_stub=False,
            )

            # Stage 2: score & pick (audit3 三分自检 enforced inside pick_segments)
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "score"})
            scored = keyword_score(chunks)
            segments = pick_segments(
                scored,
                target_count=int(params.get("target_count", 5)),
                min_segment_sec=float(params.get("min_segment_sec", 3.0)),
                max_segment_sec=float(params.get("max_segment_sec", 20.0)),
            )
            if not segments:
                # No transcript / no chunks — fall back to evenly cut segments
                segments = self._even_fallback_segments(
                    source_duration=self._probe_duration(source),
                    target_count=int(params.get("target_count", 5)),
                )

            # Stage 3: render
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "render"})
            output = self._api.get_data_dir() / "outputs" / f"{task_id}.mp4"
            ffmpeg = cfg.get("ffmpeg_path") or "ffmpeg"
            await asyncio.to_thread(
                render_highlights,
                source=source, segments=segments, output=output, ffmpeg=ffmpeg,
            )

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"output_path": str(output),
                        "segments": [s.to_dict() for s in segments]},
                extra={"output_video_path": str(output),
                       "segments_json": _json_dumps([s.to_dict() for s in segments]),
                       "transcript_json": _json_dumps([c.__dict__ for c in chunks])},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "output_path": str(output)})
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        await self._tm.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            error_message=rendered.problem,
            result={"error": rendered.to_dict()},
        )
        self._events.emit("task_updated", {"id": task_id, "status": "failed",
                                           "error": rendered.to_dict()})

    @staticmethod
    def _even_fallback_segments(*, source_duration: float, target_count: int) -> list[HighlightSegment]:
        if source_duration <= 0:
            return []
        segs: list[HighlightSegment] = []
        chunk_total = min(source_duration, 120.0)  # at most 2 minutes total
        per = chunk_total / max(1, target_count)
        for i in range(target_count):
            start = i * (source_duration / max(1, target_count))
            end = min(start + per, source_duration)
            segs.append(HighlightSegment(
                start=start, end=end, score=0.0,
                reason="fallback (no transcript) — equal split",
                label=f"段{i+1}",
            ))
        return segs

    @staticmethod
    def _probe_duration(source: Path) -> float:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        import subprocess
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(source)],
                check=True, capture_output=True, timeout=30,
            )
            return float(out.stdout.decode().strip() or 0.0)
        except (subprocess.SubprocessError, ValueError):
            return 0.0


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
