"""Volcengine Ark API client for video generation — standalone implementation.

Does NOT import from seedance-video plugin. Aligned with the official Ark
endpoint ``POST /api/v3/contents/generations/tasks`` (note the plural
``contents`` — using ``content`` returns 4xx that Ark surfaces as
"API key invalid").
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class EcomVideoClient:
    """Async client for Ark video generation APIs."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=ARK_BASE_URL,
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key
        self._client.headers["Authorization"] = f"Bearer {api_key}"

    async def create_task(
        self,
        model: str,
        prompt: str = "",
        images: dict | None = None,
        *,
        ratio: str = "16:9",
        duration: int = 5,
        resolution: str = "720p",
        n: int = 1,
        generate_audio: bool = True,
        seed: int = -1,
        watermark: bool = False,
        camera_fixed: bool = False,
        draft: bool = False,
        return_last_frame: bool = False,
        web_search: bool = False,
        tools: list[dict] | None = None,
        service_tier: str = "default",
        callback_url: str | None = None,
        execution_expires_after: int | None = None,
    ) -> dict:
        """Create a video generation task. Returns {"task_id": str, "status": str}."""
        content: list[dict[str, Any]] = []
        if images:
            for _key, asset in images.items():
                if not asset:
                    continue
                if asset.get("base64"):
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{asset['base64']}"},
                    })
                elif asset.get("url"):
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": asset["url"]},
                    })
        if prompt:
            content.append({"type": "text", "text": prompt})
        if not content:
            content.append({"type": "text", "text": ""})

        from ecom_models import get_video_model_id
        model_id = get_video_model_id(model)

        body: dict[str, Any] = {
            "model": model_id,
            "content": content,
        }
        if ratio:
            body["ratio"] = ratio
        if duration:
            body["duration"] = int(duration)
        if resolution:
            body["resolution"] = resolution
        if n and n > 1:
            body["n"] = int(n)
        body["generate_audio"] = bool(generate_audio)
        if watermark:
            body["watermark"] = True
        if seed is not None and seed >= 0:
            body["seed"] = int(seed)
        if camera_fixed:
            body["camera_fixed"] = True
        if draft:
            body["draft"] = True
        if return_last_frame:
            body["return_last_frame"] = True

        merged_tools: list[dict] = []
        if tools:
            merged_tools.extend(tools)
        if web_search and not any(
            (t.get("type") == "web_search") for t in merged_tools if isinstance(t, dict)
        ):
            merged_tools.append({"type": "web_search"})
        if merged_tools:
            body["tools"] = merged_tools

        if service_tier and service_tier != "default":
            body["service_tier"] = service_tier
        if callback_url:
            body["callback_url"] = callback_url
        if execution_expires_after:
            body["execution_expires_after"] = {"seconds": int(execution_expires_after)}

        # NOTE: ``/contents/...`` plural is the OFFICIAL endpoint.
        resp = await self._client.post("/contents/generations/tasks", json=body)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            msg = error.get("message", resp.text) if isinstance(error, dict) else str(error)
            lower = (msg or "").lower()
            if resp.status_code in (401, 403) or "api key" in lower or "ak/sk" in lower:
                raise RuntimeError(
                    "Ark API key 无效或未配置。请到「电商内容生成 → 设置 → "
                    "Ark API Key」重新填入有效的火山方舟 API Key（控制台地址："
                    "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey ）。"
                    f"原始错误：{msg}"
                )
            if "model" in lower and ("not exist" in lower or "not found" in lower or "无权" in msg):
                raise RuntimeError(
                    f"Ark 模型 '{model_id}' 不可用，请确认账号已开通该模型；"
                    f"原始错误：{msg}"
                )
            raise RuntimeError(f"Ark API error ({resp.status_code}): {msg}")

        return {
            "task_id": data.get("id", ""),
            "status": data.get("status", "running"),
        }

    async def get_task(self, task_id: str) -> dict:
        """Poll a video generation task."""
        resp = await self._client.get(f"/contents/generations/tasks/{task_id}")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data

    async def list_tasks(
        self,
        page_num: int = 1,
        page_size: int = 20,
        filter_status: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"page_num": page_num, "page_size": page_size}
        if filter_status:
            params["filter"] = f'{{"status":"{filter_status}"}}'
        resp = await self._client.get("/contents/generations/tasks", params=params)
        resp.raise_for_status()
        return resp.json()

    async def delete_task(self, task_id: str) -> dict:
        resp = await self._client.delete(f"/contents/generations/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def validate_key(self) -> bool:
        """Quick validation by listing one task."""
        try:
            await self.list_tasks(page_size=1)
            return True
        except Exception:
            return False
