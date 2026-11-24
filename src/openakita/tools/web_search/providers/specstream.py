"""SpecStream web search provider.

API: ``POST https://api.specstream.ai/v1/web-search``
Docs: https://specstream.ai/docs

Auto-detect priority: 25.
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
from ._http import describe_httpx_failure, search_httpx_client_kwargs

logger = logging.getLogger(__name__)


class SpecStreamProvider:
    id = "specstream"
    label = "SpecStream"
    requires_credential = True
    auto_detect_order = 25
    signup_url = "https://specstream.ai"
    docs_url = "https://specstream.ai/docs"

    _ENDPOINT = "https://api.specstream.ai/v1/web-search"

    def is_available(self) -> bool:
        return bool((settings.specstream_api_key or "").strip())

    async def _query(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_seconds: float = 0.0,
        news: bool = False,
    ) -> list[SearchResult]:
        api_key = (settings.specstream_api_key or "").strip()
        if not api_key:
            raise MissingCredentialError("SPECSTREAM_API_KEY not configured", provider_id=self.id)

        payload: dict[str, Any] = {
            "q": query,
            "num": min(max(1, max_results), 20),
            "passages": True,
        }
        if news:
            payload["news"] = True

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        import httpx

        try:
            async with httpx.AsyncClient(
                **search_httpx_client_kwargs(timeout=timeout, target_url=self._ENDPOINT)
            ) as client:
                resp = await client.post(self._ENDPOINT, headers=headers, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise NetworkUnreachableError(
                f"specstream transport failure: {describe_httpx_failure(exc)}",
                provider_id=self.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachableError(
                f"specstream HTTP error: {describe_httpx_failure(exc)}",
                provider_id=self.id,
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthFailedError(
                f"specstream rejected credential (HTTP {resp.status_code})",
                provider_id=self.id,
            )
        if resp.status_code == 429:
            raise RateLimitedError("specstream rate-limited (HTTP 429)", provider_id=self.id)
        if resp.status_code >= 400:
            raise NetworkUnreachableError(
                f"specstream HTTP {resp.status_code}: {resp.text[:200]}",
                provider_id=self.id,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise NetworkUnreachableError(
                "specstream returned non-JSON response",
                provider_id=self.id,
            ) from exc

        sources = data.get("sources") if isinstance(data, dict) else []
        out: list[SearchResult] = []
        for item in sources or []:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("excerpt") or item.get("snippet") or "")
            out.append(
                SearchResult(
                    title=str(item.get("title") or "Untitled"),
                    url=str(item.get("url") or ""),
                    snippet=snippet,
                    source=str(item.get("source") or ""),
                )
            )
        return out

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        return await self._query(
            query,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
            news=False,
        )

    async def news_search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult] | None:
        return await self._query(
            query,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
            news=True,
        )


register(SpecStreamProvider())
