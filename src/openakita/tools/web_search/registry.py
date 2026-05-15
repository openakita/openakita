"""Provider registry — central lookup table.

Providers are registered once at import time (lazy: provider modules import
themselves on first :func:`iter_providers` / :func:`get_provider` call to
avoid pulling in optional deps until needed).

Lookup APIs:
    - :func:`iter_providers` — sorted by ``auto_detect_order``
    - :func:`get_provider` — by id, raises ``KeyError`` if not registered
    - :func:`available_providers` — only those whose ``is_available()`` is True
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .base import WebSearchProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, WebSearchProvider] = {}
_LOADED = False


def register(provider: WebSearchProvider) -> None:
    """Register a provider. Last write wins (intentional — supports test overrides)."""
    if provider.id in _PROVIDERS and _PROVIDERS[provider.id] is not provider:
        logger.info("[web_search.registry] Overriding existing provider %r", provider.id)
    _PROVIDERS[provider.id] = provider


def _ensure_loaded() -> None:
    """Lazy-load all built-in providers on first registry access.

    Importing eagerly at package init time would force every consumer (CLI,
    tests, MCP discovery) to pay the cost of loading httpx / ddgs even when
    they never invoke web_search. The lazy path keeps cold start fast.
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    # Import provider modules — each calls ``register(...)`` at module scope
    from .providers import bocha, duckduckgo, jina, searxng, tavily  # noqa: F401


def iter_providers() -> list[WebSearchProvider]:
    """Return all registered providers, sorted by ``auto_detect_order`` (asc)."""
    _ensure_loaded()
    return sorted(_PROVIDERS.values(), key=lambda p: p.auto_detect_order)


def available_providers() -> list[WebSearchProvider]:
    """Return only providers whose ``is_available()`` returns True, sorted."""
    return [p for p in iter_providers() if p.is_available()]


def get_provider(provider_id: str) -> WebSearchProvider:
    """Look up a provider by id. Raises :class:`KeyError` if not registered."""
    _ensure_loaded()
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown web_search provider {provider_id!r}; "
            f"available: {sorted(_PROVIDERS)}"
        ) from exc


def known_provider_ids() -> Iterable[str]:
    """Return all registered provider ids (sorted by priority)."""
    return [p.id for p in iter_providers()]


__all__ = [
    "available_providers",
    "get_provider",
    "iter_providers",
    "known_provider_ids",
    "register",
]
