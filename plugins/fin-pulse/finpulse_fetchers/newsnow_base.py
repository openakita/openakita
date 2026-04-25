"""Shared NewsNow aggregator helper.

The public NewsNow API speaks a compact envelope
``{status, items:[{title, url, mobileUrl, desc, ...}]}``. We keep that
contract in one helper so the CN hot-list fetchers
(``wallstreetcn`` / ``cls`` / ``eastmoney`` / ``xueqiu``) can try the
aggregator first and only fall back to their fragile direct scrapers
when the aggregator is unavailable.

Keeping the logic here (instead of duplicating it into every fetcher)
gives us one testable surface: the parse rules, the retry behaviour,
and the rate-limit hook live in one place and every CN fetcher opts in
by pointing at a platform id.

The helper keeps our own canonical-item payload (``NormalizedItem``) so
dedupe via ``articles.url_hash`` stays consistent.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from finpulse_fetchers._http import make_client
from finpulse_fetchers.base import NormalizedItem

try:  # pragma: no cover — httpx ships with the host.
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

logger = logging.getLogger(__name__)


class NewsNowTransportError(RuntimeError):
    """Raised when the NewsNow upstream call surfaces a retryable problem.

    Common causes observed in the wild:

    - ``cloudflare_blocked`` — upstream returns an HTML challenge page
      (we switched the shared UA to a real Chrome banner but the flag
      stays so ops can eyeball Cloudflare re-classification).
    - ``invalid_source_id`` — NewsNow responds 500 with
      ``{"error": true, "message": "Invalid source id"}``; typically
      means the ``platform_id`` never existed on the aggregator (e.g.
      eastmoney) and we should stop retrying for that source.
    - ``http_<status>`` — any other non-2xx; callers may choose to
      retry or fall back.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# The public NewsNow endpoint maintained by the open-source author.
DEFAULT_NEWSNOW_URL = "https://newsnow.busiyi.world/api/s"

# Only these statuses are treated as usable — matches
# ``DataFetcher.fetch_data`` (L95-100). Any other value (``error``,
# ``forbidden``, ``rate_limited``, missing) raises so the caller can
# count it as a failed probe and fall back to the direct scraper.
_ALLOWED_STATUS: frozenset[str] = frozenset({"success", "cache"})


def _resolve_api_url(config: dict[str, str]) -> str:
    """Pick the NewsNow endpoint from config, honouring self-host mode."""
    url = (config.get("newsnow.api_url") or "").strip()
    if url:
        return url
    return DEFAULT_NEWSNOW_URL


def newsnow_mode(config: dict[str, str]) -> str:
    """Return ``public`` / ``self_host`` / ``off`` normalised."""
    mode = (config.get("newsnow.mode") or "off").strip().lower()
    return mode if mode in {"public", "self_host", "off"} else "off"


async def fetch_from_newsnow(
    *,
    platform_id: str,
    source_id: str,
    config: dict[str, str],
    timeout_sec: float,
) -> list[NormalizedItem]:
    """Call the NewsNow aggregator and return normalised items.

    ``platform_id`` is the NewsNow-side slug (e.g. ``wallstreetcn-hot``,
    ``cls-hot``, ``eastmoney``, ``xueqiu-hotstock``). ``source_id`` is
    the fin-pulse-side id we want to tag the items with so they land in
    ``articles.source_id`` under the same slug the rest of the codebase
    uses (so the Today tab filter keeps working).

    The helper is deliberately small: no retries, no sleeps — the caller
    (a CN fetcher) is responsible for deciding what to do when we
    return an empty list or raise. Keeping it dumb makes it easy to
    monkey-patch in tests without stubbing an HTTP client.
    """
    mode = newsnow_mode(config)
    if mode == "off":
        return []

    api_url = _resolve_api_url(config)
    if not api_url:
        return []

    url = f"{api_url}?id={platform_id}&latest"
    # Chrome UA (already default) + explicit JSON Accept header keeps us
    # off the Cloudflare bot list. One transparent
    # retry absorbs the volunteer-run upstream's occasional cold-start
    # 502/504s without flooding the failure drawer.
    payload = await _call_newsnow(url, timeout_sec=timeout_sec)

    return _parse_envelope(payload, platform_id=platform_id, source_id=source_id)


async def _call_newsnow(url: str, *, timeout_sec: float) -> Any:
    """One HTTP call against the NewsNow endpoint with a single retry.

    Raises :class:`NewsNowTransportError` on unambiguous transport
    problems (Cloudflare challenge, invalid source id, persistent 5xx)
    so the caller can classify them instead of swallowing them as an
    empty-payload.
    """
    headers = {"Accept": "application/json, text/plain, */*"}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            async with make_client(
                timeout=timeout_sec, extra_headers=headers
            ) as client:
                resp = await client.get(url)
            # Surface Cloudflare challenge pages (403/503 + HTML body)
            # as a distinct error — swallowing them as "empty" hid the
            # real cause from users for weeks before the UA bump.
            if resp.status_code in (403, 503):
                body_head = (resp.text or "")[:160].lower()
                if "cloudflare" in body_head or "attention required" in body_head:
                    raise NewsNowTransportError(
                        "cloudflare_blocked",
                        f"newsnow blocked by cloudflare ({resp.status_code})",
                    )
            if resp.status_code >= 400:
                # NewsNow ships a JSON envelope for its own 500s
                # (``{error:true, message:"Invalid source id"}``). Pull
                # that out so the failure breadcrumb is useful.
                detail = ""
                try:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("error"):
                        detail = str(body.get("message") or "").strip()
                        if detail.lower().startswith("invalid source id"):
                            raise NewsNowTransportError(
                                "invalid_source_id", detail or "invalid source id"
                            )
                except NewsNowTransportError:
                    raise
                except ValueError:
                    pass
                raise NewsNowTransportError(
                    f"http_{resp.status_code}",
                    detail or f"newsnow returned http {resp.status_code}",
                )
            return resp.json()
        except NewsNowTransportError as exc:
            # ``invalid_source_id`` is permanent — short-circuit the retry.
            if exc.kind == "invalid_source_id":
                raise
            last_exc = exc
        except Exception as exc:  # noqa: BLE001 — single retry on transport glitches
            last_exc = exc
        if attempt == 0:
            await asyncio.sleep(1.5)
    assert last_exc is not None
    raise last_exc


def _parse_envelope(
    payload: Any, *, platform_id: str, source_id: str
) -> list[NormalizedItem]:
    """Turn a NewsNow response body into :class:`NormalizedItem` rows.

    Guard rails: skip ``None`` / ``float`` / empty titles, use ``url`` with
    ``mobileUrl`` fallback, and leave ranking / extra metadata inside
    ``extra`` so downstream consumers can opt into the signal.
    """
    if not isinstance(payload, dict):
        return []

    status = payload.get("status")
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"unexpected newsnow status: {status!r}")

    rows = payload.get("items") or []
    if not isinstance(rows, list):
        return []

    out: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue

        title_raw = row.get("title")
        if title_raw is None or isinstance(title_raw, float):
            continue
        title = str(title_raw).strip()
        if not title:
            continue

        url_raw = row.get("url") or row.get("mobileUrl") or ""
        url = str(url_raw).strip()
        if not url:
            continue

        # Title-based dedupe inside a single platform response so the
        # pinned-plus-repeated-in-body pattern (toutiao, weibo, cls) does
        # not double-count rows.
        if url in seen_urls:
            continue
        seen_urls.add(url)

        summary = _first_non_empty(row.get("desc"), row.get("summary"))
        published_at = _coerce_published(row.get("pubDate") or row.get("time"))

        extra: dict[str, Any] = {
            "via": "newsnow",
            "platform": platform_id,
            "rank": idx,
            "mobileUrl": row.get("mobileUrl") or None,
        }
        # Preserve any other key we didn't explicitly map so downstream
        # consumers (ai scoring, radar) can opt into extra signal.
        for k, v in row.items():
            if k in {"title", "url", "mobileUrl", "desc", "summary", "pubDate", "time"}:
                continue
            if v is None:
                continue
            extra.setdefault(k, v)

        out.append(
            NormalizedItem(
                source_id=source_id,
                title=title,
                url=url,
                summary=summary,
                published_at=published_at,
                extra=extra,
            )
        )

    return out


def _first_non_empty(*values: Any) -> str | None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _coerce_published(value: Any) -> str | None:
    """NewsNow payloads use either an ISO string, a human date, or a
    millisecond timestamp. We only return lexically sortable ISO-ish
    timestamps; relative/human labels are left as ``None`` so the
    database falls back to ``fetched_at`` for time-window queries.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        # NewsNow ships both 10-digit and 13-digit timestamps; normalise.
        if ts > 1e12:
            ts /= 1000.0

        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    s = str(value).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
        return s

    # Common NewsNow/direct-source shape: "2026-04-25 15:30[:00]".
    # Treat it as source-local wall time. Do not append "Z": that makes
    # browsers display the value as UTC and shifts Chinese news by +8h.
    m = re.match(
        r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
        r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
        s,
    )
    if m:
        second = m.group("second") or "00"
        return (
            f"{int(m.group('year')):04d}-{int(m.group('month')):02d}-"
            f"{int(m.group('day')):02d}T{int(m.group('hour')):02d}:"
            f"{int(m.group('minute')):02d}:{int(second):02d}"
        )

    return None


__all__ = [
    "DEFAULT_NEWSNOW_URL",
    "NewsNowTransportError",
    "fetch_from_newsnow",
    "newsnow_mode",
]
