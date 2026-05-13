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

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from media_fetchers.rss import (
    FeedItem,
    UnsafeFeedUrl,
    _decode_response_text,
    _infer_date_from_url,
    _parse_date,
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
_CHINATIMES_DATE_RE = re.compile(r"/(?:realtimenews|newspapers)/((?:19|20)\d{2})(\d{2})(\d{2})")
_HUANQIU_INLINE_RE = re.compile(
    r"(?P<id>[0-9A-Za-z]{8,})article"
    r"(?:(?://[^\s<]+?\.(?:jpg|jpeg|png|webp))?)"
    r"(?P<title>[^<\n\r]{6,120}?)"
    r"(?P<domain>(?:taiwan|world|china)\.huanqiu\.com)"
    r"(?P<ts>\d{13})?",
    re.IGNORECASE,
)
_JSON_LD_TYPES = {"newsarticle", "article", "reportageNewsArticle".lower()}


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _iso_from_epoch_ms(value: str | None) -> str | None:
    if not value:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _infer_chinatimes_date(url: str) -> str | None:
    match = _CHINATIMES_DATE_RE.search(url)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=UTC,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _meta_content(soup: Any, *names: str) -> str:
    for name in names:
        node = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if node is not None:
            value = _normalized_text(node.get("content"))
            if value:
                return value
    return ""


def _json_ld_objects(soup: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                stack.extend(item["@graph"])
            elif isinstance(item, dict):
                objects.append(item)
    return objects


def _best_json_ld_article(soup: Any) -> dict[str, Any]:
    for item in _json_ld_objects(soup):
        raw_type = item.get("@type") or ""
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        normalized = {str(t).lower() for t in types}
        if normalized.intersection(_JSON_LD_TYPES):
            return item
    return {}


def _summary_from_article_body(soup: Any) -> str:
    candidates = [
        "article",
        ".article",
        ".content",
        ".article-content",
        ".main-content",
        ".post-content",
        "#content",
        "#article",
    ]
    for selector in candidates:
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _normalized_text(node.get_text(" "))
        if len(text) >= 40:
            return text[:500]
    paragraphs = [
        _normalized_text(p.get_text(" "))
        for p in soup.find_all("p")
        if len(_normalized_text(p.get_text(" "))) >= 20
    ]
    return _normalized_text(" ".join(paragraphs))[:500]


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


def parse_huanqiu_channel(
    source_id: str,
    html: str,
    base_url: str,
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[FeedItem]:
    """Parse Huanqiu channel pages rendered from compact CSR config blobs."""

    out: list[FeedItem] = []
    seen: set[str] = set()
    if BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select(".data-container .item"):
            aid = _normalized_text(
                node.select_one(".item-aid").get_text(" ") if node.select_one(".item-aid") else ""
            )
            addltype = _normalized_text(
                node.select_one(".item-addltype").get_text(" ")
                if node.select_one(".item-addltype")
                else "article"
            )
            title = _normalized_text(
                node.select_one(".item-title").get_text(" ")
                if node.select_one(".item-title")
                else ""
            )
            host = _normalized_text(
                node.select_one(".item-cnf-host").get_text(" ")
                if node.select_one(".item-cnf-host")
                else urlparse(base_url).netloc
            )
            ts = _normalized_text(
                node.select_one(".item-time").get_text(" ") if node.select_one(".item-time") else ""
            )
            if not aid or addltype != "article" or not (_TITLE_MIN <= len(title) <= 120):
                continue
            url = f"https://{host}/article/{aid}"
            if url in seen:
                continue
            published_at = _iso_from_epoch_ms(ts)
            if not published_at:
                continue
            seen.add(url)
            out.append(
                FeedItem(
                    source_id=source_id,
                    title=title,
                    url=url,
                    published_at=published_at,
                    raw={"parser": "huanqiu_csr", "source_final_url": base_url},
                )
            )
            if len(out) >= max_items:
                break
    if out:
        out.sort(key=lambda item: item.published_at or "", reverse=True)
        return out[:max_items]
    for match in _HUANQIU_INLINE_RE.finditer(html):
        title = _normalized_text(match.group("title"))
        title = re.sub(r"^.*?jpg", "", title, flags=re.IGNORECASE).strip()
        if not (_TITLE_MIN <= len(title) <= 120):
            continue
        domain = match.group("domain")
        article_id = match.group("id")
        url = f"https://{domain}/article/{article_id}"
        if url in seen:
            continue
        published_at = _iso_from_epoch_ms(match.group("ts")) or _infer_date_from_url(url)
        if not published_at:
            continue
        seen.add(url)
        out.append(
            FeedItem(
                source_id=source_id,
                title=title,
                url=url,
                published_at=published_at,
                raw={"parser": "huanqiu_csr", "source_final_url": base_url},
            )
        )
        if len(out) >= max_items:
            break
    out.sort(key=lambda item: item.published_at or "", reverse=True)
    return out


def parse_chinatimes_listing(
    source_id: str,
    html: str,
    base_url: str,
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[FeedItem]:
    """Parse ChinaTimes listing pages with date-bearing article URLs."""

    if not BS4_AVAILABLE:
        raise RuntimeError("beautifulsoup4 is required for HTML sources")
    soup = BeautifulSoup(html, "html.parser")
    items: list[FeedItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if "/realtimenews/" not in href and "/newspapers/" not in href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = _normalized_text(anchor.get_text(" ")) or _normalized_text(anchor.get("title"))
        if not (_TITLE_MIN <= len(title) <= 120):
            continue
        published_at = _infer_date_from_url(url) or _infer_chinatimes_date(url)
        if not published_at:
            continue
        seen.add(url)
        items.append(
            FeedItem(
                source_id=source_id,
                title=title,
                url=url,
                published_at=published_at,
                raw={"parser": "chinatimes_listing", "source_final_url": base_url},
            )
        )
        if len(items) >= max_items:
            break
    items.sort(key=lambda item: item.published_at or "", reverse=True)
    return items


def parse_single_article(
    source_id: str,
    html: str,
    base_url: str,
    *,
    allow_fetched_time: bool = False,
) -> FeedItem:
    """Parse one public news article page into the same item shape as feeds."""

    if not BS4_AVAILABLE:
        raise RuntimeError("beautifulsoup4 is required for HTML sources")
    soup = BeautifulSoup(html, "html.parser")
    json_article = _best_json_ld_article(soup)
    title = (
        _normalized_text(str(json_article.get("headline") or ""))
        or _meta_content(soup, "og:title", "twitter:title")
        or _normalized_text(soup.title.get_text(" ") if soup.title else "")
    )
    url = (
        _normalized_text(str(json_article.get("url") or ""))
        or _meta_content(soup, "og:url")
        or base_url
    )
    summary = (
        _normalized_text(str(json_article.get("description") or ""))
        or _meta_content(soup, "description", "og:description", "twitter:description")
        or _summary_from_article_body(soup)
    )
    author = ""
    raw_author = json_article.get("author") if isinstance(json_article, dict) else None
    if isinstance(raw_author, dict):
        author = _normalized_text(str(raw_author.get("name") or ""))
    elif isinstance(raw_author, list) and raw_author:
        first = raw_author[0]
        author = _normalized_text(str(first.get("name") if isinstance(first, dict) else first))
    elif raw_author:
        author = _normalized_text(str(raw_author))
    published_text = (
        str(json_article.get("datePublished") or json_article.get("dateModified") or "")
        or _meta_content(
            soup,
            "article:published_time",
            "article:modified_time",
            "pubdate",
            "publishdate",
            "date",
        )
    )
    if not published_text:
        time_node = soup.find("time")
        if time_node is not None:
            published_text = str(time_node.get("datetime") or time_node.get_text(" "))
    published_at = _parse_date(published_text) or _infer_date_from_url(url)
    date_source = "page"
    if not published_at and allow_fetched_time:
        published_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        date_source = "fetched_at"
    if not title or not url:
        raise ValueError("article page does not expose a reliable title or url")
    if not published_at:
        raise ValueError("article page does not expose a reliable published time")
    return FeedItem(
        source_id=source_id,
        title=title[:140],
        url=url,
        summary=summary,
        published_at=published_at,
        author=author,
        raw={"parser": "single_article", "source_final_url": base_url, "date_source": date_source},
    )


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
                raw={"parser": "html_explicit", "source_final_url": base_url, "date_source": "url"},
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
                raw={"parser": "html_heuristic", "source_final_url": base_url, "date_source": "url"},
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
    items.sort(key=lambda item: item.published_at or "", reverse=True)
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
    parser = str(selectors.get("parser") or "").strip()
    source_id = str(source["id"])
    if parser == "huanqiu_csr":
        return final_url, parse_huanqiu_channel(source_id, body, final_url)
    if parser == "chinatimes_listing":
        return final_url, parse_chinatimes_listing(source_id, body, final_url)
    if parser == "news_article":
        return final_url, [
            parse_single_article(
                source_id,
                body,
                final_url,
                allow_fetched_time=bool(selectors.get("allow_fetched_time")),
            )
        ]
    return final_url, parse_html_listing(source_id, body, final_url, selectors)


async def fetch_and_parse_article_url(
    url: str,
    *,
    source_id: str = "manual-url",
    timeout_sec: float,
    user_agent: str,
    allow_fetched_time: bool = False,
) -> tuple[str, FeedItem]:
    """Fetch one user-supplied news page and parse it as a temporary article."""

    final_url, body = await fetch_html_text(url, timeout_sec=timeout_sec, user_agent=user_agent)
    item = parse_single_article(
        source_id,
        body,
        final_url,
        allow_fetched_time=allow_fetched_time,
    )
    return final_url, item
