"""Jina provider — 无 Key 可用（限速）.

API: ``GET https://s.jina.ai/{query}`` with Accept: application/json
Docs: https://jina.ai/reader

Auto-detect priority: 40. 免费额度有限（每分钟 ~20 次），配置 JINA_API_KEY 后
额度大幅提升。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ....config import settings
from ..base import (
    AuthFailedError,
    NetworkUnreachableError,
    RateLimitedError,
    SearchResult,
)
from ..registry import register

logger = logging.getLogger(__name__)


class JinaProvider:
    id = "jina"
    label = "Jina"
    requires_credential = False  # 免费额度可直接用
    auto_detect_order = 40
    signup_url = "https://jina.ai/reader"
    docs_url = "https://jina.ai/reader"

    _ENDPOINT_PREFIX = "https://s.jina.ai/"

    def is_available(self) -> bool:
        # Jina 走免费额度也能用，所以始终 available
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
        api_key = (settings.jina_api_key or "").strip()
        url = self._ENDPOINT_PREFIX + quote(query, safe="")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise NetworkUnreachableError(
                f"jina transport failure: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachableError(
                f"jina HTTP error: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthFailedError(
                f"jina rejected credential (HTTP {resp.status_code})",
                provider_id=self.id,
            )
        if resp.status_code == 429:
            raise RateLimitedError(
                "jina free-tier rate-limited (HTTP 429); "
                "configure JINA_API_KEY for higher quota",
                provider_id=self.id,
            )
        if resp.status_code >= 400:
            raise NetworkUnreachableError(
                f"jina HTTP {resp.status_code}: {resp.text[:200]}",
                provider_id=self.id,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise NetworkUnreachableError(
                "jina returned non-JSON response", provider_id=self.id,
            ) from exc

        # Jina shape: ``{"data": [{"title", "url", "description"}]}`` or sometimes top-level list
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []

        out: list[SearchResult] = []
        for item in items[:max_results]:
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    title=str(item.get("title") or "无标题"),
                    url=str(item.get("url") or item.get("link") or ""),
                    snippet=str(item.get("description") or item.get("snippet") or ""),
                    source=str(item.get("source") or ""),
                )
            )
        return out

    async def news_search(self, *args: Any, **kwargs: Any) -> list[SearchResult] | None:
        # Jina Reader 没有独立的新闻接口
        return None


register(JinaProvider())
