"""Web search Provider Protocol + structured exceptions.

Each concrete provider (DuckDuckGo, 博查, Tavily, SearXNG, Jina) implements
:class:`WebSearchProvider`. Providers raise :class:`ProviderError` subclasses
to signal *why* a call failed; the runtime translates these into the
``ToolConfigError`` family so the chat UI can render an actionable hint.

Why a separate exception family from :class:`ToolConfigError`?
    - ``ProviderError`` is *internal* to the web_search subsystem (per-provider)
    - ``ToolConfigError`` is the cross-system contract for "user-correctable
      tool-level config issue" — handlers translate provider errors → tool errors
    - This keeps providers focused on transport semantics; only the handler
      knows how to phrase user-facing actions like "前往配置搜索源"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..tool_hints import ConfigHintErrorCode


class ProviderError(Exception):
    """Base class for per-provider failures. Maps to :class:`ConfigHintErrorCode`."""

    error_code: ConfigHintErrorCode = "unknown"

    def __init__(self, message: str = "", *, provider_id: str | None = None) -> None:
        super().__init__(message)
        self.provider_id = provider_id


class MissingCredentialError(ProviderError):
    """Provider requires a credential (API key / base URL) that is not configured."""

    error_code: ConfigHintErrorCode = "missing_credential"


class AuthFailedError(ProviderError):
    """Provider rejected the credential (HTTP 401/403)."""

    error_code: ConfigHintErrorCode = "auth_failed"


class RateLimitedError(ProviderError):
    """Provider throttled the request (HTTP 429)."""

    error_code: ConfigHintErrorCode = "rate_limited"


class NetworkUnreachableError(ProviderError):
    """Transport layer failed (timeout / DNS / TLS / connection refused).

    DuckDuckGo in mainland China typically surfaces here — that's the root
    pain point this whole subsystem exists to alleviate.
    """

    error_code: ConfigHintErrorCode = "network_unreachable"


class ContentFilterError(ProviderError):
    """Provider rejected the query content (e.g. upstream content policy)."""

    error_code: ConfigHintErrorCode = "content_filter"


class NoProviderAvailable(ProviderError):
    """Auto-detect tried every available provider and they all failed.

    The ``error_code`` is set to the *last* failure's code (with a preference
    for ``missing_credential`` if all failures were credential-related), giving
    the UI a useful hint about the dominant root cause.
    """

    def __init__(
        self,
        message: str = "",
        *,
        error_code: ConfigHintErrorCode = "missing_credential",
        attempted: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.attempted = list(attempted or [])


@dataclass(frozen=True)
class SearchResult:
    """Normalized search result. All providers map to this shape."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""
    date: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }
        if self.source:
            out["source"] = self.source
        if self.date:
            out["date"] = self.date
        if self.extra:
            out.update({k: v for k, v in self.extra.items() if k not in out})
        return out


@dataclass(frozen=True)
class SearchBundle:
    """Wrapper carrying results + which provider answered (for telemetry / UI)."""

    provider_id: str
    results: list[SearchResult]


@runtime_checkable
class WebSearchProvider(Protocol):
    """Provider interface. Implementations live under ``providers/``.

    Implementations are *singletons* — instantiated once at module import,
    registered in ``registry.py``. They MUST be safe to call concurrently
    from multiple async tasks (no per-instance mutable state required for
    a request).

    Attributes:
        id: Stable identifier used in config (``settings.web_search_provider``)
            and API URLs. Lowercase, ascii.
        label: Human-readable name shown in the settings panel.
        requires_credential: Whether ``is_available()`` depends on a configured
            credential. False for DuckDuckGo (uses ddgs lib) and Jina (free
            tier).
        auto_detect_order: Lower = higher priority during auto-detect fallback.
            Recommended: bocha=10, tavily=20, searxng=30, jina=40, ddg=100.
        signup_url: Where the user should go to register and get a key
            (rendered as a "申请 Key" button in the settings panel).
        docs_url: Provider documentation URL (rendered as a small link).
    """

    id: str
    label: str
    requires_credential: bool
    auto_detect_order: int
    signup_url: str
    docs_url: str

    def is_available(self) -> bool:
        """Return True if this provider can be used right now (credential present, lib installed)."""
        ...

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        """Run a web search. Raise :class:`ProviderError` on failure.

        Implementations must:
          - never block the event loop (use ``asyncio.to_thread`` for sync libs)
          - respect ``timeout_seconds`` (0 = no executor-level timeout)
          - return an empty list (not raise) when no results matched
          - raise :class:`MissingCredentialError` when ``is_available()`` is False
        """
        ...

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
        """Run a news search. Return ``None`` if this provider doesn't support news."""
        ...


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
]
