"""Media Post — publishing-kit plugin (Phase 4: routes + lifecycle).

Per ``docs/media-post-plan.md`` §11 Phase 4 + §3.1 / §3.4 / §6.6:
exposes 22 FastAPI routes wired into the 4 modes via
``mediapost_pipeline.run_pipeline``. All Pydantic schemas use
``ConfigDict(extra="forbid")`` so unknown fields return HTTP 422 with
the canonical "ignored field list" error contract (mirrors
subtitle-craft / clip-sense).

Architectural rules baked in (red-line guardrails per §13):

- Self-contained imports — only the host plugin API surface plus
  sibling ``mediapost_*`` / ``mediapost_inline.*`` modules. Imports
  from the legacy archive tree or the removed SDK contrib subpackage
  are forbidden and grep-guarded by ``tests/test_skeleton.py``.
- Cross-plugin dispatch routes (the v2.0 handoff layer) are absent
  from this file in v1.0; the absence is grep-guarded by the same
  test module.
- Playwright lazy import — ``playwright`` is never imported at module
  scope; only inside :func:`mediapost_chapter_renderer.render_chapter_cards`.
- The shell-injection escape hatch on ``subprocess`` calls is forbidden;
  every ffmpeg invocation goes through ``asyncio.create_subprocess_exec``
  with positional arguments inside the mode modules.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from mediapost_chapter_renderer import (
    builtin_template_ids,
    probe_playwright_runtime,
)
from mediapost_inline.storage_stats import collect_storage_stats
from mediapost_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from mediapost_models import (
    ALLOWED_ASPECTS,
    ALLOWED_MODES,
    ALLOWED_PLATFORMS,
    ASPECTS,
    ERROR_HINTS,
    MODES,
    PLATFORMS,
    PRICE_TABLE,
    aspect_to_dict,
    estimate_cost,
    mode_to_dict,
    platform_to_dict,
)
from mediapost_pipeline import MediaPostContext, run_pipeline
from mediapost_recompose import ffprobe_duration
from mediapost_task_manager import MediaPostTaskManager
from mediapost_vlm_client import MediaPostVlmClient
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


PLUGIN_ID = "media-post"


# ---------------------------------------------------------------------------
# Pydantic request bodies (extra="forbid" → unknown fields become HTTP 422)
# ---------------------------------------------------------------------------


class CreateTaskBody(BaseModel):
    """Request body for ``POST /tasks``."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(..., description="One of cover_pick / multi_aspect / seo_pack / chapter_cards")
    video_path: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    cost_approved: bool = False


class UpdateTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    progress: float | None = None


class ConfigUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: dict[str, str]


class CostEstimateBody(BaseModel):
    """Pre-flight cost estimation (no DB writes)."""

    model_config = ConfigDict(extra="forbid")
    mode: str
    duration_sec: float = 0.0
    quantity: int = 8
    target_aspects: list[str] | None = None
    platforms: list[str] | None = None
    recompose_fps: float = 2.0
    chapter_count: int = 0


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class Plugin(PluginBase):
    """Media Post plugin — Phase 4 (lifecycle + 22 routes)."""

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        if data_dir is None:
            raise RuntimeError("media-post requires a data dir (host bug)")
        self._data_dir: Path = data_dir
        self._tm = MediaPostTaskManager(data_dir / "media_post.sqlite")
        self._vlm_client: MediaPostVlmClient | None = None
        self._running: dict[str, MediaPostContext] = {}
        self._task_handles: dict[str, asyncio.Task[None]] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(
            [
                {
                    "name": "media_post_create",
                    "description": "Create a media-post task (cover_pick / multi_aspect / seo_pack / chapter_cards)",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": sorted(ALLOWED_MODES),
                            },
                            "video_path": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["mode"],
                    },
                },
                {
                    "name": "media_post_status",
                    "description": "Get the status of a media-post task",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
                {
                    "name": "media_post_list",
                    "description": "List recent media-post tasks",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "default": 20},
                            "mode": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "media_post_cancel",
                    "description": "Cancel a running media-post task",
                    "input_schema": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            ],
            handler=self._handle_tool,
        )

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log(f"{PLUGIN_ID} plugin loaded (22 routes registered)")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("dashscope_api_key")
        if api_key:
            self._vlm_client = MediaPostVlmClient(api_key)

    async def on_unload(self) -> None:
        for tid, handle in list(self._task_handles.items()):
            if not handle.done():
                handle.cancel()
                try:
                    await handle
                except (asyncio.CancelledError, Exception):
                    logger.debug("task %s cancel drain", tid, exc_info=True)
        if self._vlm_client is not None:
            try:
                await self._vlm_client.close()
            except Exception:
                logger.debug("vlm_client close", exc_info=True)
        try:
            await self._tm.close()
        except Exception:
            logger.debug("tm close", exc_info=True)
        self._api.log(f"{PLUGIN_ID} plugin unloaded")

    # ------------------------------------------------------------------
    # Tool dispatcher (LLM brain access)
    # ------------------------------------------------------------------

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "media_post_create":
            task = await self._create_task_internal(
                mode=str(args.get("mode", "cover_pick")),
                video_path=str(args.get("video_path", "")),
                params=dict(args.get("params") or {}),
                cost_approved=bool(args.get("cost_approved")),
            )
            return f"Task created: {task['id']} (mode={task['mode']}, status={task['status']})"
        if tool_name == "media_post_status":
            task = await self._tm.get_task(str(args.get("task_id", "")))
            if not task:
                return "Task not found"
            return (
                f"Task {task['id']}: status={task['status']}, mode={task['mode']}, "
                f"progress={task.get('progress', 0)}, step={task.get('pipeline_step') or 'N/A'}"
            )
        if tool_name == "media_post_list":
            limit = int(args.get("limit", 20))
            result = await self._tm.list_tasks(
                mode=args.get("mode"),
                status=args.get("status"),
                limit=limit,
            )
            lines = [f"Total: {result['total']}"]
            for t in result["tasks"][:limit]:
                lines.append(f"  {t['id']}: {t['mode']} / {t['status']}")
            return "\n".join(lines)
        if tool_name == "media_post_cancel":
            tid = str(args.get("task_id", ""))
            self._tm.request_cancel(tid)
            ctx = self._running.get(tid)
            if ctx:
                ctx.cancelled = True
            return f"Cancel requested for task {tid}"
        return f"Unknown tool: {tool_name}"

    # ------------------------------------------------------------------
    # Routes (22 total)
    # ------------------------------------------------------------------

    def _register_routes(self, router: APIRouter) -> None:  # noqa: PLR0915
        uploads_dir = self._data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        # Route #1: GET /uploads/{rel_path:path} (registered by helper).
        add_upload_preview_route(router, base_dir=uploads_dir)

        # Route #2: POST /upload — receive a video file.
        @router.post("/upload")
        async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
            safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename or 'video.mp4'}"
            dest = uploads_dir / safe_name
            with open(dest, "wb") as fh:
                while chunk := await file.read(1024 * 1024):
                    fh.write(chunk)
            url = build_preview_url(PLUGIN_ID, safe_name)
            return {
                "path": str(dest),
                "filename": safe_name,
                "url": url,
                "size": dest.stat().st_size,
            }

        # Route #3: GET /modes — 4-mode catalog.
        @router.get("/modes")
        async def list_modes() -> list[dict[str, Any]]:
            return [mode_to_dict(m) for m in MODES]

        # Route #4: GET /platforms — 5-platform catalog.
        @router.get("/platforms")
        async def list_platforms() -> list[dict[str, Any]]:
            return [platform_to_dict(p) for p in PLATFORMS]

        # Route #5: GET /aspects — 2-aspect catalog (multi_aspect mode).
        @router.get("/aspects")
        async def list_aspects() -> list[dict[str, Any]]:
            return [aspect_to_dict(a) for a in ASPECTS]

        # Route #6: GET /pricing — read-only price table.
        @router.get("/pricing")
        async def get_pricing() -> list[dict[str, Any]]:
            return [
                {"api": p.api, "unit": p.unit, "price_cny": p.price_cny}
                for p in PRICE_TABLE
            ]

        # Route #7: POST /estimate — pre-flight cost preview (no DB write).
        @router.post("/estimate")
        async def estimate(body: CostEstimateBody) -> dict[str, Any]:
            if body.mode not in ALLOWED_MODES:
                raise HTTPException(400, f"unknown mode: {body.mode}")
            preview = estimate_cost(
                body.mode,
                duration_sec=body.duration_sec,
                quantity=body.quantity,
                target_aspects=body.target_aspects,
                platforms=body.platforms,
                recompose_fps=body.recompose_fps,
                chapter_count=body.chapter_count,
            )
            return {
                "total_cny": preview.total_cny,
                "items": preview.items,
                "cost_kind": preview.cost_kind,
            }

        # Route #8: POST /tasks — create + spawn.
        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            return await self._create_task_internal(
                mode=body.mode,
                video_path=body.video_path,
                params=body.params,
                cost_approved=body.cost_approved,
            )

        # Route #9: GET /tasks — list with filters.
        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict[str, Any]:
            return await self._tm.list_tasks(
                status=status, mode=mode, offset=offset, limit=limit
            )

        # Route #10: GET /tasks/{task_id} — single task.
        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return task

        # Route #11: DELETE /tasks/{task_id}
        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, str]:
            if not await self._tm.delete_task(task_id):
                raise HTTPException(404, "Task not found")
            shutil.rmtree(self._task_dir(task_id), ignore_errors=True)
            return {"status": "deleted"}

        # Route #12: POST /tasks/{task_id}/cancel
        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, str]:
            self._tm.request_cancel(task_id)
            ctx = self._running.get(task_id)
            if ctx:
                ctx.cancelled = True
            return {"status": "cancel_requested"}

        # Route #13: POST /tasks/{task_id}/retry
        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] not in ("failed", "cancelled", "approval_required"):
                raise HTTPException(
                    400, "Can only retry failed / cancelled / approval_required tasks"
                )
            params = dict(task.get("params") or {})
            return await self._create_task_internal(
                mode=task["mode"],
                video_path=task.get("video_path", "") or "",
                params=params,
                cost_approved=True,
            )

        # Route #14: POST /tasks/{task_id}/approve — mark cost approved + restart.
        @router.post("/tasks/{task_id}/approve")
        async def approve_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] != "approval_required":
                raise HTTPException(400, "Task is not pending approval")
            params = dict(task.get("params") or {})
            params["cost_approved"] = True
            await self._tm.update_task(
                task_id,
                params=params,
                status="pending",
                error_kind=None,
                error_message=None,
            )
            await self._spawn_pipeline(task_id)
            return {"status": "started"}

        # Route #15: GET /tasks/{task_id}/results/cover
        @router.get("/tasks/{task_id}/results/cover")
        async def get_cover_results(task_id: str) -> list[dict[str, Any]]:
            return await self._tm.list_cover_results(task_id)

        # Route #16: GET /tasks/{task_id}/results/recompose
        @router.get("/tasks/{task_id}/results/recompose")
        async def get_recompose_results(task_id: str) -> list[dict[str, Any]]:
            return await self._tm.list_recompose_outputs(task_id)

        # Route #17: GET /tasks/{task_id}/results/seo
        @router.get("/tasks/{task_id}/results/seo")
        async def get_seo_results(task_id: str) -> list[dict[str, Any]]:
            return await self._tm.list_seo_results(task_id)

        # Route #18: GET /tasks/{task_id}/results/chapters
        @router.get("/tasks/{task_id}/results/chapters")
        async def get_chapter_results(task_id: str) -> list[dict[str, Any]]:
            return await self._tm.list_chapter_card_results(task_id)

        # Route #19: GET /settings
        @router.get("/settings")
        async def get_settings() -> dict[str, str]:
            return await self._tm.get_all_config()

        # Route #20: PUT /settings
        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict[str, str]:
            await self._tm.set_configs(body.updates)
            if "dashscope_api_key" in body.updates:
                key = body.updates["dashscope_api_key"]
                if key:
                    if self._vlm_client is None:
                        self._vlm_client = MediaPostVlmClient(key)
                    else:
                        self._vlm_client.update_api_key(key)
                else:
                    self._vlm_client = None
            return {"status": "ok"}

        # Route #21: GET /storage/stats — returns per-directory aggregates plus
        # the host-allocated data root so the Settings UI can render a card grid
        # similar to seedance-video and offer an "open folder" shortcut.
        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            named_roots = {
                "uploads_dir": self._data_dir / "uploads",
                "tasks_dir": self._data_dir / "tasks",
            }
            per_dir: dict[str, dict[str, Any]] = {}
            for key, root in named_roots.items():
                stats = await collect_storage_stats([root]) if root.exists() else None
                size_bytes = stats.total_bytes if stats else 0
                per_dir[key] = {
                    "path": str(root),
                    "exists": root.exists(),
                    "file_count": stats.total_files if stats else 0,
                    "size_bytes": size_bytes,
                    "size_mb": round(size_bytes / (1024 * 1024), 2),
                    "truncated": bool(stats.truncated) if stats else False,
                }
            total_files = sum(d["file_count"] for d in per_dir.values())
            total_bytes = sum(d["size_bytes"] for d in per_dir.values())
            return {
                "data_dir": str(self._data_dir),
                "per_dir": per_dir,
                "total_files": total_files,
                "total_bytes": total_bytes,
                "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            }

        # Route #22b: GET /playwright/probe — realtime detection so the Settings
        # UI can render an accurate "Playwright OK / drawtext fallback" pill
        # plus install hints, instead of trusting the cached config flag.
        @router.get("/playwright/probe")
        async def playwright_probe() -> dict[str, Any]:
            probe = await probe_playwright_runtime()
            available = bool(probe.get("import_ok") and probe.get("browser_ok"))
            return {
                "available": available,
                "import_ok": bool(probe.get("import_ok")),
                "browser_ok": bool(probe.get("browser_ok")),
                "render_path": "playwright" if available else "drawtext",
                "error": str(probe.get("error") or ""),
                "hint_install": "pip install playwright",
                "hint_browsers": "python -m playwright install chromium",
            }

        # Route #22: GET /errors — public 9-key error catalog (UI ErrorPanel).
        @router.get("/errors")
        async def get_errors() -> dict[str, Any]:
            return {
                "kinds": [
                    {
                        "kind": kind,
                        "label_zh": data.get("label_zh"),
                        "label_en": data.get("label_en"),
                        "color": data.get("color"),
                        "hints_zh": data.get("hints_zh", []),
                        "hints_en": data.get("hints_en", []),
                    }
                    for kind, data in ERROR_HINTS.items()
                ],
                "templates": list(builtin_template_ids()),
                "platforms": sorted(ALLOWED_PLATFORMS),
                "aspects": sorted(ALLOWED_ASPECTS),
            }

    # ------------------------------------------------------------------
    # Internal task lifecycle
    # ------------------------------------------------------------------

    def _task_dir(self, task_id: str) -> Path:
        return self._data_dir / "tasks" / task_id

    async def _create_task_internal(
        self,
        *,
        mode: str,
        video_path: str = "",
        params: dict[str, Any] | None = None,
        cost_approved: bool = False,
    ) -> dict[str, Any]:
        if mode not in ALLOWED_MODES:
            raise HTTPException(400, f"Unknown mode: {mode!r}")
        merged_params = dict(params or {})
        if cost_approved:
            merged_params["cost_approved"] = True

        # Best-effort duration probe so the cost preview is meaningful.
        if video_path and "duration_sec" not in merged_params:
            try:
                merged_params["duration_sec"] = await ffprobe_duration(Path(video_path))
            except Exception:
                merged_params["duration_sec"] = 0.0

        preview = estimate_cost(
            mode,
            duration_sec=float(merged_params.get("duration_sec", 0.0) or 0.0),
            quantity=int(merged_params.get("quantity", 8) or 8),
            target_aspects=list(merged_params.get("target_aspects") or []) or None,
            platforms=list(merged_params.get("platforms") or []) or None,
            recompose_fps=float(merged_params.get("recompose_fps", 2.0) or 2.0),
            chapter_count=len(merged_params.get("chapters") or []),
        )

        task = await self._tm.create_task(
            mode=mode,
            video_path=video_path,
            params=merged_params,
            cost_estimated=preview.total_cny,
            cost_kind=preview.cost_kind,
            status="pending",
        )
        await self._spawn_pipeline(task["id"])
        return task

    async def _spawn_pipeline(self, task_id: str) -> None:
        task = await self._tm.get_task(task_id)
        if task is None:
            return
        params = dict(task.get("params") or {})
        video_path = task.get("video_path") or ""
        ctx = MediaPostContext(
            task_id=task_id,
            mode=task["mode"],
            params=params,
            task_dir=self._task_dir(task_id),
            api=self._api,
            tm=self._tm,
            vlm_client=self._vlm_client,
        )
        if video_path:
            ctx.video_path = Path(video_path)
        meta = task.get("video_meta")
        if isinstance(meta, dict):
            ctx.video_meta = meta

        self._running[task_id] = ctx

        async def _run_and_cleanup() -> None:
            try:
                await run_pipeline(ctx)
            finally:
                self._running.pop(task_id, None)
                self._task_handles.pop(task_id, None)
                self._tm.clear_cancel(task_id)

        handle = self._api.spawn_task(
            _run_and_cleanup(), name=f"{PLUGIN_ID}:task:{task_id}"
        )
        self._task_handles[task_id] = handle


__all__ = ["PLUGIN_ID", "Plugin"]
