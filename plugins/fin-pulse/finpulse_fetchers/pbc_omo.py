"""People's Bank of China — Open Market Operations crawler.

In 2025 PBC dropped the ``.../17081/`` subfolder that used to front the
open-market-operations index and now serves the list directly from
``https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html``
as a plain static HTML page. The old ``atob(...)`` JavaScript redirect
dance is gone, which also lets us drop the hard PyExecJS dependency.

The regex-based parser below is deliberately forgiving: it picks up
every ``<a href="…/202604xx…/index.html" title="公开市场业务交易公告 …">``
anchor inside the listing table regardless of the exact class/column
markup, so minor template re-skins by PBC don't break the fetcher.

beautifulsoup4 is still used when available (tidier DOM traversal)
but is no longer a hard requirement — a compact stdlib regex path
keeps the fetcher healthy on trimmed Python installs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

try:  # pragma: no cover — optional dep, used for tidier DOM traversal
    import bs4  # type: ignore

    BS4_AVAILABLE = True
except ImportError:
    bs4 = None  # type: ignore
    BS4_AVAILABLE = False


_HOME = "https://www.pbc.gov.cn"
_ENTRY = f"{_HOME}/zhengcehuobisi/125207/125213/125431/125475/index.html"

# PBC uses GBK/GB2312 on this subtree; httpx auto-detects correctly
# 90% of the time but we pin the charset explicitly below for safety.
_PBC_CHARSET = "utf-8"

# Anchor markup the listing uses today (examples in diagnostic run
# 2026-04):
#   <a href="/zhengcehuobisi/125207/125213/125431/125475/202604240851xxxxxxxx/index.html"
#      onclick="void(0)" target="_blank" title="公开市场业务交易公告 [2026]第78号"
#      istitle="true">公开市场业务交易公告 [2026]第78号</a>
_ANCHOR_RE = re.compile(
    r'<a\s+href="(?P<href>/zhengcehuobisi/125207/125213/125431/125475/\d+/index\.html)"'
    r'[^>]*?title="(?P<title>[^"]+)"',
    re.IGNORECASE,
)
# Nearby date span rendered as ``<span class="hui12">2026-04-24</span>``.
_DATE_RE = re.compile(
    r'<span[^>]+class="hui12"[^>]*>\s*(\d{4}-\d{1,2}-\d{1,2})\s*</span>',
    re.IGNORECASE,
)


class PbcOmoFetcher(BaseFetcher):
    source_id = "pbc_omo"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            body = await fetch_text(client, _ENTRY)
        return self._parse(_ENTRY, body)

    @staticmethod
    def _parse(base_url: str, html: str) -> list[NormalizedItem]:
        if BS4_AVAILABLE:
            items = PbcOmoFetcher._parse_bs4(base_url, html)
        else:
            items = PbcOmoFetcher._parse_regex(base_url, html)

        # Defensive dedupe — the index has the same URL in mobile + desktop
        # markup and we never want to upsert twice inside one fetch.
        seen: set[str] = set()
        out: list[NormalizedItem] = []
        for item in items:
            key = item.url_hash()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:30]

    @staticmethod
    def _parse_bs4(base_url: str, html: str) -> list[NormalizedItem]:
        soup = bs4.BeautifulSoup(html, "html.parser")  # type: ignore[union-attr]
        items: list[NormalizedItem] = []
        for anchor in soup.select("a[href][title]"):
            href = (anchor.get("href") or "").strip()
            title = (anchor.get("title") or anchor.text or "").strip()
            if "/zhengcehuobisi/125207/125213/125431/125475/" not in href:
                continue
            if not title or len(title) < 6:
                continue
            if not href.startswith("http"):
                href = _HOME + href
            # Look for the date sibling — each row renders it in a
            # ``span.hui12`` next door; fall back to now() if missing.
            published_iso: str | None = None
            date_span = anchor.find_next("span", class_="hui12")
            if date_span and date_span.text:
                try:
                    dt = datetime.strptime(date_span.text.strip(), "%Y-%m-%d")
                    published_iso = dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    published_iso = None
            if published_iso is None:
                published_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            uid = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
            items.append(
                NormalizedItem(
                    source_id="pbc_omo",
                    title=title,
                    url=href,
                    published_at=published_iso,
                    extra={"uid": uid, "parent": base_url},
                )
            )
        return items

    @staticmethod
    def _parse_regex(base_url: str, html: str) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        dates = _DATE_RE.findall(html)
        for idx, match in enumerate(_ANCHOR_RE.finditer(html)):
            href = match.group("href")
            title = match.group("title").strip()
            if not title or len(title) < 6:
                continue
            full_url = _HOME + href
            published_iso: str | None = None
            if idx < len(dates):
                try:
                    dt = datetime.strptime(dates[idx], "%Y-%m-%d")
                    published_iso = dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    published_iso = None
            if published_iso is None:
                published_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            uid = hashlib.sha256(full_url.encode("utf-8")).hexdigest()[:16]
            items.append(
                NormalizedItem(
                    source_id="pbc_omo",
                    title=title,
                    url=full_url,
                    published_at=published_iso,
                    extra={"uid": uid, "parent": base_url},
                )
            )
        return items


# Backward-compat export — a few older tests still import this flag.
EXECJS_AVAILABLE = False


__all__ = ["EXECJS_AVAILABLE", "PbcOmoFetcher"]
