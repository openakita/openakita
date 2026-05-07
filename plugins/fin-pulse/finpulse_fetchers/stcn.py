"""Securities Times (证券时报) — HTML list scraper.

STCN's old RSS feeds (``app.stcn.com/rss.php``) are dead. We scrape the
public article-list page which is stable and anonymous-friendly.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

logger = logging.getLogger(__name__)

_ENTRY_URL = "https://www.stcn.com/article/list/yw.html"

_ITEM_RE = re.compile(
    r'<a[^>]+href="(?P<url>(?:(?:https?:)?//(?:www\.)?stcn\.com)?'
    r'/article/detail/[^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*(?P<title>[^<]+?)\s*</a>',
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r'(?P<date>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
)


class STCNFetcher(BaseFetcher):
    source_id = "stcn"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            html = await fetch_text(client, _ENTRY_URL)
        return _parse_html(html)


def _parse_html(html: str) -> list[NormalizedItem]:
    out: list[NormalizedItem] = []
    seen_urls: set[str] = set()

    for m in _ITEM_RE.finditer(html):
        raw_url = m.group("url").strip()
        title = m.group("title").strip()
        if not title or not raw_url:
            continue

        if not raw_url.startswith("http"):
            raw_url = f"https://www.stcn.com{raw_url}" if raw_url.startswith("/") else f"https://{raw_url}"
        if raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)

        pub = None
        tail = html[m.end():m.end() + 300]
        dm = _DATE_RE.search(tail)
        if dm:
            pub = dm.group("date").strip().replace(" ", "T") + ":00Z"

        out.append(
            NormalizedItem(
                source_id="stcn",
                title=title,
                url=raw_url,
                published_at=pub,
                extra={"via": "stcn_html"},
            )
        )
    return out


__all__ = ["STCNFetcher"]
