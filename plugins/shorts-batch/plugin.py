"""shorts-batch — plugin entry point.

Glues :mod:`shorts_engine` to the OpenAkita plugin host (FastAPI
routes + brain tools + lifecycle).  All scene planning / risk
scoring / aggregation lives in the engine; this file only owns
HTTP, persistence and the worker thread.

Two extension points are intentionally exposed via
:meth:`Plugin.set_planner` and :meth:`Plugin.set_renderer`: a host
embedding this plugin can swap in a real LLM planner / a downstream
seedance-video renderer at boot time without touching the engine.
The defaults are deterministic stubs so the plugin is useful out of
the box for testing and dry runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
)
from pipeline_orchestrator import (
    PipelineConfig,
    run_pipeline,
)
from pipeline_orchestrator import (
    to_verification as pipeline_to_verification,
)
from pydantic import BaseModel, Field
from shorts_engine import (
    ALLOWED_ASPECTS,
    DEFAULT_MAX_SHOTS,
    DEFAULT_MIN_SHOTS,
    BatchResult,
    ShortBrief,
    ShortPlan,
    default_scene_planner,
    plan_briefs,
    run_briefs,
    to_verification,
)

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class BriefBody(BaseModel):
    topic: str = Field(..., min_length=1)
    duration_sec: float = 15.0
    style: str = "vlog"
    target_aspect: str = "9:16"
    language: str = "zh-CN"
    extra: dict[str, Any] | None = None


class CreateBatchBody(BaseModel):
    briefs: list[BriefBody] = Field(..., min_length=1, max_length=50)
    risk_block_threshold: str | None = None
    min_shots: int = DEFAULT_MIN_SHOTS
    max_shots: int = DEFAULT_MAX_SHOTS


class PreviewRiskBody(BaseModel):
    briefs: list[BriefBody] = Field(..., min_length=1, max_length=50)
    min_shots: int = DEFAULT_MIN_SHOTS
    max_shots: int = DEFAULT_MAX_SHOTS


class PipelineBody(BaseModel):
    """Run the 6-stage video-pipeline orchestrator for a single brief.

    Stages default to deterministic stubs so the call is safe to issue
    without external API keys; see :mod:`pipeline_orchestrator` for how
    a host wires real downstream plugins.
    """

    brief: BriefBody
    skip_video_stage: bool = True
    skip_subtitle_stage: bool = False
    burn_subtitles: bool = False
    target_language: str = "zh"


# ── stub renderer ─────────────────────────────────────────────────────


def _make_stub_renderer(out_dir: Path) -> Callable[[ShortPlan], tuple[str, int]]:
    """Default renderer: writes a 1-byte placeholder + returns its path.

    Lets the plugin be useful for end-to-end smoke tests without
    pulling in seedance-video / ffmpeg.  Real integrations call
    :meth:`Plugin.set_renderer` to override.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _render(plan: ShortPlan) -> tuple[str, int]:
        # Filename uses brief topic so users can see what was rendered;
        # we sanitize only the most dangerous characters.
        safe_topic = "".join(
            c for c in plan.brief.topic if c.isalnum() or c in (" ", "-", "_")
        )[:40] or "short"
        path = out_dir / f"{safe_topic}.mp4"
        path.write_bytes(b"\x00")  # 1-byte placeholder, intentional
        return str(path), 1

    return _render


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        from task_manager import ShortsBatchTaskManager
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = ShortsBatchTaskManager(data_dir / "shorts_batch.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        self._planner: Callable[[ShortBrief], list[dict[str, Any]]] = (
            default_scene_planner
        )
        self._renderer: Callable[[ShortPlan], tuple[str, int]] = (
            _make_stub_renderer(data_dir / "outputs")
        )
        # 6-stage pipeline: missing keys fall back to the default stubs in
        # pipeline_orchestrator. Hosts override per stage via set_pipeline_stage.
        self._pipeline_stages: dict[str, Callable[..., Any]] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "shorts_batch_create",
                 "description": "Create a batch of shorts. Each brief is expanded into a scene plan, risk-scored with slideshow_risk, then rendered through the configured downstream renderer.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "briefs": {"type": "array"},
                         "risk_block_threshold": {"type": "string"},
                     },
                     "required": ["briefs"],
                 }},
                {"name": "shorts_batch_status",
                 "description": "Get the status + risk distribution of a shorts-batch job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "shorts_batch_list",
                 "description": "List recent shorts-batch jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "shorts_batch_cancel",
                 "description": "Cancel a running shorts-batch job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "shorts_batch_preview_risk",
                 "description": "Plan the briefs WITHOUT rendering and return the slideshow_risk verdict for each. Use this before spending API quota.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"briefs": {"type": "array"}},
                     "required": ["briefs"],
                 }},
            ],
            self._handle_tool_call,
        )
        api.log("shorts-batch loaded")

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
                        f"shorts-batch on_unload drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── extension points ───────────────────────────────────────────

    def set_planner(
        self, planner: Callable[[ShortBrief], list[dict[str, Any]]],
    ) -> None:
        """Swap in a real scene planner (e.g. an LLM bridge)."""
        self._planner = planner

    def set_renderer(
        self, renderer: Callable[[ShortPlan], tuple[str, int]],
    ) -> None:
        """Swap in a real renderer (e.g. seedance-video bridge)."""
        self._renderer = renderer

    def set_pipeline_stage(self, stage: str, fn: Callable[..., Any]) -> None:
        """Override one stage of the 6-step video pipeline orchestrator.

        ``stage`` must be one of ``plan|image|video|audio|subtitle|mux``.
        Pass ``None``-equivalent (call again with the default callable)
        to revert to the deterministic stub from
        :mod:`pipeline_orchestrator`.
        """
        from pipeline_orchestrator import STAGE_IDS
        if stage not in STAGE_IDS:
            raise ValueError(
                f"unknown pipeline stage {stage!r}; expected one of {STAGE_IDS}"
            )
        self._pipeline_stages[stage] = fn

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "shorts_batch_create":
                tid = await self._create(CreateBatchBody(**args))
                return f"已创建 shorts-batch 任务 {tid}"
            if tool_name == "shorts_batch_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                line = f"{rec.status}"
                if rec.error_message:
                    line += f"：{rec.error_message}"
                return line
            if tool_name == "shorts_batch_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} "
                    f"{r.extra.get('succeeded_count', 0)}/"
                    f"{r.extra.get('brief_count', 0)} 成功"
                    for r in rows
                )
            if tool_name == "shorts_batch_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "shorts_batch_preview_risk":
                briefs = [_brief_from_dict(b) for b in args.get("briefs") or []]
                plans = plan_briefs(
                    briefs, scene_planner=self._planner,
                )
                lines = []
                for p in plans:
                    lines.append(
                        f"- {p.brief.topic}: 风险={p.risk.verdict} "
                        f"(score={p.risk.score}/6, "
                        f"shots={len(p.scene_plan)}, "
                        f"~${p.estimated_cost_usd:.3f})"
                    )
                return "\n".join(lines) or "(无 briefs)"
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True, "plugin": "shorts-batch",
                "allowed_aspects": list(ALLOWED_ASPECTS),
                "min_shots": DEFAULT_MIN_SHOTS, "max_shots": DEFAULT_MAX_SHOTS,
            }

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/preview-risk")
        async def preview_risk(body: PreviewRiskBody) -> dict[str, Any]:
            try:
                briefs = [_brief_from_pydantic(b) for b in body.briefs]
                plans = plan_briefs(
                    briefs, scene_planner=self._planner,
                    min_shots=body.min_shots, max_shots=body.max_shots,
                )
            except (ValueError, KeyError) as exc:
                rendered = self._coach.render(exc, raw_message=str(exc))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from exc
            return {
                "items": [p.to_dict() for p in plans],
                "total_estimated_cost_usd": round(
                    sum(p.estimated_cost_usd for p in plans), 4,
                ),
            }

        @router.post("/tasks")
        async def create_task(body: CreateBatchBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["briefs"],
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

        @router.post("/pipeline")
        async def run_pipeline_route(body: PipelineBody) -> dict[str, Any]:
            """Execute the 6-stage video-pipeline orchestrator synchronously.

            This is the Phase-3 surface area: a single brief flows through
            plan → image → video → audio → subtitle → mux. The default
            stages are deterministic stubs (1-byte placeholders) so this
            endpoint is safe to call in CI / dry-runs. Real pipelines wire
            ``Plugin.set_pipeline_stage()`` at boot to swap in
            ``tongyi-image`` / ``seedance-video`` / ``subtitle-maker`` /
            ffmpeg implementations.
            """
            try:
                brief = _brief_from_pydantic(body.brief)
            except (ValueError, KeyError) as exc:
                rendered = self._coach.render(exc, raw_message=str(exc))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from exc

            cfg = PipelineConfig(
                out_dir=self._data_dir / "pipeline_runs"
                / f"{int(asyncio.get_event_loop().time() * 1000)}",
                skip_video_stage=body.skip_video_stage,
                skip_subtitle_stage=body.skip_subtitle_stage,
                burn_subtitles=body.burn_subtitles,
                target_language=body.target_language,
            )
            stages = self._pipeline_stages
            result = await asyncio.to_thread(
                run_pipeline,
                brief,
                cfg,
                plan_stage=stages.get("plan"),
                image_stage=stages.get("image"),
                video_stage=stages.get("video"),
                audio_stage=stages.get("audio"),
                subtitle_stage=stages.get("subtitle"),
                mux_stage=stages.get("mux"),
            )
            verification = pipeline_to_verification(result)
            payload = result.to_dict()
            payload["verification"] = verification.to_dict()
            return payload

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBatchBody) -> str:
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={"brief_count": len(body.briefs)},
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

            briefs = [_brief_from_dict(b) for b in params.get("briefs", [])]
            plans = await asyncio.to_thread(
                plan_briefs,
                briefs,
                scene_planner=self._planner,
                min_shots=int(params.get("min_shots", DEFAULT_MIN_SHOTS)),
                max_shots=int(params.get("max_shots", DEFAULT_MAX_SHOTS)),
            )

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "rendering",
                "total": len(plans),
            })

            def _on_progress(done: int, total: int, _r) -> None:
                self._events.emit("task_updated", {
                    "id": task_id, "status": "running",
                    "stage": "rendering", "done": done, "total": total,
                })

            batch: BatchResult = await asyncio.to_thread(
                run_briefs,
                plans,
                renderer=self._renderer,
                risk_block_threshold=params.get("risk_block_threshold") or None,
                on_progress=_on_progress,
            )

            verification_dict = to_verification(batch).to_dict()
            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={
                    **batch.to_dict(),
                    "verification": verification_dict,
                },
                extra={
                    "succeeded_count": batch.succeeded,
                    "failed_count": batch.failed,
                    "total_cost_usd": batch.total_cost_usd,
                    "verification_json": json.dumps(verification_dict, ensure_ascii=False),
                    "plans_json": json.dumps(
                        [p.to_dict() for p in plans], ensure_ascii=False,
                    ),
                    "results_json": json.dumps(
                        [r.to_dict() for r in batch.results], ensure_ascii=False,
                    ),
                    "risk_distribution_json": json.dumps(
                        batch.risk_distribution, ensure_ascii=False,
                    ),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "succeeded": batch.succeeded, "failed": batch.failed,
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
                f"shorts-batch failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed", "error": rendered.to_dict(),
        })


# ── helpers ────────────────────────────────────────────────────────────


def _brief_from_pydantic(b: BriefBody) -> ShortBrief:
    return ShortBrief(
        topic=b.topic, duration_sec=b.duration_sec, style=b.style,
        target_aspect=b.target_aspect, language=b.language,
        extra=dict(b.extra or {}),
    )


def _brief_from_dict(d: dict) -> ShortBrief:
    return ShortBrief(
        topic=str(d.get("topic", "")),
        duration_sec=float(d.get("duration_sec", 15.0)),
        style=str(d.get("style", "vlog")),
        target_aspect=str(d.get("target_aspect", "9:16")),
        language=str(d.get("language", "zh-CN")),
        extra=dict(d.get("extra") or {}),
    )


__all__ = ["Plugin"]
