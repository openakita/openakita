"""Runtime dispatcher — routes a search request to one or more providers.

Two execution modes:

1. **Explicit selection** (``provider_id`` given): the request goes to that
   provider only. Failures are raised as-is — no fallback. This is the mode
   the test endpoint uses (``POST /api/tools/web-search/test``).

2. **Auto-detect** (``provider_id`` is ``None``): walk providers by
   ``auto_detect_order``, skip ones whose ``is_available()`` is False, and
   on per-provider failure decide whether to fall back to the next:

   - :class:`MissingCredentialError` / :class:`AuthFailedError` /
     :class:`RateLimitedError` / :class:`NetworkUnreachableError`
     → **try next** (different provider may have a working network path,
     valid credential, or available quota)
   - :class:`ContentFilterError` → **raise immediately** (the query content
     itself is the problem; retrying with another provider hits the same wall)

If every available provider fails, raise :class:`NoProviderAvailable` with
the dominant (last) error code so the UI can render an actionable hint.
"""

from __future__ import annotations

import logging

from .base import (
    AuthFailedError,
    MissingCredentialError,
    NetworkUnreachableError,
    NoProviderAvailable,
    ProviderError,
    RateLimitedError,
    SearchBundle,
    SearchResult,
)
from .registry import available_providers, get_provider, iter_providers

logger = logging.getLogger(__name__)


# Errors that mean "this provider can't help; try the next one":
#   - missing credential  → user hasn't set up this provider yet
#   - auth failed         → key expired or wrong
#   - rate limited        → quota exhausted on this provider
#   - network unreachable → transport down for this endpoint (different host = may work)
# ContentFilterError is intentionally NOT in this list: the query itself is the
# problem, so trying another provider with the same query just wastes budget.
_FALLBACK_ERRORS = (
    MissingCredentialError,
    AuthFailedError,
    RateLimitedError,
    NetworkUnreachableError,
)


def _provider_unavailable_message(provider_id: str) -> str:
    return (
        f"Provider {provider_id!r} is registered but not available. "
        "Configure its API key or choose another search provider."
    )


async def run_web_search(
    query: str,
    *,
    provider_id: str | None = None,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timeout_seconds: float = 0.0,
    allow_fallback: bool = False,
) -> SearchBundle:
    """Dispatch a web search to a provider (explicit or auto-detect).

    ``allow_fallback`` (reliability fix, 2026-06): when an EXPLICIT
    ``provider_id`` is configured (e.g. ``settings.web_search_provider``) and
    that one provider is unavailable / fails with a credential / auth / quota /
    network error, fall back to auto-detect across the OTHER available
    providers instead of hard-failing. This is what lets the org/agent path
    survive a broken default source (real bug: ``jina`` 401 because the free
    endpoint now needs a key). The dedicated *test* endpoint keeps the strict
    "no fallback" contract by leaving ``allow_fallback=False`` so it can probe
    one specific provider.
    """
    if provider_id:
        provider = get_provider(provider_id)
        if provider.is_available():
            try:
                results = await provider.search(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timeout_seconds=timeout_seconds,
                )
                return SearchBundle(provider_id=provider.id, results=results)
            except _FALLBACK_ERRORS as exc:
                if not allow_fallback:
                    raise
                logger.info(
                    "[web_search] explicit provider %s failed (%s); "
                    "falling back to auto-detect across other sources",
                    provider_id,
                    type(exc).__name__,
                )
        elif not allow_fallback:
            raise MissingCredentialError(
                _provider_unavailable_message(provider_id),
                provider_id=provider_id,
            )
        else:
            logger.info(
                "[web_search] explicit provider %s unavailable; "
                "falling back to auto-detect across other sources",
                provider_id,
            )
        # Fallback path: auto-detect excluding the failed explicit provider.
        return await _auto_search(
            kind="web",
            query=query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=None,
            timeout_seconds=timeout_seconds,
            exclude={provider_id},
        )

    return await _auto_search(
        kind="web",
        query=query,
        max_results=max_results,
        region=region,
        safesearch=safesearch,
        timelimit=None,
        timeout_seconds=timeout_seconds,
    )


async def run_news_search(
    query: str,
    *,
    provider_id: str | None = None,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    timeout_seconds: float = 0.0,
    allow_fallback: bool = False,
) -> SearchBundle:
    """Dispatch a news search to a provider, skipping providers that don't support news.

    ``allow_fallback`` mirrors :func:`run_web_search`: an explicit provider that
    is unavailable / fails on credential / auth / quota / network falls back to
    auto-detect across the other news-capable providers.
    """
    if provider_id:
        provider = get_provider(provider_id)
        if provider.is_available():
            try:
                results = await provider.news_search(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timelimit=timelimit,
                    timeout_seconds=timeout_seconds,
                )
                if results is not None:
                    return SearchBundle(provider_id=provider.id, results=results)
                # Provider doesn't do news → fall through to auto (or raise).
                if not allow_fallback:
                    raise MissingCredentialError(
                        f"Provider {provider_id!r} does not support news_search",
                        provider_id=provider_id,
                    )
            except _FALLBACK_ERRORS as exc:
                if not allow_fallback:
                    raise
                logger.info(
                    "[news_search] explicit provider %s failed (%s); "
                    "falling back to auto-detect across other sources",
                    provider_id,
                    type(exc).__name__,
                )
        elif not allow_fallback:
            raise MissingCredentialError(
                _provider_unavailable_message(provider_id),
                provider_id=provider_id,
            )
        else:
            logger.info(
                "[news_search] explicit provider %s unavailable; "
                "falling back to auto-detect across other sources",
                provider_id,
            )
        return await _auto_search(
            kind="news",
            query=query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
            timeout_seconds=timeout_seconds,
            exclude={provider_id},
        )

    return await _auto_search(
        kind="news",
        query=query,
        max_results=max_results,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        timeout_seconds=timeout_seconds,
    )


async def _auto_search(
    *,
    kind: str,
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timelimit: str | None,
    timeout_seconds: float,
    exclude: set[str] | None = None,
) -> SearchBundle:
    candidates = available_providers()
    if exclude:
        candidates = [p for p in candidates if p.id not in exclude]
    if not candidates:
        # No provider is available at all (e.g. ddgs lib uninstalled + no Keys set)
        raise NoProviderAvailable(
            "No web_search provider is configured or available",
            error_code="missing_credential",
            attempted=[p.id for p in iter_providers()],
        )

    attempted: list[str] = []
    last_err: ProviderError | None = None

    for provider in candidates:
        attempted.append(provider.id)
        try:
            if kind == "news":
                results = await provider.news_search(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timelimit=timelimit,
                    timeout_seconds=timeout_seconds,
                )
                if results is None:
                    # This provider doesn't do news — try the next one
                    continue
            else:
                results = await provider.search(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    timeout_seconds=timeout_seconds,
                )
        except _FALLBACK_ERRORS as exc:
            logger.info(
                "[web_search.auto] %s skipped (%s): %s",
                provider.id,
                type(exc).__name__,
                exc,
            )
            last_err = exc
            continue
        # ContentFilterError (and any unexpected exceptions) propagate out —
        # the query itself is unsuitable; retrying another provider doesn't help.
        return SearchBundle(provider_id=provider.id, results=list(results))

    # Pick the most actionable error code from the chain. Heuristic:
    # if ANY provider lacked credential / had auth failure, surface that —
    # because the user can fix it. Otherwise surface the network error so
    # the UI tells them to check connectivity.
    final_code = (
        getattr(last_err, "error_code", "missing_credential")
        if last_err is not None
        else "missing_credential"
    )
    raise NoProviderAvailable(
        f"All available providers failed (last error code: {final_code})",
        error_code=final_code,
        attempted=attempted,
    )


__all__ = [
    "SearchBundle",
    "SearchResult",
    "run_news_search",
    "run_web_search",
]
