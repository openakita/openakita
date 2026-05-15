"""Tavily provider — 海外推荐.

API: ``POST https://api.tavily.com/search``
Docs: https://docs.tavily.com/

Auto-detect priority: 20.
"""

from __future__ import annotations

import logging
from typing import Any

from ....config import settings
from ..base import (
    AuthFailedError,
    MissingCredentialError,
    NetworkUnreachableError,
    RateLimitedError,
    SearchResult,
)
from ..registry import register

logger = logging.getLogger(__name__)


class TavilyProvider:
    id = "tavily"
    label = "Tavily"
    requires_credential = True
    auto_detect_order = 20
    signup_url = "https://app.tavily.com/home"
    docs_url = "https://docs.tavily.com/"

    _ENDPOINT = "https://api.tavily.com/search"

    def is_available(self) -> bool:
        return bool((settings.tavily_api_key or "").strip())

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        api_key = (settings.tavily_api_key or "").strip()
        if not api_key:
            raise MissingCredentialError(
                "TAVILY_API_KEY not configured", provider_id=self.id
            )

        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": min(max(1, max_results), 20),
            "include_answer": False,
        }
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._ENDPOINT, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise NetworkUnreachableError(
                f"tavily transport failure: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachableError(
                f"tavily HTTP error: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthFailedError(
                f"tavily rejected credential (HTTP {resp.status_code})",
                provider_id=self.id,
            )
        if resp.status_code == 429:
            raise RateLimitedError("tavily rate-limited (HTTP 429)", provider_id=self.id)
        if resp.status_code >= 400:
            raise NetworkUnreachableError(
                f"tavily HTTP {resp.status_code}: {resp.text[:200]}",
                provider_id=self.id,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise NetworkUnreachableError(
                "tavily returned non-JSON response", provider_id=self.id,
            ) from exc

        results = data.get("results") or []
        out: list[SearchResult] = []
        for item in results:
            out.append(
                SearchResult(
                    title=str(item.get("title") or "无标题"),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                    source=str(item.get("source") or ""),
                )
            )
        return out

    async def news_search(self, *args: Any, **kwargs: Any) -> list[SearchResult] | None:
        # Tavily 没有独立的 news 端点；返回 None 让 runtime 换下一家
        return None


register(TavilyProvider())
