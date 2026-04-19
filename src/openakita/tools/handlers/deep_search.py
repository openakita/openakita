"""
Deep Search handler — multi-provider deep research using Tavily + Exa.

Capabilities:
  - Parallel multi-query fan-out across Tavily and Exa
  - Tavily extract endpoint for content retrieval
  - Exa contents + highlights for rich snippet extraction
  - Automatic deduplication by URL
  - Configurable source targets (50-500+ sources)
  - Relevance scoring and ranking

Configure via .env:
  TAVILY_API_KEY=tvly-...
  EXA_API_KEY=exa-...
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DeepSource:
    """A single source from a deep search result."""
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    source_provider: str = ""
    relevance_score: float = 0.0
    query_match: str = ""  # which query found this source

    @property
    def url_hash(self) -> str:
        normalized = self.url.lower().rstrip("/")
        return hashlib.md5(normalized.encode()).hexdigest()


@dataclass
class DeepSearchResult:
    """Aggregated deep search results."""
    query: str
    sources: list[DeepSource] = field(default_factory=list)
    total_found: int = 0
    duplicates_removed: int = 0
    elapsed_seconds: float = 0.0
    providers_used: list[str] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Query expansion — generate diverse sub-queries
# ---------------------------------------------------------------------------

_QUERY_TEMPLATES = [
    "{q}",
    "{q} overview guide",
    "{q} latest research 2025 2026",
    "{q} best practices tutorial",
    "{q} comparison review analysis",
    "what is {q}",
    "how does {q} work",
    "{q} examples case studies",
    "{q} pros and cons advantages disadvantages",
    "{q} technical documentation",
    "{q} alternatives competitors",
    "{q} statistics data metrics",
    "{q} expert opinion analysis",
    "{q} open source tools",
    "{q} implementation steps",
]


def expand_queries(query: str, target_sources: int = 100) -> list[str]:
    """Generate diverse search queries from a base query.

    More queries are generated for higher target source counts.
    """
    n_queries = max(5, min(30, target_sources // 15))
    templates = _QUERY_TEMPLATES[:n_queries]
    return [t.format(q=query) for t in templates]


# ---------------------------------------------------------------------------
# Tavily Deep Search
# ---------------------------------------------------------------------------

class TavilyDeepSearch:
    """Tavily-powered deep search with multi-query fan-out + extract."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_sources: int = 50,
        include_raw_content: bool = False,
    ) -> list[DeepSource]:
        """Run a deep search via Tavily with query fan-out."""
        try:
            from tavily import TavilyClient
        except ImportError:
            logger.warning("[DeepSearch] tavily-python not installed")
            return []

        client = TavilyClient(api_key=self._api_key)
        queries = expand_queries(query, target_sources=max_sources)
        all_sources: list[DeepSource] = []

        # Run searches in parallel batches of 5
        batch_size = 5
        for i in range(0, len(queries), batch_size):
            batch = queries[i : i + batch_size]
            tasks = [self._tavily_search_one(client, q, include_raw_content) for q in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    all_sources.extend(result)
                elif isinstance(result, Exception):
                    logger.debug("[TavilyDeepSearch] query failed: %s", result)

        return all_sources

    async def _tavily_search_one(
        self, client: TavilyClient, query: str, include_raw: bool
    ) -> list[DeepSource]:
        """Single Tavily search call (runs in executor for sync SDK)."""
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=10,
                    include_raw_content=include_raw,
                ),
            )
        except Exception as e:
            logger.debug("[Tavily] search failed for '%s': %s", query, e)
            return []

        sources = []
        for r in response.get("results", []):
            sources.append(
                DeepSource(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    content=r.get("raw_content", "")[:2000] if include_raw else "",
                    source_provider="tavily",
                    relevance_score=r.get("score", 0.0),
                    query_match=query,
                )
            )
        return sources

    async def extract(self, urls: list[str]) -> list[DeepSource]:
        """Extract content from a list of URLs using Tavily extract."""
        try:
            from tavily import TavilyClient
        except ImportError:
            return []

        client = TavilyClient(api_key=self._api_key)
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None, lambda: client.extract(urls=urls[:50])
            )
        except Exception as e:
            logger.debug("[Tavily] extract failed: %s", e)
            return []

        sources = []
        for r in response.get("results", []):
            sources.append(
                DeepSource(
                    title=r.get("url", ""),
                    url=r.get("url", ""),
                    content=r.get("raw_content", "")[:5000],
                    source_provider="tavily-extract",
                )
            )
        return sources


# ---------------------------------------------------------------------------
# Exa Deep Search
# ---------------------------------------------------------------------------

class ExaDeepSearch:
    """Exa-powered deep search with multi-query fan-out + contents."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_sources: int = 50,
        include_text: bool = True,
    ) -> list[DeepSource]:
        """Run a deep search via Exa with query fan-out."""
        try:
            from exa_py import Exa
        except ImportError:
            logger.warning("[DeepSearch] exa-py not installed")
            return []

        exa = Exa(api_key=self._api_key)
        queries = expand_queries(query, target_sources=max_sources)
        all_sources: list[DeepSource] = []

        batch_size = 5
        for i in range(0, len(queries), batch_size):
            batch = queries[i : i + batch_size]
            tasks = [self._exa_search_one(exa, q, include_text) for q in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    all_sources.extend(result)
                elif isinstance(result, Exception):
                    logger.debug("[ExaDeepSearch] query failed: %s", result)

        return all_sources

    async def _exa_search_one(
        self, exa: Any, query: str, include_text: bool
    ) -> list[DeepSource]:
        """Single Exa search call (runs in executor for sync SDK)."""
        loop = asyncio.get_event_loop()
        try:
            kwargs: dict[str, Any] = {
                "query": query,
                "num_results": 10,
                "type": "neural",
                # "category" removed — auto-detect based on query
            }
            if include_text:
                kwargs["text"] = {"maxCharacters": 1000}
                kwargs["highlights"] = {"numSentences": 3}

            response = await loop.run_in_executor(
                None, lambda: exa.search_and_contents(**kwargs)
            )
        except Exception as e:
            logger.debug("[Exa] search failed for '%s': %s", query, e)
            return []

        sources = []
        for r in response.results:
            snippet = ""
            if hasattr(r, "text") and r.text:
                snippet = r.text[:500]
            elif hasattr(r, "highlights") and r.highlights:
                snippet = " ".join(r.highlights[:3])

            sources.append(
                DeepSource(
                    title=getattr(r, "title", "") or "",
                    url=getattr(r, "url", ""),
                    snippet=snippet,
                    content=getattr(r, "text", "")[:2000] if include_text else "",
                    source_provider="exa",
                    relevance_score=getattr(r, "score", 0.0) or 0.0,
                    query_match=query,
                )
            )
        return sources


# ---------------------------------------------------------------------------
# Deep Search Orchestrator
# ---------------------------------------------------------------------------

class DeepSearchOrchestrator:
    """Orchestrates multi-provider deep search with dedup and ranking."""

    def __init__(
        self,
        tavily_key: str = "",
        exa_key: str = "",
    ):
        self.tavily = TavilyDeepSearch(tavily_key) if tavily_key else None
        self.exa = ExaDeepSearch(exa_key) if exa_key else None

    async def deep_search(
        self,
        query: str,
        max_sources: int = 100,
        providers: list[str] | None = None,
        include_content: bool = False,
    ) -> DeepSearchResult:
        """Execute a deep search across configured providers.

        Args:
            query: Research topic / question.
            max_sources: Target number of unique sources (50-500).
            providers: List of providers to use. None = all available.
            include_content: Whether to fetch full content (slower).

        Returns:
            DeepSearchResult with deduplicated, ranked sources.
        """
        start = time.time()
        providers = providers or self._available_providers()
        queries = expand_queries(query, target_sources=max_sources)

        tasks: list[asyncio.Task] = []

        if "tavily" in providers and self.tavily:
            tasks.append(
                asyncio.create_task(
                    self.tavily.search(query, max_sources, include_content)
                )
            )

        if "exa" in providers and self.exa:
            tasks.append(
                asyncio.create_task(
                    self.exa.search(query, max_sources, include_content)
                )
            )

        if not tasks:
            return DeepSearchResult(
                query=query,
                elapsed_seconds=time.time() - start,
                queries_used=queries,
            )

        all_raw: list[DeepSource] = []
        gather_results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in gather_results:
            if isinstance(result, list):
                all_raw.extend(result)
            elif isinstance(result, Exception):
                logger.warning("[DeepSearch] provider error: %s", result)

        total_before_dedup = len(all_raw)

        # Deduplicate by URL
        seen_urls: dict[str, DeepSource] = {}
        for src in all_raw:
            h = src.url_hash
            if h not in seen_urls:
                seen_urls[h] = src
            else:
                # Keep the one with higher relevance or more content
                existing = seen_urls[h]
                if src.relevance_score > existing.relevance_score:
                    seen_urls[h] = src
                elif src.content and not existing.content:
                    seen_urls[h] = src

        deduped = list(seen_urls.values())

        # Sort by relevance score descending
        deduped.sort(key=lambda s: s.relevance_score, reverse=True)

        # Trim to max_sources
        final_sources = deduped[:max_sources]

        elapsed = time.time() - start
        providers_used = []
        if any(s.source_provider.startswith("tavily") for s in final_sources):
            providers_used.append("tavily")
        if any(s.source_provider == "exa" for s in final_sources):
            providers_used.append("exa")

        return DeepSearchResult(
            query=query,
            sources=final_sources,
            total_found=total_before_dedup,
            duplicates_removed=total_before_dedup - len(deduped),
            elapsed_seconds=round(elapsed, 2),
            providers_used=providers_used,
            queries_used=queries,
        )

    def _available_providers(self) -> list[str]:
        providers = []
        if self.tavily:
            providers.append("tavily")
        if self.exa:
            providers.append("exa")
        return providers


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_deep_results(result: DeepSearchResult, max_display: int = 0) -> str:
    """Format DeepSearchResult into a readable string for LLM consumption."""
    sources = result.sources
    if max_display > 0:
        sources = sources[:max_display]

    lines = [
        f"# Deep Search Results",
        f"**Query**: {result.query}",
        f"**Unique Sources**: {len(result.sources)} / {result.total_found} raw results",
        f"**Duplicates Removed**: {result.duplicates_removed}",
        f"**Providers**: {', '.join(result.providers_used) or 'none'}",
        f"**Queries Used**: {len(result.queries_used)}",
        f"**Elapsed**: {result.elapsed_seconds}s",
        "",
    ]

    for i, src in enumerate(sources, 1):
        lines.append(f"### [{i}] {src.title or 'Untitled'}")
        lines.append(f"**URL**: {src.url}")
        lines.append(f"**Provider**: {src.source_provider} | **Score**: {src.relevance_score:.2f}")
        if src.snippet:
            lines.append(f"**Snippet**: {src.snippet[:300]}")
        if src.content:
            lines.append(f"**Content**: {src.content[:500]}...")
        lines.append("")

    if max_display > 0 and len(result.sources) > max_display:
        lines.append(
            f"_Showing {max_display} of {len(result.sources)} sources. "
            f"Request more with max_display parameter._"
        )

    return "\n".join(lines)
