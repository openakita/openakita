"""DuckDuckGo provider — auto-detect 兜底.

Uses the ``ddgs`` Python package (sync-only; we ``asyncio.to_thread`` it). No
credential required, but mainland China network conditions usually surface
``NetworkUnreachableError`` here. Kept for parity / overseas users.
"""

from __future__ import annotations

import asyncio
import logging

from ..base import (
    NetworkUnreachableError,
    SearchResult,
)
from ..registry import register

logger = logging.getLogger(__name__)


def _ddgs_available() -> bool:
    try:
        import ddgs  # noqa: F401
    except ImportError:
        return False
    return True


def _sync_web(query: str, max_results: int, region: str, safesearch: str) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
        ))


def _sync_news(
    query: str, max_results: int, region: str, safesearch: str, timelimit: str | None
) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.news(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
        ))


async def _run(coro_func, *, timeout_seconds: float, **kwargs) -> list[dict]:
    task = asyncio.to_thread(coro_func, **kwargs)
    if timeout_seconds and timeout_seconds > 0:
        return await asyncio.wait_for(task, timeout=timeout_seconds)
    return await task


def _to_result(d: dict, *, news: bool = False) -> SearchResult:
    return SearchResult(
        title=str(d.get("title") or "无标题"),
        url=str(d.get("href") or d.get("url") or d.get("link") or ""),
        snippet=str(d.get("body") or d.get("snippet") or d.get("excerpt") or ""),
        source=str(d.get("source") or ""),
        date=str(d.get("date") or "") if news else "",
    )


class DuckDuckGoProvider:
    id = "duckduckgo"
    label = "DuckDuckGo"
    requires_credential = False
    auto_detect_order = 100  # lowest priority — it's the fallback
    signup_url = ""
    docs_url = "https://duckduckgo.com/"

    def is_available(self) -> bool:
        return _ddgs_available()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        try:
            raw = await _run(
                _sync_web,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
            )
        except TimeoutError as exc:
            raise NetworkUnreachableError(
                f"DuckDuckGo timed out after {timeout_seconds}s",
                provider_id=self.id,
            ) from exc
        except Exception as exc:
            # ddgs wraps everything (DNS, TLS, 403 anti-scraping…) in
            # generic Exception. Treating as transport failure is the
            # safest bet for the chat UI; users see "网络不可达 → 切换其他源".
            raise NetworkUnreachableError(
                f"DuckDuckGo request failed: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc
        return [_to_result(r) for r in raw]

    async def news_search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult] | None:
        try:
            raw = await _run(
                _sync_news,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
        except TimeoutError as exc:
            raise NetworkUnreachableError(
                f"DuckDuckGo news timed out after {timeout_seconds}s",
                provider_id=self.id,
            ) from exc
        except Exception as exc:
            raise NetworkUnreachableError(
                f"DuckDuckGo news request failed: {type(exc).__name__}: {exc}",
                provider_id=self.id,
            ) from exc
        return [_to_result(r, news=True) for r in raw]


register(DuckDuckGoProvider())
