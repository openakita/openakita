# ruff: noqa: N999
"""NewsNow aggregator fetcher for media-strategy sources.

NewsNow exposes hot-list style public channels at ``/api/s?id=...``. For
platform trend sources, the feed snapshot time is the reliable freshness
signal, so items without their own article timestamp use the envelope's
``updatedTime`` rather than the local fetch time.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from media_models import SOURCE_DEFS

from media_fetchers.rss import FeedItem, validate_feed_url

DEFAULT_NEWSNOW_URL = "https://newsnow.busiyi.world/api/s"
_ALLOWED_STATUS: frozenset[str] = frozenset({"success", "cache"})


class NewsNowError(RuntimeError):
    """Raised when NewsNow returns a non-retryable or malformed response."""


def newsnow_mode(settings: dict[str, Any]) -> str:
    mode = str(settings.get("newsnow.mode") or "public").strip().lower()
    return mode if mode in {"public", "self_host", "off"} else "off"


def newsnow_api_url(settings: dict[str, Any]) -> str:
    return str(settings.get("newsnow.api_url") or DEFAULT_NEWSNOW_URL).strip()


async def fetch_from_newsnow(
    source: dict[str, Any],
    *,
    settings: dict[str, Any],
    timeout_sec: float,
    user_agent: str,
) -> tuple[str, list[FeedItem]]:
    """Fetch one NewsNow channel and normalize it into ``FeedItem`` rows."""

    if newsnow_mode(settings) == "off":
        return "", []
    api_url = validate_feed_url(newsnow_api_url(settings))
    source_id = str(source.get("id") or "").strip()
    builtin = SOURCE_DEFS.get(source_id, {})
    platform_id = str(source.get("newsnow_id") or builtin.get("newsnow_id") or source_id).strip()
    if not platform_id:
        raise NewsNowError("missing newsnow_id")
    url = f"{api_url}?id={platform_id}&latest"
    payload = await _call_json(url, timeout_sec=timeout_sec, user_agent=user_agent)
    return url, _parse_envelope(payload, source_id=source_id, platform_id=platform_id)


async def _call_json(url: str, *, timeout_sec: float, user_agent: str) -> Any:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=timeout_sec, follow_redirects=True, headers=headers
            ) as client:
                response = await client.get(url)
            if response.status_code >= 400:
                raise NewsNowError(f"newsnow returned http {response.status_code}")
            return response.json()
        except Exception as exc:  # noqa: BLE001 - one transparent retry for public node glitches
            last_exc = exc
        if attempt == 0:
            await asyncio.sleep(1.2)
    assert last_exc is not None
    raise last_exc


def _parse_envelope(payload: Any, *, source_id: str, platform_id: str) -> list[FeedItem]:
    if not isinstance(payload, dict):
        raise NewsNowError("newsnow payload is not an object")
    status = payload.get("status")
    if status not in _ALLOWED_STATUS:
        raise NewsNowError(f"unexpected newsnow status: {status!r}")
    rows = payload.get("items") or []
    if not isinstance(rows, list):
        return []
    envelope_time = _coerce_published(payload.get("updatedTime"))
    out: list[FeedItem] = []
    seen_urls: set[str] = set()
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("mobileUrl") or "").strip()
        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        published_at = _coerce_published(
            row.get("pubDate")
            or row.get("time")
            or extra.get("date")
            or extra.get("time")
            or envelope_time
        )
        if not published_at:
            continue
        summary = str(row.get("desc") or row.get("summary") or extra.get("hover") or "").strip()
        out.append(
            FeedItem(
                source_id=source_id,
                title=title,
                url=url,
                summary=summary,
                published_at=published_at,
                raw={
                    "parser": "newsnow",
                    "platform": platform_id,
                    "rank": rank,
                    "status": status,
                    "mobileUrl": row.get("mobileUrl") or None,
                    "extra": extra,
                },
            )
        )
    return out


def _coerce_published(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts <= 0:
            return None
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z") and "T" in raw:
        return raw
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return None


def newsnow_rate_limit_remaining(settings: dict[str, Any], *, now_ts: float) -> float:
    """Return cooldown seconds left for the public NewsNow node."""

    if newsnow_mode(settings) != "public":
        return 0.0
    try:
        floor = float(settings.get("newsnow.min_interval_s") or 300)
    except (TypeError, ValueError):
        floor = 300.0
    if floor <= 0:
        return 0.0
    try:
        last = float(settings.get("newsnow.last_fetch_ts") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0:
        return 0.0
    return floor - (now_ts - last)


__all__ = [
    "DEFAULT_NEWSNOW_URL",
    "NewsNowError",
    "fetch_from_newsnow",
    "newsnow_api_url",
    "newsnow_mode",
    "newsnow_rate_limit_remaining",
]
