"""storyboard — script → shot list, with 5-level fallback parsing & 3-thirds self-check."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach, QualityGates, TaskStatus, UIEventEmitter,
)

from storyboard_engine import (
    _SYSTEM, Shot, Storyboard, parse_storyboard_llm_output, self_check,
    to_seedance_payload, to_tongyi_payload, to_verification,
)
from task_manager import StoryboardTaskManager

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    script: str = Field(..., min_length=1)
    title: str = ""
    target_duration_sec: float = 30.0
    style_hint: str = ""


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = StoryboardTaskManager(data_dir / "storyboard.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "storyboard_create",
                 "description": "Generate a storyboard / shot list from a script.",
                 "input_schema": {"type": "object",
                                  "properties": {"script": {"type": "string"},
                                                 "target_duration_sec": {"type": "number"}},
                                  "required": ["script"]}},
                {"name": "storyboard_status",
                 "description": "Get a storyboard task status.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
                {"name": "storyboard_list",
                 "description": "List recent storyboards.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "storyboard_cancel",
                 "description": "Cancel a storyboard task.",
                 "input_schema": {"type": "object",
                                  "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}},
            ],
            self._handle_tool_call,
        )
        api.log("storyboard loaded")

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
                        f"storyboard on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "storyboard_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建任务 {tid}"
            if tool_name == "storyboard_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "storyboard_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "storyboard_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "storyboard"}

        @router.get("/config")
        async def get_config():
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict):
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

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

        @router.get("/tasks/{task_id}/export.csv")
        async def export_csv(task_id: str):
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.result.get("storyboard"):
                raise HTTPException(status_code=404, detail={"problem": "no storyboard"})
            sb = rec.result["storyboard"]
            from io import StringIO
            buf = StringIO()
            buf.write("index,duration_sec,visual,camera,dialogue,sound,notes\n")
            for s in sb.get("shots", []):
                row = [str(s.get("index", "")), str(s.get("duration_sec", "")),
                       _csv_safe(s.get("visual", "")), _csv_safe(s.get("camera", "")),
                       _csv_safe(s.get("dialogue", "")), _csv_safe(s.get("sound", "")),
                       _csv_safe(s.get("notes", ""))]
                buf.write(",".join(row) + "\n")
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                     headers={"Content-Disposition": f'attachment; filename="{task_id}.csv"'})

        @router.get("/tasks/{task_id}/export-seedance.json")
        async def export_seedance(
            task_id: str,
            model: str = "doubao-seedance-2-0-260128",
            ratio: str = "16:9",
            resolution: str = "720p",
            plugin_model: str = "2.0",
        ):
            """Export storyboard as a Seedance task list (dual delivery).

            One JSON entry per shot plus three copy-pasteable channels:

            * ``cli_examples`` — ``python scripts/seedance.py create ...``
            * ``post_examples`` — bodies for ``POST /api/plugins/seedance-video/tasks``
            * ``curl_examples`` — POSIX-quoted ``curl`` invocations

            Sprint 7 dual-mode upgrade: now bridges to the in-process
            ``plugins/seedance-video`` REST API in addition to the CLI,
            so storyboard → video can stay 100% inside the running host
            without shelling out.
            """
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.result.get("storyboard"):
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "no storyboard"},
                )
            sb_dict = rec.result["storyboard"]
            sb = Storyboard(
                title=sb_dict.get("title", "未命名分镜"),
                target_duration_sec=float(
                    sb_dict.get("target_duration_sec", 30.0)
                ),
                style_notes=sb_dict.get("style_notes", ""),
                shots=[
                    Shot(
                        index=int(s.get("index", i + 1)),
                        duration_sec=float(s.get("duration_sec", 0.0)),
                        visual=str(s.get("visual", "")),
                        camera=str(s.get("camera", "")),
                        dialogue=str(s.get("dialogue", "")),
                        sound=str(s.get("sound", "")),
                        notes=str(s.get("notes", "")),
                    )
                    for i, s in enumerate(sb_dict.get("shots", []))
                ],
            )
            payload = to_seedance_payload(
                sb,
                model=model,
                ratio=ratio,
                resolution=resolution,
                plugin_model=plugin_model,
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(
                payload,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-seedance.json"'
                    ),
                },
            )

        @router.get("/tasks/{task_id}/export-tongyi.json")
        async def export_tongyi(
            task_id: str,
            model: str = "wan27-pro",
            size: str = "1024*1024",
            n: int = 1,
        ):
            """Export storyboard as a Tongyi-image-compatible task list.

            One JSON entry per shot, plus copy-pasteable curl examples
            and a ``post_examples`` array whose ``body`` field can be
            POSTed verbatim to ``/api/plugins/tongyi-image/tasks``.

            Bridges the "plan → image" gap so users can feed a finished
            storyboard straight into batch generation without re-typing
            each shot.  Mirrors the export-seedance.json contract.
            """
            rec = await self._tm.get_task(task_id)
            if rec is None or not rec.result.get("storyboard"):
                raise HTTPException(
                    status_code=404,
                    detail={"problem": "no storyboard"},
                )
            sb_dict = rec.result["storyboard"]
            sb = Storyboard(
                title=sb_dict.get("title", "未命名分镜"),
                target_duration_sec=float(
                    sb_dict.get("target_duration_sec", 30.0)
                ),
                style_notes=sb_dict.get("style_notes", ""),
                shots=[
                    Shot(
                        index=int(s.get("index", i + 1)),
                        duration_sec=float(s.get("duration_sec", 0.0)),
                        visual=str(s.get("visual", "")),
                        camera=str(s.get("camera", "")),
                        dialogue=str(s.get("dialogue", "")),
                        sound=str(s.get("sound", "")),
                        notes=str(s.get("notes", "")),
                    )
                    for i, s in enumerate(sb_dict.get("shots", []))
                ],
            )
            payload = to_tongyi_payload(sb, model=model, size=size, n=n)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                payload,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-tongyi.json"'
                    ),
                },
            )

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.script[:200],
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={"script_text": body.script},
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

        try:
            await self._tm.update_task(task_id, status=TaskStatus.RUNNING.value)
            self._events.emit("task_updated", {"id": task_id, "status": "running",
                                               "stage": "llm"})

            text = await self._call_llm(
                script=params.get("script", ""),
                title=params.get("title", "") or "未命名分镜",
                duration=float(params.get("target_duration_sec", 30.0)),
                style=params.get("style_hint", ""),
            )

            sb = parse_storyboard_llm_output(
                text,
                fallback_title=params.get("title", "") or "未命名分镜",
                fallback_duration=float(params.get("target_duration_sec", 30.0)),
            )
            check = self_check(sb)
            sb_dict = sb.to_dict()
            check_dict = check.to_dict()
            verification_dict = to_verification(sb, check).to_dict()

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={"storyboard": sb_dict, "self_check": check_dict,
                        "verification": verification_dict, "raw_llm": text[:1000]},
                extra={"storyboard_json": json.dumps(sb_dict, ensure_ascii=False),
                       "self_check_json": json.dumps(check_dict, ensure_ascii=False),
                       "verification_json": json.dumps(verification_dict, ensure_ascii=False)},
            )
            self._events.emit("task_updated", {"id": task_id, "status": "succeeded",
                                               "shot_count": len(sb_dict.get("shots", [])),
                                               "self_check": check_dict,
                                               "verification": verification_dict})
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _call_llm(self, *, script: str, title: str, duration: float, style: str) -> str:
        try:
            brain = self._api.get_brain()
        except Exception:  # noqa: BLE001
            brain = None
        if brain is None or not callable(getattr(brain, "think_lightweight", None)):
            # No brain → degrade to a deterministic stub script the parser can chew
            shot_count = max(1, int(duration / 5))
            stub = {"title": title, "target_duration_sec": duration,
                    "style_notes": style or "(无 LLM，退化为均匀分段)",
                    "shots": [
                        {"index": i + 1, "duration_sec": duration / shot_count,
                         "visual": script[i*60:(i+1)*60] or f"段落 {i+1}",
                         "camera": "固定",
                         "dialogue": "", "sound": "", "notes": "stub fallback"}
                        for i in range(shot_count)
                    ]}
            return json.dumps(stub, ensure_ascii=False)

        sys_prompt = _SYSTEM
        if style:
            sys_prompt += f"\n\n## 风格\n{style}"
        user_prompt = (
            f"标题: {title}\n"
            f"目标时长(秒): {duration}\n\n"
            f"## 脚本\n{script}\n"
        )
        resp = await brain.think_lightweight(prompt=user_prompt, system=sys_prompt,
                                              max_tokens=2000)
        text = getattr(resp, "text", None) or getattr(resp, "content", None) or ""
        if not isinstance(text, str):
            try: text = "".join(getattr(b, "text", "") for b in text)
            except TypeError: text = str(text)
        return text

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        await self._tm.update_task(
            task_id, status=TaskStatus.FAILED.value,
            error_message=rendered.problem, result={"error": rendered.to_dict()},
        )
        self._events.emit("task_updated", {"id": task_id, "status": "failed",
                                           "error": rendered.to_dict()})


def _csv_safe(s: str) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ")
    if any(c in s for c in (",", '"')):
        s = '"' + s.replace('"', '""') + '"'
    return s
