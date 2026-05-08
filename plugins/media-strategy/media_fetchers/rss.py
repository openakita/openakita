# ruff: noqa: N999
"""RSS/Atom fetcher with URL safety checks and stdlib fallback parsing."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

try:  # pragma: no cover - optional fast path
    import feedparser  # type: ignore

    FEEDPARSER_AVAILABLE = True
except Exception:  # noqa: BLE001
    feedparser = None  # type: ignore
    FEEDPARSER_AVAILABLE = False

_MAX_REDIRECTS = 8
_TAG_RE = re.compile(r"\{[^}]*\}")
_HTML_RE = re.compile(r"<[^>]+>")


@dataclass
class FeedItem:
    source_id: str
    title: str
    url: str
    summary: str = ""
    published_at: str | None = None
    author: str = ""
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class UnsafeFeedUrl(ValueError):
    """Raised when a feed URL points to a private or unsupported target."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeFeedUrl(f"cannot resolve host: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def validate_feed_url(url: str) -> str:
    """Validate a public HTTP(S) feed URL and return a stripped version."""

    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UnsafeFeedUrl("feed_url must be a public http or https URL")
    host = parsed.hostname or ""
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        raise UnsafeFeedUrl("feed_url host is not allowed")
    if _is_private_ip(host):
        raise UnsafeFeedUrl("feed_url resolves to a private or reserved IP")
    return cleaned


async def fetch_feed_text(
    url: str,
    *,
    timeout_sec: float = 15.0,
    user_agent: str = "OpenAkita-MediaStrategy/0.1",
) -> tuple[str, str]:
    """Fetch a feed with validated manual redirects.

    Returns ``(final_url, text)``. Every redirect target is validated
    before it is followed.
    """

    current = validate_feed_url(url)
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, */*"}
    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=False, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.get(current)
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                return str(response.url), response.text
            location = response.headers.get("Location", "")
            if not location:
                response.raise_for_status()
            current = validate_feed_url(urljoin(current, location))
    raise UnsafeFeedUrl(f"too many redirects fetching {url!r}")


def parse_feed(source_id: str, body: str) -> list[FeedItem]:
    """Parse RSS/Atom text into normalized feed items."""

    if FEEDPARSER_AVAILABLE:
        return _parse_feedparser(source_id, body)
    return _parse_stdlib(source_id, body)


def _struct_to_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _parse_feedparser(source_id: str, body: str) -> list[FeedItem]:
    parsed = feedparser.parse(body)  # type: ignore[union-attr]
    out: list[FeedItem] = []
    for entry in parsed.entries or []:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        if not title or not url:
            continue
        tags = [t.get("term") for t in entry.get("tags", []) if isinstance(t, dict) and t.get("term")]
        out.append(
            FeedItem(
                source_id=source_id,
                title=title,
                url=url,
                summary=_strip_html(entry.get("summary") or entry.get("description") or ""),
                published_at=_struct_to_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
                author=(entry.get("author") or "").strip(),
                tags=tags,
                raw={"id": entry.get("id"), "parser": "feedparser"},
            )
        )
    return out


def _local(tag: str) -> str:
    return _TAG_RE.sub("", tag or "").lower()


def _strip_html(value: str | None) -> str:
    return _HTML_RE.sub("", value or "").strip()


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return raw or None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stdlib(source_id: str, body: str) -> list[FeedItem]:
    try:
        root = ET.fromstring(body.encode("utf-8") if isinstance(body, str) else body)
    except ET.ParseError:
        return []
    root_name = _local(root.tag)
    if root_name == "rss":
        channel = next((c for c in root if _local(c.tag) == "channel"), None)
        entries = [c for c in channel or [] if _local(c.tag) == "item"]
    elif root_name == "feed":
        entries = [c for c in root if _local(c.tag) == "entry"]
    else:
        entries = []

    out: list[FeedItem] = []
    for entry in entries:
        title = ""
        link = ""
        summary = ""
        published = None
        author = ""
        tags: list[str] = []
        entry_id = ""
        for child in entry:
            lname = _local(child.tag)
            text = (child.text or "").strip()
            if lname == "title":
                title = text
            elif lname == "link":
                href = (child.get("href") or "").strip()
                rel = (child.get("rel") or "alternate").lower()
                if href and rel == "alternate" and not link:
                    link = href
                elif text and not link:
                    link = text
            elif lname in {"summary", "description", "content"}:
                summary = _strip_html(text)
            elif lname in {"published", "updated", "pubdate", "date"}:
                published = _parse_date(text) or published
            elif lname in {"author", "creator"}:
                author = text
            elif lname in {"guid", "id"}:
                entry_id = text
            elif lname == "category":
                term = (child.get("term") or text).strip()
                if term:
                    tags.append(term)
        if title and link:
            out.append(
                FeedItem(
                    source_id=source_id,
                    title=title,
                    url=link,
                    summary=summary,
                    published_at=published or _utcnow_iso(),
                    author=author,
                    tags=tags,
                    raw={"id": entry_id, "parser": "stdlib"},
                )
            )
    return out


async def fetch_and_parse(
    source: dict[str, Any],
    *,
    timeout_sec: float,
    user_agent: str,
) -> tuple[str, list[FeedItem]]:
    """Fetch and parse a source definition."""

    final_url, body = await fetch_feed_text(
        str(source["url"]),
        timeout_sec=timeout_sec,
        user_agent=user_agent,
    )
    return final_url, parse_feed(str(source["id"]), body)
