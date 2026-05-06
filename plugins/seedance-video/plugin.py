"""Seedance Video Generator — full-stack plugin for AI video generation.

Backend entry point providing all REST API endpoints for the frontend UI.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

from ark_client import ArkClient
from fastapi import APIRouter, File, HTTPException, UploadFile
from long_video import (
    ChainGenerator,
    concat_videos,
    decompose_storyboard,
    ffmpeg_available,
)
from models import (
    RESOLUTION_PIXEL_MAP,
    SEEDANCE_MODELS,
    get_model,
    model_to_dict,
)
from prompt_optimizer import (
    ATMOSPHERE_KEYWORDS,
    CAMERA_KEYWORDS,
    MODE_FORMULAS,
    PROMPT_TEMPLATES,
    PromptOptimizeError,
    optimize_prompt,
)
from pydantic import BaseModel, Field
from seedance_inline.storage_stats import collect_storage_stats
from seedance_inline.system_deps import SystemDepsManager
from seedance_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from seedance_inline.vendor_client import VendorError
from task_manager import TaskManager

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


# ── Request / Response models ──

class CreateTaskBody(BaseModel):
    prompt: str = ""
    mode: str = "t2v"
    model: str = "2.0"
    ratio: str = "16:9"
    duration: int = 5
    resolution: str = "720p"
    n: int = 1
    generate_audio: bool = True
    seed: int = -1
    watermark: bool = False
    camera_fixed: bool = False
    return_last_frame: bool = False
    web_search: bool = False
    service_tier: str = "default"
    callback_url: str | None = None
    execution_expires_after: int | None = None
    content: list[dict] | None = None

class DraftConfirmBody(BaseModel):
    resolution: str = "720p"
    watermark: bool = False
    return_last_frame: bool = False

class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]

class PromptOptimizeBody(BaseModel):
    prompt: str
    mode: str = "t2v"
    duration: int = 5
    ratio: str = "16:9"
    asset_summary: str = "无"
    level: str = "professional"


class StoryboardDecomposeBody(BaseModel):
    story: str
    total_duration: int = 60
    segment_duration: int = 10
    ratio: str = "16:9"
    style: str = "电影级画质"


class LongVideoCreateBody(BaseModel):
    segments: list[dict] = Field(default_factory=list)
    model: str = "2.0"
    ratio: str = "16:9"
    resolution: str = "720p"
    mode: str = "serial"
    transition: str = "none"
    fade_duration: float = 0.5


class ConcatBody(BaseModel):
    task_ids: list[str]
    transition: str = "none"
    fade_duration: float = 0.5
    output_name: str = ""


class SystemInstallBody(BaseModel):
    method_index: int = 0


class SystemUninstallBody(BaseModel):
    method_index: int = 0


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._tm = TaskManager(data_dir / "seedance.db")
        self._ark: ArkClient | None = None
        self._poll_task: asyncio.Task | None = None
        self._brain = None
        # In-plugin replacement for the retired SDK 0.6.x DependencyGate —
        # see seedance_inline/system_deps.py module docstring for rationale.
        self._sysdeps = SystemDepsManager()

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "seedance_create",
                "description": "Create a Seedance video generation task",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Video generation prompt"},
                        "mode": {"type": "string", "enum": ["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"]},
                        "duration": {"type": "integer", "default": 5},
                        "ratio": {"type": "string", "default": "16:9"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "seedance_status",
                "description": "Check status of a Seedance video generation task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "seedance_list",
                "description": "List recent Seedance video generation tasks",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
        ], handler=self._handle_tool)

        api.spawn_task(self._async_init(), name="seedance-video:init")
        api.log("Seedance Video plugin loaded")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("ark_api_key")
        if api_key:
            self._ark = ArkClient(api_key)
        self._start_polling()

    async def on_unload(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("seedance-video poll task drain error: %s", exc)
        if self._ark is not None:
            try:
                await self._ark.close()
            except Exception as exc:
                logger.warning("seedance-video Ark client close error: %s", exc)
        try:
            await self._sysdeps.aclose()
        except Exception as exc:
            logger.warning("seedance-video system deps close error: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("seedance-video task manager close error: %s", exc)

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        if tool_name == "seedance_create":
            task = await self._create_task_internal(args)
            return f"Task created: {task['id']} (status: {task['status']})"
        elif tool_name == "seedance_status":
            task = await self._tm.get_task(args["task_id"])
            if not task:
                return f"Task {args['task_id']} not found"
            return f"Task {task['id']}: status={task['status']}, video_url={task.get('video_url', 'N/A')}"
        elif tool_name == "seedance_list":
            tasks, total = await self._tm.list_tasks(limit=args.get("limit", 10))
            lines = [f"Total: {total} tasks"]
            for t in tasks:
                lines.append(f"  {t['id']}: {t['status']} - {t['prompt'][:50]}")
            return "\n".join(lines)
        return f"Unknown tool: {tool_name}"

    # ── Internal task creation ──

    async def _create_task_internal(self, params: dict) -> dict:
        if not self._ark:
            raise HTTPException(status_code=400, detail="API Key not configured")

        model_info = get_model(params.get("model", "2.0"))
        if not model_info:
            raise HTTPException(status_code=400, detail=f"Unknown model: {params.get('model')}")

        content = params.get("content") or [{"type": "text", "text": params.get("prompt", "")}]

        config = await self._tm.get_all_config()
        service_tier = params.get("service_tier", config.get("service_tier_default", "default"))
        callback_url = params.get("callback_url") or config.get("callback_url") or None
        expires = params.get("execution_expires_after")
        if service_tier == "flex" and not expires:
            expires = 172800

        try:
            result = await self._ark.create_task(
                model=model_info.model_id,
                content=content,
                ratio=params.get("ratio", "16:9"),
                duration=params.get("duration", 5),
                resolution=params.get("resolution", "720p"),
                n=params.get("n", 1),
                generate_audio=params.get("generate_audio", True),
                seed=params.get("seed", -1),
                watermark=params.get("watermark", False),
                camera_fixed=params.get("camera_fixed", False),
                draft=params.get("draft", False),
                return_last_frame=params.get("return_last_frame", False),
                tools=[{"type": "web_search"}] if params.get("web_search") else None,
                service_tier=service_tier,
                callback_url=callback_url,
                execution_expires_after=expires,
            )
        except VendorError as e:
            logger.error("Ark API error: %s (kind=%s)", e, e.kind)
            raise HTTPException(status_code=502, detail=f"Ark API error: {e}")
        except Exception as e:
            logger.error("Ark API unexpected error: %s", e)
            raise HTTPException(status_code=502, detail=f"Ark API error: {e}")

        ark_task_id = result.get("id", "")
        task = await self._tm.create_task(
            ark_task_id=ark_task_id,
            status="running",
            prompt=params.get("prompt", ""),
            mode=params.get("mode", "t2v"),
            model=params.get("model", "2.0"),
            params=params,
            service_tier=service_tier,
            is_draft=params.get("draft", False),
            callback_url=callback_url,
        )
        return task

    # ── Polling ──

    def _start_polling(self) -> None:
        self._poll_task = self._api.spawn_task(
            self._poll_loop(), name="seedance-video:poll"
        )

    async def _poll_loop(self) -> None:
        while True:
            try:
                interval = int(await self._tm.get_config("poll_interval") or "15")
                await asyncio.sleep(max(interval, 5))
                await self._poll_running_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Poll error: %s", e)
                await asyncio.sleep(15)

    async def _poll_running_tasks(self) -> None:
        if not self._ark:
            return
        tasks = await self._tm.get_running_tasks()
        for task in tasks:
            if not task.get("ark_task_id"):
                continue
            try:
                result = await self._ark.get_task(task["ark_task_id"])
                status = result.get("status", "")
                if status == "succeeded":
                    video_url = ""
                    revised_prompt = ""
                    last_frame_url = ""

                    content = result.get("content", {})
                    if isinstance(content, dict):
                        video_url = content.get("video_url", "") or ""
                        last_frame_url = (
                            content.get("last_frame_url", "")
                            or content.get("image_url", "")
                            or ""
                        )
                        revised_prompt = content.get("revised_prompt", "") or ""

                    if not video_url:
                        output = result.get("output", {})
                        if isinstance(output, dict):
                            content_list = output.get("content", [])
                            if isinstance(content_list, list):
                                for item in content_list:
                                    if isinstance(item, dict) and item.get("type") == "video_url":
                                        video_url = item.get("video_url", {}).get("url", "")
                                    if isinstance(item, dict) and item.get("type") == "image_url":
                                        if not last_frame_url:
                                            last_frame_url = item.get("image_url", {}).get("url", "")
                            if not revised_prompt:
                                revised_prompt = output.get("revised_prompt", "")
                            if not last_frame_url:
                                last_frame_url = output.get("last_frame_url", "") or ""

                    updates: dict[str, Any] = {"status": "succeeded", "video_url": video_url}
                    if revised_prompt:
                        updates["revised_prompt"] = revised_prompt
                    if last_frame_url:
                        updates["last_frame_url"] = last_frame_url
                    await self._tm.update_task(task["id"], **updates)

                    auto_dl = await self._tm.get_config("auto_download")
                    if auto_dl == "true" and video_url:
                        self._api.spawn_task(
                            self._download_video(task["id"], video_url),
                            name=f"seedance-video:dl:{task['id']}",
                        )

                    self._broadcast_update(task["id"], "succeeded")

                elif status == "failed":
                    error = result.get("error", {})
                    error_msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
                    await self._tm.update_task(task["id"], status="failed", error_message=error_msg)
                    self._broadcast_update(task["id"], "failed")

            except Exception as e:
                logger.debug("Poll task %s error: %s", task["id"], e)

    async def _download_video(self, task_id: str, url: str) -> None:
        """Download video to local output directory."""
        try:
            import httpx
            config = await self._tm.get_all_config()
            output_dir = config.get("output_dir") or str(Path.home() / "seedance-output")
            subdir_mode = config.get("output_subdir_mode", "date")
            naming = config.get("output_naming_rule", "{date}_{prompt_prefix}")

            out_path = Path(output_dir)
            if subdir_mode == "date":
                import datetime
                out_path = out_path / datetime.date.today().isoformat()
            out_path.mkdir(parents=True, exist_ok=True)

            task = await self._tm.get_task(task_id)
            if not task:
                return

            prompt_prefix = (task.get("prompt", "")[:20] or "video").strip()
            safe_prefix = "".join(c if c.isalnum() or c in "-_ " else "_" for c in prompt_prefix)
            filename = naming.format(
                task_id=task_id,
                date=time.strftime("%Y%m%d"),
                prompt_prefix=safe_prefix,
                mode=task.get("mode", "t2v"),
                seq=task_id[:6],
            ) + ".mp4"

            filepath = out_path / filename
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                filepath.write_bytes(resp.content)

            await self._tm.update_task(task_id, local_video_path=str(filepath))
            logger.info("Downloaded video for task %s to %s", task_id, filepath)
        except Exception as e:
            logger.warning("Failed to download video for task %s: %s", task_id, e)

    def _broadcast_update(self, task_id: str, status: str) -> None:
        try:
            self._api.broadcast_ui_event(
                "task_update", {"task_id": task_id, "status": status}
            )
        except Exception as exc:
            # Don't let UI broadcast failures bring down the polling loop, but
            # log them so an operator can spot a stuck WebSocket / event bus.
            logger.warning("broadcast_ui_event failed for %s: %s", task_id, exc)

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:

        # Sprint 7 / C1 (issue #479) — register a safe GET /uploads/{rel_path:path}
        # so the UI can preview uploaded reference images via
        # <img src="/api/plugins/seedance-video/uploads/<file>"> after upload.
        # base_dir is plugin-owned data so the URL stays stable even if the
        # user later changes assets_dir in /settings.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        # --- Tasks CRUD ---

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict:
            task = await self._create_task_internal(body.model_dump())
            return {"ok": True, "task": task}

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            is_draft: bool | None = None,
            service_tier: str | None = None,
            offset: int = 0,
            limit: int = 20,
        ) -> dict:
            tasks, total = await self._tm.list_tasks(
                status=status, is_draft=is_draft, service_tier=service_tier,
                offset=offset, limit=limit,
            )
            return {"ok": True, "tasks": tasks, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"ok": True, "task": task}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict:
            await self._tm.delete_task(task_id)
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            new_task = await self._create_task_internal(task.get("params", {}))
            return {"ok": True, "task": new_task}

        # --- Draft mode ---

        @router.post("/tasks/draft")
        async def create_draft(body: CreateTaskBody) -> dict:
            params = body.model_dump()
            params["draft"] = True
            task = await self._create_task_internal(params)
            return {"ok": True, "task": task}

        @router.post("/tasks/draft/{draft_task_id}/confirm")
        async def confirm_draft(draft_task_id: str, body: DraftConfirmBody) -> dict:
            draft = await self._tm.get_task(draft_task_id)
            if not draft:
                raise HTTPException(status_code=404, detail="Draft task not found")
            if draft["status"] != "succeeded":
                raise HTTPException(status_code=400, detail="Draft not yet completed")
            if not self._ark:
                raise HTTPException(status_code=400, detail="API Key not configured")

            model_info = get_model(draft["model"])
            if not model_info:
                raise HTTPException(status_code=400, detail="Unknown model")

            content = [{"type": "draft_task", "draft_task": {"id": draft["ark_task_id"]}}]
            result = await self._ark.create_task(
                model=model_info.model_id,
                content=content,
                resolution=body.resolution,
                watermark=body.watermark,
                return_last_frame=body.return_last_frame,
                ratio=draft["params"].get("ratio", "16:9"),
                duration=draft["params"].get("duration", 5),
            )
            task = await self._tm.create_task(
                ark_task_id=result.get("id", ""),
                status="running",
                prompt=draft["prompt"],
                mode=draft["mode"],
                model=draft["model"],
                params={**draft["params"], "draft_parent_id": draft_task_id},
                draft_parent_id=draft_task_id,
            )
            return {"ok": True, "task": task}

        # --- File operations ---

        @router.post("/upload")
        async def upload_file(file: UploadFile = File(...)) -> dict:
            # Sprint 7 / C1 — uploads now land in plugin data dir so the
            # preview GET route above can serve them back without exposing
            # the user's home directory.  Old files in legacy assets_dir
            # remain accessible by absolute path (asset table stores it).
            uploads_dir = self._api.get_data_dir() / "uploads"
            content = await file.read()
            ext = Path(file.filename or "file").suffix.lower()

            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif"):
                subdir = "images"
                atype = "image"
            elif ext in (".mp4", ".mov"):
                subdir = "videos"
                atype = "video"
            elif ext in (".wav", ".mp3"):
                subdir = "audios"
                atype = "audio"
            else:
                subdir = "other"
                atype = "file"

            dest_dir = uploads_dir / subdir
            dest_dir.mkdir(parents=True, exist_ok=True)

            import uuid as _uuid
            filename = f"{_uuid.uuid4().hex[:8]}_{file.filename or 'file'}"
            filepath = dest_dir / filename
            filepath.write_bytes(content)
            rel_path = f"{subdir}/{filename}"

            b64 = base64.b64encode(content).decode("ascii")
            asset = await self._tm.create_asset(
                type=atype,
                file_path=str(filepath),
                original_name=file.filename,
                size_bytes=len(content),
            )
            return {
                "ok": True,
                "asset": asset,
                "url": build_preview_url("seedance-video", rel_path),
                "base64": (
                    f"data:{file.content_type};base64,{b64}"
                    if len(content) < 10_000_000
                    else None
                ),
            }

        @router.get("/videos/{task_id}")
        async def proxy_video(task_id: str, download: int = 0):
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            local_path = task.get("local_video_path")
            video_url = task.get("video_url")
            source = local_path if (local_path and Path(local_path).is_file()) else video_url
            if not source:
                raise HTTPException(status_code=404, detail="No video available")

            prompt_prefix = (task.get("prompt", "") or "video")[:30].strip() or "video"
            safe_prefix = "".join(
                c if c.isalnum() or c in "-_ " else "_" for c in prompt_prefix
            ).strip(" .") or "video"
            fname = f"seedance_{safe_prefix}.mp4"

            return self._api.create_file_response(
                source,
                filename=fname,
                media_type="video/mp4",
                as_download=bool(download),
            )

        @router.get("/videos/{task_id}/download")
        async def download_video(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            video_url = task.get("video_url")
            if not video_url:
                raise HTTPException(status_code=404, detail="No video available")
            await self._download_video(task_id, video_url)
            updated = await self._tm.get_task(task_id)
            return {"ok": True, "task": updated}

        # --- Config (use /settings to avoid collision with generic /config in routes/plugins.py) ---

        @router.get("/settings")
        async def get_settings() -> dict:
            cfg = await self._tm.get_all_config()
            cfg.setdefault("ark_api_key", "")
            return {"ok": True, "config": cfg}

        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict:
            await self._tm.set_configs(body.updates)
            if "ark_api_key" in body.updates and body.updates["ark_api_key"]:
                key = body.updates["ark_api_key"]
                if self._ark:
                    self._ark.update_api_key(key)
                else:
                    self._ark = ArkClient(key)
            saved = await self._tm.get_all_config()
            return {"ok": True, "config": saved}

        @router.get("/models")
        async def list_models() -> dict:
            return {
                "ok": True,
                "models": [model_to_dict(m) for m in SEEDANCE_MODELS],
            }

        @router.get("/models/{model_id}/capabilities")
        async def model_capabilities(model_id: str) -> dict:
            m = get_model(model_id)
            if not m:
                raise HTTPException(status_code=404, detail="Model not found")
            return {"ok": True, "model": model_to_dict(m)}

        @router.get("/resolution-map")
        async def resolution_map() -> dict:
            return {"ok": True, "map": RESOLUTION_PIXEL_MAP}

        # --- Prompt ---

        @router.get("/prompt-guide")
        async def get_prompt_guide() -> dict:
            return {
                "ok": True,
                "cameras": CAMERA_KEYWORDS,
                "atmosphere": ATMOSPHERE_KEYWORDS,
                "formulas": MODE_FORMULAS,
            }

        @router.get("/prompt-templates")
        async def get_prompt_templates() -> dict:
            return {"ok": True, "templates": PROMPT_TEMPLATES}

        @router.get("/prompt-formulas")
        async def get_prompt_formulas(mode: str = "t2v") -> dict:
            formula = MODE_FORMULAS.get(mode, MODE_FORMULAS["t2v"])
            return {"ok": True, "mode": mode, "formula": formula}

        @router.post("/prompt-optimize")
        async def optimize_prompt_endpoint(body: PromptOptimizeBody) -> dict:
            # Distinguish "permission not granted" (fixable in-app via the
            # /permissions/grant button) from "host has no brain configured"
            # (needs the user to set up an LLM in main settings). Both look
            # identical via get_brain()==None but have very different fixes.
            if not self._api.has_permission("brain.access"):
                return {
                    "ok": False,
                    "error": "missing_permission",
                    "permission": "brain.access",
                    "message": (
                        "AI 优化未授权：插件缺少 brain.access 权限。"
                        "请到「设置 → 系统组件 → 权限」点「一键授予」，"
                        "或到「设置中心 → 插件管理 → 即梦工作室 → 权限」勾选保存。"
                    ),
                }
            brain = self._api.get_brain()
            if not brain:
                return {
                    "ok": False,
                    "error": "brain_unavailable",
                    "message": "LLM 不可用：主进程未注入 brain（请确认 OpenAkita 已正常配置 LLM）。",
                }
            try:
                result = await optimize_prompt(
                    brain=brain,
                    user_prompt=body.prompt,
                    mode=body.mode,
                    duration=body.duration,
                    ratio=body.ratio,
                    asset_summary=body.asset_summary,
                    level=body.level,
                )
                return {"ok": True, "result": result}
            except PromptOptimizeError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:
                logger.error("Prompt optimize endpoint error: %s", e)
                return {"ok": False, "error": f"优化失败: {e}"}

        # --- Assets ---

        @router.get("/assets")
        async def list_assets(
            type: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict:
            assets, total = await self._tm.list_assets(
                asset_type=type, offset=offset, limit=limit
            )
            return {"ok": True, "assets": assets, "total": total}

        @router.delete("/assets/{asset_id}")
        async def delete_asset(asset_id: str) -> dict:
            asset = await self._tm.get_asset(asset_id)
            if asset:
                fpath = Path(asset.get("file_path", ""))
                if fpath.is_file():
                    fpath.unlink(missing_ok=True)
            await self._tm.delete_asset(asset_id)
            return {"ok": True}

        # --- Webhook callback ---

        @router.post("/webhook/callback")
        async def webhook_callback(body: dict) -> dict:
            task_id = body.get("id", "")
            status = body.get("status", "")
            if task_id and status:
                tasks, _ = await self._tm.list_tasks()
                for t in tasks:
                    if t.get("ark_task_id") == task_id:
                        if status == "succeeded":
                            await self._poll_running_tasks()
                        elif status == "failed":
                            await self._tm.update_task(
                                t["id"], status="failed",
                                error_message=body.get("error", {}).get("message", ""),
                            )
                        self._broadcast_update(t["id"], status)
                        break
            return {"ok": True}

        # --- Storage management ---

        @router.get("/storage/stats")
        async def storage_stats() -> dict:
            # Sprint 7 / C4 — switched to SDK collect_storage_stats so the
            # walk runs off-loop and is hard-capped at max_files (avoids UI
            # stalls when users accumulate thousands of generated videos).
            config = await self._tm.get_all_config()
            stats: dict[str, dict] = {}
            truncated_any = False
            for key, default in [
                ("output_dir", str(Path.home() / "seedance-output")),
                ("assets_dir", str(Path.home() / "seedance-assets")),
                ("cache_dir", str(self._api.get_data_dir() / "cache")),
                ("uploads", str(self._api.get_data_dir() / "uploads")),
            ]:
                d = Path(config.get(key) or default)
                report = await collect_storage_stats(
                    d, max_files=20000, sample_paths=0, skip_hidden=True,
                )
                truncated_any = truncated_any or report.truncated
                stats[key] = {
                    "path": str(d),
                    "size_bytes": report.total_bytes,
                    "size_mb": round(report.total_bytes / 1048576, 1),
                    "file_count": report.total_files,
                    "truncated": report.truncated,
                }
            return {"ok": True, "stats": stats, "truncated": truncated_any}

        @router.post("/storage/cleanup")
        async def storage_cleanup(dir_type: str = "cache") -> dict:
            config = await self._tm.get_all_config()
            if dir_type == "cache":
                d = Path(config.get("cache_dir") or str(self._api.get_data_dir() / "cache"))
            elif dir_type == "assets":
                d = Path(config.get("assets_dir") or str(Path.home() / "seedance-assets"))
            else:
                raise HTTPException(status_code=400, detail="Invalid dir_type")

            removed = 0
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        removed += 1
            return {"ok": True, "removed": removed}

        @router.post("/storage/open-folder")
        async def open_folder(body: dict) -> dict:
            # Resolve target path:
            #   1) explicit `path` (after ~ expansion), OR
            #   2) `key` ∈ {output_dir, assets_dir, cache_dir, uploads}
            #      → user config value, else built-in default (mirrors
            #      /storage/stats so "Open" works even before the user
            #      customizes anything).
            raw_path = (body.get("path") or "").strip()
            key = (body.get("key") or "").strip()

            if not raw_path and not key:
                raise HTTPException(status_code=400, detail="Missing path or key")

            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                defaults = {
                    "output_dir": Path.home() / "seedance-output",
                    "assets_dir": Path.home() / "seedance-assets",
                    "cache_dir": self._api.get_data_dir() / "cache",
                    "uploads": self._api.get_data_dir() / "uploads",
                }
                if key not in defaults:
                    raise HTTPException(status_code=400, detail=f"Unknown key: {key}")
                config = await self._tm.get_all_config()
                cfg_val = (config.get(key) or "").strip()
                target = (Path(cfg_val).expanduser() if cfg_val else defaults[key])

            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Cannot create folder: {exc}",
                ) from exc

            import subprocess
            import sys
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except (OSError, FileNotFoundError) as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Cannot open folder: {exc}",
                ) from exc
            return {"ok": True, "path": str(target)}

        # --- In-plugin folder picker (works regardless of Tauri/bridge) ---
        # Backs the FolderPickerModal in the Settings page, so users can
        # navigate the local filesystem without depending on the host's
        # native dialog (which has been unreliable across Tauri versions
        # and capability config).

        @router.get("/storage/list-dir")
        async def list_dir(path: str = "") -> dict:
            import sys
            raw = (path or "").strip()
            # Empty path → return anchor list (Home, common subfolders,
            # and on Windows every available drive letter). The UI can
            # render this as a "starting point" picker.
            if not raw:
                anchors: list[dict] = []
                home = Path.home()
                anchors.append({
                    "name": "Home", "path": str(home), "is_dir": True, "kind": "home",
                })
                for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Movies"):
                    p = home / sub
                    if p.is_dir():
                        anchors.append({
                            "name": sub, "path": str(p), "is_dir": True, "kind": "shortcut",
                        })
                if sys.platform == "win32":
                    import string
                    for letter in string.ascii_uppercase:
                        drv = Path(f"{letter}:/")
                        try:
                            if drv.exists():
                                anchors.append({
                                    "name": f"{letter}:",
                                    "path": str(drv),
                                    "is_dir": True,
                                    "kind": "drive",
                                })
                        except OSError:
                            continue
                else:
                    anchors.append({
                        "name": "/", "path": "/", "is_dir": True, "kind": "drive",
                    })
                return {
                    "ok": True, "path": "", "parent": None,
                    "items": anchors, "is_anchor": True,
                }

            try:
                target = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            items: list[dict] = []
            try:
                for entry in target.iterdir():
                    name = entry.name
                    # Skip hidden entries (Unix dotfiles, Windows hidden via
                    # name-prefix heuristic). Permission errors on individual
                    # entries are swallowed so one bad child does not blank
                    # the whole listing.
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            items.append({"name": name, "path": str(entry), "is_dir": True})
                    except (PermissionError, OSError):
                        continue
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            items.sort(key=lambda it: it["name"].lower())
            parent_path = str(target.parent) if target.parent != target else None
            return {
                "ok": True, "path": str(target), "parent": parent_path,
                "items": items, "is_anchor": False,
            }

        @router.post("/storage/mkdir")
        async def make_dir(body: dict) -> dict:
            parent = (body.get("parent") or "").strip()
            name = (body.get("name") or "").strip()
            if not parent or not name:
                raise HTTPException(status_code=400, detail="Missing parent or name")
            # Reject anything that could escape the parent dir.
            if "/" in name or "\\" in name or name in (".", ".."):
                raise HTTPException(status_code=400, detail="Invalid folder name")
            try:
                parent_path = Path(parent).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not parent_path.is_dir():
                raise HTTPException(status_code=400, detail="Parent is not a directory")
            new_path = parent_path / name
            try:
                new_path.mkdir(parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail="Folder already exists") from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"ok": True, "path": str(new_path)}

        # --- Long video / storyboard ---

        @router.get("/long-video/ffmpeg-check")
        async def check_ffmpeg() -> dict:
            # Route through SystemDepsManager so this endpoint and the
            # Settings page agree on detection state. Falls back to the
            # legacy shutil.which check if for any reason the manager is
            # not yet initialised (defensive — should not happen in prod).
            try:
                snap = self._sysdeps.detect("ffmpeg")
                return {"ok": True, "available": bool(snap.get("found"))}
            except Exception:
                return {"ok": True, "available": ffmpeg_available()}

        # --- System components (in-plugin FFmpeg installer) ---

        @router.get("/system/components")
        async def system_components() -> dict:
            # Snapshot of every system dep this plugin manages (currently
            # only ffmpeg) — drives the Settings > 系统组件 panel in the UI.
            return {"ok": True, "items": self._sysdeps.list_components()}

        @router.post("/system/{dep_id}/install")
        async def system_install(dep_id: str, body: SystemInstallBody) -> dict:
            try:
                result = await self._sysdeps.start_install(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        @router.post("/system/{dep_id}/uninstall")
        async def system_uninstall(dep_id: str, body: SystemUninstallBody) -> dict:
            try:
                result = await self._sysdeps.start_uninstall(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        @router.get("/system/{dep_id}/status")
        async def system_status(dep_id: str) -> dict:
            try:
                return self._sysdeps.status(dep_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        # --- Self-service permission check ---
        #
        # Surfaces the gap between manifest.permissions (what the plugin
        # NEEDS) and the runtime granted set (what the host actually gave
        # us). The frontend uses this to render an in-app "Grant" banner
        # so first-time users are not silently broken because they never
        # opened the host's plugin-manager permission dialog.

        @router.get("/permissions/check")
        async def permissions_check() -> dict:
            # Hard-coded list of permissions this plugin's user-facing
            # features actually exercise. Keep in sync with plugin.json.
            required = [
                ("brain.access",   "AI 优化提示词 / 故事板分镜（需要主进程 LLM）"),
                ("routes.register", "插件 HTTP 接口（前端调用）"),
            ]
            checks = [
                {
                    "permission": p,
                    "feature": label,
                    "granted": bool(self._api.has_permission(p)),
                }
                for p, label in required
            ]
            missing = [c["permission"] for c in checks if not c["granted"]]
            return {
                "ok": True,
                "all_granted": not missing,
                "missing": missing,
                "checks": checks,
            }

        @router.post("/long-video/storyboard")
        async def decompose_storyboard_ep(body: StoryboardDecomposeBody) -> dict:
            brain = self._api.get_brain()
            if not brain:
                return {"ok": False, "error": "LLM not available"}
            result = await decompose_storyboard(
                brain=brain,
                story=body.story,
                total_duration=body.total_duration,
                segment_duration=body.segment_duration,
                ratio=body.ratio,
                style=body.style,
            )
            if "error" in result:
                return {"ok": False, "error": result["error"], "raw": result.get("raw", "")}
            return {"ok": True, "storyboard": result}

        @router.post("/long-video/generate")
        async def generate_long_video(body: LongVideoCreateBody) -> dict:
            if not self._ark:
                raise HTTPException(status_code=400, detail="API Key not configured")
            chain = ChainGenerator(self._ark, self._tm)
            results = await chain.generate_chain(
                segments=body.segments,
                model_id=get_model(body.model).model_id if get_model(body.model) else body.model,
                ratio=body.ratio,
                resolution=body.resolution,
                mode=body.mode,
            )
            return {"ok": True, "results": results}

        @router.post("/long-video/concat")
        async def concat_task_videos(body: ConcatBody) -> dict:
            if not ffmpeg_available():
                raise HTTPException(
                    status_code=400,
                    detail="ffmpeg not installed — please install ffmpeg first",
                )

            video_paths: list[str] = []
            for tid in body.task_ids:
                task = await self._tm.get_task(tid)
                if not task:
                    raise HTTPException(status_code=404, detail=f"Task {tid} not found")
                local = task.get("local_video_path")
                if not local or not Path(local).is_file():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Task {tid} has no local video — download first",
                    )
                video_paths.append(local)

            config = await self._tm.get_all_config()
            output_dir = Path(config.get("output_dir") or str(Path.home() / "seedance-output"))
            output_dir.mkdir(parents=True, exist_ok=True)

            name = body.output_name or f"concat_{time.strftime('%Y%m%d_%H%M%S')}"
            if not name.endswith(".mp4"):
                name += ".mp4"
            output_path = str(output_dir / name)

            try:
                ok = await concat_videos(
                    video_paths, output_path,
                    transition=body.transition,
                    fade_duration=body.fade_duration,
                )
            except Exception as exc:
                logger.exception("ffmpeg concat raised")
                raise HTTPException(
                    status_code=500, detail=f"ffmpeg concat error: {exc}"
                ) from exc
            if not ok:
                raise HTTPException(status_code=500, detail="ffmpeg concat failed")

            return {"ok": True, "output_path": output_path}

        @router.get("/long-video/tasks/{group_id}")
        async def get_chain_tasks(group_id: str) -> dict:
            """List all segment tasks belonging to a chain generation group."""
            tasks, _ = await self._tm.list_tasks(limit=100)
            chain = [
                t for t in tasks
                if t.get("params", {}).get("chain_group") == group_id
            ]
            chain.sort(key=lambda t: t.get("params", {}).get("segment_index", 0))
            return {"ok": True, "tasks": chain}
