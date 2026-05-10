# ruff: noqa: N999
"""HTML listing fetcher for news sites without public RSS feeds.

Many Chinese news outlets (中国台湾网、东南网、台海网 等) only publish
HTML listing pages and never shipped a stable RSS feed. This module
provides a small, SSRF-safe scraper that returns ``FeedItem`` objects
compatible with the existing RSS pipeline.

Two extraction strategies:

1. **Explicit selectors**: each source can declare CSS selectors via the
   ``selectors`` config (``item`` / ``title`` / ``link`` / ``link_attr``
   / ``title_attr``). Editors can tweak them per site without code edits.
2. **Heuristic fallback**: if selectors are missing or under-deliver,
   walk every ``<a>`` tag and keep the ones whose href looks like a news
   article (path contains ``/news/``, ``/jsbg/``, ``/twxw/``, ``.shtml``
   etc., or has a date-like segment) and whose visible text length is
   sensible for a headline.

Both paths reuse :func:`media_fetchers.rss.validate_feed_url` so private
IPs and localhost stay rejected the same way as RSS feeds.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from media_fetchers.rss import (
    FeedItem,
    UnsafeFeedUrl,
    _decode_response_text,
    _infer_date_from_url,
    validate_feed_url,
)

try:  # pragma: no cover - optional fast path
    from bs4 import BeautifulSoup, Tag  # type: ignore

    BS4_AVAILABLE = True
except Exception:  # noqa: BLE001
    BeautifulSoup = None  # type: ignore[assignment]
    Tag = None  # type: ignore[assignment]
    BS4_AVAILABLE = False

_MAX_REDIRECTS = 8
_TITLE_MIN = 6
_TITLE_MAX = 90
_DEFAULT_MAX_ITEMS = 60

# Path fragments that strongly suggest "this anchor is an article". The
# heuristic mode treats a URL as a candidate when it matches any of these
# OR ends with ``.shtml/.html/.htm`` and has a non-trivial path length OR
# embeds a numeric segment that looks like a date or article id.
_ARTICLE_PATH_HINTS: tuple[str, ...] = (
    "/news/",
    "/article/",
    "/jsbg/",
    "/twxw/",
    "/taihai/",
    "/taiwan/",
    "/cross_",
    "/cross-strait",
    "/cn/",
    "/c/",
    "/p/",
    "/zt/",
    "/local/",
    "/world/",
    "/politic/",
    "/society/",
    "/finance/",
    "/economy/",
    "/tech/",
)
_ARTICLE_ID_RE = re.compile(r"/\d{4,}(?:[-/_]\d{2,})*")


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_article(href: str) -> bool:
    if not href:
        return False
    lowered = href.lower()
    if lowered.startswith(("javascript:", "mailto:", "tel:", "#")):
        return False
    parsed = urlparse(href)
    path = (parsed.path or "/").lower()
    if path in {"", "/"}:
        return False
    if any(hint in path for hint in _ARTICLE_PATH_HINTS):
        return True
    if path.endswith((".shtml", ".html", ".htm")) and len(path) > 12:
        return True
    if _ARTICLE_ID_RE.search(path):
        return True
    return False


async def fetch_html_text(
    url: str,
    *,
    timeout_sec: float = 20.0,
    user_agent: str = "OpenAkita-MediaStrategy/0.1",
) -> tuple[str, str]:
    """Fetch HTML with the same SSRF-safe redirect handling as the RSS fetcher."""

    current = validate_feed_url(url)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
    async with httpx.AsyncClient(
        timeout=timeout_sec, follow_redirects=False, headers=headers
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.get(current)
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                return str(response.url), _decode_response_text(response)
            location = response.headers.get("Location", "")
            if not location:
                response.raise_for_status()
            current = validate_feed_url(urljoin(current, location))
    raise UnsafeFeedUrl(f"too many redirects fetching {url!r}")


def _extract_with_selectors(
    soup: Any,
    base_url: str,
    selectors: dict[str, Any],
    seen_urls: set[str],
    source_id: str,
    max_items: int,
) -> list[FeedItem]:
    items: list[FeedItem] = []
    item_selector = (selectors.get("item") or "").strip()
    if not item_selector:
        return items
    title_selector = (selectors.get("title") or "").strip()
    link_selector = (selectors.get("link") or "").strip()
    title_attr = (selectors.get("title_attr") or "").strip()
    link_attr = (selectors.get("link_attr") or "href").strip() or "href"

    for node in soup.select(item_selector):
        title_node = node.select_one(title_selector) if title_selector else node
        link_node = node.select_one(link_selector) if link_selector else node
        if title_node is None or link_node is None:
            continue
        if title_attr:
            title = _normalized_text(title_node.get(title_attr))
        else:
            title = _normalized_text(title_node.get_text())
        href = (link_node.get(link_attr) or "").strip()
        if not title or not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen_urls:
            continue
        if not (_TITLE_MIN <= len(title) <= 120):
            continue
        published_at = _infer_date_from_url(absolute)
        if not published_at:
            continue
        seen_urls.add(absolute)
        items.append(
            FeedItem(
                source_id=source_id,
                title=title,
                url=absolute,
                summary="",
                published_at=published_at,
                raw={"parser": "html_explicit"},
            )
        )
        if len(items) >= max_items:
            break
    return items


def _extract_heuristic(
    soup: Any,
    base_url: str,
    seen_urls: set[str],
    source_id: str,
    max_items: int,
) -> list[FeedItem]:
    items: list[FeedItem] = []
    base_root = base_url.rstrip("/")
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href or not _looks_like_article(href):
            continue
        text = _normalized_text(anchor.get_text())
        if not text:
            # title="..." 兜底（部分模板把标题塞 title 属性里）
            text = _normalized_text(anchor.get("title"))
        if not text or not (_TITLE_MIN <= len(text) <= _TITLE_MAX):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen_urls or absolute.rstrip("/") == base_root:
            continue
        published_at = _infer_date_from_url(absolute)
        if not published_at:
            continue
        seen_urls.add(absolute)
        items.append(
            FeedItem(
                source_id=source_id,
                title=text,
                url=absolute,
                summary="",
                published_at=published_at,
                raw={"parser": "html_heuristic"},
            )
        )
        if len(items) >= max_items:
            break
    return items


def parse_html_listing(
    source_id: str,
    html: str,
    base_url: str,
    selectors: dict[str, Any] | None = None,
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[FeedItem]:
    """Parse a listing page into ``FeedItem`` candidates.

    Tries explicit selectors first; if they yield fewer than 5 items the
    heuristic anchor scan kicks in to top up the list. Both share the
    same ``seen_urls`` set so duplicates are pruned consistently.
    """

    if not BS4_AVAILABLE:
        raise RuntimeError("beautifulsoup4 is required for HTML sources")
    selectors = selectors or {}
    soup = BeautifulSoup(html, "html.parser")
    seen_urls: set[str] = set()

    items = _extract_with_selectors(
        soup, base_url, selectors, seen_urls, source_id, max_items
    )
    if len(items) < 5:
        items.extend(
            _extract_heuristic(
                soup, base_url, seen_urls, source_id, max_items - len(items)
            )
        )
    return items[:max_items]


async def fetch_and_parse_html(
    source: dict[str, Any],
    *,
    timeout_sec: float,
    user_agent: str,
) -> tuple[str, list[FeedItem]]:
    """Fetch and parse an HTML-style source definition."""

    final_url, body = await fetch_html_text(
        str(source["url"]),
        timeout_sec=timeout_sec,
        user_agent=user_agent,
    )
    selectors = source.get("selectors") or {}
    return final_url, parse_html_listing(
        str(source["id"]), body, final_url, selectors
    )
