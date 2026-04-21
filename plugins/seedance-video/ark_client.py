"""Volcengine Ark API client for Seedance video generation.

Uses ``BaseVendorClient`` from the SDK for automatic retry, timeout,
error classification (``ERROR_KIND_*``), and content-moderation body
detection.  All HTTP calls go through ``self.request()`` /
``self.post_json()`` / ``self.get_json()`` which create a fresh
``httpx.AsyncClient`` per call — no long-lived connection to manage.
"""

from __future__ import annotations

import logging
from typing import Any

from openakita_plugin_sdk.contrib import BaseVendorClient, VendorError

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class ArkClient(BaseVendorClient):
    def __init__(self, api_key: str) -> None:
        super().__init__(base_url=ARK_BASE_URL, timeout=60.0)
        self._api_key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running Ark task via DELETE."""
        await self.request("DELETE", f"/contents/generations/tasks/{task_id}")
        return True

    async def close(self) -> None:
        """No-op — BaseVendorClient creates per-call httpx clients."""

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

        return await self.post_json(
            "/contents/generations/tasks", json_body=body, timeout=120.0,
        )

    async def get_task(self, task_id: str) -> dict:
        return await self.get_json(f"/contents/generations/tasks/{task_id}")

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
        return await self.get_json(
            "/contents/generations/tasks", params=params,
        )

    async def delete_task(self, task_id: str) -> dict:
        return await self.request(
            "DELETE", f"/contents/generations/tasks/{task_id}",
        )

    async def validate_key(self) -> bool:
        """Quick validation by listing one task."""
        try:
            await self.list_tasks(page_size=1)
            return True
        except VendorError as exc:
            logger.warning("Ark key validation failed: %s (kind=%s)", exc, exc.kind)
            return False
