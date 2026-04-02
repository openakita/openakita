"""
AgentHubClient — 与 OpenAkita Platform Agent Store 交互的客户端

功能：
- search: 搜索平台上的 Agent
- get_detail: 获取 Agent 详情
- download: 下载 .akita-agent 包并返回本地路径
- publish: 上传本地 Agent 到平台
- rate: 为 Agent 评分
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx


from ..config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0


class AgentHubClient:
    """Agent Store HTTP 客户端"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.hub_api_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": f"OpenAkita/{self._get_version()}"}
        if settings.hub_api_key:
            headers["X-Akita-Key"] = settings.hub_api_key
        if settings.hub_device_id:
            headers["X-Akita-Device"] = settings.hub_device_id
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=DEFAULT_TIMEOUT,
                headers=self._auth_headers(),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _get_version() -> str:
        try:
            from .._bundled_version import __version__
            return __version__
        except Exception:
            return "dev"

    async def search(
        self,
        query: str = "",
        category: str = "",
        sort: str = "downloads",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        client = await self._get_client()
        params: dict[str, Any] = {"page": str(page), "limit": str(limit), "sort": sort}
        if query:
            params["q"] = query
        if category:
            params["category"] = category

        resp = await client.get("/agents", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_detail(self, agent_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.get(f"/agents/{agent_id}")
        resp.raise_for_status()
        return resp.json()

    async def download(self, agent_id: str, save_dir: Path | None = None) -> Path:
        """下载 Agent 包到本地，返回文件路径"""
        client = await self._get_client()
        resp = await client.get(
            f"/agents/{agent_id}/download",
            follow_redirects=True,
            timeout=DOWNLOAD_TIMEOUT,
        )
        resp.raise_for_status()

        if save_dir is None:
            save_dir = settings.project_root / "data" / "agent_packages"
        save_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{agent_id}.akita-agent"
        cd = resp.headers.get("content-disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('" ')

        file_path = save_dir / filename
        file_path.write_bytes(resp.content)
        logger.info(f"Downloaded agent package: {file_path} ({len(resp.content)} bytes)")
        return file_path

    async def publish(
        self,
        package_path: Path,
        token: str,
        description: str = "",
        category: str = "general",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """上传 .akita-agent 包到平台"""
        client = await self._get_client()
        with open(package_path, "rb") as f:
            files = {"package": (package_path.name, f, "application/zip")}
            data: dict[str, str] = {"category": category}
            if description:
                data["description"] = description
            if tags:
                data["tags"] = ",".join(tags)

            resp = await client.post(
                "/agents",
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=DOWNLOAD_TIMEOUT,
            )
        resp.raise_for_status()
        return resp.json()

    async def rate(self, agent_id: str, score: int, comment: str = "", token: str = "") -> dict[str, Any]:
        client = await self._get_client()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await client.post(
            f"/agents/{agent_id}/rate",
            json={"score": score, "comment": comment},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_categories(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        resp = await client.get("/agents", params={"limit": "0"})
        resp.raise_for_status()
        data = resp.json()
        return data.get("categories", [])
