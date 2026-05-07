"""Yicai (第一财经) — public JSON API fetcher.

Yicai exposes unauthenticated JSON endpoints that the RSSHub ``yicai``
adapter also relies on.  Two are useful:

- ``/api/ajax/getlatest`` — latest news across all channels
- ``/api/ajax/getbrieflist`` — rolling flash/brief feed ("正在")

We default to ``getlatest`` and parse the returned JSON array into
:class:`NormalizedItem` rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import fetch_json, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem

logger = logging.getLogger(__name__)

_API_LATEST = "https://www.yicai.com/api/ajax/getlatest"
_API_BRIEF = "https://www.yicai.com/api/ajax/getbrieflist"


class YicaiFetcher(BaseFetcher):
    source_id = "yicai"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        page_size = 30
        url = f"{_API_LATEST}?page=1&pagesize={page_size}"
        async with make_client(timeout=self._timeout_sec) as client:
            data = await fetch_json(client, url)
        return _parse_latest(data)


def _parse_latest(data: Any) -> list[NormalizedItem]:
    if not isinstance(data, list):
        if isinstance(data, dict):
            data = data.get("data") or data.get("items") or []
        else:
            return []

    out: list[NormalizedItem] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = (
            item.get("title")
            or item.get("indexTitle")
            or item.get("NewsTitle")
            or item.get("Title")
            or ""
        ).strip()
        if not title:
            continue
        relative_url = item.get("url") or ""
        if relative_url and not relative_url.startswith("http"):
            article_url = f"https://www.yicai.com{relative_url}"
        else:
            article_url = relative_url
        if not article_url:
            continue

        summary = (
            item.get("NewsContent")
            or item.get("newcontent")
            or item.get("NewsNotes")
            or ""
        ).strip()
        pub = _parse_yicai_date(item)

        out.append(
            NormalizedItem(
                source_id="yicai",
                title=title,
                url=article_url,
                summary=summary[:500] if summary else None,
                published_at=pub,
                extra={"via": "yicai_api"},
            )
        )
    return out


def _parse_yicai_date(item: dict) -> str | None:
    raw = item.get("CreatedDate") or item.get("CreateDate") or item.get("date") or ""
    if not raw:
        datekey = item.get("datekey") or ""
        hm = item.get("hm") or ""
        if datekey and hm:
            raw = f"{datekey} {hm}"
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return raw if raw else None


__all__ = ["YicaiFetcher"]
