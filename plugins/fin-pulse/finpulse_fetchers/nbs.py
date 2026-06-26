"""National Bureau of Statistics of China (国家统计局) — RSS-first."""

from __future__ import annotations

from typing import Any

from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.rss import fetch_one_feed


_RSS_URL = "https://www.stats.gov.cn/sj/zxfb/rss.xml"


class NBSFetcher(BaseFetcher):
    source_id = "nbs"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        return await fetch_one_feed(self.source_id, _RSS_URL, timeout=self._timeout_sec)


__all__ = ["NBSFetcher"]
