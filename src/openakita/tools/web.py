"""
Web tool — HTTP requests.
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
    """HTTP response."""

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
    """Search result."""

    title: str
    url: str
    snippet: str = ""


class WebTool:
    """Web tool — HTTP requests."""

    def __init__(
        self,
        timeout: int = 30,
        user_agent: str = "OpenAkita/1.0",
    ):
        self.timeout = timeout
        self.user_agent = user_agent
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get the HTTP client."""
        if self._client is None:
            from ..llm.providers.proxy_utils import get_httpx_client_kwargs
            self._client = httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=self.timeout),
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the client."""
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
        Send a GET request.

        Args:
            url: URL
            params: Query parameters
            headers: Request headers

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
        Send a POST request.

        Args:
            url: URL
            data: Form data
            json: JSON data
            headers: Request headers

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
        Download a file.

        Args:
            url: URL
            path: Save path
            chunk_size: Chunk size

        Returns:
            Whether the download succeeded.
        """
        client = await self._get_client()

        logger.info(f"Downloading {url} to {path}")

        try:
            async with client.stream("GET", url) as resp:
                if not resp.is_success:
                    logger.error(f"Download failed: {resp.status_code}")
                    return False

                # Ensure the directory exists
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
        Search GitHub repositories.

        Args:
            query: Search query
            language: Programming language
            sort: Sort order
            limit: Number of results

        Returns:
            List of search results.
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
        Fetch a GitHub file's content.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path
            branch: Branch name

        Returns:
            File content or None.
        """
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

        resp = await self.get(url)

        if resp.success:
            return resp.text
        return None
