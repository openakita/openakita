"""Tongyi Image Generator — full-stack plugin for AI image generation.

Backend entry point providing REST API endpoints for the frontend UI.
Supports text-to-image, image editing, style repaint, background generation,
outpainting, and sketch-to-image via DashScope APIs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from tongyi_inline.storage_stats import collect_storage_stats
from tongyi_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)

from tongyi_dashscope_client import DashScopeClient, DashScopeError
from tongyi_models import (
    IMAGE_MODELS, MODELS_BY_ID, MODELS_BY_CATEGORY,
    STYLE_REPAINT_PRESETS, SKETCH_STYLES, RECOMMENDED_SIZES,
    ECOMMERCE_SCENE_PRESETS,
    get_model, get_models_for_category, model_to_dict,
)
from tongyi_prompt_optimizer import (
    optimize_prompt, PromptOptimizeError, get_prompt_guide_data,
    PROMPT_TEMPLATES, generate_ecommerce_prompts,
)
from tongyi_task_manager import TaskManager

logger = logging.getLogger(__name__)


def _safe_log(data: dict, max_len: int = 500) -> str:
    """Truncate dict repr for safe logging."""
    import json as _json
    try:
        s = _json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        s = repr(data)
    return s[:max_len] + "..." if len(s) > max_len else s


# ── Request models ──

class CreateTaskBody(BaseModel):
    mode: str = "text2img"
    prompt: str = ""
    negative_prompt: str = ""
    model: str = ""
    size: str = ""
    n: int = 1
    watermark: bool = False
    seed: int | None = None
    prompt_extend: bool | None = None
    thinking_mode: bool | None = None
    enable_sequential: bool | None = None
    color_palette: list[dict] | None = None
    bbox_list: list | None = None
    images: list[str] | None = None
    edit_instruction: str = ""
    style_index: int = 0
    style_ref_url: str | None = None
    ref_prompt: str | None = None
    ref_image_url: str | None = None
    noise_level: int = 300
    ref_prompt_weight: float = 0.5
    output_ratio: str | None = None
    x_scale: float | None = None
    y_scale: float | None = None
    angle: int = 0
    left_offset: int | None = None
    right_offset: int | None = None
    top_offset: int | None = None
    bottom_offset: int | None = None
    sketch_weight: int = 3
    sketch_style: str = "<watercolor>"
    # ecommerce suite
    ecommerce_scenes: list[str] | None = None
    product_name: str = ""


class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]


class PromptOptimizeBody(BaseModel):
    prompt: str
    model: str = "wan27-pro"
    size: str = "2K"
    style: str = ""
    level: str = "professional"


class EcommerceSuiteBody(BaseModel):
    product_name: str = ""
    prompt: str = ""
    images: list[str] | None = None
    scenes: list[str] | None = None
    model: str = ""
    size: str = ""


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._tm = TaskManager(data_dir / "tongyi_image.db")
        self._client: DashScopeClient | None = None
        self._poll_task: asyncio.Task | None = None

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "tongyi_image_create",
                "description": "Create a Tongyi image generation task (text-to-image, editing, style repaint, etc.)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Image generation prompt"},
                        "model": {"type": "string", "description": "Model ID (e.g. wan27-pro, qwen-pro)"},
                        "size": {"type": "string", "description": "Image size (e.g. 2K, 1024*1024)"},
                        "negative_prompt": {"type": "string", "description": "Negative prompt"},
                        "n": {"type": "integer", "default": 1, "description": "Number of images"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "tongyi_image_status",
                "description": "Check status of a Tongyi image generation task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "tongyi_image_list",
                "description": "List recent Tongyi image generation tasks",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
        ], handler=self._handle_tool)

        api.spawn_task(self._async_init(), name="tongyi-image:init")
        api.log("Tongyi Image plugin loaded")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("dashscope_api_key")
        if api_key:
            self._client = DashScopeClient(api_key)
        self._start_polling()

    async def on_unload(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("tongyi-image poll task drain error: %s", exc)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("tongyi-image DashScope client close error: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("tongyi-image task manager close error: %s", exc)

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        if tool_name == "tongyi_image_create":
            task = await self._create_task_internal(args)
            return f"Task created: {task['id']} (status: {task['status']}, mode: {task['mode']})"
        elif tool_name == "tongyi_image_status":
            task = await self._tm.get_task(args["task_id"])
            if not task:
                return f"Task {args['task_id']} not found"
            urls = task.get("image_urls", [])
            url_info = f", images: {len(urls)}" if urls else ""
            return f"Task {task['id']}: status={task['status']}{url_info}"
        elif tool_name == "tongyi_image_list":
            result = await self._tm.list_tasks(limit=args.get("limit", 10))
            lines = [f"Total: {result['total']} tasks"]
            for t in result["tasks"]:
                lines.append(f"  {t['id']}: [{t['mode']}] {t['status']} - {t['prompt'][:50]}")
            return "\n".join(lines)
        return f"Unknown tool: {tool_name}"

    # ── Internal task creation ──

    async def _create_task_internal(self, params: dict) -> dict:
        if not self._client:
            raise HTTPException(status_code=400, detail="API Key 未配置，请在设置中配置 DashScope API Key")

        mode = params.get("mode", "text2img")
        model_id = params.get("model", "")

        if not model_id:
            config = await self._tm.get_all_config()
            model_id = config.get("default_model", "wan27-pro")

        model_info = get_model(model_id)
        prompt = params.get("prompt", "") or params.get("edit_instruction", "")

        try:
            api_result = await self._dispatch_api_call(mode, model_info, params)
        except DashScopeError as e:
            raise HTTPException(status_code=502, detail=f"DashScope API 错误: {e.message}")
        except Exception as e:
            logger.error("API call error: %s", e)
            raise HTTPException(status_code=502, detail=f"API 调用失败: {e}")

        is_async = self._is_async_result(api_result)
        api_task_id = ""
        image_urls: list[str] = []
        status = "running" if is_async else "succeeded"

        if is_async:
            api_task_id = api_result.get("output", {}).get("task_id", "")
            logger.info("Async task created: api_task_id=%s", api_task_id)
        else:
            image_urls = self._extract_image_urls(api_result)
            logger.info("Sync result: %d images. Output keys: %s",
                        len(image_urls), list(api_result.get("output", {}).keys()))

        task = await self._tm.create_task(
            prompt=prompt,
            negative_prompt=params.get("negative_prompt", ""),
            model=model_id,
            mode=mode,
            params=params,
            api_task_id=api_task_id,
            status=status,
            image_urls=image_urls,
        )

        if status == "succeeded" and image_urls:
            config = await self._tm.get_all_config()
            if config.get("auto_download") == "true":
                self._api.spawn_task(
                    self._download_images(task["id"], image_urls),
                    name=f"tongyi-image:download:{task['id']}",
                )
            self._broadcast_update(task["id"], "succeeded")

        return task

    async def _dispatch_api_call(
        self, mode: str, model_info: Any, params: dict
    ) -> dict:
        """Route API call to the correct DashScope endpoint based on mode."""
        assert self._client

        if mode in ("text2img", "img_edit"):
            return await self._call_multimodal(mode, model_info, params)
        elif mode == "style_repaint":
            images = params.get("images", [])
            image_url = images[0] if images else ""
            return await self._client.style_repaint(
                image_url=image_url,
                style_index=params.get("style_index", 0),
                style_ref_url=params.get("style_ref_url"),
            )
        elif mode == "background":
            images = params.get("images", [])
            return await self._client.generate_background(
                base_image_url=images[0] if images else "",
                ref_prompt=params.get("ref_prompt"),
                ref_image_url=params.get("ref_image_url"),
                n=params.get("n", 1),
                noise_level=params.get("noise_level", 300),
                ref_prompt_weight=params.get("ref_prompt_weight", 0.5),
            )
        elif mode == "outpaint":
            images = params.get("images", [])
            return await self._client.outpaint(
                image_url=images[0] if images else "",
                x_scale=params.get("x_scale"),
                y_scale=params.get("y_scale"),
                output_ratio=params.get("output_ratio"),
                angle=params.get("angle", 0),
                left_offset=params.get("left_offset"),
                right_offset=params.get("right_offset"),
                top_offset=params.get("top_offset"),
                bottom_offset=params.get("bottom_offset"),
            )
        elif mode == "sketch":
            images = params.get("images", [])
            return await self._client.sketch_to_image(
                sketch_image_url=images[0] if images else "",
                prompt=params.get("prompt", ""),
                style=params.get("sketch_style", "<watercolor>"),
                size=params.get("size", "768*768"),
                n=params.get("n", 1),
                sketch_weight=params.get("sketch_weight", 3),
            )
        elif mode == "ecommerce":
            raise HTTPException(
                status_code=400,
                detail="电商套图请使用 /tasks/ecommerce-suite 端点",
            )
        else:
            raise HTTPException(status_code=400, detail=f"不支持的模式: {mode}")

    async def _call_multimodal(self, mode: str, model_info: Any, params: dict) -> dict:
        """Build messages and call multimodal or image-generation endpoint."""
        assert self._client
        messages: list[dict] = []
        prompt = params.get("prompt", "")

        model_id_str = model_info.model_id if model_info else params.get("model", "wan2.7-image-pro")
        use_async = model_info and model_info.api_type in ("async", "both")

        if mode == "img_edit":
            images = params.get("images", [])
            edit_instruction = params.get("edit_instruction", "") or prompt
            content: list[dict] = []
            for img_url in images:
                content.append({"type": "image_url", "image_url": {"url": img_url}})
            content.append({"type": "text", "text": edit_instruction})
            messages = [{"role": "user", "content": content}]
        else:
            content_items: list[dict] = []
            ref_images = params.get("images") or []
            for img_url in ref_images:
                content_items.append({"type": "image_url", "image_url": {"url": img_url}})
            content_items.append({"type": "text", "text": prompt})
            messages = [{"role": "user", "content": content_items}]

        kwargs: dict[str, Any] = {
            "model": model_id_str,
            "messages": messages,
            "size": params.get("size") or None,
            "n": params.get("n", 1),
            "watermark": params.get("watermark", False),
        }
        if params.get("negative_prompt"):
            kwargs["negative_prompt"] = params["negative_prompt"]
        if params.get("prompt_extend") is not None:
            kwargs["prompt_extend"] = params["prompt_extend"]
        if params.get("seed") is not None:
            kwargs["seed"] = params["seed"]
        if params.get("thinking_mode") is not None:
            kwargs["thinking_mode"] = params["thinking_mode"]
        if params.get("enable_sequential") is not None:
            kwargs["enable_sequential"] = params["enable_sequential"]
        if params.get("color_palette"):
            kwargs["color_palette"] = params["color_palette"]
        if params.get("bbox_list"):
            kwargs["bbox_list"] = params["bbox_list"]

        if use_async:
            return await self._client.generate_image_async(**kwargs)
        return await self._client.generate_image(**kwargs)

    @staticmethod
    def _is_async_result(result: dict) -> bool:
        output = result.get("output", {})
        return bool(output.get("task_id") and output.get("task_status"))

    @staticmethod
    def _extract_image_urls(result: dict) -> list[str]:
        """Extract image URLs from any DashScope response format."""
        urls: list[str] = []
        output = result.get("output", {})

        # Format 1: choices[].message.content[] — handles both key names
        for choice in output.get("choices", []):
            msg = choice.get("message", {})
            for item in msg.get("content", []):
                if not isinstance(item, dict):
                    continue
                url = ""
                img = item.get("image_url")
                if img:
                    url = img.get("url", "") if isinstance(img, dict) else str(img)
                if not url:
                    url = item.get("image", "")
                if url and isinstance(url, str) and url.startswith("http"):
                    urls.append(url)

        # Format 2: async task result — results[].url
        for r in output.get("results", []):
            if isinstance(r, dict):
                url = r.get("url") or r.get("image_url") or r.get("image") or r.get("orig_url") or ""
                if url:
                    urls.append(url)
            elif isinstance(r, str) and r.startswith("http"):
                urls.append(r)

        # Format 3: flat keys — output.result_url / output_image_url / ...
        for key in ("result_url", "output_image_url", "image_url", "image_urls", "image"):
            val = output.get(key)
            if isinstance(val, list):
                urls.extend(u for u in val if isinstance(u, str) and u.startswith("http"))
            elif isinstance(val, str) and val.startswith("http"):
                urls.append(val)

        # Format 4: root-level results (some endpoints)
        for r in result.get("results", []):
            if isinstance(r, dict):
                url = r.get("url") or r.get("image") or ""
                if url:
                    urls.append(url)

        if not urls:
            logger.warning("No image URLs extracted. Response: %s", _safe_log(result))

        return list(dict.fromkeys(urls))

    # ── Polling ──

    def _start_polling(self) -> None:
        self._poll_task = self._api.spawn_task(
            self._poll_loop(), name="tongyi-image:poll"
        )

    async def _poll_loop(self) -> None:
        while True:
            try:
                interval = int(await self._tm.get_config("poll_interval") or "10")
                await asyncio.sleep(max(interval, 3))
                await self._poll_running_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Poll error: %s", e)
                await asyncio.sleep(10)

    async def _poll_running_tasks(self) -> None:
        if not self._client:
            return
        tasks = await self._tm.get_running_tasks()
        for task in tasks:
            api_id = task.get("api_task_id")
            if not api_id:
                continue
            try:
                result = await self._client.get_task(api_id)
                output = result.get("output", {})
                status = output.get("task_status", "")

                if status == "SUCCEEDED":
                    image_urls = self._extract_image_urls(result)
                    logger.info(
                        "Task %s completed: %d images. Raw output keys: %s",
                        task["id"], len(image_urls),
                        list(result.get("output", {}).keys()),
                    )
                    if not image_urls:
                        logger.warning(
                            "Task %s SUCCEEDED but no images extracted. Response: %s",
                            task["id"], _safe_log(result),
                        )
                    await self._tm.update_task(
                        task["id"],
                        status="succeeded",
                        image_urls=image_urls,
                        usage=result.get("usage", {}),
                    )
                    config = await self._tm.get_all_config()
                    if config.get("auto_download") == "true" and image_urls:
                        self._api.spawn_task(
                            self._download_images(task["id"], image_urls),
                            name=f"tongyi-image:download:{task['id']}",
                        )
                    self._broadcast_update(task["id"], "succeeded")

                elif status == "FAILED":
                    error_msg = output.get("message", "") or output.get("error_message", "Unknown error")
                    await self._tm.update_task(
                        task["id"], status="failed", error_message=error_msg
                    )
                    self._broadcast_update(task["id"], "failed")

                elif status == "RUNNING":
                    if task.get("status") != "running":
                        await self._tm.update_task(task["id"], status="running")

            except Exception as e:
                logger.debug("Poll task %s error: %s", task["id"], e)

    async def _download_images(self, task_id: str, urls: list[str]) -> None:
        try:
            import httpx
            config = await self._tm.get_all_config()
            output_dir = config.get("output_dir") or str(
                self._api.get_data_dir() / "images"
            )
            out_path = Path(output_dir)
            out_path.mkdir(parents=True, exist_ok=True)

            local_paths: list[str] = []
            async with httpx.AsyncClient(timeout=60.0) as http:
                for i, url in enumerate(urls):
                    ext = ".png"
                    if ".jpg" in url or ".jpeg" in url:
                        ext = ".jpg"
                    elif ".webp" in url:
                        ext = ".webp"
                    filename = f"{task_id}_{i}{ext}"
                    filepath = out_path / filename
                    resp = await http.get(url)
                    resp.raise_for_status()
                    filepath.write_bytes(resp.content)
                    local_paths.append(str(filepath))

            await self._tm.update_task(task_id, local_image_paths=local_paths)
            logger.info("Downloaded %d images for task %s", len(local_paths), task_id)
        except Exception as e:
            logger.warning("Failed to download images for task %s: %s", task_id, e)

    def _broadcast_update(self, task_id: str, status: str) -> None:
        try:
            self._api.broadcast_ui_event(
                "task_update", {"task_id": task_id, "status": status}
            )
        except Exception:
            pass

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:

        # Issue #479: serve previously uploaded images so the UI can render
        # <img src="/api/plugins/tongyi-image/uploads/<file>"> after upload.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict:
            task = await self._create_task_internal(body.model_dump())
            return {"ok": True, "task": task}

        @router.post("/tasks/ecommerce-suite")
        async def create_ecommerce_suite(body: EcommerceSuiteBody) -> dict:
            """One-click e-commerce product image suite generation."""
            if not self._client:
                raise HTTPException(status_code=400, detail="API Key 未配置")

            scenes = body.scenes or [s["id"] for s in ECOMMERCE_SCENE_PRESETS]
            base_images = body.images or []
            model_id = body.model or (await self._tm.get_all_config()).get(
                "default_model", "wan27-pro"
            )
            model_info = get_model(model_id)
            size = body.size or ""

            prompts = generate_ecommerce_prompts(
                product_name=body.product_name,
                base_prompt=body.prompt,
                scenes=scenes,
            )

            group_id = __import__("uuid").uuid4().hex[:10]
            tasks_out = []

            for scene_id, scene_prompt in prompts:
                try:
                    messages = [{"role": "user", "content": [
                        {"type": "text", "text": scene_prompt},
                    ]}]

                    model_str = model_info.model_id if model_info else model_id
                    use_async = model_info and model_info.api_type in ("async", "both")
                    call_fn = (
                        self._client.generate_image_async
                        if use_async
                        else self._client.generate_image
                    )
                    api_result = await call_fn(
                        model=model_str,
                        messages=messages,
                        size=size or None,
                        n=1,
                        watermark=False,
                    )

                    is_async = self._is_async_result(api_result)
                    api_task_id = ""
                    ec_image_urls: list[str] = []
                    ec_status = "running" if is_async else "succeeded"
                    if is_async:
                        api_task_id = api_result.get("output", {}).get("task_id", "")
                    else:
                        ec_image_urls = self._extract_image_urls(api_result)

                    task = await self._tm.create_task(
                        prompt=scene_prompt,
                        model=model_id,
                        mode="ecommerce",
                        params={
                            "group_id": group_id,
                            "scene_id": scene_id,
                            "product_name": body.product_name,
                        },
                        api_task_id=api_task_id,
                        status=ec_status,
                        image_urls=ec_image_urls,
                    )
                    tasks_out.append(task)

                    if ec_status == "succeeded" and ec_image_urls:
                        self._broadcast_update(task["id"], "succeeded")

                except Exception as e:
                    logger.warning("Ecommerce scene %s failed: %s", scene_id, e)
                    task = await self._tm.create_task(
                        prompt=scene_prompt,
                        model=model_id,
                        mode="ecommerce",
                        params={"group_id": group_id, "scene_id": scene_id},
                        status="failed",
                    )
                    await self._tm.update_task(task["id"], error_message=str(e))
                    tasks_out.append(task)

            return {
                "ok": True,
                "group_id": group_id,
                "tasks": tasks_out,
                "total": len(tasks_out),
            }

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            offset: int = 0,
            limit: int = 20,
        ) -> dict:
            result = await self._tm.list_tasks(
                status=status, mode=mode, offset=offset, limit=limit
            )
            return {"ok": True, "tasks": result["tasks"], "total": result["total"]}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"ok": True, "task": task}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict:
            ok = await self._tm.delete_task(task_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"ok": True}

        @router.post("/tasks/{task_id}/recheck")
        async def recheck_task(task_id: str) -> dict:
            """Re-query DashScope API for a succeeded task with missing images."""
            if not self._client:
                raise HTTPException(status_code=400, detail="API Key 未配置")
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            api_id = task.get("api_task_id")
            if not api_id:
                raise HTTPException(status_code=400, detail="无异步任务 ID，无法重新查询")
            result = await self._client.get_task(api_id)
            out = result.get("output", {})
            status = out.get("task_status", "")
            if status == "SUCCEEDED":
                image_urls = self._extract_image_urls(result)
                await self._tm.update_task(
                    task_id,
                    status="succeeded",
                    image_urls=image_urls,
                    usage=result.get("usage", {}),
                )
                if image_urls:
                    config = await self._tm.get_all_config()
                    if config.get("auto_download") == "true":
                        self._api.spawn_task(
                            self._download_images(task_id, image_urls),
                            name=f"tongyi-image:download:{task_id}",
                        )
                updated = await self._tm.get_task(task_id)
                return {"ok": True, "task": updated, "images_found": len(image_urls)}
            elif status == "FAILED":
                err = out.get("message", "Unknown error")
                await self._tm.update_task(task_id, status="failed", error_message=err)
                return {"ok": False, "error": err}
            else:
                return {"ok": True, "task": task, "api_status": status}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            new_task = await self._create_task_internal(task.get("params", {}))
            return {"ok": True, "task": new_task}

        @router.get("/images/{task_id}")
        async def proxy_image(task_id: str, idx: int = 0, download: int = 0):
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            local_paths = task.get("local_image_paths", [])
            image_urls = task.get("image_urls", [])

            source = None
            if idx < len(local_paths) and Path(local_paths[idx]).is_file():
                source = local_paths[idx]
            elif idx < len(image_urls):
                source = image_urls[idx]

            if not source:
                raise HTTPException(status_code=404, detail="Image not available")

            prompt_prefix = (task.get("prompt", "") or "image")[:30].strip() or "image"
            fname = f"tongyi_{prompt_prefix}_{idx}.png"

            return self._api.create_file_response(
                source, filename=fname, media_type="image/png",
                as_download=bool(download),
            )

        @router.get("/images/{task_id}/download")
        async def download_images(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            urls = task.get("image_urls", [])
            if not urls:
                raise HTTPException(status_code=404, detail="No images available")
            await self._download_images(task_id, urls)
            updated = await self._tm.get_task(task_id)
            return {"ok": True, "task": updated}

        @router.post("/upload")
        async def upload_file(file: UploadFile = File(...)) -> dict:
            content = await file.read()
            ext = Path(file.filename or "file").suffix.lower()
            assets_dir = self._api.get_data_dir() / "uploads"
            assets_dir.mkdir(parents=True, exist_ok=True)

            import uuid as _uuid
            filename = f"{_uuid.uuid4().hex[:8]}_{file.filename or 'file'}"
            filepath = assets_dir / filename
            filepath.write_bytes(content)

            b64 = base64.b64encode(content).decode("ascii")
            mime = file.content_type or "image/png"
            return {
                "ok": True,
                "path": str(filepath),
                "url": build_preview_url("tongyi-image", filename),
                "base64": f"data:{mime};base64,{b64}" if len(content) < 10_000_000 else None,
            }

        @router.get("/settings")
        async def get_settings() -> dict:
            cfg = await self._tm.get_all_config()
            return {"ok": True, "config": cfg}

        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict:
            await self._tm.set_configs(body.updates)
            if "dashscope_api_key" in body.updates and body.updates["dashscope_api_key"]:
                key = body.updates["dashscope_api_key"]
                if self._client:
                    self._client.update_api_key(key)
                else:
                    self._client = DashScopeClient(key)
            saved = await self._tm.get_all_config()
            return {"ok": True, "config": saved}

        @router.get("/models")
        async def list_models(category: str | None = None) -> dict:
            if category:
                models = get_models_for_category(category)
            else:
                models = IMAGE_MODELS
            return {"ok": True, "models": [model_to_dict(m) for m in models]}

        @router.get("/models/{model_id}")
        async def get_model_info(model_id: str) -> dict:
            m = get_model(model_id)
            if not m:
                raise HTTPException(status_code=404, detail="Model not found")
            return {"ok": True, "model": model_to_dict(m)}

        @router.get("/sizes")
        async def get_sizes() -> dict:
            return {"ok": True, "sizes": RECOMMENDED_SIZES}

        @router.get("/style-presets")
        async def get_style_presets() -> dict:
            return {
                "ok": True,
                "repaint_presets": STYLE_REPAINT_PRESETS,
                "sketch_styles": SKETCH_STYLES,
                "ecommerce_scenes": ECOMMERCE_SCENE_PRESETS,
            }

        @router.post("/prompt-optimize")
        async def optimize_prompt_endpoint(body: PromptOptimizeBody) -> dict:
            brain = self._api.get_brain()
            if not brain:
                return {"ok": False, "error": "LLM 不可用，请在主设置中配置 LLM"}
            try:
                result = await optimize_prompt(
                    brain=brain,
                    user_prompt=body.prompt,
                    model=body.model,
                    size=body.size,
                    style=body.style,
                    level=body.level,
                )
                return {"ok": True, "result": result}
            except PromptOptimizeError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:
                logger.error("Prompt optimize error: %s", e)
                return {"ok": False, "error": f"优化失败: {e}"}

        @router.get("/prompt-guide")
        async def get_prompt_guide(locale: str | None = None) -> dict:
            return {"ok": True, **get_prompt_guide_data(locale)}

        @router.get("/prompt-templates")
        async def get_prompt_templates() -> dict:
            return {"ok": True, "templates": PROMPT_TEMPLATES}

        @router.get("/storage/stats")
        async def storage_stats() -> dict:
            # Wrap each subdir in collect_storage_stats so the walk runs off
            # the loop and is hard-capped at max_files (avoids UI stalls when
            # users accumulate tens of thousands of generated images).
            data_dir = self._api.get_data_dir()
            stats: dict[str, dict] = {}
            truncated_any = False
            for label, d in [
                ("images", data_dir / "images"),
                ("uploads", data_dir / "uploads"),
            ]:
                report = await collect_storage_stats(
                    d, max_files=20000, sample_paths=0, skip_hidden=True,
                )
                truncated_any = truncated_any or report.truncated
                stats[label] = {
                    "path": str(d),
                    "size_bytes": report.total_bytes,
                    "size_mb": round(report.total_bytes / 1048576, 1),
                    "file_count": report.total_files,
                    "truncated": report.truncated,
                }
            return {"ok": True, "stats": stats, "truncated": truncated_any}
