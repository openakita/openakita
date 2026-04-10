"""
Web 工具 - 网络请求
"""

import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Response:
    """HTTP 响应"""

    status_code: int
    headers: dict = field(default_factory=dict)
    text: str = ""
    json_data: Any = None

    @property
    def success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self.json_data


@dataclass
class SearchResult:
    """搜索结果"""

    title: str
    url: str
    snippet: str = ""


class WebTool:
    """Web 工具 - 网络请求"""

    def __init__(
        self,
        timeout: int = 30,
        user_agent: str = "OpenAkita/1.0",
    ):
        self.timeout = timeout
        self.user_agent = user_agent
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None:
            from ..llm.providers.proxy_utils import get_httpx_client_kwargs
            self._client = httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=self.timeout),
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Response:
        """
        发送 GET 请求

        Args:
            url: URL
            params: 查询参数
            headers: 请求头

        Returns:
            Response
        """
        client = await self._get_client()

        logger.info(f"GET {url}")

        try:
            resp = await client.get(url, params=params, headers=headers)

            json_data = None
            with contextlib.suppress(Exception):
                json_data = resp.json()

            return Response(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                text=resp.text,
                json_data=json_data,
            )
        except Exception as e:
            logger.error(f"GET request failed: {e}")
            return Response(
                status_code=0,
                text=str(e),
            )

    async def post(
        self,
        url: str,
        data: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> Response:
        """
        发送 POST 请求

        Args:
            url: URL
            data: 表单数据
            json: JSON 数据
            headers: 请求头

        Returns:
            Response
        """
        client = await self._get_client()

        logger.info(f"POST {url}")

        try:
            resp = await client.post(url, data=data, json=json, headers=headers)

            json_data = None
            with contextlib.suppress(Exception):
                json_data = resp.json()

            return Response(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                text=resp.text,
                json_data=json_data,
            )
        except Exception as e:
            logger.error(f"POST request failed: {e}")
            return Response(
                status_code=0,
                text=str(e),
            )

    async def download(
        self,
        url: str,
        path: str,
        chunk_size: int = 8192,
    ) -> bool:
        """
        下载文件

        Args:
            url: URL
            path: 保存路径
            chunk_size: 块大小

        Returns:
            是否成功
        """
        client = await self._get_client()

        logger.info(f"Downloading {url} to {path}")

        try:
            async with client.stream("GET", url) as resp:
                if not resp.is_success:
                    logger.error(f"Download failed: {resp.status_code}")
                    return False

                # 确保目录存在
                Path(path).parent.mkdir(parents=True, exist_ok=True)

                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        f.write(chunk)

            logger.info(f"Downloaded to {path}")
            return True

        except Exception as e:
            logger.error(f"Download failed: {e}")
            return False

    async def search_github(
        self,
        query: str,
        language: str | None = None,
        sort: str = "stars",
        limit: int = 10,
    ) -> list[SearchResult]:
        """
        搜索 GitHub 仓库

        Args:
            query: 搜索词
            language: 编程语言
            sort: 排序方式
            limit: 结果数量

        Returns:
            搜索结果列表
        """
        q = query
        if language:
            q += f" language:{language}"

        url = "https://api.github.com/search/repositories"
        params = {
            "q": q,
            "sort": sort,
            "per_page": limit,
        }

        resp = await self.get(url, params=params)

        if not resp.success or not resp.json_data:
            logger.error("GitHub search failed")
            return []

        results = []
        for item in resp.json_data.get("items", []):
            results.append(
                SearchResult(
                    title=item.get("full_name", ""),
                    url=item.get("html_url", ""),
                    snippet=item.get("description", ""),
                )
            )

        return results

    async def fetch_github_file(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str = "main",
    ) -> str | None:
        """
        获取 GitHub 文件内容

        Args:
            owner: 仓库所有者
            repo: 仓库名
            path: 文件路径
            branch: 分支

        Returns:
            文件内容或 None
        """
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

        resp = await self.get(url)

        if resp.success:
            return resp.text
        return None
