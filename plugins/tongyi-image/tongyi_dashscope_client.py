"""DashScope HTTP API client for all image generation capabilities."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

EP_MULTIMODAL = "/services/aigc/multimodal-generation/generation"
EP_IMAGE_GEN = "/services/aigc/image-generation/generation"
EP_BG_GEN = "/services/aigc/background-generation/generation/"
EP_OUTPAINT = "/services/aigc/image2image/out-painting"
EP_IMAGE_SYNTH = "/services/aigc/image2image/image-synthesis"


class DashScopeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 0):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")


class DashScopeClient:
    """Thin async wrapper around DashScope image APIs."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 120,
    ):
        self._api_key = api_key
        self._base_url = (base_url or DASHSCOPE_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=15),
            headers=self._make_headers(),
        )

    def _make_headers(self, async_mode: bool = False) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return h

    def update_api_key(self, key: str) -> None:
        self._api_key = key
        self._client.headers.update({"Authorization": f"Bearer {key}"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, endpoint: str, body: dict, async_mode: bool = False) -> dict:
        headers = {"X-DashScope-Async": "enable"} if async_mode else {}
        resp = await self._client.post(endpoint, json=body, headers=headers)
        data = resp.json()
        if resp.status_code != 200:
            code = data.get("code", str(resp.status_code))
            msg = data.get("message", resp.text)
            raise DashScopeError(code, msg, resp.status_code)
        if data.get("code"):
            raise DashScopeError(data["code"], data.get("message", ""))
        return data

    # ------------------------------------------------------------------
    # Endpoint A: Multimodal Generation (sync) — wan2.7/qwen text2img + edit
    # ------------------------------------------------------------------
    async def generate_image(
        self,
        model: str,
        messages: list[dict],
        *,
        size: str | None = None,
        n: int = 1,
        negative_prompt: str | None = None,
        prompt_extend: bool | None = None,
        watermark: bool = False,
        seed: int | None = None,
        thinking_mode: bool | None = None,
        enable_sequential: bool | None = None,
        color_palette: list[dict] | None = None,
        bbox_list: list | None = None,
    ) -> dict:
        params: dict[str, Any] = {"n": n, "watermark": watermark}
        if size:
            params["size"] = size
        if negative_prompt:
            params["negative_prompt"] = negative_prompt
        if prompt_extend is not None:
            params["prompt_extend"] = prompt_extend
        if seed is not None:
            params["seed"] = seed
        if thinking_mode is not None:
            params["thinking_mode"] = thinking_mode
        if enable_sequential is not None:
            params["enable_sequential"] = enable_sequential
        if color_palette:
            params["color_palette"] = color_palette
        if bbox_list:
            params["bbox_list"] = bbox_list

        body = {
            "model": model,
            "input": {"messages": messages},
            "parameters": params,
        }
        return await self._post(EP_MULTIMODAL, body)

    # ------------------------------------------------------------------
    # Endpoint B: Image Generation (async) — wan2.7 async + style repaint
    # ------------------------------------------------------------------
    async def generate_image_async(
        self,
        model: str,
        messages: list[dict],
        *,
        size: str | None = None,
        n: int = 1,
        watermark: bool = False,
        thinking_mode: bool | None = None,
        enable_sequential: bool | None = None,
        seed: int | None = None,
        color_palette: list[dict] | None = None,
        bbox_list: list | None = None,
    ) -> dict:
        params: dict[str, Any] = {"n": n, "watermark": watermark}
        if size:
            params["size"] = size
        if thinking_mode is not None:
            params["thinking_mode"] = thinking_mode
        if enable_sequential is not None:
            params["enable_sequential"] = enable_sequential
        if seed is not None:
            params["seed"] = seed
        if color_palette:
            params["color_palette"] = color_palette
        if bbox_list:
            params["bbox_list"] = bbox_list

        body = {
            "model": model,
            "input": {"messages": messages},
            "parameters": params,
        }
        return await self._post(EP_IMAGE_GEN, body, async_mode=True)

    # ------------------------------------------------------------------
    # Style Repaint (async via Endpoint B)
    # ------------------------------------------------------------------
    async def style_repaint(
        self,
        image_url: str,
        style_index: int = 0,
        style_ref_url: str | None = None,
    ) -> dict:
        inp: dict[str, Any] = {"image_url": image_url, "style_index": style_index}
        if style_ref_url and style_index == -1:
            inp["style_ref_url"] = style_ref_url
        body = {"model": "wanx-style-repaint-v1", "input": inp}
        return await self._post(EP_IMAGE_GEN, body, async_mode=True)

    # ------------------------------------------------------------------
    # Endpoint C: Background Generation (async)
    # ------------------------------------------------------------------
    async def generate_background(
        self,
        base_image_url: str,
        *,
        ref_prompt: str | None = None,
        ref_image_url: str | None = None,
        n: int = 1,
        noise_level: int = 300,
        ref_prompt_weight: float = 0.5,
        model_version: str = "v3",
        foreground_edge: list[str] | None = None,
        background_edge: list[str] | None = None,
        foreground_edge_prompt: list[str] | None = None,
        background_edge_prompt: list[str] | None = None,
    ) -> dict:
        inp: dict[str, Any] = {"base_image_url": base_image_url}
        if ref_prompt:
            inp["ref_prompt"] = ref_prompt
        if ref_image_url:
            inp["ref_image_url"] = ref_image_url
        if foreground_edge or background_edge:
            edge: dict[str, Any] = {}
            if foreground_edge:
                edge["foreground_edge"] = foreground_edge
            if background_edge:
                edge["background_edge"] = background_edge
            if foreground_edge_prompt:
                edge["foreground_edge_prompt"] = foreground_edge_prompt
            if background_edge_prompt:
                edge["background_edge_prompt"] = background_edge_prompt
            inp["reference_edge"] = edge

        params: dict[str, Any] = {
            "model_version": model_version,
            "n": n,
        }
        if ref_image_url:
            params["noise_level"] = noise_level
        if ref_prompt and ref_image_url:
            params["ref_prompt_weight"] = ref_prompt_weight

        body = {
            "model": "wanx-background-generation-v2",
            "input": inp,
            "parameters": params,
        }
        return await self._post(EP_BG_GEN, body, async_mode=True)

    # ------------------------------------------------------------------
    # Endpoint D: Outpainting (async)
    # ------------------------------------------------------------------
    async def outpaint(
        self,
        image_url: str,
        *,
        x_scale: float | None = None,
        y_scale: float | None = None,
        output_ratio: str | None = None,
        angle: int = 0,
        left_offset: int | None = None,
        right_offset: int | None = None,
        top_offset: int | None = None,
        bottom_offset: int | None = None,
        best_quality: bool = False,
        limit_image_size: bool = True,
    ) -> dict:
        params: dict[str, Any] = {}
        if x_scale is not None:
            params["x_scale"] = x_scale
        if y_scale is not None:
            params["y_scale"] = y_scale
        if output_ratio:
            params["output_ratio"] = output_ratio
        if angle:
            params["angle"] = angle
        if left_offset is not None:
            params["left_offset"] = left_offset
        if right_offset is not None:
            params["right_offset"] = right_offset
        if top_offset is not None:
            params["top_offset"] = top_offset
        if bottom_offset is not None:
            params["bottom_offset"] = bottom_offset
        params["best_quality"] = best_quality
        params["limit_image_size"] = limit_image_size

        body = {
            "model": "image-out-painting",
            "input": {"image_url": image_url},
            "parameters": params,
        }
        return await self._post(EP_OUTPAINT, body, async_mode=True)

    # ------------------------------------------------------------------
    # Endpoint E: Sketch-to-Image / wan2.5 edit (async)
    # ------------------------------------------------------------------
    async def sketch_to_image(
        self,
        sketch_image_url: str,
        prompt: str,
        *,
        style: str = "<watercolor>",
        size: str = "768*768",
        n: int = 1,
        sketch_weight: int = 3,
    ) -> dict:
        body = {
            "model": "wanx-sketch-to-image-lite",
            "input": {
                "sketch_image_url": sketch_image_url,
                "prompt": prompt,
            },
            "parameters": {
                "size": size,
                "n": n,
                "sketch_weight": sketch_weight,
                "style": style,
            },
        }
        return await self._post(EP_IMAGE_SYNTH, body, async_mode=True)

    async def wan25_edit(
        self,
        prompt: str,
        images: list[str],
        *,
        n: int = 1,
        size: str | None = None,
        negative_prompt: str | None = None,
        prompt_extend: bool = True,
        watermark: bool = False,
        seed: int | None = None,
    ) -> dict:
        inp: dict[str, Any] = {"prompt": prompt, "images": images}
        params: dict[str, Any] = {"n": n, "prompt_extend": prompt_extend, "watermark": watermark}
        if size:
            params["size"] = size
        if negative_prompt:
            params["negative_prompt"] = negative_prompt
        if seed is not None:
            params["seed"] = seed
        body = {
            "model": "wan2.5-i2i-preview",
            "input": inp,
            "parameters": params,
        }
        return await self._post(EP_IMAGE_SYNTH, body, async_mode=True)

    # ------------------------------------------------------------------
    # Task query (all async endpoints)
    # ------------------------------------------------------------------
    async def get_task(self, task_id: str) -> dict:
        resp = await self._client.get(f"/tasks/{task_id}")
        data = resp.json()
        if resp.status_code != 200:
            code = data.get("code", str(resp.status_code))
            raise DashScopeError(code, data.get("message", ""), resp.status_code)
        return data

    # ------------------------------------------------------------------
    # Key validation
    # ------------------------------------------------------------------
    async def validate_key(self) -> bool:
        try:
            resp = await self._client.get("/tasks/non-existent-id-for-validation")
            return resp.status_code in (200, 400, 404)
        except Exception:
            return False

