"""smart-poster-grid — plugin entry point.

Wires :mod:`grid_engine` and :mod:`task_manager` to the OpenAkita
plugin host (HTTP routes + brain tools + lifecycle).  All of the
actual layout / rendering math lives in the sibling ``poster-maker``
plugin (loaded via :func:`grid_engine._load_poster_maker_module`) and
the SDK — this file is purely glue.

Conventions copied verbatim from ``plugins/video-color-grade/plugin.py``:

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* :class:`QualityGates` validates every request body before queuing,
* the worker offloads Pillow rendering to a thread (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach,
    QualityGates,
    TaskStatus,
    UIEventEmitter,
    add_upload_preview_route,
    build_preview_url,
)

from grid_engine import (
    DEFAULT_RATIOS,
    build_grid_plan,
    list_ratios,
    render_grid,
    to_verification,
)
from task_manager import GridTaskManager

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    """POST /tasks payload."""

    text_values: dict[str, str] = Field(default_factory=dict)
    background_image_path: str | None = None
    ratio_ids: list[str] | None = None  # default = all 4 in DEFAULT_RATIOS


class PreviewBody(BaseModel):
    """POST /preview — return the plan WITHOUT rendering."""

    text_values: dict[str, str] = Field(default_factory=dict)
    background_image_path: str | None = None
    ratio_ids: list[str] | None = None


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = GridTaskManager(data_dir / "smart_poster_grid.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "smart_poster_grid_create",
                 "description": "Render the same poster across 4 social aspect ratios "
                                "(1:1, 3:4, 9:16, 16:9) in one job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "text_values": {"type": "object"},
                         "background_image_path": {"type": "string"},
                         "ratio_ids": {"type": "array",
                                       "items": {"type": "string"}},
                     },
                 }},
                {"name": "smart_poster_grid_status",
                 "description": "Get a grid job's status.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "smart_poster_grid_list",
                 "description": "List recent grid jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "smart_poster_grid_cancel",
                 "description": "Cancel a running grid job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "smart_poster_grid_ratios",
                 "description": "List the 4 supported aspect ratios.",
                 "input_schema": {"type": "object", "properties": {}}},
            ],
            self._handle_tool_call,
        )
        api.log("smart-poster-grid loaded")

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
                        f"smart-poster-grid on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "smart_poster_grid_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建海报多尺寸任务 {tid}"
            if tool_name == "smart_poster_grid_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "smart_poster_grid_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} ({r.extra.get('output_dir', '') or ''})"
                    for r in rows
                )
            if tool_name == "smart_poster_grid_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "smart_poster_grid_ratios":
                return "\n".join(
                    f"{r['id']}  {r['width']}x{r['height']}  {r['label']}"
                    for r in list_ratios()
                )
        except Exception as e:  # noqa: BLE001
            r = self._coach.render(e)
            return f"[{r.cause_category}] {r.problem} → {r.next_step}"
        return f"unknown tool: {tool_name}"

    # ── routes ──────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        # Reuse the SDK's preview-route helper so ``<img src=...>``
        # works in the UI without each plugin reinventing the wheel.
        add_upload_preview_route(
            router, base_dir=self._data_dir / "uploads",
        )

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": "smart-poster-grid",
                "ratios": list_ratios(),
            }

        @router.get("/ratios")
        async def ratios() -> dict[str, Any]:
            return {"ratios": list_ratios()}

        @router.get("/config")
        async def get_config() -> dict[str, str]:
            return await self._tm.get_config()

        @router.post("/config")
        async def set_config(updates: dict[str, Any]) -> dict[str, str]:
            await self._tm.set_config({k: str(v) for k, v in updates.items()})
            return await self._tm.get_config()

        @router.post("/upload-background")
        async def upload_background(file: UploadFile = File(...)) -> dict[str, str]:
            data_dir = self._data_dir / "uploads"
            data_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(file.filename or "image.png").suffix or ".png"
            target = data_dir / f"{uuid.uuid4().hex[:12]}{ext}"
            with target.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            return {
                "path": str(target),
                "url": build_preview_url("smart-poster-grid", target.name),
            }

        @router.post("/preview")
        async def preview(body: PreviewBody) -> dict[str, Any]:
            try:
                plan = await asyncio.to_thread(
                    build_grid_plan,
                    text_values=body.text_values,
                    background_image_path=body.background_image_path,
                    output_dir=str(self._data_dir / "outputs" / "preview"),
                    ratio_ids=body.ratio_ids,
                )
            except ValueError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from e
            return {"plan": plan.to_dict(), "ratios": list_ratios()}

        @router.post("/tasks")
        async def create_task(body: CreateBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                # ``text_values`` may legitimately be empty (templates
                # have placeholders), so the only blocking check is
                # that *something* sane was sent.
                required=[],
                non_empty_strings=[],
            )
            if gate.blocking:
                rendered = self._coach.render(
                    ValueError(gate.message), raw_message=gate.message,
                )
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            try:
                # Validate ratio_ids early — fail with a 400, not a 500.
                build_grid_plan(
                    text_values=body.text_values,
                    background_image_path=body.background_image_path,
                    output_dir=str(self._data_dir / "outputs" / "validate"),
                    ratio_ids=body.ratio_ids,
                )
            except ValueError as e:
                rendered = self._coach.render(e, raw_message=str(e))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from e
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

        @router.get("/tasks/{task_id}/poster/{ratio_id}")
        async def serve_poster(task_id: str, ratio_id: str) -> FileResponse:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                raise HTTPException(
                    status_code=404, detail={"problem": "task not found"},
                )
            # ``renders`` lives in the API-facing result blob (the
            # ``renders_json`` column is intentionally not surfaced via
            # TaskRecord.extra — the SDK strips ``*_json`` columns to
            # avoid double-decoding).
            renders = (rec.result or {}).get("renders") or []
            for r in renders:
                if r.get("ratio_id") == ratio_id and r.get("output_path"):
                    p = Path(r["output_path"])
                    if p.is_file():
                        return FileResponse(p, media_type="image/png", filename=p.name)
            raise HTTPException(
                status_code=404,
                detail={"problem": f"no poster file for ratio {ratio_id!r}"},
            )

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        ratios = body.ratio_ids or [r.id for r in DEFAULT_RATIOS]
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={
                "background_image_path": body.background_image_path or "",
                "ratio_ids_json": json.dumps(ratios, ensure_ascii=False),
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

            output_dir = self._data_dir / "outputs" / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            plan = await asyncio.to_thread(
                build_grid_plan,
                text_values=params.get("text_values") or {},
                background_image_path=params.get("background_image_path") or None,
                output_dir=str(output_dir),
                ratio_ids=params.get("ratio_ids"),
            )

            self._events.emit("task_updated", {
                "id": task_id, "status": "running", "stage": "rendering",
                "ratios": [r.id for r in plan.ratios],
            })
            result = await asyncio.to_thread(render_grid, plan)

            verification = to_verification(result)
            verification_dict = verification.to_dict()
            renders = [r.to_dict() for r in result.renders]
            plan_dict = plan.to_dict()

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                # Dual-storage pattern (see video-color-grade):
                # ``result`` is the API-facing payload, ``extra``
                # writes the same blobs into typed columns so a
                # future SQL query can read them without re-parsing.
                result={
                    "plan": plan_dict,
                    "renders": renders,
                    "succeeded_count": result.succeeded_count,
                    "failed_count": result.failed_count,
                    "verification": verification_dict,
                },
                extra={
                    "output_dir": str(output_dir),
                    "background_image_path": plan.background_image_path or "",
                    "ratio_ids_json": json.dumps(
                        [r.id for r in plan.ratios], ensure_ascii=False,
                    ),
                    "renders_json": json.dumps(renders, ensure_ascii=False),
                    "verification_json": json.dumps(
                        verification_dict, ensure_ascii=False,
                    ),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "succeeded_count": result.succeeded_count,
                "failed_count": result.failed_count,
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
                f"smart-poster-grid failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed",
            "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
