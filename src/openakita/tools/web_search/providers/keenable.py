"""Keenable provider — 无 Key 可用（按 IP 限速），可选 KEENABLE_API_KEY.

API:
    - 无 Key: ``POST https://api.keenable.ai/v1/search/public``
      （需要 ``X-Keenable-Title`` 头标识调用方）
    - 有 Key: ``POST https://api.keenable.ai/v1/search`` + ``X-API-Key``
Docs: https://docs.keenable.ai/api-reference/search

Auto-detect priority: 45（排在 Jina 之后、DuckDuckGo 之前）。
"""

from __future__ import annotations

import logging
from typing import Any

from ....config import settings
from ..base import (
    AuthFailedError,
    NetworkUnreachableError,
    RateLimitedError,
    SearchResult,
)
from ..registry import register
from ._http import describe_httpx_failure, search_httpx_client_kwargs

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.keenable.ai/v1/search"
_PUBLIC_URL = _BASE_URL + "/public"
_CLIENT_TITLE = "OpenAkita"


def build_request(query: str, max_results: int, api_key: str) -> tuple[str, dict[str, str], dict]:
    """Return ``(url, headers, json_body)`` for a Keenable search call."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        url = _BASE_URL
        headers["X-API-Key"] = api_key
    else:
        url = _PUBLIC_URL
        headers["X-Keenable-Title"] = _CLIENT_TITLE
    body = {"query": query, "max_results": max(1, int(max_results))}
    return url, headers, body


def parse_results(data: Any, max_results: int) -> list[SearchResult]:
    """Map Keenable ``{"results": [{title, url, description, snippet}]}`` to SearchResult."""
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        snippet = str(item.get("description") or item.get("snippet") or "")
        out.append(
            SearchResult(
                title=str(item.get("title") or "无标题"),
                url=url,
                snippet=snippet[:500],
                source="keenable",
            )
        )
        if len(out) >= max_results:
            break
    return out


class KeenableProvider:
    id = "keenable"
    label = "Keenable"
    requires_credential = False  # 公共端点无需 Key
    auto_detect_order = 45
    signup_url = "https://keenable.ai"
    docs_url = "https://docs.keenable.ai/api-reference/search"

    def is_available(self) -> bool:
        # 公共端点无需 Key，所以始终 available
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        api_key = (getattr(settings, "keenable_api_key", "") or "").strip()
        url, headers, body = build_request(query, max_results, api_key)
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        import httpx

        try:
            async with httpx.AsyncClient(
                **search_httpx_client_kwargs(timeout=timeout, target_url=url)
            ) as client:
                resp = await client.post(url, headers=headers, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise NetworkUnreachableError(
                f"keenable transport failure: {describe_httpx_failure(exc)}",
                provider_id=self.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachableError(
                f"keenable HTTP error: {describe_httpx_failure(exc)}",
                provider_id=self.id,
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthFailedError(
                f"keenable rejected credential (HTTP {resp.status_code})",
                provider_id=self.id,
            )
        if resp.status_code == 429:
            raise RateLimitedError(
                "keenable public endpoint rate-limited (HTTP 429); "
                "configure KEENABLE_API_KEY for higher quota",
                provider_id=self.id,
            )
        if resp.status_code >= 400:
            raise NetworkUnreachableError(
                f"keenable HTTP {resp.status_code}: {resp.text[:200]}",
                provider_id=self.id,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise NetworkUnreachableError(
                "keenable returned non-JSON response",
                provider_id=self.id,
            ) from exc

        return parse_results(data, max_results)

    async def news_search(self, *args: Any, **kwargs: Any) -> list[SearchResult] | None:
        # Keenable 当前不暴露独立的新闻接口
        return None


register(KeenableProvider())
