"""Fetcher base — :class:`NormalizedItem` + :class:`BaseFetcher` ABC.

Every concrete fetcher (``wallstreetcn``, ``cls``, ``pbc_omo``, …) emits a
list of :class:`NormalizedItem`. The pipeline upstream is agnostic to the
source format; the fetcher owns request / parsing / error handling. All
fetcher errors surface as Python exceptions — the pipeline catches them,
maps to the canonical ``error_kind`` via :mod:`finpulse_errors`, and
writes the ``last_error`` config key for the Settings → Sources panel.

URL canonicalisation is centralised here so the de-dupe path in
:mod:`finpulse_task_manager` (``articles.url_hash`` UNIQUE constraint)
stays aligned with the hash the fetcher reports up.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# Canonical query-string keys to strip (tracking cruft). Anything listed
# below is removed before hashing so the same article posted with /
# without ``utm_*`` collapses to a single dedupe row.
_STRIP_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "spm",
        "fromuid",
        "from",
        "source",
        "share_id",
        "share_from",
        "_ref",
    }
)


def canonicalize_url(url: str) -> str:
    """Return a stable canonical form of ``url`` for de-dupe hashing.

    Rules (deliberately conservative so genuinely-different URLs keep
    their distinct hashes):

    1. Lower-case the scheme + host.
    2. Drop the trailing slash on the path (except the bare root).
    3. Strip tracking query parameters (``utm_*``, ``spm``, ``from``…).
    4. Drop the fragment.
    """
    if not url:
        return ""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "http").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    if parsed.query:
        keep = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in _STRIP_QUERY_KEYS
        ]
        query = urlencode(keep, doseq=True)
    else:
        query = ""

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def url_hash(url: str) -> str:
    """SHA-256 of the canonical form of ``url`` — stable across sources."""
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


@dataclass
class NormalizedItem:
    """Source-agnostic article shape produced by every fetcher.

    ``extra`` captures source-specific payload (rank, raw body, mobileUrl,
    sentiment from CLS, form-type from SEC, …) so downstream consumers
    can opt into extra signal without us bloating the canonical schema.
    """

    source_id: str
    title: str
    url: str
    published_at: str | None = None
    summary: str | None = None
    content: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def url_hash(self) -> str:
        return url_hash(self.url)

    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "canonical_url": self.canonical_url(),
            "url_hash": self.url_hash(),
            "published_at": self.published_at,
            "summary": self.summary,
            "content": self.content,
            "extra": self.extra,
        }


@dataclass
class FetchReport:
    """Outcome of a single fetcher invocation — consumed by the pipeline
    to update ``config['source.{id}.last_ok']`` / ``last_error``.
    """

    source_id: str
    items: list[NormalizedItem] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None
    duration_ms: float = 0.0


class BaseFetcher(abc.ABC):
    """Abstract base for every data source.

    Subclasses must:

    * set :attr:`source_id` to a registry key (``wallstreetcn`` / ``cls``…).
    * implement :meth:`fetch` — returns a list of :class:`NormalizedItem`.

    Optional hooks:

    * :meth:`probe` — lightweight health check invoked by the Settings
      page's "Test" button; default returns ``True`` on a cheap ping.
    * :meth:`supports_since` — whether ``fetch(since=...)`` honours the
      cursor (defaults to False — the base pipeline does its own
      time-window filtering post-fetch).
    """

    source_id: str = ""

    def __init__(self, *, config: dict[str, str] | None = None, timeout_sec: float = 15.0) -> None:
        self._config = config or {}
        self._timeout_sec = float(timeout_sec)

    @abc.abstractmethod
    async def fetch(self, *, since: datetime | None = None) -> list[NormalizedItem]:
        """Return the list of canonical items fetched from the source."""

    async def probe(self) -> bool:
        """Default probe — subclasses override with something cheap."""
        return True

    @property
    def supports_since(self) -> bool:
        return False


__all__ = [
    "BaseFetcher",
    "FetchReport",
    "NormalizedItem",
    "canonicalize_url",
    "url_hash",
]
