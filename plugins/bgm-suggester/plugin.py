"""bgm-suggester — scene + mood → structured BGM brief.

Mirrors storyboard's worker pattern (LLM call + 5-level fallback parse +
self-check + bridge exports) but produces a music-recommendation brief
instead of a shot list.  No ffmpeg, no file uploads, no model downloads.

Bridges (read-only GET routes):

* ``GET  /tasks/{id}/export.csv``           — single-row CSV
* ``GET  /tasks/{id}/export-suno.json``     — Suno AI custom-mode prompt
* ``GET  /tasks/{id}/export-search.json``   — YouTube/Spotify/Epidemic/Artlist queries
* ``GET  /tasks/{id}/export-all.json``      — bundle of brief + self_check + above

The plugin is intentionally feature-light at first commit — UI dist is
deferred so the new-plugin surface stays narrow and the diff to the rest
of the repo stays at zero.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from bgm_engine import (
    _SYSTEM,
    build_user_prompt,
    parse_bgm_llm_output,
    self_check,
    stub_brief_text,
    to_csv,
    to_export_payload,
    to_search_queries,
    to_suno_prompt,
)
from fastapi import APIRouter, HTTPException
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)
from pydantic import BaseModel, Field
from task_manager import BgmTaskManager

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class CreateBody(BaseModel):
    scene: str = Field(..., min_length=1,
                        description="Scene description, e.g. 'sunset beach vlog'")
    mood: str = Field("", description="Mood / emotion, e.g. 'calm, nostalgic'")
    target_duration_sec: float = Field(30.0, gt=0, le=600)
    tempo_hint: str = Field("", description="Optional tempo hint, e.g. 'midtempo'")
    language: str = Field("auto", description="'auto' / 'zh' / 'en'")


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._tm = BgmTaskManager(data_dir / "bgm_suggester.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {
                    "name": "bgm_create",
                    "description": (
                        "Generate a structured BGM brief (style/bpm/mood-arc/keywords) "
                        "from a scene + mood + target duration."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "scene": {"type": "string"},
                            "mood": {"type": "string"},
                            "target_duration_sec": {"type": "number"},
                            "tempo_hint": {"type": "string"},
                        },
                        "required": ["scene"],
                    },
                },
                {
                    "name": "bgm_status",
                    "description": "Get a BGM brief task status.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
                {
                    "name": "bgm_list",
                    "description": "List recent BGM brief tasks.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "bgm_cancel",
                    "description": "Cancel a BGM brief task.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            ],
            self._handle_tool_call,
        )
        api.log("bgm-suggester loaded")

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
                        f"bgm-suggester on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── tool dispatch ───────────────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "bgm_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建 BGM 简报任务 {tid}"
            if tool_name == "bgm_status":
                rec = await self._tm.get_task(args["task_id"])
                return f"{rec.status}: {rec.error_message or ''}" if rec else "未找到"
            if tool_name == "bgm_list":
                rows = await self._tm.list_tasks(limit=20)
                return "\n".join(f"{r.id} {r.status}" for r in rows) or "(空)"
            if tool_name == "bgm_cancel":
                out = await self._cancel(args["task_id"])
                return "已取消" if out else "未找到"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz():
            return {"ok": True, "plugin": "bgm-suggester"}

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
                body.model_dump(),
                required=["scene"],
                non_empty_strings=["scene"],
            )
            if gate.blocking:
                rendered = self._coach.render(
                    ValueError(gate.message), raw_message=gate.message,
                )
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
                rendered = self._coach.render(
                    status=404, raw_message=f"task {task_id} not found",
                )
                raise HTTPException(status_code=404, detail=rendered.to_dict())
            return rec.to_dict()

        @router.post("/tasks/{task_id}/cancel")
        async def cancel(task_id: str):
            out = await self._cancel(task_id)
            if not out:
                raise HTTPException(status_code=404,
                                     detail={"problem": "task not found"})
            return {"ok": True, "status": out.status}

        # ── exports ──
        # Each route delegates to ``self._load_brief`` (defined below) so
        # the 404 / "no brief" error path stays consistent across routes.

        @router.get("/tasks/{task_id}/export.csv")
        async def export_csv(task_id: str):
            brief, _check = await self._load_brief(task_id)
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                to_csv(brief),
                media_type="text/csv",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-bgm.csv"'
                    ),
                },
            )

        @router.get("/tasks/{task_id}/export-suno.json")
        async def export_suno(task_id: str):
            brief, _check = await self._load_brief(task_id)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                to_suno_prompt(brief),
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-suno.json"'
                    ),
                },
            )

        @router.get("/tasks/{task_id}/export-search.json")
        async def export_search(task_id: str):
            brief, _check = await self._load_brief(task_id)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                to_search_queries(brief),
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-search.json"'
                    ),
                },
            )

        @router.get("/tasks/{task_id}/export-all.json")
        async def export_all(task_id: str):
            brief, check = await self._load_brief(task_id)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                to_export_payload(brief, check),
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{task_id}-bgm-bundle.json"'
                    ),
                },
            )

    async def _load_brief(self, task_id: str):
        """Fetch a task's persisted BGM brief + self-check.

        Raises ``HTTPException(404)`` if the task is missing OR the brief
        is empty (e.g. task is still queued / failed before LLM ran).
        Returning a synthetic empty brief here would silently produce
        meaningless CSV / Suno prompts so we hard-stop instead.
        """
        rec = await self._tm.get_task(task_id)
        if rec is None or not rec.result.get("brief"):
            raise HTTPException(
                status_code=404,
                detail={"problem": "no BGM brief for this task"},
            )
        # Reconstruct dataclasses (the persistence layer stores plain
        # dicts in result_json / extra columns, so we round-trip through
        # the parser to get type-safe objects back).
        brief_dict = rec.result["brief"]
        check_dict = rec.result.get("self_check", {"passed": True, "issues": []})

        # Re-coerce via the same engine path for type safety — defends
        # against a stale row written by an older plugin version with
        # missing fields.
        from bgm_engine import BgmBrief, SelfCheck  # local import: keeps top tidy
        brief = BgmBrief(
            title=str(brief_dict.get("title", "")),
            target_duration_sec=float(brief_dict.get("target_duration_sec", 30.0)),
            style=str(brief_dict.get("style", "ambient")),
            tempo_bpm=int(brief_dict.get("tempo_bpm", 80)),
            tempo_label=str(brief_dict.get("tempo_label", "midtempo")),
            mood_arc=list(brief_dict.get("mood_arc") or []),
            energy_curve=[float(x) for x in (brief_dict.get("energy_curve") or [])],
            keywords=list(brief_dict.get("keywords") or []),
            avoid=list(brief_dict.get("avoid") or []),
            instrument_hints=list(brief_dict.get("instrument_hints") or []),
            notes=str(brief_dict.get("notes") or ""),
        )
        check = SelfCheck(
            passed=bool(check_dict.get("passed", True)),
            issues=list(check_dict.get("issues") or []),
        )
        return brief, check

    # ── lifecycle ───────────────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            prompt=body.scene[:200],
            params=body.model_dump(),
            status=TaskStatus.QUEUED.value,
            extra={
                "scene_text": body.scene,
                "mood_text": body.mood,
            },
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
            self._events.emit("task_updated",
                              {"id": task_id, "status": "running", "stage": "llm"})

            text = await self._call_llm(
                scene=params.get("scene", ""),
                mood=params.get("mood", ""),
                duration_sec=float(params.get("target_duration_sec", 30.0)),
                tempo_hint=params.get("tempo_hint", ""),
                language=params.get("language", "auto"),
            )

            brief = parse_bgm_llm_output(
                text,
                fallback_title=f"BGM 简报：{params.get('scene', '')[:20]}",
                fallback_duration=float(params.get("target_duration_sec", 30.0)),
            )
            check = self_check(brief)
            brief_dict = brief.to_dict()
            check_dict = check.to_dict()

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={
                    "brief": brief_dict,
                    "self_check": check_dict,
                    "raw_llm": text[:1000],
                },
                extra={
                    "brief_json": json.dumps(brief_dict, ensure_ascii=False),
                    "self_check_json": json.dumps(check_dict, ensure_ascii=False),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id,
                "status": "succeeded",
                "style": brief_dict.get("style", ""),
                "tempo_bpm": brief_dict.get("tempo_bpm", 0),
                "self_check": check_dict,
            })
        except asyncio.CancelledError:
            await self._tm.update_task(task_id, status=TaskStatus.CANCELLED.value)
            raise
        except Exception as e:  # noqa: BLE001
            await self._fail(task_id, e)

    async def _call_llm(
        self,
        *,
        scene: str,
        mood: str,
        duration_sec: float,
        tempo_hint: str,
        language: str,
    ) -> str:
        try:
            brain = self._api.get_brain()
        except Exception:  # noqa: BLE001
            brain = None

        if brain is None or not callable(getattr(brain, "think_lightweight", None)):
            # No brain installed → emit the deterministic stub so the
            # parser still has something to chew (matches storyboard's
            # behaviour, lets the smoke test pass without an LLM key).
            return stub_brief_text(scene=scene, mood=mood, duration_sec=duration_sec)

        user_prompt = build_user_prompt(
            scene=scene,
            mood=mood,
            duration_sec=duration_sec,
            tempo_hint=tempo_hint,
            language=language,
        )
        resp = await brain.think_lightweight(
            prompt=user_prompt,
            system=_SYSTEM,
            max_tokens=1500,
        )
        text = (
            getattr(resp, "text", None)
            or getattr(resp, "content", None)
            or ""
        )
        if not isinstance(text, str):
            try:
                text = "".join(getattr(b, "text", "") for b in text)
            except TypeError:
                text = str(text)
        return text

    async def _fail(self, task_id: str, exc: Exception) -> None:
        rendered = self._coach.render(exc)
        await self._tm.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            error_message=rendered.problem,
            result={"error": rendered.to_dict()},
        )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed", "error": rendered.to_dict(),
        })
