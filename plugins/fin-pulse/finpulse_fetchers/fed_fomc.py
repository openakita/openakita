"""Fed FOMC fetcher backed by the official press-release RSS.

Earlier this fetcher scraped the *calendar* page
(``fomccalendars.htm``) and gated ingest on a baked-in
``extra/fomc_release_calendar.txt`` schedule. Two things went wrong
in production:

1. The calendar page lists *meeting schedules*, not statements —
   anchors there matched helper pages (implementation notes, minutes)
   rather than freshly published statements, so the parser often
   returned 0 rows even on release day.
2. The schedule file was a hard gate: if today was not on the list,
   the fetcher returned an empty list *silently*, producing the
   "无新文章" card the user saw in the drawer screenshot.

The Federal Reserve publishes a proper RSS feed for monetary-policy
press releases at
``https://www.federalreserve.gov/feeds/press_monetary.xml`` — this is
the exact channel recommended on their `Syndicated Content` page.
We now treat that feed as the primary source (parsed through our
shared stdlib-or-feedparser helper) and fall back to the HTML scrape
only if the feed ever disappears. The calendar gate is kept but
downgraded to an *opt-in* optimisation flag
(``fed_fomc.use_calendar_gate=true``) so operators who actually
maintain the release file can still save a round-trip.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from finpulse_fetchers._http import fetch_text, make_client
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.rss import parse_feed

_CALENDAR_FILE = Path(__file__).resolve().parent.parent / "extra" / "fomc_release_calendar.txt"
_PRESS_RSS = "https://www.federalreserve.gov/feeds/press_monetary.xml"
_STATEMENTS_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

logger = logging.getLogger(__name__)


def _load_calendar() -> set[str]:
    if not _CALENDAR_FILE.exists():
        return set()
    out: set[str] = set()
    for line in _CALENDAR_FILE.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


class FedFOMCFetcher(BaseFetcher):
    source_id = "fed_fomc"

    @property
    def supports_since(self) -> bool:
        return True

    async def fetch(self, *, since: datetime | None = None, **_: Any) -> list[NormalizedItem]:
        # Opt-in schedule gate — off by default so daily FOMC press
        # items (implementation notes, on-the-record speeches) keep
        # flowing between meeting weeks.
        use_gate = (
            self._config.get("fed_fomc.use_calendar_gate", "false") or ""
        ).strip().lower() == "true"
        if use_gate:
            calendar = _load_calendar()
            today = datetime.now(timezone.utc).date().isoformat()
            if calendar and today not in calendar:
                return []

        cursor = self._config.get("fed_fomc.most_recent_date", "")

        items = await self._fetch_rss(cursor_date=cursor)
        if items:
            return items[:20]

        # RSS is by far the more reliable path; the HTML scrape is only
        # a safety net for the (rare) case the Fed retires the feed.
        logger.info("fed_fomc RSS empty — falling back to HTML scrape")
        html_items = await self._fetch_html(cursor_date=cursor)
        return html_items[:20]

    async def _fetch_rss(self, *, cursor_date: str) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            try:
                body = await fetch_text(client, _PRESS_RSS)
            except Exception as exc:  # noqa: BLE001
                logger.info("fed_fomc RSS fetch failed: %s", exc)
                return []
        parsed = parse_feed(self.source_id, body)
        if not cursor_date:
            return parsed
        return [it for it in parsed if not it.published_at or it.published_at[:10] > cursor_date]

    async def _fetch_html(self, *, cursor_date: str) -> list[NormalizedItem]:
        async with make_client(timeout=self._timeout_sec) as client:
            try:
                body = await fetch_text(client, _STATEMENTS_URL)
            except Exception as exc:  # noqa: BLE001
                logger.info("fed_fomc HTML fetch failed: %s", exc)
                return []
        return self._parse_html(body, cursor_date=cursor_date)

    @staticmethod
    def _parse_html(html: str, *, cursor_date: str = "") -> list[NormalizedItem]:
        try:
            import bs4  # type: ignore
        except ImportError:
            # Regex fallback so the HTML safety net still works on hosts
            # without BeautifulSoup installed.
            return FedFOMCFetcher._parse_html_regex(html, cursor_date=cursor_date)
        soup = bs4.BeautifulSoup(html, "html.parser")
        items: list[NormalizedItem] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if "newsevents/pressreleases" not in href:
                continue
            title = (anchor.text or "").strip()
            if not title or len(title) < 6:
                continue
            if not href.startswith("http"):
                href = "https://www.federalreserve.gov" + href
            published = _extract_iso_date(href)
            if cursor_date and published and published <= cursor_date:
                continue
            if href in seen:
                continue
            seen.add(href)
            items.append(
                NormalizedItem(
                    source_id="fed_fomc",
                    title=title,
                    url=href,
                    published_at=published,
                    extra={"via": "html"},
                )
            )
        return items

    @staticmethod
    def _parse_html_regex(html: str, *, cursor_date: str = "") -> list[NormalizedItem]:
        import re

        anchor_re = re.compile(
            r'<a\s+href="(?P<href>[^"]*newsevents/pressreleases[^"]+)"'
            r"[^>]*>(?P<text>[^<]+)</a>",
            re.IGNORECASE,
        )
        items: list[NormalizedItem] = []
        seen: set[str] = set()
        for m in anchor_re.finditer(html):
            href = m.group("href").strip()
            text = m.group("text").strip()
            if not text or len(text) < 6:
                continue
            if not href.startswith("http"):
                href = "https://www.federalreserve.gov" + href
            if href in seen:
                continue
            seen.add(href)
            published = _extract_iso_date(href)
            if cursor_date and published and published <= cursor_date:
                continue
            items.append(
                NormalizedItem(
                    source_id="fed_fomc",
                    title=text,
                    url=href,
                    published_at=published,
                    extra={"via": "html_regex"},
                )
            )
        return items


def _extract_iso_date(href: str) -> str | None:
    import re

    match = re.search(r"(20\d{2})(\d{2})(\d{2})", href)
    if not match:
        return None
    try:
        parts = tuple(int(g) for g in match.groups())
        d = date(parts[0], parts[1], parts[2])
    except ValueError:
        return None
    return d.isoformat()


__all__ = ["FedFOMCFetcher"]
