"""National Business Daily (每日经济新闻) — HTML list scraper.

NBD does not expose a public RSS or JSON API. We scrape the "要闻"
(headlines) column page which renders a stable ``<li>`` list.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

logger = logging.getLogger(__name__)

_ENTRY_URL = "https://www.nbd.com.cn/columns/3"

_ITEM_RE = re.compile(
    r'<a[^>]+href="(?P<url>https?://www\.nbd\.com\.cn/articles/\d{4}-\d{2}-\d{2}/[^"]+)"'
    r'[^>]*>(?P<title>[^<]+)</a>',
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r'(?P<date>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',
)


class NBDFetcher(BaseFetcher):
    source_id = "nbd"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            html = await fetch_text(client, _ENTRY_URL)
        return _parse_html(html)


def _parse_html(html: str) -> list[NormalizedItem]:
    out: list[NormalizedItem] = []
    seen_urls: set[str] = set()

    for m in _ITEM_RE.finditer(html):
        url = m.group("url").strip()
        title = m.group("title").strip()
        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)

        pub = None
        tail = html[m.end():m.end() + 300]
        dm = _DATE_RE.search(tail)
        if dm:
            pub = dm.group("date").strip().replace(" ", "T") + "Z"

        out.append(
            NormalizedItem(
                source_id="nbd",
                title=title,
                url=url,
                published_at=pub,
                extra={"via": "nbd_html"},
            )
        )
    return out


__all__ = ["NBDFetcher"]
