"""E-Commerce Content Generator — full-stack plugin entry point.

Provides unified REST API for 19 sub-features across 4 modules:
  - Video generation (Ark/Seedance)
  - Image generation (DashScope)
  - E-commerce detail pages (DashScope)
  - Activity posters (DashScope)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openakita.plugins.api import PluginAPI, PluginBase

from ecom_feature_registry import (
    FeatureDefinition,
    FeatureExample,
    FeatureParam,
    FeatureRegistry,
)
from ecom_execution import (
    ExecutionContext, strategy_factory, safe_format,
)
from ecom_mock import (
    MOCK_VIDEO_URL,
    build_mock_prompt,
    mock_delay_seconds,
    mock_image_urls,
    should_use_mock,
)

logger = logging.getLogger(__name__)

_FIELD_OPTIMIZE_SYSTEMS: dict[str, str] = {
    "selling_points": "你是电商文案专家。优化以下产品卖点文案，使其更有吸引力和说服力。直接输出优化后的卖点文案，不要解释。",
    "storyboard_script": "你是短视频编剧。优化以下视频脚本，使其更适合电商短视频拍摄。直接输出优化后的脚本，不要解释。",
    "target_character": "你是 AI 视角描述专家。优化以下角色描述，使其更适合 AI 视频生成模型理解。直接输出优化后的描述，不要解释。",
    "negative_prompt": "你是 AI 绘画专家。优化以下反向提示词，使其更有效地排除不需要的元素。直接输出优化后的反向提示词，不要解释。",
    "_default": "你是电商内容专家。优化以下文案使其更专业和有吸引力。直接输出优化后的文案，不要解释。",
}


# ── Request models ──

class ExecuteBody(BaseModel):
    params: dict = {}


class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]


class PromptOptimizeBody(BaseModel):
    prompt: str = ""
    feature_id: str = ""
    field_id: str = "prompt"
    params: dict = {}
    level: str = "professional"
    kind: str = "image"  # "image" | "video"
    mode: str = "t2v"  # video only
    duration: int = 5  # video only
    ratio: str = "16:9"  # video only
    asset_summary: str = "无"  # video only
    category: str = ""  # image only
    style: str = ""  # image only


# ── Plugin ──

class Plugin(PluginBase):

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._data_dir = api.get_data_dir()
        self._brain = None
        self._registry = FeatureRegistry()
        self._tm = None  # TaskManager, set in _async_init
        self._dashscope = None  # EcomClient
        self._ark = None  # EcomVideoClient
        # Both tasks are placeholders so on_unload can cancel/await them safely
        # even if on_load races with hot-reload before they have been scheduled.
        self._init_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(8)

        # Load feature registry synchronously BEFORE registering routes so that
        # /features can return non-empty results from the very first request.
        # _load_features() is pure in-memory (dataclass instantiation only),
        # zero I/O, so it is safe in the synchronous on_load path.
        self._load_features()

        # Single router-level dependency replaces per-handler guard boilerplate:
        # every request is short-circuited with 503 until _async_init finishes.
        router = APIRouter(dependencies=[Depends(self._ensure_ready)])
        self._register_feature_routes(router)
        self._register_task_routes(router)
        self._register_file_routes(router)
        self._register_config_routes(router)
        self._register_custom_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "ecom_image_create",
                "description": "Create an e-commerce image (product main image, poster, detail page)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "feature_id": {"type": "string", "description": "Feature ID like image_main_gen"},
                        "prompt": {"type": "string", "description": "Image generation prompt"},
                        "product_name": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "ecom_video_create",
                "description": "Create an e-commerce video (product showcase, ad, storyboard)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "feature_id": {"type": "string", "description": "Feature ID like video_ad_oneclick"},
                        "prompt": {"type": "string", "description": "Video generation prompt"},
                        "duration": {"type": "integer", "default": 5},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "ecom_task_status",
                "description": "Check status of an e-commerce content generation task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "ecom_task_list",
                "description": "List recent e-commerce content generation tasks",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
        ], handler=self._handle_tool)

        # Use api.spawn_task so the host can cancel + drain on unload.
        self._init_task = api.spawn_task(self._async_init(), name="ecommerce-image:init")
        api.log("E-Commerce Content plugin loaded")

    async def _async_init(self) -> None:
        from ecom_task_manager import TaskManager
        self._tm = TaskManager(self._data_dir / "ecommerce.db")
        await self._tm.init()

        dashscope_key, dashscope_base_url = self._resolve_relay_endpoint(
            "dashscope_api_key", "dashscope_relay_endpoint",
            "dashscope_relay_fallback_policy",
            required_capability="image",
            await_config=await self._tm.get_all_config(),
        )
        if dashscope_key:
            from ecom_client import EcomClient
            self._dashscope = EcomClient(dashscope_key, base_url=dashscope_base_url or None)

        ark_key, ark_base_url = self._resolve_relay_endpoint(
            "ark_api_key", "ark_relay_endpoint",
            "ark_relay_fallback_policy",
            required_capability="video",
            await_config=await self._tm.get_all_config(),
        )
        if ark_key:
            from ecom_video_client import EcomVideoClient
            self._ark = EcomVideoClient(ark_key, base_url=ark_base_url or None)

        self._start_polling()

    def _resolve_relay_endpoint(
        self,
        key_field: str,
        relay_field: str,
        policy_field: str,
        *,
        required_capability: str,
        await_config: dict,
    ) -> tuple[str, str]:
        """Resolve (api_key, base_url) honouring an optional relay reference.

        ``await_config`` is the result of ``self._tm.get_all_config()``
        — passed in so this method stays sync and easy to unit-test
        without an event loop. ``key_field`` is the per-plugin api_key
        config key (e.g. ``dashscope_api_key``); ``relay_field`` is the
        optional relay-name override; ``policy_field`` controls strict
        / official fallback.

        On strict-policy + missing relay raises HTTPException(400)
        with a Chinese user_message so the Settings UI banner can
        surface it verbatim.
        """
        api_key = str(await_config.get(key_field) or "")
        relay_name = str(await_config.get(relay_field) or "").strip()
        if not relay_name:
            return api_key, ""
        try:
            from openakita.relay import (
                SettingsRelayResolutionError,
                apply_relay_override,
            )

            merged = apply_relay_override(
                {
                    "api_key": api_key,
                    "base_url": "",
                    "relay_endpoint": relay_name,
                    "relay_fallback_policy": str(
                        await_config.get(policy_field) or "official"
                    ),
                },
                required_capability=required_capability,
                plugin_name="ecommerce-image",
            )
        except (ImportError, ModuleNotFoundError) as exc:
            logger.info(
                "ecommerce-image: openakita.relay not importable (%s); "
                "keeping per-plugin endpoint for %s",
                exc,
                key_field,
            )
            return api_key, ""
        except SettingsRelayResolutionError as exc:
            raise HTTPException(status_code=400, detail=exc.user_message) from exc
        return str(merged.get("api_key") or ""), str(merged.get("base_url") or "")

    def _load_features(self) -> None:
        """Load all 19 feature definitions from config module.

        IMPORTANT: shallow-copy each dict before pop(); never mutate ALL_FEATURES in place.
        Otherwise plugin reload / second load strips params and the UI shows no inputs.
        """
        try:
            from ecom_features_config import ALL_FEATURES
            for raw in ALL_FEATURES:
                fdict = dict(raw)
                params = [FeatureParam(**p) for p in fdict.pop("params", [])]
                examples = [FeatureExample(**e) for e in fdict.pop("examples", [])]
                fd = FeatureDefinition(**fdict, params=params, examples=examples)
                self._registry.register(fd)
            logger.info("Loaded %d features", len(self._registry.feature_ids))
        except Exception as e:
            logger.error("Failed to load features: %s", e)

    async def on_unload(self) -> None:
        # Cancel + await background tasks first so polling stops touching closed
        # clients / db handles below. spawn_task tasks are also tracked by the
        # host (defence-in-depth), but we cancel here for prompt shutdown.
        for task in (self._init_task, self._poll_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Close clients / db. Each guarded individually so a single failure
        # does not leak the others.
        for closer in (
            getattr(self._dashscope, "close", None),
            getattr(self._ark, "close", None),
            getattr(self._tm, "close", None),
        ):
            if closer is None:
                continue
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug("ecommerce-image close error: %s", e)

    # ── Lifecycle guards ──

    def _ensure_ready(self) -> None:
        """Raise 503 if backend is still warming up.

        ``_async_init`` runs concurrently with ``on_load`` returning, so the
        very first UI requests can hit routes before ``self._tm`` exists. We
        prefer a clean 503 over a stack trace / spinner.
        """
        if self._tm is None:
            raise HTTPException(
                503, "ecommerce-image backend is initializing, try again in a moment",
            )

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        self._ensure_ready()
        if tool_name == "ecom_image_create":
            feature_id = args.get("feature_id", "image_main_gen")
            task = await self._execute_feature(feature_id, args)
            return f"Task created: {task['id']} (status: {task['status']})"
        elif tool_name == "ecom_video_create":
            feature_id = args.get("feature_id", "video_ad_oneclick")
            task = await self._execute_feature(feature_id, args)
            return f"Task created: {task['id']} (status: {task['status']})"
        elif tool_name == "ecom_task_status":
            task = await self._tm.get_task(args["task_id"])
            if not task:
                return f"Task {args['task_id']} not found"
            return (
                f"Task {task['id']}: status={task['status']}, "
                f"progress={task.get('progress_current', 0)}/{task.get('progress_total', 1)}"
            )
        elif tool_name == "ecom_task_list":
            tasks, total = await self._tm.list_tasks(limit=args.get("limit", 10))
            lines = [f"Total: {total} tasks"]
            for t in tasks:
                lines.append(f"  {t['id']}: [{t['status']}] {t.get('feature_id', '')} - {(t.get('prompt') or '')[:40]}")
            return "\n".join(lines)
        return f"Unknown tool: {tool_name}"

    # ── Feature execution ──

    async def _execute_feature(self, feature_id: str, params: dict) -> dict:
        feature = self._registry.get(feature_id)
        if not feature:
            raise HTTPException(400, f"Unknown feature: {feature_id}")

        has_ds = bool(await self._tm.get_config("dashscope_api_key"))
        has_ark = bool(await self._tm.get_config("ark_api_key"))
        mock_cfg = await self._tm.get_config("mock_mode")
        use_mock = should_use_mock(
            feature_provider=feature.api_provider,
            mock_mode_cfg=mock_cfg,
            has_dashscope=has_ds,
            has_ark=has_ark,
        )

        if not use_mock:
            if not self._dashscope and feature.api_provider == "dashscope":
                raise HTTPException(400, "DashScope API Key 未配置（或在设置中开启演示模式）")
            if not self._ark and feature.api_provider == "ark":
                raise HTTPException(400, "Ark API Key 未配置（或在设置中开启演示模式）")

        defaults = {
            "default_image_model": (await self._tm.get_config("default_image_model")) or "",
            "default_video_model": (await self._tm.get_config("default_video_model")) or "",
            "default_image_size": (await self._tm.get_config("default_image_size")) or "",
            "watermark": (await self._tm.get_config("watermark")) or "false",
        }

        ctx = ExecutionContext(
            dashscope=self._dashscope,
            ark=self._ark,
            task_manager=self._tm,
            brain=self._get_brain(),
            plugin_api=self._api,
            feature=feature,
            semaphore=self._semaphore,
            defaults=defaults,
        )

        strategy = strategy_factory(feature.execution_mode)
        errors = await strategy.validate(params, ctx)
        if errors:
            raise HTTPException(400, detail="; ".join(errors))

        if use_mock:
            return await self._execute_mock(feature, params)

        return await strategy.execute(params, ctx)

    async def _execute_mock(self, feature: FeatureDefinition, params: dict) -> dict:
        """Simulate generation: same task list UX, no real API calls."""
        prompt = build_mock_prompt(feature, params)
        task_type = "video" if feature.api_provider == "ark" else "image"
        merged = {**params, "_mock": True}
        api_tid = f"mock-{uuid.uuid4().hex[:14]}"
        task = await self._tm.create_task(
            feature_id=feature.id,
            module=feature.module,
            task_type=task_type,
            api_provider=feature.api_provider,
            api_task_id=api_tid,
            status="running",
            prompt=prompt,
            model=str(params.get("model") or feature.default_model or ""),
            execution_mode=feature.execution_mode,
            params=merged,
            progress_current=0,
            progress_total=1,
        )
        self._api.spawn_task(
            self._mock_complete(task["id"], feature),
            name=f"ecommerce-image:mock:{task['id']}",
        )
        try:
            self._api.broadcast_ui_event("task_update", {"task_id": task["id"], "status": "running"})
        except Exception:
            pass
        return task

    async def _mock_complete(self, task_id: str, feature: FeatureDefinition) -> None:
        await asyncio.sleep(mock_delay_seconds())
        task = await self._tm.get_task(task_id)
        if not task or task["status"] != "running":
            return
        p = task.get("params") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {}
        try:
            qty = int(p.get("quantity", 1))
        except (TypeError, ValueError):
            qty = 1
        if feature.output_type == "images" and qty < 2:
            qty = 2
        qty = max(1, min(qty, 6))

        if feature.api_provider == "ark" or feature.output_type == "video":
            await self._tm.update_task_status(
                task_id, "succeeded", video_url=MOCK_VIDEO_URL,
            )
        else:
            urls = mock_image_urls(task_id, qty)
            await self._tm.update_task_status(
                task_id, "succeeded", image_urls=json.dumps(urls, ensure_ascii=False),
            )
        self._broadcast_update(task_id, "succeeded")

    def _get_brain(self) -> Any:
        if self._brain:
            return self._brain
        try:
            self._brain = self._api.get_brain()
        except Exception:
            pass
        return self._brain

    async def _call_brain(self, brain: Any, user_msg: str, system: str, max_tokens: int = 2048) -> str:
        from ecom_execution import _extract_text
        if hasattr(brain, "think_lightweight"):
            result = await brain.think_lightweight(prompt=user_msg, system=system, max_tokens=max_tokens)
        elif hasattr(brain, "think"):
            result = await brain.think(prompt=user_msg, system=system)
        else:
            raise HTTPException(500, "Brain has no think method")
        text = _extract_text(result).strip()
        if not text:
            raise HTTPException(500, "LLM 返回了空结果")
        return text

    # ── Polling ──

    def _start_polling(self) -> None:
        self._poll_task = self._api.spawn_task(self._poll_loop(), name="ecommerce-image:poll")

    async def _poll_loop(self) -> None:
        counter = 0
        while True:
            try:
                await asyncio.sleep(5)
                counter += 5
                img_interval = int(await self._tm.get_config("poll_interval_image") or "10")
                vid_interval = int(await self._tm.get_config("poll_interval_video") or "15")
                if counter % max(img_interval, 5) == 0:
                    await self._poll_dashscope_tasks()
                if counter % max(vid_interval, 5) == 0:
                    await self._poll_ark_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Poll error: %s", e)
                await asyncio.sleep(10)

    # ── Local download helpers ──

    async def _is_auto_download(self) -> bool:
        v = await self._tm.get_config("auto_download")
        if v is None or v == "":
            return True
        return str(v).lower() not in ("0", "false", "no", "off")

    async def _get_output_dir(self, kind: str) -> Path:
        """kind: 'image' or 'video'. Reads DB override, falls back to data_dir/<kind>s."""
        key = "image_output_dir" if kind == "image" else "video_output_dir"
        sub = "images" if kind == "image" else "videos"
        configured = await self._tm.get_config(key)
        if configured and configured.strip():
            path = Path(configured.strip()).expanduser()
        else:
            path = self._data_dir / sub
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._api.log(f"Cannot create output dir {path}: {e}; falling back to default")
            path = self._data_dir / sub
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _ext_from_response(resp: httpx.Response, default_ext: str) -> str:
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype:
            ext = mimetypes.guess_extension(ctype)
            if ext:
                if ext == ".jpe":
                    return ".jpg"
                return ext
        return default_ext

    async def _download_image_assets(self, task_id: str, urls: list[str]) -> list[str]:
        if not urls:
            return []
        out_dir = await self._get_output_dir("image")
        local_paths: list[str] = []
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            for idx, url in enumerate(urls):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    ext = self._ext_from_response(resp, ".png")
                    out_path = out_dir / f"ecom_{task_id}_{idx}{ext}"
                    out_path.write_bytes(resp.content)
                    local_paths.append(str(out_path))
                except Exception as e:
                    self._api.log(f"Failed to download image {url} for task {task_id}: {e}")
        return local_paths

    async def _download_video_asset(self, task_id: str, url: str) -> str:
        if not url:
            return ""
        out_dir = await self._get_output_dir("video")
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    ext = self._ext_from_response(resp, ".mp4")
                    out_path = out_dir / f"ecom_{task_id}{ext}"
                    with out_path.open("wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            f.write(chunk)
            return str(out_path)
        except Exception as e:
            self._api.log(f"Failed to download video {url} for task {task_id}: {e}")
            return ""

    async def _poll_dashscope_tasks(self) -> None:
        if not self._dashscope or not self._tm:
            return
        tasks = await self._tm.get_running_tasks(api_provider="dashscope")
        for task in tasks:
            if task["status"] == "cancelling":
                await self._tm.update_task_status(task["id"], "cancelled")
                self._broadcast_update(task["id"], "cancelled")
                continue
            tid = task.get("api_task_id") or ""
            if not tid or str(tid).startswith("mock-"):
                continue
            try:
                result = await self._dashscope.get_task_result(tid)
                status = result.get("status", "")
                if status in ("SUCCEEDED", "succeeded"):
                    image_urls = result.get("image_urls", [])
                    await self._tm.update_task_status(
                        task["id"], "succeeded",
                        image_urls=json.dumps(image_urls) if image_urls else None,
                    )
                    if image_urls and await self._is_auto_download():
                        local = await self._download_image_assets(task["id"], image_urls)
                        if local:
                            await self._tm.update_task(
                                task["id"], local_paths=json.dumps(local, ensure_ascii=False),
                            )
                    self._broadcast_update(task["id"], "succeeded")
                    parent_id = task.get("batch_parent_id")
                    if parent_id:
                        await self._tm.recompute_batch_parent_status(parent_id)
                        self._broadcast_update(parent_id, "progress")
                elif status in ("FAILED", "failed"):
                    error = result.get("error", "Unknown error")
                    await self._tm.update_task_status(
                        task["id"], "failed", error_message=str(error),
                    )
                    self._broadcast_update(task["id"], "failed")
                    parent_id = task.get("batch_parent_id")
                    if parent_id:
                        await self._tm.recompute_batch_parent_status(parent_id)
                        self._broadcast_update(parent_id, "progress")
            except Exception as e:
                logger.debug("Poll DashScope task %s error: %s", task["id"], e)

    async def _poll_ark_tasks(self) -> None:
        if not self._ark or not self._tm:
            return
        tasks = await self._tm.get_running_tasks(api_provider="ark")
        for task in tasks:
            if task["status"] == "cancelling":
                await self._tm.update_task_status(task["id"], "cancelled")
                self._broadcast_update(task["id"], "cancelled")
                continue
            tid = task.get("api_task_id") or ""
            if not tid or str(tid).startswith("mock-"):
                continue
            try:
                result = await self._ark.get_task(tid)
                status = result.get("status", "")
                if status == "succeeded":
                    video_url = ""
                    content = result.get("content", {})
                    if isinstance(content, dict):
                        video_url = content.get("video_url", "")
                    if not video_url:
                        output = result.get("output", {})
                        if isinstance(output, dict):
                            for item in output.get("content", []):
                                if isinstance(item, dict) and item.get("type") == "video_url":
                                    video_url = item.get("video_url", {}).get("url", "")
                    await self._tm.update_task_status(
                        task["id"], "succeeded", video_url=video_url,
                    )
                    if video_url and await self._is_auto_download():
                        local = await self._download_video_asset(task["id"], video_url)
                        if local:
                            await self._tm.update_task(
                                task["id"], local_video_path=local,
                            )
                    self._broadcast_update(task["id"], "succeeded")
                    parent_id = task.get("batch_parent_id")
                    if parent_id:
                        await self._tm.recompute_batch_parent_status(parent_id)
                        self._broadcast_update(parent_id, "progress")
                elif status == "failed":
                    error = result.get("error", {})
                    msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
                    await self._tm.update_task_status(
                        task["id"], "failed", error_message=msg,
                    )
                    self._broadcast_update(task["id"], "failed")
                    parent_id = task.get("batch_parent_id")
                    if parent_id:
                        await self._tm.recompute_batch_parent_status(parent_id)
                        self._broadcast_update(parent_id, "progress")
            except Exception as e:
                logger.debug("Poll Ark task %s error: %s", task["id"], e)

    def _broadcast_update(self, task_id: str, status: str) -> None:
        try:
            self._api.broadcast_ui_event("task_update", {"task_id": task_id, "status": status})
        except Exception:
            pass

    # ── Route registration ──

    def _register_feature_routes(self, router: APIRouter) -> None:

        @router.get("/features")
        async def list_features() -> dict:
            return {"ok": True, "features": self._registry.list_all_grouped()}

        @router.get("/features/{feature_id}")
        async def get_feature(feature_id: str) -> dict:
            f = self._registry.get(feature_id)
            if not f:
                raise HTTPException(404, "Feature not found")
            return {"ok": True, "feature": f.to_dict(include_examples=True)}

        @router.post("/features/{feature_id}/execute")
        async def execute_feature(feature_id: str, body: ExecuteBody) -> dict:
            task = await self._execute_feature(feature_id, body.params)
            if task.get("status") == "succeeded":
                self._broadcast_update(task["id"], "succeeded")
            return {"ok": True, "task": task}

        @router.post("/features/{feature_id}/validate")
        async def validate_feature(feature_id: str, body: ExecuteBody) -> dict:
            feature = self._registry.get(feature_id)
            if not feature:
                raise HTTPException(404, "Feature not found")
            ctx = ExecutionContext(
                dashscope=self._dashscope, ark=self._ark,
                task_manager=self._tm, brain=self._get_brain(),
                plugin_api=self._api, feature=feature,
            )
            strategy = strategy_factory(feature.execution_mode)
            errors = await strategy.validate(body.params, ctx)
            return {"ok": True, "valid": len(errors) == 0, "errors": errors}

    def _register_task_routes(self, router: APIRouter) -> None:

        def _make_mini_ctx(feature: FeatureDefinition):
            """Lightweight object mimicking ExecutionContext for resolve_model/resolve_size."""
            import types
            ctx = types.SimpleNamespace()
            ctx.feature = feature
            ctx.defaults = {}
            return ctx

        @router.get("/tasks")
        async def list_tasks(
            module: str | None = None,
            feature: str | None = None,
            status: str | None = None,
            offset: int = 0,
            limit: int = 20,
        ) -> dict:
            tasks, total = await self._tm.list_tasks(
                module=module, feature_id=feature, status=status,
                offset=offset, limit=limit,
            )
            return {"ok": True, "tasks": tasks, "total": total}

        @router.delete("/tasks/purge-mock")
        async def purge_mock_tasks() -> dict:
            """Delete all locally simulated tasks (api_task_id prefix mock-)."""
            n = await self._tm.delete_mock_tasks()
            return {"ok": True, "deleted_roots": n}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return {"ok": True, "task": task}

        @router.get("/tasks/{task_id}/children")
        async def get_task_children(task_id: str) -> dict:
            children = await self._tm.get_children(task_id)
            for c in children:
                if c.get("params_json") and not c.get("params"):
                    try:
                        c["params"] = json.loads(c["params_json"])
                    except Exception:
                        c["params"] = {}
            return {"ok": True, "children": children}

        @router.put("/tasks/{task_id}")
        async def update_task_fields(task_id: str, body: dict) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            allowed = {}
            if "prompt" in body:
                allowed["prompt"] = str(body["prompt"])
            if not allowed:
                raise HTTPException(400, "No valid fields to update")
            await self._tm.update_task(task_id, **allowed)
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry-child")
        async def retry_child(task_id: str) -> dict:
            child = await self._tm.get_task(task_id)
            if not child:
                raise HTTPException(404, "Task not found")
            parent_id = child.get("batch_parent_id")
            if not parent_id:
                raise HTTPException(400, "Not a child task")
            parent = await self._tm.get_task(parent_id)
            if not parent:
                raise HTTPException(404, "Parent task not found")

            params = parent.get("params") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            prompt = child.get("prompt", "")
            if not prompt:
                raise HTTPException(400, "Child task has no prompt")

            feature = self._registry.get(parent.get("feature_id", ""))
            if not feature:
                raise HTTPException(404, "Feature not found")

            from ecom_execution import resolve_model, resolve_size
            chosen_model = resolve_model(params, _make_mini_ctx(feature))
            size = resolve_size(params, _make_mini_ctx(feature))

            await self._tm.update_task_status(task_id, "running")
            self._broadcast_update(task_id, "running")

            try:
                if feature.api_provider == "dashscope" and self._dashscope:
                    api_result = await self._dashscope.generate(
                        model=chosen_model,
                        prompt=prompt,
                        images={},
                        capability=feature.api_capability,
                        size=size,
                        n=1,
                    )
                elif self._ark:
                    api_result = await self._ark.create_task(
                        model=chosen_model,
                        prompt=prompt,
                        images={},
                    )
                else:
                    raise RuntimeError("No API client available")

                api_task_id = api_result.get("task_id", "") or ""
                image_urls = api_result.get("image_urls", []) or []

                updates: dict = {}
                if api_task_id:
                    updates["api_task_id"] = api_task_id
                if image_urls:
                    updates["image_urls"] = json.dumps(image_urls)
                    updates["status"] = "succeeded"
                    await self._tm.update_task(task_id, **updates)
                    self._broadcast_update(task_id, "succeeded")
                else:
                    updates["api_task_id"] = api_task_id
                    await self._tm.update_task(task_id, **updates)

                await self._tm.recompute_batch_parent_status(parent_id)
                self._broadcast_update(parent_id, "progress")
            except Exception as e:
                await self._tm.update_task_status(
                    task_id, "failed", error_message=str(e),
                )
                self._broadcast_update(task_id, "failed")
                await self._tm.recompute_batch_parent_status(parent_id)
                self._broadcast_update(parent_id, "progress")

            return {"ok": True}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict:
            await self._tm.delete_task(task_id)
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            old = await self._tm.get_task(task_id)
            if not old:
                raise HTTPException(404, "Task not found")
            if old["status"] not in ("failed", "cancelled", "partial_success"):
                raise HTTPException(400, "只能重试失败/取消/部分成功的任务")
            new_task = await self._execute_feature(
                old["feature_id"], old.get("params", {}),
            )
            return {"ok": True, "task": new_task}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] not in ("pending", "running"):
                raise HTTPException(400, "只能取消 pending/running 任务")

            has_children = (
                task.get("execution_mode") == "batch"
                or task.get("progress_total", 0) > 1
            )
            if has_children:
                await self._tm.update_task_status(task_id, "cancelling")
                children = await self._tm.get_children(task_id)
                for child in children:
                    if child["status"] in ("pending", "running"):
                        await self._tm.update_task_status(child["id"], "cancelled")
                await self._tm.update_task_status(task_id, "cancelled")
            else:
                await self._tm.update_task_status(task_id, "cancelled")

            self._broadcast_update(task_id, "cancelled")
            return {"ok": True}

    def _register_file_routes(self, router: APIRouter) -> None:

        # Streamed read: never load >50 MB into memory; 1 MiB chunks.
        MAX_UPLOAD_BYTES = 50 * 1024 * 1024
        CHUNK = 1024 * 1024

        @router.post("/upload")
        async def upload_file(
            request: Request, file: UploadFile = File(...),
        ) -> dict:
            # Pre-check Content-Length so giant uploads die before we touch disk.
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    413, f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                )

            ext = Path(file.filename or "file").suffix.lower()
            assets_dir = self._data_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)

            import uuid
            filename = f"{uuid.uuid4().hex[:8]}_{file.filename or 'file'}"
            filepath = assets_dir / filename

            total = 0
            sha = None  # placeholder; could hash if dedup needed later
            try:
                with filepath.open("wb") as fp:
                    while True:
                        chunk = await file.read(CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            fp.close()
                            try:
                                filepath.unlink(missing_ok=True)
                            except Exception:
                                pass
                            raise HTTPException(
                                413,
                                f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                            )
                        fp.write(chunk)
            except HTTPException:
                raise
            except Exception as e:
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(500, f"Upload failed: {e}") from e

            asset = await self._tm.create_asset(
                type="image" if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else "file",
                file_path=str(filepath),
                original_name=file.filename,
                size_bytes=total,
            )

            # Inline base64 only for small images (<10 MB) so the UI can preview
            # without an extra round-trip; large files are referenced by path.
            base64_payload: str | None = None
            if total < 10 * 1024 * 1024:
                try:
                    b64 = base64.b64encode(filepath.read_bytes()).decode("ascii")
                    base64_payload = f"data:{file.content_type};base64,{b64}"
                except Exception:
                    base64_payload = None

            return {"ok": True, "asset": asset, "base64": base64_payload}

        @router.get("/images/{task_id}")
        async def proxy_image(task_id: str, idx: int = 0):
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            local_paths = []
            try:
                local_paths = json.loads(task.get("local_paths") or "[]")
            except Exception:
                local_paths = []
            if idx < len(local_paths):
                lp = Path(local_paths[idx])
                if lp.exists():
                    media = mimetypes.guess_type(str(lp))[0] or "image/png"
                    return FileResponse(str(lp), media_type=media, filename=lp.name)
            urls = json.loads(task.get("image_urls") or "[]")
            if idx >= len(urls):
                raise HTTPException(404, "Image index out of range")
            url = urls[idx]
            lp = Path(url) if url and (":\\" in url or url.startswith("/")) else None
            if lp and lp.exists():
                media = mimetypes.guess_type(str(lp))[0] or "image/png"
                return FileResponse(str(lp), media_type=media, filename=lp.name)
            return self._api.create_file_response(
                url, filename=f"ecom_{task_id}_{idx}.png", media_type="image/png",
            )

        @router.get("/videos/{task_id}")
        async def proxy_video(task_id: str):
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            local_video = task.get("local_video_path") or ""
            if local_video:
                lp = Path(local_video)
                if lp.exists():
                    media = mimetypes.guess_type(str(lp))[0] or "video/mp4"
                    return FileResponse(str(lp), media_type=media, filename=lp.name)
            video_url = task.get("video_url")
            if not video_url:
                raise HTTPException(404, "No video available")
            return self._api.create_file_response(
                video_url, filename=f"ecom_{task_id}.mp4", media_type="video/mp4",
            )

    def _register_config_routes(self, router: APIRouter) -> None:

        @router.get("/config")
        async def get_config() -> dict:
            cfg = await self._tm.get_all_config()
            safe = {k: ("***" if "key" in k.lower() and v else v) for k, v in cfg.items()}
            dsk = await self._tm.get_config("dashscope_api_key")
            ark = await self._tm.get_config("ark_api_key")
            mm = await self._tm.get_config("mock_mode") or "auto"
            auto_dl = await self._tm.get_config("auto_download")
            if auto_dl is None or auto_dl == "":
                auto_dl = "true"
            wm = await self._tm.get_config("watermark") or "false"
            from ecom_models import IMAGE_MODELS, VIDEO_MODELS
            image_model_opts = [{"id": m["id"], "name": m["name"]} for m in IMAGE_MODELS]
            video_model_opts = [{"id": m["id"], "name": m["name"]} for m in VIDEO_MODELS]
            image_size_opts = ["1K", "2K", "4K"]
            meta = {
                "mock_mode": mm,
                "has_dashscope_key": bool(dsk),
                "has_ark_key": bool(ark),
                "mock_auto_image": should_use_mock(
                    feature_provider="dashscope",
                    mock_mode_cfg=mm,
                    has_dashscope=bool(dsk),
                    has_ark=bool(ark),
                ),
                "mock_auto_video": should_use_mock(
                    feature_provider="ark",
                    mock_mode_cfg=mm,
                    has_dashscope=bool(dsk),
                    has_ark=bool(ark),
                ),
                "data_dir": str(self._data_dir),
                "default_image_dir": str(self._data_dir / "images"),
                "default_video_dir": str(self._data_dir / "videos"),
                "auto_download": auto_dl,
                "watermark": wm,
                "image_models": image_model_opts,
                "video_models": video_model_opts,
                "image_sizes": image_size_opts,
                "default_image_model_fallback": IMAGE_MODELS[0]["id"] if IMAGE_MODELS else "",
                "default_video_model_fallback": VIDEO_MODELS[0]["id"] if VIDEO_MODELS else "",
                "default_image_size_fallback": "1K",
            }
            return {"ok": True, "config": safe, "meta": meta}

        @router.put("/config")
        async def update_config(body: ConfigUpdateBody) -> dict:
            for key_field, label in (
                ("dashscope_api_key", "DashScope (通义) API Key 形如 sk-xxxxxxxx"),
                ("ark_api_key", "Ark (火山方舟) API Key 形如 UUID 或 32 位字符串"),
            ):
                if key_field in body.updates and body.updates[key_field]:
                    raw = str(body.updates[key_field]).strip()
                    body.updates[key_field] = raw
                    bad = (
                        len(raw) < 20
                        or any(ch.isspace() for ch in raw)
                        or any(sep in raw for sep in ("\\", "/", " "))
                    )
                    if bad:
                        raise HTTPException(
                            400,
                            f"{key_field} 不像是合法的 API Key（{label}），"
                            "请检查是否误粘了路径/命令/带空格的字符串。",
                        )
            await self._tm.set_configs(body.updates)
            saved_full = await self._tm.get_all_config()
            ds_endpoint_keys = {
                "dashscope_api_key",
                "dashscope_relay_endpoint",
                "dashscope_relay_fallback_policy",
            }
            ark_endpoint_keys = {
                "ark_api_key",
                "ark_relay_endpoint",
                "ark_relay_fallback_policy",
            }
            if ds_endpoint_keys & body.updates.keys():
                ds_key, ds_base = self._resolve_relay_endpoint(
                    "dashscope_api_key",
                    "dashscope_relay_endpoint",
                    "dashscope_relay_fallback_policy",
                    required_capability="image",
                    await_config=saved_full,
                )
                if ds_key:
                    from ecom_client import EcomClient
                    if self._dashscope:
                        self._dashscope.update_api_key(ds_key)
                        if hasattr(self._dashscope, "update_base_url"):
                            self._dashscope.update_base_url(ds_base or None)
                    else:
                        self._dashscope = EcomClient(ds_key, base_url=ds_base or None)
            if ark_endpoint_keys & body.updates.keys():
                ark_key, ark_base = self._resolve_relay_endpoint(
                    "ark_api_key",
                    "ark_relay_endpoint",
                    "ark_relay_fallback_policy",
                    required_capability="video",
                    await_config=saved_full,
                )
                if ark_key:
                    from ecom_video_client import EcomVideoClient
                    if self._ark:
                        self._ark.update_api_key(ark_key)
                        self._ark.update_base_url(ark_base or None)
                    else:
                        self._ark = EcomVideoClient(ark_key, base_url=ark_base or None)
            return {"ok": True}

        # --- Storage management (mirrors seedance-video for UI parity) ---

        @router.get("/storage/stats")
        async def storage_stats() -> dict:
            cfg = await self._tm.get_all_config()
            stats: dict[str, dict] = {}
            for key, default in [
                ("image_output_dir", str(self._data_dir / "images")),
                ("video_output_dir", str(self._data_dir / "videos")),
                ("assets", str(self._data_dir / "assets")),
            ]:
                d = Path(cfg.get(key) or default)
                total_bytes = 0
                file_count = 0
                try:
                    if d.is_dir():
                        for f in d.rglob("*"):
                            if file_count > 20000:
                                break
                            if f.is_file():
                                try:
                                    total_bytes += f.stat().st_size
                                    file_count += 1
                                except OSError:
                                    continue
                except OSError:
                    pass
                stats[key] = {
                    "path": str(d),
                    "size_bytes": total_bytes,
                    "size_mb": round(total_bytes / 1048576, 1),
                    "file_count": file_count,
                }
            return {"ok": True, "stats": stats}

        @router.post("/storage/open-folder")
        async def open_folder(body: dict) -> dict:
            raw_path = (body.get("path") or "").strip()
            key = (body.get("key") or "").strip()
            if not raw_path and not key:
                raise HTTPException(status_code=400, detail="Missing path or key")
            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                defaults = {
                    "image_output_dir": self._data_dir / "images",
                    "video_output_dir": self._data_dir / "videos",
                    "assets": self._data_dir / "assets",
                }
                if key not in defaults:
                    raise HTTPException(status_code=400, detail=f"Unknown key: {key}")
                cfg = await self._tm.get_all_config()
                cfg_val = (cfg.get(key) or "").strip()
                target = Path(cfg_val).expanduser() if cfg_val else defaults[key]
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Cannot create folder: {exc}") from exc
            import subprocess, sys
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except (OSError, FileNotFoundError) as exc:
                raise HTTPException(status_code=500, detail=f"Cannot open folder: {exc}") from exc
            return {"ok": True, "path": str(target)}

        @router.get("/storage/list-dir")
        async def list_dir(path: str = "") -> dict:
            import sys as _sys
            raw = (path or "").strip()
            if not raw:
                anchors: list[dict] = []
                home = Path.home()
                anchors.append({"name": "Home", "path": str(home), "is_dir": True, "kind": "home"})
                for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos"):
                    p = home / sub
                    if p.is_dir():
                        anchors.append({"name": sub, "path": str(p), "is_dir": True, "kind": "shortcut"})
                if _sys.platform == "win32":
                    import string
                    for letter in string.ascii_uppercase:
                        drv = Path(f"{letter}:/")
                        try:
                            if drv.exists():
                                anchors.append({"name": f"{letter}:", "path": str(drv), "is_dir": True, "kind": "drive"})
                        except OSError:
                            continue
                else:
                    anchors.append({"name": "/", "path": "/", "is_dir": True, "kind": "drive"})
                return {"ok": True, "path": "", "parent": None, "items": anchors, "is_anchor": True}
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
            return {"ok": True, "path": str(target), "parent": parent_path, "items": items, "is_anchor": False}

        @router.post("/storage/mkdir")
        async def make_dir(body: dict) -> dict:
            parent = (body.get("parent") or "").strip()
            name = (body.get("name") or "").strip()
            if not parent or not name:
                raise HTTPException(status_code=400, detail="Missing parent or name")
            if "/" in name or "\\" in name or name in (".", ".."):
                raise HTTPException(status_code=400, detail="Invalid folder name")
            try:
                parent_path = Path(parent).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not parent_path.is_dir():
                raise HTTPException(status_code=400, detail="Parent is not a directory")
            new_dir = parent_path / name
            try:
                new_dir.mkdir(parents=False, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"ok": True, "path": str(new_dir)}

        @router.post("/prompt-optimize")
        async def optimize_prompt(body: PromptOptimizeBody) -> dict:
            brain = self._get_brain()
            if not brain:
                raise HTTPException(400, "Brain 不可用，请在主设置中配置 LLM")

            feature = self._registry.get(body.feature_id) if body.feature_id else None
            field_id = body.field_id or "prompt"

            if feature and field_id == "prompt" and feature.execution_mode == "agent":
                from ecom_execution import AgentStrategy, split_params
                text_params, _ = split_params(feature, body.params or {})
                if not text_params.get("prompt") and body.prompt:
                    text_params["prompt"] = body.prompt

                is_video = feature.api_provider == "ark"
                user_msg = AgentStrategy._build_user_msg(
                    text_params, feature, is_video=is_video, params=body.params,
                )
                cfg = feature.execution_config or {}
                system_prompt = cfg.get("agent_system_prompt", "")
                if is_video and not system_prompt:
                    from ecom_prompt_optimizer import VIDEO_OPTIMIZE_SYSTEM_PROMPT
                    system_prompt = VIDEO_OPTIMIZE_SYSTEM_PROMPT
                if not system_prompt:
                    system_prompt = "你是电商内容创意专家，请优化以下提示词使其更适合 AI 图像生成。直接输出优化后的提示词，不要解释。"

                result = await self._call_brain(brain, user_msg, system_prompt, max_tokens=8192)
                return {"ok": True, "optimized": result, "field_id": field_id, "kind": body.kind}

            if feature and field_id == "prompt":
                kind = "video" if feature.api_provider == "ark" else "image"
            else:
                kind = body.kind

            text_to_optimize = body.prompt or (body.params or {}).get(field_id, "")
            if not text_to_optimize or not text_to_optimize.strip():
                raise HTTPException(400, "没有可优化的文本")

            if field_id != "prompt":
                context_name = (body.params or {}).get("product_name", "")
                system = _FIELD_OPTIMIZE_SYSTEMS.get(field_id, _FIELD_OPTIMIZE_SYSTEMS["_default"])
                user_msg = text_to_optimize
                if context_name:
                    user_msg = f"产品: {context_name}\n\n{text_to_optimize}"
                result = await self._call_brain(brain, user_msg, system)
                return {"ok": True, "optimized": result, "field_id": field_id, "kind": kind}

            if kind == "video":
                from ecom_prompt_optimizer import optimize_video_prompt
                result = await optimize_video_prompt(
                    brain, text_to_optimize,
                    mode=body.mode, duration=body.duration, ratio=body.ratio,
                    asset_summary=body.asset_summary, level=body.level,
                )
            else:
                from ecom_prompt_optimizer import optimize_prompt as do_optimize
                result = await do_optimize(
                    brain, text_to_optimize,
                    level=body.level, category=body.category, style=body.style,
                )
            return {"ok": True, "optimized": result, "field_id": field_id, "kind": kind}

        @router.get("/prompt-guide")
        async def prompt_guide(kind: str = "video") -> dict:
            from ecom_prompt_optimizer import get_prompt_guide
            return {"ok": True, "kind": kind, **get_prompt_guide(kind)}

        @router.get("/prompt-templates")
        async def prompt_templates(kind: str = "video") -> dict:
            from ecom_prompt_optimizer import get_prompt_templates
            return {"ok": True, "kind": kind, "templates": get_prompt_templates(kind)}

        @router.get("/models")
        async def list_models() -> dict:
            from ecom_models import get_all_models
            return {"ok": True, "models": get_all_models()}

    def _register_custom_routes(self, router: APIRouter) -> None:
        """Escape hatch for features that don't fit the declarative framework."""
        pass
