"""OpenAkita web_search subsystem — multi-provider with auto-detect fallback.

Public API:
    - :func:`run_web_search`, :func:`run_news_search` — runtime dispatch
    - :class:`SearchBundle`, :class:`SearchResult` — result shapes
    - :func:`iter_providers`, :func:`available_providers`, :func:`get_provider`
      — registry inspection (used by ``api/routes/web_search.py``)

Why this exists: the original ``web_search`` handler hard-coded DuckDuckGo,
which is unreachable from mainland China. This subsystem swaps in 博查 / Tavily /
SearXNG / Jina as alternatives, with a clean Provider Protocol so adding new
backends is a single new file under ``providers/``.

See also:
    - ``handlers/web_search.py`` — the tool handler that calls into this runtime
    - ``api/routes/web_search.py`` — the test/list endpoints used by the UI
    - ``tool_hints.py`` — the structured-error contract for chat UI hints
"""

from __future__ import annotations

from .base import (
    AuthFailedError,
    ContentFilterError,
    MissingCredentialError,
    NetworkUnreachableError,
    NoProviderAvailable,
    ProviderError,
    RateLimitedError,
    SearchBundle,
    SearchResult,
    WebSearchProvider,
)
from .registry import available_providers, get_provider, iter_providers, known_provider_ids
from .runtime import run_news_search, run_web_search

__all__ = [
    "AuthFailedError",
    "ContentFilterError",
    "MissingCredentialError",
    "NetworkUnreachableError",
    "NoProviderAvailable",
    "ProviderError",
    "RateLimitedError",
    "SearchBundle",
    "SearchResult",
    "WebSearchProvider",
    "available_providers",
    "get_provider",
    "iter_providers",
    "known_provider_ids",
    "run_news_search",
    "run_web_search",
]
