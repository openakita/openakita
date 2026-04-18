"""
Multi-provider web search backend.

Providers (in auto-mode priority order):
  1. Brave Search  — BRAVE_API_KEY
  2. Tavily        — TAVILY_API_KEY
  3. Exa           — EXA_API_KEY
  4. DuckDuckGo    — no key needed (always available as final fallback)

Usage:
    from openakita.tools.handlers.search_providers import get_router
    router = get_router()
    results = await router.get_web_results("python asyncio", max_results=5)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import UTC
from typing import Any

logger = logging.getLogger(__name__)

# Normalised result schema returned by all providers:
# [{"title": str, "url": str, "body": str}]
SearchResults = list[dict[str, str]]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseSearchProvider(ABC):
    """Abstract base for search providers."""

    name: str = "base"

    @abstractmethod
    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        """Return normalised web results."""

    async def news_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        """Return normalised news results. Default: delegates to web_search."""
        news_query = f"{query} news" if timelimit else query
        return await self.web_search(news_query, max_results=max_results, region=region)

    def is_available(self) -> bool:
        """True if this provider has the credentials / deps it needs."""
        return True

    @staticmethod
    def _norm(title: str, url: str, body: str) -> dict[str, str]:
        return {"title": title or "", "url": url or "", "body": body or ""}


# ---------------------------------------------------------------------------
# DuckDuckGo (ddgs)
# ---------------------------------------------------------------------------


class DuckDuckGoProvider(BaseSearchProvider):
    """Zero-config DuckDuckGo search via the ddgs library."""

    name = "ddgs"

    def _sync_web(
        self, query: str, max_results: int, region: str, safesearch: str
    ) -> list[dict]:
        from ddgs import DDGS  # type: ignore[import]

        with DDGS() as ddgs:
            return list(
                ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch)
            )

    def _sync_news(
        self,
        query: str,
        max_results: int,
        region: str,
        safesearch: str,
        timelimit: str | None,
    ) -> list[dict]:
        from ddgs import DDGS  # type: ignore[import]

        with DDGS() as ddgs:
            return list(
                ddgs.news(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timelimit=timelimit,
                )
            )

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        raw = await asyncio.to_thread(self._sync_web, query, max_results, region, safesearch)
        return [
            self._norm(r.get("title", ""), r.get("href", r.get("link", "")), r.get("body", ""))
            for r in raw
        ]

    async def news_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        raw = await asyncio.to_thread(
            self._sync_news, query, max_results, region, safesearch, timelimit
        )
        return [
            self._norm(
                r.get("title", ""),
                r.get("url", r.get("link", "")),
                r.get("body", r.get("excerpt", "")),
            )
            for r in raw
        ]

    def is_available(self) -> bool:
        try:
            import ddgs  # noqa: F401

            return True
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------


class BraveSearchProvider(BaseSearchProvider):
    """Brave Search API v1 — requires BRAVE_API_KEY."""

    name = "brave"
    _WEB_URL = "https://api.search.brave.com/res/v1/web/search"
    _NEWS_URL = "https://api.search.brave.com/res/v1/news/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def _call(self, url: str, params: dict) -> dict:
        import httpx

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        # Brave uses "country" (2-letter) + "search_lang" — map from ddgs region codes
        country = region.split("-")[1].upper() if "-" in region and region != "wt-wt" else "US"
        safe_map = {"on": "strict", "moderate": "moderate", "off": "off"}
        params: dict[str, Any] = {
            "q": query,
            "count": min(max_results, 20),
            "country": country,
            "safesearch": safe_map.get(safesearch, "moderate"),
            "text_decorations": False,
        }
        data = await self._call(self._WEB_URL, params)
        results: SearchResults = []
        for item in (data.get("web", {}) or {}).get("results", [])[:max_results]:
            results.append(
                self._norm(
                    item.get("title", ""),
                    item.get("url", ""),
                    item.get("description", ""),
                )
            )
        return results

    async def news_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        country = region.split("-")[1].upper() if "-" in region and region != "wt-wt" else "US"
        # Brave news freshness: pd=past day, pw=past week, pm=past month
        freshness_map = {"d": "pd", "w": "pw", "m": "pm"}
        params: dict[str, Any] = {
            "q": query,
            "count": min(max_results, 20),
            "country": country,
            "safesearch": "moderate",
        }
        if timelimit and timelimit in freshness_map:
            params["freshness"] = freshness_map[timelimit]
        data = await self._call(self._NEWS_URL, params)
        results: SearchResults = []
        for item in (data.get("results") or [])[:max_results]:
            age = item.get("age", "")
            source = (item.get("meta_url") or {}).get("netloc", "")
            extra = f" ({source} {age})".strip(" ()")
            results.append(
                self._norm(
                    item.get("title", ""),
                    item.get("url", ""),
                    (item.get("description", "") or "") + (f"  [{extra}]" if extra else ""),
                )
            )
        return results


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------


class TavilySearchProvider(BaseSearchProvider):
    """Tavily Search API — requires TAVILY_API_KEY."""

    name = "tavily"
    _URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def _call(self, payload: dict) -> dict:
        import httpx

        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self._URL, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": min(max_results, 20),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        data = await self._call(payload)
        results: SearchResults = []
        for item in (data.get("results") or [])[:max_results]:
            results.append(
                self._norm(
                    item.get("title", ""),
                    item.get("url", ""),
                    item.get("content", ""),
                )
            )
        return results

    # Tavily has no separate news endpoint — use topic="news" query param
    async def news_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "topic": "news",
            "max_results": min(max_results, 20),
            "search_depth": "basic",
        }
        # Tavily days: map timelimit -> days
        days_map = {"d": 1, "w": 7, "m": 30}
        if timelimit and timelimit in days_map:
            payload["days"] = days_map[timelimit]
        data = await self._call(payload)
        results: SearchResults = []
        for item in (data.get("results") or [])[:max_results]:
            published = item.get("published_date", "")
            body = item.get("content", "")
            if published:
                body = f"[{published}] {body}"
            results.append(self._norm(item.get("title", ""), item.get("url", ""), body))
        return results


# ---------------------------------------------------------------------------
# Exa
# ---------------------------------------------------------------------------


class ExaSearchProvider(BaseSearchProvider):
    """Exa (formerly Metaphor) neural search — requires EXA_API_KEY."""

    name = "exa"
    _URL = "https://api.exa.ai/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def _call(self, payload: dict) -> dict:
        import httpx

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self._URL, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        payload: dict[str, Any] = {
            "query": query,
            "numResults": min(max_results, 25),
            "type": "auto",  # auto = keyword + neural hybrid
            "contents": {
                "text": {"maxCharacters": 500},
            },
        }
        data = await self._call(payload)
        results: SearchResults = []
        for item in (data.get("results") or [])[:max_results]:
            body = ""
            contents = item.get("text") or item.get("content") or ""
            if isinstance(contents, str):
                body = contents
            elif isinstance(contents, dict):
                body = contents.get("text", "")
            results.append(
                self._norm(
                    item.get("title", ""),
                    item.get("url", ""),
                    body,
                )
            )
        return results

    # Exa has no dedicated news endpoint — fall back to time-filtered auto search
    async def news_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        from datetime import datetime, timedelta

        payload: dict[str, Any] = {
            "query": f"{query} news",
            "numResults": min(max_results, 25),
            "type": "auto",
            "contents": {"text": {"maxCharacters": 500}},
        }
        days_map = {"d": 1, "w": 7, "m": 30}
        if timelimit and timelimit in days_map:
            cutoff = datetime.now(tz=UTC) - timedelta(days=days_map[timelimit])
            payload["startPublishedDate"] = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        data = await self._call(payload)
        results: SearchResults = []
        for item in (data.get("results") or [])[:max_results]:
            body = ""
            contents = item.get("text") or item.get("content") or ""
            if isinstance(contents, str):
                body = contents
            elif isinstance(contents, dict):
                body = contents.get("text", "")
            published = item.get("publishedDate", "")
            if published:
                body = f"[{published[:10]}] {body}"
            results.append(self._norm(item.get("title", ""), item.get("url", ""), body))
        return results


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# Auto-mode priority order (providers without a key are skipped)
_AUTO_PRIORITY = ["brave", "tavily", "exa", "ddgs"]


class SearchProviderRouter:
    """
    Routes search requests to the configured provider.

    - ``provider="auto"``: iterates _AUTO_PRIORITY, picks first available
      (i.e. has API key configured). Falls back to ddgs if none configured.
    - Explicit provider: uses that provider, falls back to ddgs on error
      when ``fallback_enabled=True``.
    """

    def __init__(
        self,
        provider: str,
        fallback_enabled: bool,
        brave_api_key: str = "",
        tavily_api_key: str = "",
        exa_api_key: str = "",
    ) -> None:
        self._provider_name = provider.lower()
        self._fallback_enabled = fallback_enabled

        self._providers: dict[str, BaseSearchProvider] = {
            "ddgs": DuckDuckGoProvider(),
            "brave": BraveSearchProvider(brave_api_key),
            "tavily": TavilySearchProvider(tavily_api_key),
            "exa": ExaSearchProvider(exa_api_key),
        }

    def _resolve_providers(self) -> list[BaseSearchProvider]:
        """Return ordered list of providers to try for this request."""
        name = self._provider_name

        if name == "auto":
            ordered = []
            for pname in _AUTO_PRIORITY:
                p = self._providers.get(pname)
                if p and p.is_available():
                    ordered.append(p)
            # ddgs is always the last resort
            if not ordered:
                ordered = [self._providers["ddgs"]]
            return ordered

        primary = self._providers.get(name)
        if primary is None:
            logger.warning(
                "[Search] Unknown provider '%s', falling back to ddgs", name
            )
            return [self._providers["ddgs"]]

        if not primary.is_available():
            logger.warning(
                "[Search] Provider '%s' not available (missing API key?), "
                "falling back to ddgs",
                name,
            )
            return [self._providers["ddgs"]]

        if self._fallback_enabled:
            # Explicit provider + fallback: primary first, then ddgs as safety net
            ddgs = self._providers["ddgs"]
            return [primary] if primary is ddgs else [primary, ddgs]
        return [primary]

    async def get_web_results(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> SearchResults:
        providers = self._resolve_providers()
        last_exc: Exception | None = None
        for provider in providers:
            try:
                results = await provider.web_search(
                    query, max_results=max_results, region=region, safesearch=safesearch
                )
                if provider.name != providers[0].name:
                    logger.info("[Search] web_search fell back to provider '%s'", provider.name)
                else:
                    logger.debug("[Search] web_search using provider '%s'", provider.name)
                return results
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[Search] Provider '%s' web_search failed: %s — %s",
                    provider.name,
                    type(exc).__name__,
                    exc,
                )
        raise RuntimeError(
            f"All search providers failed. Last error: {last_exc}"
        ) from last_exc

    async def get_news_results(
        self,
        query: str,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str | None = None,
    ) -> SearchResults:
        providers = self._resolve_providers()
        last_exc: Exception | None = None
        for provider in providers:
            try:
                results = await provider.news_search(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timelimit=timelimit,
                )
                logger.debug("[Search] news_search using provider '%s'", provider.name)
                return results
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[Search] Provider '%s' news_search failed: %s — %s",
                    provider.name,
                    type(exc).__name__,
                    exc,
                )
        raise RuntimeError(
            f"All news search providers failed. Last error: {last_exc}"
        ) from last_exc

    @property
    def active_provider_name(self) -> str:
        """Name of the primary provider that will be tried first."""
        providers = self._resolve_providers()
        return providers[0].name if providers else "ddgs"


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_router: SearchProviderRouter | None = None


def get_router() -> SearchProviderRouter:
    """Return (or build) the global router from current settings."""
    global _router
    if _router is None:
        _router = _build_router()
    return _router


def _build_router() -> SearchProviderRouter:
    try:
        from openakita.config import settings

        return SearchProviderRouter(
            provider=getattr(settings, "search_provider", "auto"),
            fallback_enabled=getattr(settings, "search_fallback_enabled", True),
            brave_api_key=getattr(settings, "brave_api_key", "") or "",
            tavily_api_key=getattr(settings, "tavily_api_key", "") or "",
            exa_api_key=getattr(settings, "exa_api_key", "") or "",
        )
    except Exception as exc:
        logger.warning("[Search] Could not load settings, using DDGS defaults: %s", exc)
        return SearchProviderRouter(provider="ddgs", fallback_enabled=True)


def reset_router() -> None:
    """Force re-initialisation of the router (useful after config reload)."""
    global _router
    _router = None
