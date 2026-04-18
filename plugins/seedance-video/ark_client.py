"""Volcengine Ark API client for Seedance video generation."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class ArkClient:
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
        content: list[dict[str, Any]],
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
        tools: list[dict] | None = None,
        service_tier: str = "default",
        callback_url: str | None = None,
        execution_expires_after: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "content": content,
        }
        if ratio:
            body["ratio"] = ratio
        if duration:
            body["duration"] = int(duration)
        if resolution:
            body["resolution"] = resolution
        if n and n > 1:
            body["n"] = n
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if watermark:
            body["watermark"] = watermark
        if seed >= 0:
            body["seed"] = seed
        if camera_fixed:
            body["camera_fixed"] = True
        if draft:
            body["draft"] = True
        if return_last_frame:
            body["return_last_frame"] = True
        if tools:
            body["tools"] = tools
        if service_tier != "default":
            body["service_tier"] = service_tier
        if callback_url:
            body["callback_url"] = callback_url
        if execution_expires_after:
            body["execution_expires_after"] = {"seconds": execution_expires_after}

        resp = await self._client.post("/contents/generations/tasks", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_task(self, task_id: str) -> dict:
        resp = await self._client.get(f"/contents/generations/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def list_tasks(
        self,
        page_num: int = 1,
        page_size: int = 20,
        filter_status: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "page_num": page_num,
            "page_size": page_size,
        }
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
