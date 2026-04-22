"""local-sd-flux — plugin entry point.

Wires :mod:`image_engine` + :mod:`workflow_presets` + :mod:`comfy_client`
into the OpenAkita plugin host (HTTP routes + brain tools + worker).
All graph mutation lives in :mod:`workflow_presets`; HTTP & retry
discipline lives in :mod:`comfy_client`; this file is glue + lifecycle.

Conventions copied verbatim from ``plugins/ppt-to-video/plugin.py``:

* one ``asyncio.Task`` per job, tracked in ``self._workers`` so
  ``on_unload`` can drain cleanly,
* every brain tool funnels through :meth:`_handle_tool_call` which
  catches & renders exceptions through :class:`ErrorCoach`,
* :class:`QualityGates` validates request bodies before queuing.
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
    add_upload_preview_route,
)

from comfy_client import ComfyClient, DEFAULT_BASE_URL
from image_engine import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_RUN_TIMEOUT_SEC,
    describe,
    list_available_presets,
    plan_image,
    rank_image_providers,
    run_image,
    to_verification,
)
from task_manager import SDFluxTaskManager
from workflow_presets import PRESET_IDS

logger = logging.getLogger(__name__)


# ── HTTP request bodies ────────────────────────────────────────────────


class CreateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    preset_id: str = "sdxl_basic"
    overrides: dict[str, Any] | None = None
    custom_workflow: dict[str, Any] | None = None
    output_dir: str | None = None
    output_format: str = DEFAULT_OUTPUT_FORMAT
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC
    timeout_sec: float = DEFAULT_RUN_TIMEOUT_SEC
    base_url: str | None = None
    auth_token: str | None = None


class PreviewBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    preset_id: str = "sdxl_basic"
    overrides: dict[str, Any] | None = None
    custom_workflow: dict[str, Any] | None = None
    output_format: str = DEFAULT_OUTPUT_FORMAT


class RankBody(BaseModel):
    candidates: list[dict[str, Any]] = Field(..., min_length=1)


# ── plugin entry ───────────────────────────────────────────────────────


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd()
        self._data_dir = data_dir
        self._tm = SDFluxTaskManager(data_dir / "local_sd_flux.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {"name": "local_sd_flux_create",
                 "description": "Generate one or more images via a local ComfyUI server. Picks SD 1.5 / SDXL / FLUX preset workflows or accepts a custom workflow JSON.",
                 "input_schema": {
                     "type": "object",
                     "properties": {
                         "prompt": {"type": "string"},
                         "preset_id": {"type": "string"},
                         "overrides": {"type": "object"},
                     },
                     "required": ["prompt"],
                 }},
                {"name": "local_sd_flux_status",
                 "description": "Get the status of an image-generation job.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "local_sd_flux_list",
                 "description": "List recent image-generation jobs.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "local_sd_flux_cancel",
                 "description": "Interrupt the currently running ComfyUI prompt.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"],
                 }},
                {"name": "local_sd_flux_check_deps",
                 "description": "Check whether the configured ComfyUI server is reachable and which presets are ready to use.",
                 "input_schema": {"type": "object", "properties": {}}},
                {"name": "local_sd_flux_rank_providers",
                 "description": "Rank a list of ComfyUI candidates (local-GPU / local-CPU / remote) using the SDK's 7-dim provider_score.",
                 "input_schema": {
                     "type": "object",
                     "properties": {"candidates": {"type": "array"}},
                     "required": ["candidates"],
                 }},
            ],
            self._handle_tool_call,
        )
        api.log("local-sd-flux loaded")

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
                        f"local-sd-flux on_unload worker drain error: {res!r}",
                        level="warning",
                    )
        self._workers.clear()

    # ── helpers ────────────────────────────────────────────────────

    def _build_client(self, body: CreateBody | None = None) -> ComfyClient:
        """Return a ``ComfyClient`` honouring per-request overrides + config."""
        cfg_base = body.base_url if body and body.base_url else None
        cfg_token = body.auth_token if body and body.auth_token else None
        # Synchronous reads from task_manager would need an async call;
        # plugin worker is already async so we delegate config defaults
        # to ``_build_client_async`` for the actual job path.
        return ComfyClient(
            base_url=cfg_base or DEFAULT_BASE_URL,
            auth_token=cfg_token or None,
        )

    async def _build_client_async(self, body: CreateBody | None = None) -> ComfyClient:
        cfg = await self._tm.get_config()
        base = (body.base_url if body and body.base_url else None) \
            or cfg.get("default_base_url") or DEFAULT_BASE_URL
        token = (body.auth_token if body and body.auth_token else None) \
            or cfg.get("default_auth_token") or None
        return ComfyClient(base_url=base, auth_token=token or None)

    def _check_deps_sync(self) -> dict[str, Any]:
        """Return dep / preset overview WITHOUT contacting ComfyUI."""
        return {
            "presets": list_available_presets(),
            "preset_count": len(PRESET_IDS),
            "default_base_url": DEFAULT_BASE_URL,
            "comfyui_reachability": "unknown — call /check-server to probe",
        }

    async def _check_server(self) -> dict[str, Any]:
        """Probe the configured ComfyUI server's ``/system_stats``.

        Returns ``{ok: bool, base_url, devices: [...], message?}``.  Never
        raises — failures are surfaced as ``ok=False`` plus a message so
        the UI can render an actionable error badge instead of a 500.
        """
        cfg = await self._tm.get_config()
        base = cfg.get("default_base_url") or DEFAULT_BASE_URL
        token = cfg.get("default_auth_token") or None
        client = ComfyClient(base_url=base, auth_token=token or None, max_retries=0)
        try:
            stats = await client.system_stats()
            return {
                "ok": True,
                "base_url": base,
                "devices": (stats or {}).get("devices") or [],
                "system": (stats or {}).get("system") or {},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "base_url": base,
                "message": str(exc),
            }

    # ── brain tool dispatcher ───────────────────────────────────────

    async def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        try:
            if tool_name == "local_sd_flux_create":
                tid = await self._create(CreateBody(**args))
                return f"已创建本地出图任务 {tid}"
            if tool_name == "local_sd_flux_status":
                rec = await self._tm.get_task(args["task_id"])
                if not rec:
                    return "未找到该任务"
                msg = rec.status
                if rec.error_message:
                    msg += f"：{rec.error_message}"
                return msg
            if tool_name == "local_sd_flux_list":
                rows = await self._tm.list_tasks(limit=20)
                if not rows:
                    return "(空)"
                return "\n".join(
                    f"{r.id} {r.status} {r.extra.get('preset_id', '?')} "
                    f"({r.extra.get('image_count', 0)} imgs)"
                    for r in rows
                )
            if tool_name == "local_sd_flux_cancel":
                ok = await self._cancel(args["task_id"])
                return "已取消" if ok else "未找到或已结束"
            if tool_name == "local_sd_flux_check_deps":
                deps = self._check_deps_sync()
                return (
                    f"presets: {', '.join(deps['presets'])}\n"
                    f"default_base_url: {deps['default_base_url']}"
                )
            if tool_name == "local_sd_flux_rank_providers":
                cands = args.get("candidates") or []
                ranked = rank_image_providers(cands)
                return "\n".join(
                    f"#{i + 1} {r.label} ({r.base_url}) total={r.score.total:.3f}"
                    for i, r in enumerate(ranked)
                ) or "(no candidates)"
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
                "plugin": "local-sd-flux",
                "deps": self._check_deps_sync(),
            }

        @router.get("/check-deps")
        async def check_deps() -> dict[str, Any]:
            return self._check_deps_sync()

        @router.get("/check-server")
        async def check_server() -> dict[str, Any]:
            return await self._check_server()

        @router.get("/presets")
        async def get_presets() -> dict[str, Any]:
            return {
                "items": [describe(p) for p in list_available_presets()],
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
            try:
                plan = plan_image(
                    prompt=body.prompt,
                    output_dir=str(self._data_dir / "outputs" / "preview"),
                    preset_id=body.preset_id,
                    overrides=body.overrides,
                    custom_workflow=body.custom_workflow,
                    output_format=body.output_format,
                )
            except (ValueError, KeyError) as exc:
                rendered = self._coach.render(exc, raw_message=str(exc))
                raise HTTPException(status_code=400, detail=rendered.to_dict()) from exc
            return {"plan": plan.to_dict(), "presets": list_available_presets()}

        @router.post("/rank-providers")
        async def rank_providers_route(body: RankBody) -> dict[str, Any]:
            ranked = rank_image_providers(body.candidates)
            return {"items": [r.to_dict() for r in ranked]}

        @router.post("/tasks")
        async def create_task(body: CreateBody) -> dict[str, Any]:
            gate = QualityGates.check_input_integrity(
                body.model_dump(),
                required=["prompt"],
                non_empty_strings=["prompt"],
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

        @router.get("/tasks/{task_id}/image/{idx}")
        async def serve_image(task_id: str, idx: int) -> FileResponse:
            rec = await self._tm.get_task(task_id)
            if rec is None:
                raise HTTPException(status_code=404, detail={"problem": "no task"})
            # ``result.image_paths`` is the canonical list — TaskRecord.extra
            # strips ``*_json`` columns, so we read straight from result.
            paths = (rec.result or {}).get("image_paths") or []
            if not isinstance(paths, list) or idx < 0 or idx >= len(paths):
                raise HTTPException(status_code=404, detail={"problem": "image index out of range"})
            p = Path(paths[idx])
            if not p.is_file():
                raise HTTPException(status_code=404, detail={"problem": "file missing on disk"})
            ext = p.suffix.lower().lstrip(".")
            ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "webp": "image/webp"}.get(ext, "application/octet-stream")
            return FileResponse(p, media_type=ctype, filename=p.name)

    # ── lifecycle helpers ───────────────────────────────────────────

    async def _create(self, body: CreateBody) -> str:
        tid = await self._tm.create_task(
            params=body.model_dump(),
            status=TaskStatus.PENDING.value,
            extra={
                "preset_id": "custom" if body.custom_workflow else body.preset_id,
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
        # Try to interrupt the running ComfyUI prompt — best effort.
        try:
            client = await self._build_client_async()
            await client.cancel_task(task_id)
        except Exception as exc:  # noqa: BLE001
            self._api.log(
                f"local-sd-flux: cancel via ComfyUI failed: {exc!r}",
                level="warning",
            )
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

            output_dir = params.get("output_dir") or str(
                self._data_dir / "outputs" / task_id,
            )
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            plan = plan_image(
                prompt=str(params["prompt"]),
                output_dir=output_dir,
                preset_id=str(params.get("preset_id", "sdxl_basic")),
                overrides=params.get("overrides") or None,
                custom_workflow=params.get("custom_workflow") or None,
                output_format=str(params.get("output_format", DEFAULT_OUTPUT_FORMAT)),
                poll_interval_sec=float(params.get("poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC)),
                timeout_sec=float(params.get("timeout_sec", DEFAULT_RUN_TIMEOUT_SEC)),
            )
            client = await self._build_client_async(CreateBody(**params))

            def _on_progress(stage: str, done: int, total: int) -> None:
                self._events.emit("task_updated", {
                    "id": task_id, "status": "running", "stage": stage,
                    "done": done, "total": total,
                })

            result = await run_image(plan, client=client, on_progress=_on_progress)

            verification_dict = to_verification(result).to_dict()
            plan_dict = plan.to_dict()

            await self._tm.update_task(
                task_id,
                status=TaskStatus.SUCCEEDED.value,
                result={
                    "prompt_id": result.prompt_id,
                    "image_paths": list(result.image_paths),
                    "image_count": result.image_count,
                    "bytes_total": result.bytes_total,
                    "elapsed_sec": result.elapsed_sec,
                    "polls": result.polls,
                    "verification": verification_dict,
                    "plan": plan_dict,
                },
                extra={
                    "preset_id": plan.preset_id,
                    "prompt_id": result.prompt_id,
                    "output_dir": output_dir,
                    "image_count": result.image_count,
                    "bytes_total": result.bytes_total,
                    "image_paths_json": json.dumps(list(result.image_paths)),
                    "verification_json": json.dumps(verification_dict, ensure_ascii=False),
                    "plan_json": json.dumps(plan_dict, ensure_ascii=False),
                },
            )
            self._events.emit("task_updated", {
                "id": task_id, "status": "succeeded",
                "image_count": result.image_count,
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
                f"local-sd-flux failed to record failure: {inner!r}",
                level="warning",
            )
        self._events.emit("task_updated", {
            "id": task_id, "status": "failed", "error": rendered.to_dict(),
        })


__all__ = ["Plugin"]
