"""DashScope HTTP client for image generation — standalone implementation.

Does NOT import from tongyi-image plugin. Supports multimodal generation,
background generation, and async task polling.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

EP_MULTIMODAL = "/services/aigc/multimodal-generation/generation"
EP_IMAGE_GEN = "/services/aigc/image-generation/generation"
EP_BG_GEN = "/services/aigc/background-generation/generation/"
EP_IMAGE_SYNTH = "/services/aigc/image2image/image-synthesis"


class EcomClientError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 0):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")


class EcomClient:
    """Thin async wrapper around DashScope image APIs for e-commerce use."""

    def __init__(self, api_key: str, base_url: str = DASHSCOPE_BASE_URL, timeout: float = 120):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
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
        logger.debug("DashScope POST %s model=%s async=%s", endpoint, body.get("model"), async_mode)
        resp = await self._client.post(endpoint, json=body, headers=headers)
        data = resp.json()
        if resp.status_code >= 400:
            code = data.get("code", str(resp.status_code))
            msg = data.get("message", resp.text)
            logger.error("DashScope HTTP %s: [%s] %s", resp.status_code, code, msg)
            raise EcomClientError(code, msg, resp.status_code)
        if data.get("code"):
            code = data["code"]
            msg = data.get("message", "")
            logger.error("DashScope API error: [%s] %s", code, msg)
            raise EcomClientError(code, msg, resp.status_code)
        return data

    async def _get(self, url: str) -> dict:
        resp = await self._client.get(url)
        return resp.json()

    # ── High-level API ──

    async def generate(
        self,
        model: str,
        prompt: str,
        images: dict | None = None,
        capability: str = "multimodal",
        size: str = "",
        n: int = 1,
        negative_prompt: str = "",
        **kwargs: Any,
    ) -> dict:
        """Unified generate — routes by model family (mirrors tongyi-image plugin).

        Routes:
          - capability=background        → EP_BG_GEN  async
          - wan2.x models                → EP_IMAGE_GEN  async  (DashScope native format)
          - qwen-image / wanx / others   → EP_MULTIMODAL sync   (OpenAI-compat format)

        Returns {"task_id": str, "image_urls": list[str], "status": str}.
        """
        if capability == "background":
            return await self._generate_background(model, prompt, images, **kwargs)

        normalized_size = self._normalize_size(model, size)
        is_4k = normalized_size == "4K" or "4096" in normalized_size
        if images and is_4k:
            normalized_size = "2K"

        m = (model or "").lower()
        use_async = m.startswith("wan2.")

        if use_async:
            content = self._build_content_native(prompt, images)
        else:
            content = self._build_content_openai(prompt, None)

        body: dict[str, Any] = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {},
        }
        if normalized_size:
            body["parameters"]["size"] = normalized_size
        body["parameters"]["n"] = n
        if negative_prompt:
            body["parameters"]["negative_prompt"] = negative_prompt

        if use_async:
            result = await self._post(EP_IMAGE_GEN, body, async_mode=True)
        else:
            result = await self._post(EP_MULTIMODAL, body, async_mode=False)

        output = result.get("output", {})
        task_id = output.get("task_id", "")
        status = output.get("task_status", "") or ""
        image_urls: list[str] = []

        if task_id:
            status = status or "PENDING"
        else:
            image_urls = self._extract_image_urls(output)
            status = "SUCCEEDED" if image_urls else (status or "FAILED")

        return {"task_id": task_id, "image_urls": image_urls, "status": status}

    @staticmethod
    def _normalize_size(model: str, size: str) -> str:
        """Normalize size for DashScope models.

        wan2.7 models accept both K shorthand (1K/2K/4K) and pixel
        dimensions (WxH) natively — pass through as-is.
        qwen/wanx legacy models need K→pixel conversion.
        """
        if not size:
            return ""
        m = (model or "").lower()
        if m.startswith("wan2."):
            return size
        if m.startswith("qwen-image-"):
            mapping = {"1K": "1024*1024", "2K": "2048*2048", "4K": "2048*2048"}
            return mapping.get(size, size)
        if m.startswith("wanx"):
            mapping = {"1K": "1024*1024", "2K": "1280*1280", "4K": "1280*1280"}
            return mapping.get(size, size)
        return size

    async def _generate_background(
        self, model: str, prompt: str, images: dict | None = None, **kwargs: Any,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model or "wanx-background-generation-v2",
            "input": {"base_image_url": ""},
            "parameters": {},
        }
        if prompt:
            body["input"]["ref_prompt"] = prompt
        if images:
            first = next(iter(images.values()), {})
            if first.get("base64"):
                body["input"]["base_image_url"] = f"data:image/png;base64,{first['base64']}"

        result = await self._post(EP_BG_GEN, body, async_mode=True)
        output = result.get("output", {})
        return {
            "task_id": output.get("task_id", ""),
            "image_urls": [],
            "status": output.get("task_status", "PENDING"),
        }

    async def get_task_result(self, task_id: str) -> dict:
        """Poll an async task. Returns {"status": str, "image_urls": list, "error": str}."""
        url = f"/tasks/{task_id}"
        data = await self._get(url)
        output = data.get("output", {})
        status = output.get("task_status", "UNKNOWN")
        image_urls: list[str] = []
        error = ""

        if status == "SUCCEEDED":
            image_urls = self._extract_image_urls(output)
        elif status == "FAILED":
            error = output.get("message", "") or data.get("message", "Unknown error")

        return {"status": status, "image_urls": image_urls, "error": error}

    # ── Helpers ──

    @staticmethod
    def _build_content_native(prompt: str, images: dict | None) -> list[dict]:
        """Build DashScope-native content array for wan2.x models.

        Format: {"image": url} and {"text": str}.
        """
        content: list[dict] = []
        if images:
            for key, asset in images.items():
                if isinstance(asset, list):
                    for item in asset:
                        if item.get("base64"):
                            content.append({"image": f"data:image/png;base64,{item['base64']}"})
                elif isinstance(asset, dict) and asset.get("base64"):
                    content.append({"image": f"data:image/png;base64,{asset['base64']}"})
        content.append({"text": prompt})
        return content

    @staticmethod
    def _build_content_openai(prompt: str, images: dict | None) -> list[dict]:
        """Build OpenAI-compatible content array for qwen-image / wanx models.

        Format: {"type": "image_url", "image_url": {"url": ...}} and {"type": "text", "text": ...}.
        """
        content: list[dict] = []
        if images:
            for key, asset in images.items():
                if isinstance(asset, list):
                    for item in asset:
                        if item.get("base64"):
                            url = f"data:image/png;base64,{item['base64']}"
                            content.append({"type": "image_url", "image_url": {"url": url}})
                elif isinstance(asset, dict) and asset.get("base64"):
                    url = f"data:image/png;base64,{asset['base64']}"
                    content.append({"type": "image_url", "image_url": {"url": url}})
        content.append({"type": "text", "text": prompt})
        return content

    @staticmethod
    def _extract_image_urls(output: dict) -> list[str]:
        """Extract image URLs from any DashScope response format.

        Supports:
          - Format A (multimodal sync, wan2.7/qwen): output.choices[].message.content[]
            with item.image_url.url / item.image / item.image_url(string)
          - Format B (async task result):           output.results[].url / .b64_image
          - Format C (legacy multimodal content):   output.content[].image_url.url
        """
        urls: list[str] = []

        for choice in output.get("choices", []) or []:
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message", {}) or {}
            for item in msg.get("content", []) or []:
                if not isinstance(item, dict):
                    continue
                url = ""
                img = item.get("image_url")
                if isinstance(img, dict):
                    url = img.get("url", "")
                elif isinstance(img, str):
                    url = img
                if not url:
                    url = item.get("image", "") or ""
                if url and isinstance(url, str) and url.startswith("http"):
                    urls.append(url)

        for r in output.get("results", []) or []:
            if isinstance(r, dict):
                url = (
                    r.get("url")
                    or r.get("image_url")
                    or r.get("image")
                    or r.get("orig_url")
                    or r.get("b64_image")
                    or ""
                )
                if url:
                    urls.append(url)
            elif isinstance(r, str) and r.startswith("http"):
                urls.append(r)

        if not urls:
            content = output.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url:
                            urls.append(url)

        return urls
