"""RSS / Atom / JSON-Feed fetcher.

Wraps ``feedparser`` as an optional dependency (only used by sources
that opt into the RSS flow) and exposes :class:`GenericRSSFetcher`
which reads its feed list from ``config['rss_generic.feeds']`` (one
URL per line).

The helper :func:`parse_feed` is exported so RSS-first sources
(wallstreetcn / xueqiu / nbs / sec_edgar) can delegate to it without
re-implementing the feedparser dance.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

try:  # pragma: no cover — feedparser is preferred when available
    import feedparser  # type: ignore

    FEEDPARSER_AVAILABLE = True
except ImportError:
    feedparser = None  # type: ignore
    FEEDPARSER_AVAILABLE = False

logger = logging.getLogger(__name__)


def _to_iso(struct_time: Any) -> str | None:
    if struct_time is None:
        return None
    try:
        dt = datetime(*struct_time[:6], tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return None


def parse_feed(source_id: str, body: str) -> list[NormalizedItem]:
    """Parse an RSS/Atom ``body`` into canonical items.

    Prefers :mod:`feedparser` when available (richer metadata, better
    oddball-feed tolerance), falls back to a stdlib :mod:`xml.etree`
    parser so ``nbs`` / ``fed_fomc`` / ``sec_edgar`` / ``rss_generic``
    keep working on hosts where ``pip install feedparser`` hasn't been
    run. The fallback handles RSS 2.0 and Atom 1.0 — the two shapes
    every official regulator we target ships today.
    """
    if FEEDPARSER_AVAILABLE:
        return _parse_with_feedparser(source_id, body)
    return _parse_with_stdlib(source_id, body)


def _parse_with_feedparser(source_id: str, body: str) -> list[NormalizedItem]:
    parsed = feedparser.parse(body)  # type: ignore[union-attr]
    items: list[NormalizedItem] = []
    for entry in parsed.entries or []:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = (entry.get("summary") or entry.get("description") or "").strip() or None
        published_iso = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        items.append(
            NormalizedItem(
                source_id=source_id,
                title=title,
                url=link,
                summary=summary,
                published_at=published_iso,
                extra={
                    "id": entry.get("id"),
                    "author": entry.get("author"),
                    "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                },
            )
        )
    return items


_TAG_RE = re.compile(r"\{[^}]*\}")
_HTML_STRIP_RE = re.compile(r"<[^>]+>")


def _localname(tag: str) -> str:
    """Strip XML namespace so ``{http://…/atom}title`` → ``title``."""
    return _TAG_RE.sub("", tag or "")


def _strip_html(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = _HTML_STRIP_RE.sub("", text).strip()
    return stripped or None


def _parse_rfc822(value: str | None) -> str | None:
    """Parse an RFC-822 / ISO-8601 timestamp into a canonical ISO-8601 UTC string."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        # Atom ``<updated>`` tags already ship ISO-8601; keep them as-is.
        return value.strip() or None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_with_stdlib(source_id: str, body: str) -> list[NormalizedItem]:
    """Minimal RSS 2.0 + Atom 1.0 reader backed by :mod:`xml.etree`.

    Trades off a handful of edge cases (malformed feeds, media
    enclosures, bozo flags) for zero extra dependencies. Every
    regulator RSS we ship (Fed / SEC / NBS) parses fine here.
    """
    try:
        root = ET.fromstring(body.encode("utf-8") if isinstance(body, str) else body)
    except ET.ParseError as exc:
        logger.warning("stdlib rss parse failed for %s: %s", source_id, exc)
        return []

    entries: list[ET.Element] = []
    root_name = _localname(root.tag).lower()
    if root_name == "rss":
        channel = next((c for c in root if _localname(c.tag).lower() == "channel"), None)
        if channel is None:
            return []
        entries = [c for c in channel if _localname(c.tag).lower() == "item"]
    elif root_name == "feed":
        entries = [c for c in root if _localname(c.tag).lower() == "entry"]
    else:
        return []

    items: list[NormalizedItem] = []
    for entry in entries:
        title = None
        link = None
        summary_raw = None
        published = None
        entry_id = None
        author = None
        tags: list[str] = []

        for child in entry:
            lname = _localname(child.tag).lower()
            if lname == "title" and child.text:
                title = child.text.strip()
            elif lname == "link":
                # Atom links live in href attribute; RSS links sit in text.
                href = (child.get("href") or "").strip()
                if href:
                    # Prefer rel=alternate (default) links when available.
                    rel = (child.get("rel") or "alternate").lower()
                    if rel == "alternate" and not link:
                        link = href
                elif child.text and not link:
                    link = child.text.strip()
            elif lname in {"description", "summary", "content"} and child.text:
                summary_raw = _strip_html(child.text)
            elif lname in {"pubdate", "published", "updated", "date"} and child.text:
                published = _parse_rfc822(child.text) or published
            elif lname in {"guid", "id"} and child.text:
                entry_id = child.text.strip()
            elif lname in {"author", "dc:creator", "creator"}:
                if child.text:
                    author = child.text.strip()
            elif lname == "category":
                term = (child.get("term") or "").strip() or (child.text or "").strip()
                if term:
                    tags.append(term)

        if not title or not link:
            continue
        items.append(
            NormalizedItem(
                source_id=source_id,
                title=title,
                url=link,
                summary=summary_raw,
                published_at=published,
                extra={
                    "id": entry_id,
                    "author": author,
                    "tags": tags,
                    "parser": "stdlib",
                },
            )
        )
    return items


class GenericRSSFetcher(BaseFetcher):
    """Configurable RSS aggregator — reads feed URLs from config.

    ``config['rss_generic.feeds']`` is a newline-separated list of feed
    URLs. ``config['rss_generic.feeds_json']`` may also hold TrendRadar-
    style structured entries (``[{name,url,enabled}]``). Each URL emits
    items under ``source_id='rss_generic'``; the originating feed URL/name
    is preserved in ``extra`` so the UI can distinguish sources within the
    same aggregator.
    """

    source_id = "rss_generic"

    def _resolve_feeds(self) -> list[dict[str, str]]:
        raw_json = (self._config.get("rss_generic.feeds_json") or "").strip()
        feeds: list[dict[str, str]] = []
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("enabled") is False:
                            continue
                        url = str(entry.get("url") or "").strip()
                        if not url:
                            continue
                        name = str(entry.get("name") or url).strip()
                        feeds.append({"url": url, "name": name})
            except Exception as exc:  # noqa: BLE001 - fallback to legacy list
                logger.warning("invalid rss_generic.feeds_json: %s", exc)
        if feeds:
            return feeds
        feeds_cfg = self._config.get("rss_generic.feeds", "")
        return [
            {"url": ln.strip(), "name": ln.strip()}
            for ln in feeds_cfg.splitlines()
            if ln.strip()
        ]

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        feeds = self._resolve_feeds()
        if not feeds:
            return []
        out: list[NormalizedItem] = []
        async with make_client(timeout=self._timeout_sec) as client:
            for feed in feeds[:32]:  # hard cap so a huge paste cannot DoS the run
                feed_url = feed["url"]
                try:
                    body = await fetch_text(client, feed_url)
                except Exception as exc:  # noqa: BLE001 — per-feed isolation
                    logger.warning("rss feed failed %s: %s", feed_url, exc)
                    continue
                try:
                    items = parse_feed(self.source_id, body)
                except ImportError:
                    raise  # surface dependency error to the pipeline
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rss parse failed %s: %s", feed_url, exc)
                    continue
                for item in items:
                    item.extra.setdefault("feed_url", feed_url)
                    item.extra.setdefault("feed_name", feed.get("name") or feed_url)
                out.extend(items)
        return out


async def fetch_one_feed(
    source_id: str, feed_url: str, *, timeout: float = 15.0
) -> list[NormalizedItem]:
    """Fetch + parse a single feed. Used by RSS-first fetchers that map
    to exactly one feed URL (wallstreetcn / xueqiu / nbs / sec_edgar).
    """
    async with make_client(timeout=timeout) as client:
        body = await fetch_text(client, feed_url)
    return parse_feed(source_id, body)


__all__ = [
    "FEEDPARSER_AVAILABLE",
    "GenericRSSFetcher",
    "fetch_one_feed",
    "parse_feed",
]
