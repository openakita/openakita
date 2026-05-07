"""EastMoney (东方财富) — 证券聚焦 HTML list scraper.

Unlike the other three CN hot-list sources (``wallstreetcn`` / ``cls`` /
``xueqiu``), NewsNow does not expose an ``eastmoney`` platform — the
public aggregator replies ``{"error":true,"message":"Invalid source id"}``
for every variant we tried (``eastmoney`` / ``eastmoney-hot`` /
``eastmoney-bulletin``). The old private JSON endpoint at
``np-listapi.eastmoney.com/comm/web/getListInfo`` now refuses every
unsigned request with ``Required String parameter 'mTypeAndCode' is not
present``, so we stopped pretending to use it.

The replacement path scrapes the public "证券聚焦" rolling page at
``finance.eastmoney.com/a/czqyw.html`` which stays anonymous-friendly
and ships a stable HTML layout (``<ul>`` of ``<li>`` with a titled
``<a>`` per item). A regex-based extractor is used so we stay
independent of BeautifulSoup availability.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import fetch_json, fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

try:  # pragma: no cover — prefer bs4 when available, stdlib regex works otherwise
    import bs4  # type: ignore

    BS4_AVAILABLE = True
except ImportError:
    bs4 = None  # type: ignore
    BS4_AVAILABLE = False


# "证券聚焦" (Securities focus) aggregates the newsroom's rolling feed
# and is served as static HTML. The ``czqyw.html`` slug is stable
# since 2023; the ``.html`` pagination suffix (``_2``, ``_3``, …) gives
# us more history if we ever need it.
_ENTRY = "https://finance.eastmoney.com/a/czqyw.html"
_API = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"

# Items in the list look roughly like:
#   <p class="title"><a href="//finance.eastmoney.com/a/202604241234567890.html"
#      target="_blank" title="A股三大指数低开…">A股三大指数低开…</a></p>
# Some pagination variants render ``<li>`` wrappers — regex catches
# either shape by anchoring on the eastmoney news URL pattern.
_ITEM_RE = re.compile(
    r'<a\s+href="(?P<href>(?:https?:)?//(?:finance|stock|forex|money|bond|data)'
    r'\.eastmoney\.com/(?:a|news)/[^"]+\.html)"'
    r'[^>]*?title="(?P<title>[^"]+)"',
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r'<(?:span|p)[^>]*class="[^"]*(?:time|infor)[^"]*"[^>]*>\s*'
    r'(?P<date>\d{4}-\d{1,2}-\d{1,2}[^<]{0,12})\s*</(?:span|p)>',
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


class EastmoneyFetcher(BaseFetcher):
    source_id = "eastmoney"
    # Kept for backward-compat — the pipeline inspects this constant
    # but there is no working NewsNow mapping for eastmoney yet.
    NEWSNOW_PLATFORM_ID = "eastmoney"

    def __init__(
        self, *, config: dict[str, str] | None = None, timeout_sec: float = 15.0
    ) -> None:
        super().__init__(config=config, timeout_sec=timeout_sec)
        self._last_via: str = "none"
        self._last_via_reason: str | None = None

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        # NewsNow returns ``{error:true, message:"Invalid source id"}``
        # for every eastmoney variant — retrying on each run just wastes
        # a round-trip. If and when NewsNow adds eastmoney support the
        # ``prefer_newsnow`` knob below can flip the behaviour without
        # a code change.
        prefer_newsnow = (
            self._config.get("source.eastmoney.prefer_newsnow") or ""
        ).strip().lower() == "true"
        if prefer_newsnow:
            from finpulse_fetchers.newsnow_base import (
                NewsNowTransportError,
                fetch_from_newsnow,
            )

            try:
                primary = await fetch_from_newsnow(
                    platform_id=self.NEWSNOW_PLATFORM_ID,
                    source_id=self.source_id,
                    config=self._config,
                    timeout_sec=self._timeout_sec,
                )
            except NewsNowTransportError as exc:
                logger.info(
                    "eastmoney via newsnow failed (%s): %s — falling back to direct",
                    exc.kind,
                    exc,
                )
                self._last_via_reason = f"newsnow:{exc.kind}"
                primary = []
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "eastmoney via newsnow failed: %s — falling back to direct", exc
                )
                self._last_via_reason = (
                    f"newsnow:error:{exc.__class__.__name__}"
                )
                primary = []
            if primary:
                self._last_via = "newsnow"
                self._last_via_reason = None
                return primary

        if (self._config.get("source.eastmoney.fallback_direct") or "true").lower() == "false":
            self._last_via = "none"
            return []

        direct = await self._fetch_direct()
        self._last_via = "direct" if direct else "none"
        return direct

    async def _fetch_direct(self) -> list[NormalizedItem]:
        try:
            items = await self._fetch_api()
            if items:
                return items
        except Exception as exc:  # noqa: BLE001
            logger.info("eastmoney api fetch failed, falling back to html: %s", exc)

        async with make_client(timeout=self._timeout_sec) as client:
            body = await fetch_text(client, _ENTRY)
        items = self._parse(body)
        # Hard cap — the rolling page loads ~80 items per render; we
        # keep the fresh 30 to stay consistent with the other CN sources.
        return items[:30]

    async def _fetch_api(self) -> list[NormalizedItem]:
        """Fetch EastMoney's current dynamic list API.

        The public page now renders ``#newsListContent`` empty and lets
        ``newslistbefore.js`` call this endpoint. Scraping the static HTML
        therefore returns zero rows even though the site has fresh news.
        """
        params = {
            "client": "web",
            "biz": "web_news_col",
            "column": "353",
            "order": "1",
            "needInteractData": "0",
            "page_index": "1",
            "page_size": "30",
            "req_trace": str(int(time.time() * 1000)),
            "fields": "code,showTime,title,mediaName,summary,image,url,uniqueUrl,Np_dst",
            "types": "1,20",
        }
        async with make_client(timeout=self._timeout_sec) as client:
            data = await fetch_json(client, _API, params=params)
        return self._parse_api(data)

    @staticmethod
    def _parse_api(data: Any) -> list[NormalizedItem]:
        if not isinstance(data, dict):
            return []
        payload = data.get("data")
        if not isinstance(payload, dict):
            return []
        rows = payload.get("list")
        if not isinstance(rows, list):
            return []

        out: list[NormalizedItem] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            url = str(row.get("uniqueUrl") or row.get("url") or "").strip()
            if not title or len(title) < 6 or not url:
                continue
            if url.startswith("//"):
                url = "https:" + url
            if url in seen:
                continue
            seen.add(url)
            pub = _parse_eastmoney_time(row.get("showTime"))
            out.append(
                NormalizedItem(
                    source_id="eastmoney",
                    title=title,
                    url=url,
                    summary=str(row.get("summary") or "").strip()[:500] or None,
                    published_at=pub,
                    extra={
                        "parser": "eastmoney_api",
                        "media": row.get("mediaName"),
                        "image": row.get("image"),
                    },
                )
            )
        return out

    @staticmethod
    def _parse(html: str) -> list[NormalizedItem]:
        if BS4_AVAILABLE:
            return EastmoneyFetcher._parse_bs4(html)
        return EastmoneyFetcher._parse_regex(html)

    @staticmethod
    def _parse_bs4(html: str) -> list[NormalizedItem]:
        soup = bs4.BeautifulSoup(html, "html.parser")  # type: ignore[union-attr]
        out: list[NormalizedItem] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href][title]"):
            href = (anchor.get("href") or "").strip()
            title = (anchor.get("title") or anchor.text or "").strip()
            if not href or not title or len(title) < 6:
                continue
            if "eastmoney.com/" not in href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            if href in seen:
                continue
            seen.add(href)
            out.append(
                NormalizedItem(
                    source_id="eastmoney",
                    title=title,
                    url=href,
                    published_at=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    extra={"parser": "bs4"},
                )
            )
        return out

    @staticmethod
    def _parse_regex(html: str) -> list[NormalizedItem]:
        out: list[NormalizedItem] = []
        seen: set[str] = set()
        for m in _ITEM_RE.finditer(html):
            href = m.group("href")
            title = m.group("title").strip()
            if not href or not title or len(title) < 6:
                continue
            if href.startswith("//"):
                href = "https:" + href
            if href in seen:
                continue
            seen.add(href)
            out.append(
                NormalizedItem(
                    source_id="eastmoney",
                    title=title,
                    url=href,
                    published_at=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    extra={"parser": "regex"},
                )
            )
        return out


def _parse_eastmoney_time(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return text


__all__ = ["EastmoneyFetcher"]
