"""SearXNG provider — 自部署元搜索引擎.

API: ``GET {base_url}/search?q=...&format=json``
Docs: https://docs.searxng.org/

Auto-detect priority: 30. 适合不想依赖外部 SaaS 的用户；自己部署一个
SearXNG（Docker 一行命令），完全免费、可调聚合源。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from ....config import settings
from ..base import (
    MissingCredentialError,
    NetworkUnreachableError,
    SearchResult,
)
from ..registry import register

logger = logging.getLogger(__name__)


class SearXNGProvider:
    id = "searxng"
    label = "SearXNG (自部署)"
    requires_credential = True  # base_url is a "credential" semantically
    auto_detect_order = 30
    signup_url = "https://docs.searxng.org/admin/installation-docker.html"
    docs_url = "https://docs.searxng.org/"

    def is_available(self) -> bool:
        return bool((settings.searxng_base_url or "").strip())

    def _endpoint(self) -> str:
        base = (settings.searxng_base_url or "").strip().rstrip("/")
        return urljoin(base + "/", "search")

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        if not self.is_available():
            raise MissingCredentialError(
                "SEARXNG_BASE_URL not configured", provider_id=self.id
            )

        params = {
            "q": query,
            "format": "json",
            "safesearch": _safesearch_to_int(safesearch),
            "language": _region_to_lang(region),
        }
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(self._endpoint(), params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise NetworkUnreachableError(
                f"searxng transport failure: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachableError(
                f"searxng HTTP error: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc

        if resp.status_code >= 400:
            raise NetworkUnreachableError(
                f"searxng HTTP {resp.status_code}: {resp.text[:200]}",
                provider_id=self.id,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise NetworkUnreachableError(
                "searxng returned non-JSON (check that the instance has format=json enabled)",
                provider_id=self.id,
            ) from exc

        results = data.get("results") or []
        out: list[SearchResult] = []
        for item in results[:max_results]:
            out.append(
                SearchResult(
                    title=str(item.get("title") or "无标题"),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                    source=str(item.get("engine") or ""),
                    date=str(item.get("publishedDate") or ""),
                )
            )
        return out

    async def news_search(self, *args: Any, **kwargs: Any) -> list[SearchResult] | None:
        # SearXNG supports category=news but per-instance config varies.
        # Return None to defer to the next provider during auto-detect.
        return None


def _safesearch_to_int(s: str) -> int:
    # SearXNG accepts 0/1/2 (off/moderate/strict)
    return {"off": 0, "moderate": 1, "strict": 2}.get(s, 1)


def _region_to_lang(region: str) -> str:
    # ddgs uses "wt-wt" / "cn-zh" / "us-en"; SearXNG uses "all"/"zh"/"en"
    if not region or region in ("wt-wt", ""):
        return "all"
    if region.startswith("cn") or region.endswith("zh"):
        return "zh"
    if region.startswith("us") or region.endswith("en"):
        return "en"
    return "all"


register(SearXNGProvider())
