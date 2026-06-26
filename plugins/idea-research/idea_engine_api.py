"""Engine A — safe collectors for idea-research (§6.1).

Four collectors backed by official APIs / RSS feeds, all driven by
``httpx.AsyncClient`` so they can be unit-tested by patching the
client. None of them require user-supplied cookies; the only optional
secret is a YouTube Data API v3 key.

Each collector exposes:

    async def fetch_trending(keywords, time_window, limit) -> list[TrendItem]
    async def fetch_single(url, with_comments=False) -> TrendItem | None

``BiliCollector`` and ``YouTubeCollector`` additionally implement
``fetch_user`` / ``fetch_creator`` for ``compare_accounts`` (B 站公开
``x/space/arc/list``；YouTube Data API v3 channels + uploads playlist).

The base ``ApiCollectorBase`` enforces a tiny per-instance rate limit
plus uniform error mapping into ``VendorError`` subclasses (which carry
``error_kind`` already, so the pipeline / route layer can render the
bilingual hint from §15 without translating).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from idea_models import ResolvedSource, TrendItem
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorError,
    VendorFormatError,
    VendorNetworkError,
    VendorQuotaError,
    VendorRateLimitError,
    VendorTimeoutError,
)

WINDOW_TO_SECONDS: dict[str, int] = {
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}


def _now() -> int:
    return int(time.time())


def _window_seconds(label: str) -> int:
    return WINDOW_TO_SECONDS.get(label or "24h", WINDOW_TO_SECONDS["24h"])


def _new_item_id() -> str:
    return str(uuid.uuid4())


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _matches_keywords(text: str, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    haystack = (text or "").lower()
    return [k for k in keywords if k and k.lower() in haystack]


def _vendor_error_info(source: str, exc: VendorError) -> dict[str, Any]:
    return {
        "source": source,
        "error_kind": exc.error_kind,
        "message": str(exc),
        "status_code": getattr(exc, "status_code", None),
        "payload": getattr(exc, "payload", None),
    }


def _fallback_message(prefix: str, errors: list[dict[str, Any]]) -> str:
    details = []
    for err in errors:
        source = err.get("source") or "unknown"
        kind = err.get("error_kind") or "unknown"
        message = err.get("message") or ""
        details.append(f"{source} / {kind}: {message}")
    return f"{prefix}; " + "; ".join(details)


def filter_items_by_keywords(
    items: list[TrendItem],
    keywords: list[str],
) -> list[TrendItem]:
    if not keywords:
        return items
    out: list[TrendItem] = []
    for item in items:
        matched = _matches_keywords(f"{item.title} {item.description or ''}", keywords)
        if matched:
            out.append(item)
    return out


def _normalize_media_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    value = url.strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://i") and ".hdslb.com/" in value:
        return "https://" + value[len("http://") :]
    return value


class CollectorError(VendorError):
    """Marker for collector-side failures (re-uses error_kind taxonomy)."""


class ApiCollectorBase:
    """Tiny wrapper around an injected ``httpx.AsyncClient``.

    Collectors should never instantiate their own client; tests inject
    a transport-level mock instead.
    """

    name: str = "base"
    platform: str = "other"
    rate_limit_per_min: int = 60

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        rsshub_base: str = "https://rsshub.app",
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._rsshub_base = rsshub_base.rstrip("/")
        self._last_calls: list[float] = []

    async def _throttle(self) -> None:
        now = time.monotonic()
        window = 60.0
        self._last_calls = [t for t in self._last_calls if now - t < window]
        if len(self._last_calls) >= self.rate_limit_per_min:
            sleep_for = window - (now - self._last_calls[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        self._last_calls.append(time.monotonic())

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        await self._throttle()
        try:
            r = await self._client.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(f"timeout fetching {url}", payload={"url": url}) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise VendorNetworkError(
                f"http error ({type(exc).__name__}) fetching {url}: {detail}",
                payload={"url": url, "error_type": type(exc).__name__, "error": detail},
            ) from exc
        if r.status_code == 401 or r.status_code == 403:
            raise VendorAuthError(
                f"auth failed ({r.status_code}) fetching {url}",
                status_code=r.status_code,
            )
        if r.status_code == 429:
            raise VendorRateLimitError(f"rate limited fetching {url}", status_code=r.status_code)
        if r.status_code >= 500:
            raise VendorNetworkError(
                f"upstream {r.status_code} fetching {url}",
                status_code=r.status_code,
            )
        if r.status_code != 200:
            raise VendorNetworkError(
                f"unexpected {r.status_code} fetching {url}",
                status_code=r.status_code,
            )
        try:
            return r.json()
        except json.JSONDecodeError as exc:
            raise VendorFormatError(f"non-json response from {url}") from exc

    async def _get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> str:
        await self._throttle()
        try:
            r = await self._client.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(f"timeout fetching {url}", payload={"url": url}) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise VendorNetworkError(
                f"http error ({type(exc).__name__}) fetching {url}: {detail}",
                payload={"url": url, "error_type": type(exc).__name__, "error": detail},
            ) from exc
        if r.status_code != 200:
            raise VendorNetworkError(
                f"unexpected {r.status_code} fetching {url}",
                status_code=r.status_code,
            )
        return r.text


# --------------------------------------------------------------------------- #
# 1. BiliCollector — official popular feed                                     #
# --------------------------------------------------------------------------- #


_BILI_BV_RE = re.compile(r"BV[A-Za-z0-9]{10}")
_BILI_AID_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_BILI_SPACE_MID_RE = re.compile(
    r"(?:space\.bilibili\.com|bilibili\.com/space)/(\d+)",
    re.IGNORECASE,
)
_BILI_INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*\(function",
    re.DOTALL,
)
_BILI_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_tags(text: str) -> str:
    return _BILI_HTML_TAG_RE.sub("", text or "").strip()


_BILI_MIXIN_KEY_ENC_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)
_bili_wbi_key_cache: dict[str, Any] = {"img_key": "", "sub_key": "", "fetched_at": 0}
_BILI_WBI_CACHE_TTL_S = 6 * 3600


def _bili_mixin_key(raw: str) -> str:
    return "".join(raw[i] for i in _BILI_MIXIN_KEY_ENC_TAB)[:32]


def _bili_enc_wbi_params(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    from urllib.parse import urlencode

    signed = dict(params)
    signed["wts"] = round(time.time())
    signed = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(signed.items())
    }
    query = urlencode(signed)
    signed["w_rid"] = hashlib.md5((query + _bili_mixin_key(img_key + sub_key)).encode()).hexdigest()
    return signed


def _bili_wbi_key_from_url(url: str) -> str:
    return str(url or "").rsplit("/", 1)[-1].split(".", 1)[0]


class BiliCollector(ApiCollectorBase):
    name = "bili_api"
    platform = "bilibili"
    rate_limit_per_min = 60

    POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular"
    RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"
    SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
    WBI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
    REPLY_URL = "https://api.bilibili.com/x/v2/reply/main"
    ARC_LIST_URL = "https://api.bilibili.com/x/space/arc/list"
    POPULAR_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        if keywords:
            return await self._fetch_keyword_items(keywords, time_window, limit)
        try:
            data = await self._get_json(
                self.POPULAR_URL,
                params={"ps": min(50, max(1, limit * 2)), "pn": 1},
                headers=self.POPULAR_HEADERS,
            )
        except VendorNetworkError as exc:
            if exc.status_code != 412:
                raise
            fallback_errors: list[dict[str, Any]] = []
            try:
                data = await self._get_json(
                    self.RANKING_URL,
                    params={"rid": 0, "type": "all"},
                    headers=self.POPULAR_HEADERS,
                )
                return self._parse_listing_payload(data, keywords, time_window, limit)
            except VendorError as ranking_exc:
                fallback_errors.append(
                    self._vendor_error_info("bili_ranking", ranking_exc)
                )
            try:
                return await RssHubCollector(
                    client=self._client,
                    rsshub_base=self._rsshub_base,
                ).fetch_trending(
                    keywords,
                    time_window,
                    limit,
                    platform="bilibili",
                )
            except VendorError as rss_exc:
                exc.payload = {
                    "url": self.POPULAR_URL,
                    "fallback_errors": [
                        *fallback_errors,
                        self._vendor_error_info("rsshub", rss_exc),
                    ],
                }
                raise exc
        parsed = self._parse_listing_payload(data, keywords, time_window, limit)
        return parsed

    async def _fetch_wbi_keys(self) -> tuple[str, str]:
        now = _now()
        cached_at = int(_bili_wbi_key_cache.get("fetched_at") or 0)
        img_key = str(_bili_wbi_key_cache.get("img_key") or "")
        sub_key = str(_bili_wbi_key_cache.get("sub_key") or "")
        if img_key and sub_key and now - cached_at < _BILI_WBI_CACHE_TTL_S:
            return img_key, sub_key
        data = await self._get_json(self.NAV_URL, headers=self.POPULAR_HEADERS)
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_key = _bili_wbi_key_from_url(str(wbi.get("img_url") or ""))
        sub_key = _bili_wbi_key_from_url(str(wbi.get("sub_url") or ""))
        if not img_key or not sub_key:
            raise VendorFormatError("bili nav missing wbi_img keys")
        _bili_wbi_key_cache["img_key"] = img_key
        _bili_wbi_key_cache["sub_key"] = sub_key
        _bili_wbi_key_cache["fetched_at"] = now
        return img_key, sub_key

    async def _fetch_keyword_items(
        self,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        query = " ".join(k for k in keywords if k).strip()
        if not query:
            return []
        from urllib.parse import quote

        search_headers = {
            **self.POPULAR_HEADERS,
            "Referer": f"https://search.bilibili.com/all?keyword={quote(query)}",
            "Origin": "https://search.bilibili.com",
        }
        search_exc: VendorNetworkError | None = None
        max_pages = min(10, max(2, (limit + 9) // 10 + 1))
        for use_wbi in (True, False):
            try:
                out: list[TrendItem] = []
                seen: set[str] = set()
                for page in range(1, max_pages + 1):
                    remaining = limit - len(out)
                    if remaining <= 0:
                        break
                    params: dict[str, Any] = {
                        "search_type": "video",
                        "keyword": query,
                        "page": page,
                        "order": "totalrank",
                    }
                    url = self.WBI_SEARCH_URL if use_wbi else self.SEARCH_URL
                    if use_wbi:
                        img_key, sub_key = await self._fetch_wbi_keys()
                        params = _bili_enc_wbi_params(params, img_key, sub_key)
                    data = await self._get_json(url, params=params, headers=search_headers)
                    batch = self._parse_search_payload(data, keywords, time_window, remaining)
                    for item in batch:
                        if item.external_id in seen:
                            continue
                        seen.add(item.external_id)
                        out.append(item)
                        if len(out) >= limit:
                            break
                    raw_count = len(
                        [
                            raw
                            for raw in ((data.get("data") or {}).get("result") or [])
                            if isinstance(raw, dict) and raw.get("type") == "video"
                        ]
                    )
                    if len(out) >= limit or raw_count < 10:
                        break
                return out[:limit]
            except VendorNetworkError as exc:
                if exc.status_code != 412:
                    raise
                search_exc = exc
        assert search_exc is not None
        return await self._fetch_keyword_listing_fallback(
            keywords,
            time_window,
            limit,
            search_exc,
        )

    async def _fetch_keyword_listing_fallback(
        self,
        keywords: list[str],
        time_window: str,
        limit: int,
        search_exc: VendorNetworkError,
    ) -> list[TrendItem]:
        fallback_errors: list[dict[str, Any]] = []
        listing_attempts = (
            ("bili_popular", self.POPULAR_URL, {"ps": min(50, max(1, limit * 3)), "pn": 1}),
            ("bili_ranking", self.RANKING_URL, {"rid": 0, "type": "all"}),
        )
        for source, url, params in listing_attempts:
            try:
                data = await self._get_json(url, params=params, headers=self.POPULAR_HEADERS)
                items = self._parse_listing_payload(data, keywords, time_window, limit)
                if items:
                    return items
            except VendorError as listing_exc:
                fallback_errors.append(self._vendor_error_info(source, listing_exc))
        try:
            items = await RssHubCollector(
                client=self._client,
                rsshub_base=self._rsshub_base,
            ).fetch_trending(
                keywords,
                time_window,
                limit,
                platform="bilibili",
            )
            if items:
                return items
        except VendorError as rss_exc:
            fallback_errors.append(self._vendor_error_info("rsshub", rss_exc))
        search_exc.payload = {
            "url": self.SEARCH_URL,
            "fallback_errors": fallback_errors,
        }
        raise search_exc

    def _parse_search_payload(
        self,
        data: Any,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        if not isinstance(data, dict) or data.get("code") != 0:
            raise VendorFormatError(f"bili search bad payload: {data!r}"[:200])
        items_raw = [
            raw
            for raw in ((data.get("data") or {}).get("result") or [])
            if isinstance(raw, dict) and raw.get("type") == "video"
        ]
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for raw in items_raw:
            pub = _coerce_int(raw.get("pubdate")) or 0
            if pub and pub < cutoff:
                continue
            title = _strip_html_tags(str(raw.get("title") or ""))
            desc = _strip_html_tags(str(raw.get("description") or ""))
            matched = _matches_keywords(f"{title} {desc}", keywords)
            if keywords and not matched:
                continue
            bvid = raw.get("bvid")
            aid = raw.get("aid") or raw.get("id")
            arcurl = str(raw.get("arcurl") or "").split("?", 1)[0]
            item = TrendItem(
                id=_new_item_id(),
                platform="bilibili",
                external_id=str(bvid or aid or ""),
                external_url=arcurl
                or (
                    f"https://www.bilibili.com/video/{bvid}"
                    if bvid
                    else f"https://www.bilibili.com/video/av{aid}"
                ),
                title=title,
                author=str(raw.get("author") or ""),
                author_url=(
                    f"https://space.bilibili.com/{raw['mid']}" if raw.get("mid") else None
                ),
                cover_url=_normalize_media_url(raw.get("pic") or raw.get("cover")),
                description=desc or None,
                like_count=_coerce_int(raw.get("favorites")),
                comment_count=_coerce_int(raw.get("video_review") or raw.get("review")),
                view_count=_coerce_int(raw.get("play")),
                publish_at=pub,
                fetched_at=_now(),
                engine_used="a",
                collector_name=self.name,
                raw_payload_json=json.dumps(raw, ensure_ascii=False),
                keywords_matched=matched,
                data_quality="high",
            )
            out.append(item)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _vendor_error_info(source: str, exc: VendorError) -> dict[str, Any]:
        return _vendor_error_info(source, exc)

    @staticmethod
    def _page_url(*, bvid: str | None, aid: str | None, url: str) -> str:
        if "bilibili.com/video/" in (url or ""):
            return (url or "").split("?", 1)[0]
        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"
        if aid:
            return f"https://www.bilibili.com/video/av{aid}"
        return url

    def _trend_item_from_view_data(self, data: dict[str, Any], *, external_url: str) -> TrendItem:
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        return TrendItem(
            id=_new_item_id(),
            platform="bilibili",
            external_id=str(data.get("bvid") or data.get("aid")),
            external_url=external_url,
            title=str(data.get("title") or ""),
            author=str(owner.get("name") or ""),
            cover_url=_normalize_media_url(data.get("pic")),
            duration_seconds=_coerce_int(data.get("duration")),
            description=data.get("desc"),
            like_count=_coerce_int(stat.get("like")),
            comment_count=_coerce_int(stat.get("reply")),
            share_count=_coerce_int(stat.get("share")),
            view_count=_coerce_int(stat.get("view")),
            publish_at=_coerce_int(data.get("pubdate")) or 0,
            fetched_at=_now(),
            engine_used="a",
            collector_name=self.name,
            raw_payload_json=json.dumps(data, ensure_ascii=False),
        )

    def _parse_initial_state_html(self, html: str) -> dict[str, Any] | None:
        m = _BILI_INITIAL_STATE_RE.search(html or "")
        if not m:
            return None
        try:
            state = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        video = state.get("videoData") or {}
        if not isinstance(video, dict):
            return None
        owner = video.get("owner") or state.get("upData") or {}
        stat = video.get("stat") or {}
        return {
            "aid": video.get("aid") or state.get("aid"),
            "bvid": video.get("bvid") or state.get("bvid"),
            "title": video.get("title"),
            "desc": video.get("desc"),
            "pic": video.get("pic"),
            "duration": video.get("duration"),
            "pubdate": video.get("pubdate"),
            "owner": owner if isinstance(owner, dict) else {},
            "stat": stat if isinstance(stat, dict) else {},
        }

    def _parse_listing_payload(
        self,
        data: Any,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        if not isinstance(data, dict) or data.get("code") != 0:
            raise VendorFormatError(f"bili listing bad payload: {data!r}"[:200])
        items_raw = (data.get("data") or {}).get("list") or []
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for raw in items_raw:
            pub = _coerce_int(raw.get("pubdate")) or 0
            if keywords and pub and pub < cutoff:
                continue
            title = raw.get("title") or ""
            matched = _matches_keywords(f"{title} {raw.get('desc', '')}", keywords)
            if keywords and not matched:
                continue
            stat = raw.get("stat") or {}
            owner = raw.get("owner") or {}
            item = TrendItem(
                id=_new_item_id(),
                platform="bilibili",
                external_id=str(raw.get("bvid") or raw.get("aid")),
                external_url=(
                    f"https://www.bilibili.com/video/{raw.get('bvid')}"
                    if raw.get("bvid")
                    else f"https://www.bilibili.com/video/av{raw.get('aid')}"
                ),
                title=title,
                author=str(owner.get("name") or ""),
                author_url=(
                    f"https://space.bilibili.com/{owner['mid']}" if owner.get("mid") else None
                ),
                cover_url=_normalize_media_url(raw.get("pic") or raw.get("cover")),
                duration_seconds=_coerce_int(raw.get("duration")),
                description=raw.get("desc"),
                like_count=_coerce_int(stat.get("like")),
                comment_count=_coerce_int(stat.get("reply")),
                share_count=_coerce_int(stat.get("share")),
                view_count=_coerce_int(stat.get("view")),
                publish_at=pub,
                fetched_at=_now(),
                engine_used="a",
                collector_name=self.name,
                raw_payload_json=json.dumps(raw, ensure_ascii=False),
                keywords_matched=matched,
                data_quality="high",
            )
            out.append(item)
            if len(out) >= limit:
                break
        return out

    async def fetch_user(self, url: str, limit: int = 20) -> list[TrendItem]:
        """Recent public uploads for a space / homepage URL (engine A, no cookies)."""

        clean = (url or "").split("?", 1)[0].strip()
        m = _BILI_SPACE_MID_RE.search(clean)
        if not m:
            raise VendorFormatError(f"无法在 URL 中解析 B 站空间 mid: {url!r}")
        mid = m.group(1)
        ps = min(max(1, int(limit)), 30)
        data = await self._get_json(
            self.ARC_LIST_URL,
            params={"mid": mid, "ps": str(ps), "pn": "1", "order": "pubdate"},
            headers=self.POPULAR_HEADERS,
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            raise VendorFormatError(f"bili space arc/list bad payload: {data!r}"[:240])
        archives = (data.get("data") or {}).get("archives") or []
        if not isinstance(archives, list):
            return []
        out: list[TrendItem] = []
        for raw in archives:
            if not isinstance(raw, dict):
                continue
            bvid = raw.get("bvid")
            aid = raw.get("aid")
            if not bvid and not aid:
                continue
            stat = raw.get("stat") or {}
            author = raw.get("author") or {}
            pub = _coerce_int(raw.get("pubdate")) or 0
            title = str(raw.get("title") or "")
            author_mid = author.get("mid")
            item = TrendItem(
                id=_new_item_id(),
                platform="bilibili",
                external_id=str(bvid or aid),
                external_url=(
                    f"https://www.bilibili.com/video/{bvid}"
                    if bvid
                    else f"https://www.bilibili.com/video/av{aid}"
                ),
                title=title,
                author=str(author.get("name") or ""),
                author_url=(
                    f"https://space.bilibili.com/{author_mid}"
                    if author_mid
                    else f"https://space.bilibili.com/{mid}"
                ),
                cover_url=_normalize_media_url(raw.get("pic") or raw.get("cover")),
                duration_seconds=_coerce_int(raw.get("duration")),
                description=raw.get("desc"),
                like_count=_coerce_int(stat.get("like")),
                comment_count=_coerce_int(stat.get("reply")),
                share_count=_coerce_int(stat.get("share")),
                view_count=_coerce_int(stat.get("view")),
                publish_at=pub,
                fetched_at=_now(),
                engine_used="a",
                collector_name=self.name,
                raw_payload_json=json.dumps(raw, ensure_ascii=False),
                data_quality="high",
            )
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def _parse_comments_payload(self, data: Any, *, limit: int = 40) -> list[dict[str, Any]]:
        if not isinstance(data, dict) or data.get("code") != 0:
            raise VendorFormatError(f"bili comments bad payload: {data!r}"[:200])
        payload = data.get("data") or {}
        reply_groups = [payload.get("top_replies") or [], payload.get("replies") or []]
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in reply_groups:
            if not isinstance(group, list):
                continue
            for raw in group:
                if not isinstance(raw, dict):
                    continue
                content = raw.get("content") or {}
                member = raw.get("member") or {}
                message = str(content.get("message") or "").strip()
                if not message:
                    continue
                rpid = str(raw.get("rpid") or "")
                if rpid and rpid in seen:
                    continue
                if rpid:
                    seen.add(rpid)
                out.append(
                    {
                        "author": str(member.get("uname") or ""),
                        "author_id": str(member.get("mid") or ""),
                        "text": message,
                        "like_count": _coerce_int(raw.get("like")) or 0,
                        "reply_count": _coerce_int(raw.get("rcount")) or 0,
                        "published_at": _coerce_int(raw.get("ctime")) or 0,
                    }
                )
                if len(out) >= limit:
                    return out
        return out

    async def _fetch_comments(self, aid: int, *, limit: int = 40) -> list[dict[str, Any]]:
        data = await self._get_json(
            self.REPLY_URL,
            params={
                "type": "1",
                "oid": str(aid),
                "mode": "3",
                "ps": str(min(max(1, limit), 50)),
                "next": "0",
            },
            headers=self.POPULAR_HEADERS,
        )
        return self._parse_comments_payload(data, limit=limit)

    async def fetch_single_source(
        self, url: str, *, with_comments: bool = False
    ) -> ResolvedSource | None:
        bv = _BILI_BV_RE.search(url or "")
        av = _BILI_AID_RE.search(url or "")
        params: dict[str, Any] = {}
        bvid: str | None = None
        aid: str | None = None
        if bv:
            bvid = bv.group(0)
            params["bvid"] = bvid
        elif av:
            aid = av.group(1)
            params["aid"] = aid
        else:
            raise VendorFormatError(f"unrecognized bilibili url: {url!r}")
        try:
            data = await self._get_json(
                self.VIEW_URL,
                params=params,
                headers=self.POPULAR_HEADERS,
            )
        except VendorNetworkError as exc:
            if exc.status_code != 412:
                raise
            page_url = self._page_url(bvid=bvid, aid=aid, url=url)
            html = await self._get_text(page_url, headers=self.POPULAR_HEADERS)
            parsed = self._parse_initial_state_html(html)
            if parsed:
                item = self._trend_item_from_view_data(parsed, external_url=page_url)
                comments: list[dict[str, Any]] = []
                if with_comments:
                    parsed_aid = _coerce_int(parsed.get("aid"))
                    if parsed_aid:
                        with contextlib.suppress(VendorError):
                            comments = await self._fetch_comments(parsed_aid)
                return ResolvedSource(item=item, comments=comments)
            raise
        if not isinstance(data, dict) or data.get("code") != 0:
            return None
        d = data.get("data") or {}
        item = self._trend_item_from_view_data(d, external_url=url)
        comments: list[dict[str, Any]] = []
        if with_comments:
            aid_value = _coerce_int(d.get("aid"))
            if aid_value:
                with contextlib.suppress(VendorError):
                    comments = await self._fetch_comments(aid_value)
        return ResolvedSource(item=item, comments=comments)

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        resolved = await self.fetch_single_source(url, with_comments=with_comments)
        return resolved.item if resolved is not None else None


# --------------------------------------------------------------------------- #
# 2. YouTubeCollector — Data API v3                                            #
# --------------------------------------------------------------------------- #

_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S = 8.0
_YOUTUBE_RSSHUB_HTTP_TIMEOUT_S = 10.0
_YOUTUBE_YTDLP_TIMEOUT_S = 45.0
_DEFAULT_INVIDIOUS_BASES: tuple[str, ...] = (
    "https://inv.nadeko.net",
    "https://vid.puffyan.us",
    "https://yt.artemislena.eu",
)


def _iter_invidious_bases() -> list[str]:
    custom = (
        os.environ.get("INVIDIOUS_BASE")
        or os.environ.get("YOUTUBE_INVIDIOUS_BASE")
        or ""
    ).strip().rstrip("/")
    bases: list[str] = []
    if custom:
        bases.append(custom)
    for base in _DEFAULT_INVIDIOUS_BASES:
        if base not in bases:
            bases.append(base)
    return bases


_RUNTIME_HTTP_PROXY: str | None = None


def set_runtime_http_proxy(proxy: str | None) -> None:
    global _RUNTIME_HTTP_PROXY
    _RUNTIME_HTTP_PROXY = (proxy or "").strip() or None


def _windows_system_proxy() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            server = str(server).strip()
            if not server:
                return None
            if "://" in server:
                return server
            if "=" in server:
                for part in server.split(";"):
                    part = part.strip()
                    lower = part.lower()
                    if lower.startswith("https="):
                        host = part.split("=", 1)[1]
                        return host if "://" in host else f"http://{host}"
                    if lower.startswith("http="):
                        host = part.split("=", 1)[1]
                        return host if "://" in host else f"http://{host}"
            return server if "://" in server else f"http://{server}"
    except OSError:
        return None


def resolve_http_proxy() -> str | None:
    if _RUNTIME_HTTP_PROXY:
        return _RUNTIME_HTTP_PROXY
    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    try:
        from urllib.request import getproxies

        proxies = getproxies()
        for key in ("https", "http"):
            val = proxies.get(key)
            if val and val.strip():
                return val.strip()
    except Exception:
        pass
    return _windows_system_proxy()


def _proxy_debug_label() -> str | None:
    proxy = resolve_http_proxy()
    if not proxy:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(proxy)
        if parsed.hostname:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return f"{parsed.scheme}://{parsed.hostname}:{port}"
    except Exception:
        pass
    return proxy.split("@")[-1][:80]


def _proxy_unreachable_hint(text: str) -> str | None:
    lower = (text or "").lower()
    if any(
        token in lower
        for token in (
            "10061",
            "actively refused",
            "积极拒绝",
            "connection refused",
            "connect call failed",
        )
    ):
        label = _proxy_debug_label()
        target = f" ({label})" if label else ""
        return f"代理连接被拒绝{target}：请确认 Clash/V2Ray 已启动，且 Settings 中 http_proxy 使用 HTTP 端口（如 http://127.0.0.1:7890）"
    return None


def _ytdlp_proxy_args() -> list[str]:
    proxy = resolve_http_proxy()
    return ["--proxy", proxy] if proxy else []


def _ytdlp_radar_targets(keywords: list[str], limit: int) -> list[str]:
    count = min(50, max(limit * 2, limit))
    query = " ".join(k for k in keywords if k).strip()
    if query:
        return [f"ytsearch{count}:{query}"]
    return [
        f"ytsearch{count}:trending",
        "https://www.youtube.com/feed/trending",
    ]


class YouTubeCollector(ApiCollectorBase):
    name = "youtube_api"
    platform = "youtube"
    rate_limit_per_min = 100  # quota gates real ceiling

    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    ALT_SEARCH_URL = "https://youtube.googleapis.com/youtube/v3/search"
    CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
    ALT_CHANNELS_URL = "https://youtube.googleapis.com/youtube/v3/channels"
    PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
    ALT_PLAYLIST_ITEMS_URL = "https://youtube.googleapis.com/youtube/v3/playlistItems"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
    ALT_VIDEOS_URL = "https://youtube.googleapis.com/youtube/v3/videos"
    COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
    ALT_COMMENTS_URL = "https://youtube.googleapis.com/youtube/v3/commentThreads"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
        *,
        region_code: str = "US",
    ) -> list[TrendItem]:
        proxy = resolve_http_proxy()
        if not self._api_key or not proxy:
            official_errors: list[dict[str, Any]] = []
            first_official_error: VendorError | None = None
            if self._api_key and not proxy:
                skip_err = VendorNetworkError(
                    "跳过 YouTube Data API：未检测到 HTTP 代理；"
                    "请在 Settings 填写 http_proxy（如 http://127.0.0.1:7890）"
                )
                official_errors.append(_vendor_error_info("googleapis", skip_err))
                first_official_error = skip_err
            return await self._fetch_rsshub_or_raise(
                official_errors=official_errors,
                first_official_error=first_official_error,
                keywords=keywords,
                time_window=time_window,
                limit=limit,
                region_code=region_code,
            )
        if keywords:
            return await self._fetch_keyword_items(keywords, time_window, limit)
        params = self._trending_params(region_code=region_code, limit=limit)
        data, errors, first_error = await self._fetch_official_json(
            params=params,
            candidates=(
                ("googleapis", self.VIDEOS_URL),
                ("youtube.googleapis", self.ALT_VIDEOS_URL),
            ),
            http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
        )
        if data is None:
            return await self._fetch_rsshub_or_raise(
                official_errors=errors,
                first_official_error=first_error,
                keywords=keywords,
                time_window=time_window,
                limit=limit,
                region_code=region_code,
            )
        return self._parse_video_items(
            data,
            keywords,
            time_window,
            limit,
            require_keyword_match=True,
        )

    def _trending_params(self, *, region_code: str, limit: int) -> dict[str, Any]:
        return {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(50, max(1, limit * 2)),
            "key": self._api_key,
        }

    async def _fetch_keyword_items(
        self,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        query = " ".join(k for k in keywords if k).strip()
        if not query:
            return []
        search_params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "order": "date",
            "publishedAfter": _rfc3339_utc(_now() - _window_seconds(time_window)),
            "maxResults": min(50, max(1, limit * 2)),
            "key": self._api_key,
        }
        search_data, search_errors, search_first_error = await self._fetch_official_json(
            params=search_params,
            candidates=(
                ("googleapis_search", self.SEARCH_URL),
                ("youtube.googleapis_search", self.ALT_SEARCH_URL),
            ),
            http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
        )
        if search_data is None:
            return await self._fetch_rsshub_or_raise(
                official_errors=search_errors,
                first_official_error=search_first_error,
                keywords=keywords,
                time_window=time_window,
                limit=limit,
                region_code="US",
            )
        ids = self._video_ids_from_search(search_data, limit=limit * 2)
        if not ids:
            return []
        video_params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(ids),
            "key": self._api_key,
        }
        video_data, video_errors, video_first_error = await self._fetch_official_json(
            params=video_params,
            candidates=(
                ("googleapis_videos", self.VIDEOS_URL),
                ("youtube.googleapis_videos", self.ALT_VIDEOS_URL),
            ),
            http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
        )
        if video_data is None:
            return await self._fetch_rsshub_or_raise(
                official_errors=[*search_errors, *video_errors],
                first_official_error=video_first_error or search_first_error,
                keywords=keywords,
                time_window=time_window,
                limit=limit,
                region_code="US",
            )
        return self._parse_video_items(
            video_data,
            keywords,
            time_window,
            limit,
            require_keyword_match=False,
        )

    async def _fetch_official_json(
        self,
        *,
        params: dict[str, Any],
        candidates: tuple[tuple[str, str], ...],
        http_timeout: float = 30.0,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], VendorError | None]:
        official_errors: list[dict[str, Any]] = []
        first_official_error: VendorError | None = None
        for source, url in candidates:
            try:
                data = await self._get_json(url, params=params, timeout=http_timeout)
            except (VendorNetworkError, VendorTimeoutError) as exc:
                if first_official_error is None:
                    first_official_error = exc
                official_errors.append(_vendor_error_info(source, exc))
                continue
            if not isinstance(data, dict):
                raise VendorFormatError(f"youtube bad payload: {data!r}"[:200])
            if "error" in data:
                err = data["error"]
                kind = "quota" if err.get("code") == 403 else "auth"
                raise (
                    VendorQuotaError(str(err))
                    if kind == "quota"
                    else VendorAuthError(str(err))
                )
            return data, official_errors, first_official_error
        return None, official_errors, first_official_error

    async def _fetch_rsshub_or_raise(
        self,
        *,
        official_errors: list[dict[str, Any]],
        first_official_error: VendorError | None,
        keywords: list[str],
        time_window: str,
        limit: int,
        region_code: str,
    ) -> list[TrendItem]:
        rss_errors: list[dict[str, Any]] = []
        for region in self._rsshub_regions(region_code):
            try:
                items = await RssHubCollector(
                    client=self._client,
                    rsshub_base=self._rsshub_base,
                ).fetch_trending(
                    keywords,
                    time_window,
                    limit,
                    platform="youtube",
                    region=region,
                    http_timeout=_YOUTUBE_RSSHUB_HTTP_TIMEOUT_S,
                )
                return items
            except VendorError as rss_exc:
                rss_errors.append(_vendor_error_info(f"rsshub:{region}", rss_exc))
        for base in _iter_invidious_bases():
            try:
                items = await self._fetch_invidious_trending(
                    base,
                    keywords=keywords,
                    time_window=time_window,
                    limit=limit,
                    region_code=region_code,
                )
                return items
            except VendorError as inv_exc:
                rss_errors.append(_vendor_error_info(f"invidious:{base}", inv_exc))
        if resolve_http_proxy():
            try:
                items = await self._fetch_ytdlp_trending(
                    keywords=keywords,
                    time_window=time_window,
                    limit=limit,
                )
                return items
            except VendorError as ytdlp_exc:
                rss_errors.append(_vendor_error_info("ytdlp", ytdlp_exc))
        else:
            skip_ytdlp = VendorNetworkError(
                "跳过 yt-dlp：未检测到 HTTP 代理；"
                "请在 Settings 填写 http_proxy（如 http://127.0.0.1:7890）"
            )
            rss_errors.append(_vendor_error_info("ytdlp", skip_ytdlp))
        errors = [*official_errors, *rss_errors]
        payload = {
            **(getattr(first_official_error, "payload", None) or {}),
            "fallback_errors": errors,
        }
        raise VendorNetworkError(
            _fallback_message("YouTube A failed all network fallbacks", errors),
            status_code=getattr(first_official_error, "status_code", None),
            payload={
                **payload,
                "proxy_label": _proxy_debug_label(),
            },
        ) from first_official_error

    @staticmethod
    def _video_ids_from_search(data: dict[str, Any], *, limit: int) -> list[str]:
        ids: list[str] = []
        for raw in data.get("items") or []:
            rid = raw.get("id") or {}
            vid = rid.get("videoId") if isinstance(rid, dict) else None
            if vid and vid not in ids:
                ids.append(str(vid))
            if len(ids) >= limit:
                break
        return ids

    def _parse_video_items(
        self,
        data: dict[str, Any],
        keywords: list[str],
        time_window: str,
        limit: int,
        *,
        require_keyword_match: bool,
    ) -> list[TrendItem]:
        items = data.get("items") or []
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for raw in items:
            sn = raw.get("snippet") or {}
            st = raw.get("statistics") or {}
            published = sn.get("publishedAt") or ""
            pub_ts = _parse_iso_ts(published)
            if pub_ts and pub_ts < cutoff:
                continue
            title = sn.get("title") or ""
            matched = _matches_keywords(f"{title} {sn.get('description', '')}", keywords)
            if require_keyword_match and keywords and not matched:
                continue
            if keywords and not matched:
                matched = [k for k in keywords if k]
            vid = raw.get("id") or ""
            out.append(
                TrendItem(
                    id=_new_item_id(),
                    platform="youtube",
                    external_id=vid,
                    external_url=f"https://www.youtube.com/watch?v={vid}",
                    title=title,
                    author=sn.get("channelTitle") or "",
                    author_url=(
                        f"https://www.youtube.com/channel/{sn['channelId']}"
                        if sn.get("channelId")
                        else None
                    ),
                    cover_url=(sn.get("thumbnails", {}).get("high") or {}).get("url"),
                    duration_seconds=_parse_iso_duration(
                        (raw.get("contentDetails") or {}).get("duration")
                    ),
                    description=sn.get("description"),
                    like_count=_coerce_int(st.get("likeCount")),
                    comment_count=_coerce_int(st.get("commentCount")),
                    view_count=_coerce_int(st.get("viewCount")),
                    publish_at=pub_ts,
                    fetched_at=_now(),
                    engine_used="a",
                    collector_name=self.name,
                    raw_payload_json=json.dumps(raw, ensure_ascii=False),
                    keywords_matched=matched,
                )
            )
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _rsshub_regions(region_code: str) -> list[str]:
        regions: list[str] = []
        primary = (region_code or "").strip().upper()
        for region in (primary or "US", "US"):
            if region and region not in regions:
                regions.append(region)
        return regions

    async def _fetch_invidious_trending(
        self,
        base: str,
        *,
        keywords: list[str],
        time_window: str,
        limit: int,
        region_code: str,
    ) -> list[TrendItem]:
        root = base.rstrip("/")
        if keywords:
            query = " ".join(k for k in keywords if k).strip()
            url = f"{root}/api/v1/search"
            params = {"q": query, "type": "video", "sort_by": "date", "page": 1}
        else:
            url = f"{root}/api/v1/trends"
            region = self._rsshub_regions(region_code)[0]
            params = {"region": region}
        data = await self._get_json(
            url,
            params=params,
            timeout=_YOUTUBE_RSSHUB_HTTP_TIMEOUT_S,
        )
        if not isinstance(data, list):
            raise VendorFormatError(f"invidious bad payload from {url}")
        items = self._parse_invidious_items(data, keywords, time_window, limit)
        if not items:
            raise VendorFormatError(f"invidious returned no items from {url}")
        return items

    def _parse_invidious_items(
        self,
        raw_items: list[Any],
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            if raw.get("type") and raw.get("type") != "video":
                continue
            vid = str(raw.get("videoId") or raw.get("video_id") or "")
            if not vid:
                continue
            title = str(raw.get("title") or "")
            author = str(raw.get("author") or "")
            desc = str(raw.get("description") or "")
            pub_ts = _coerce_int(raw.get("published")) or 0
            if pub_ts and pub_ts < cutoff:
                continue
            matched = _matches_keywords(f"{title} {desc}", keywords)
            if keywords and not matched:
                continue
            thumbs = raw.get("videoThumbnails") or []
            cover = None
            if thumbs and isinstance(thumbs[-1], dict):
                cover = thumbs[-1].get("url")
            author_id = raw.get("authorId")
            out.append(
                TrendItem(
                    id=_new_item_id(),
                    platform="youtube",
                    external_id=vid,
                    external_url=f"https://www.youtube.com/watch?v={vid}",
                    title=title,
                    author=author,
                    author_url=(
                        f"https://www.youtube.com/channel/{author_id}" if author_id else None
                    ),
                    cover_url=cover,
                    duration_seconds=_coerce_int(raw.get("lengthSeconds")),
                    description=desc,
                    view_count=_coerce_int(raw.get("viewCount")),
                    publish_at=pub_ts or None,
                    fetched_at=_now(),
                    engine_used="a",
                    collector_name="invidious",
                    raw_payload_json=json.dumps(raw, ensure_ascii=False),
                    keywords_matched=matched,
                    data_quality="medium",
                )
            )
            if len(out) >= limit:
                break
        return out

    async def _fetch_ytdlp_trending(
        self,
        *,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]:
        from idea_research_inline.dep_bootstrap import resolve_ytdlp_runner, run_ytdlp_subprocess

        runner = resolve_ytdlp_runner()
        if runner is None:
            err = VendorError("yt-dlp 未安装；执行 `pip install yt-dlp`")
            err.error_kind = "dependency"
            raise err
        proxy = resolve_http_proxy()
        ytdlp_timeout = _YOUTUBE_YTDLP_TIMEOUT_S if proxy else 20.0
        targets = _ytdlp_radar_targets(keywords, limit)
        ytdlp_errors: list[dict[str, Any]] = []
        for target in targets:
            args = [
                *runner,
                "--flat-playlist",
                "-j",
                "--no-warnings",
                "--no-playlist",
                "--extractor-args",
                "youtube:player_client=android,web",
                "--socket-timeout",
                "15" if not proxy else "30",
                *_ytdlp_proxy_args(),
                target,
            ]
            try:
                proc = await asyncio.to_thread(
                    run_ytdlp_subprocess,
                    args,
                    timeout=ytdlp_timeout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                hint = (
                    "；请为 OpenAkita 进程配置 HTTPS_PROXY 或系统代理"
                    if not proxy
                    else ""
                )
                ytdlp_errors.append(
                    _vendor_error_info(
                        f"ytdlp:{target}",
                        VendorTimeoutError(
                            f"yt-dlp radar timed out after {ytdlp_timeout:.0f}s{hint}",
                            payload={"target": target, "has_proxy": bool(proxy)},
                        ),
                    )
                )
                continue
            stderr = proc.stderr or ""
            if proc.returncode != 0:
                proxy_hint = _proxy_unreachable_hint(stderr)
                if proxy_hint:
                    err: VendorError = VendorNetworkError(f"yt-dlp radar failed: {proxy_hint}")
                else:
                    err = VendorError(f"yt-dlp radar failed: {stderr[-200:]}")
                    text = stderr.lower()
                    err.error_kind = (
                        "network"
                        if any(
                            token in text
                            for token in ("timed out", "timeout", "unable to download")
                        )
                        else "format"
                    )
                ytdlp_errors.append(_vendor_error_info(f"ytdlp:{target}", err))
                continue
            cutoff = _now() - _window_seconds(time_window)
            out: list[TrendItem] = []
            for line in (proc.stdout or "").splitlines():
                raw_line = line.strip()
                if not raw_line:
                    continue
                try:
                    raw = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                vid = str(raw.get("id") or "")
                if not vid and raw.get("url"):
                    match = _YOUTUBE_VIDEO_ID_RE.search(str(raw.get("url")))
                    vid = match.group(1) if match else ""
                if not vid:
                    continue
                title = str(raw.get("title") or "")
                author = str(
                    raw.get("channel")
                    or raw.get("uploader")
                    or raw.get("uploader_id")
                    or ""
                )
                desc = str(raw.get("description") or "")
                pub_ts = _coerce_int(raw.get("timestamp") or raw.get("release_timestamp")) or 0
                if pub_ts and pub_ts < cutoff:
                    continue
                matched = _matches_keywords(f"{title} {desc}", keywords)
                if keywords and not matched:
                    continue
                out.append(
                    TrendItem(
                        id=_new_item_id(),
                        platform="youtube",
                        external_id=vid,
                        external_url=f"https://www.youtube.com/watch?v={vid}",
                        title=title,
                        author=author,
                        cover_url=raw.get("thumbnail")
                        or raw.get("thumbnails", [{}])[-1].get("url")
                        if isinstance(raw.get("thumbnails"), list)
                        else None,
                        duration_seconds=_coerce_int(raw.get("duration")),
                        description=desc,
                        view_count=_coerce_int(raw.get("view_count")),
                        publish_at=pub_ts or None,
                        fetched_at=_now(),
                        engine_used="a",
                        collector_name="ytdlp",
                        raw_payload_json=json.dumps(raw, ensure_ascii=False),
                        keywords_matched=matched,
                        data_quality="high",
                    )
                )
                if len(out) >= limit:
                    break
            if out:
                return out
            empty_err = VendorError(f"yt-dlp radar returned no entries for {target}")
            empty_err.error_kind = "empty"
            ytdlp_errors.append(_vendor_error_info(f"ytdlp:{target}", empty_err))
        if ytdlp_errors:
            last = ytdlp_errors[-1]
            err = VendorError(str(last.get("message") or "yt-dlp radar failed"))
            err.error_kind = str(last.get("error_kind") or "network")
            err.payload = {"fallback_errors": ytdlp_errors}
            raise err
        err = VendorError("yt-dlp radar failed")
        err.error_kind = "network"
        raise err

    async def _fetch_comments(self, video_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
        data, _errors, _first_error = await self._fetch_official_json(
            params={
                "part": "snippet",
                "videoId": video_id,
                "maxResults": min(100, max(1, limit)),
                "order": "relevance",
                "textFormat": "plainText",
                "key": self._api_key,
            },
            candidates=(
                ("googleapis_comments", self.COMMENTS_URL),
                ("youtube.googleapis_comments", self.ALT_COMMENTS_URL),
            ),
        )
        if not data:
            return []
        out: list[dict[str, Any]] = []
        for raw in data.get("items") or []:
            snippet = (((raw or {}).get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            text = snippet.get("textDisplay") or snippet.get("textOriginal") or ""
            if not text:
                continue
            out.append(
                {
                    "comment_id": raw.get("id") or "",
                    "text": text,
                    "author": snippet.get("authorDisplayName") or "",
                    "like_count": _coerce_int(snippet.get("likeCount")) or 0,
                    "publish_at": _parse_iso_ts(snippet.get("publishedAt") or ""),
                }
            )
            if len(out) >= limit:
                break
        return out

    async def fetch_creator(self, url: str, limit: int = 20) -> dict[str, Any]:
        """Recent uploads + channel profile for compare_accounts (Data API v3)."""

        if not self._api_key:
            raise VendorAuthError("YouTube Data API key not configured")
        channel_query = _youtube_account_params_from_url(url)
        ps = min(max(1, int(limit)), 50)
        ch_data, ch_errors, ch_first = await self._fetch_official_json(
            params={
                "part": "snippet,statistics,contentDetails",
                "key": self._api_key,
                **channel_query,
            },
            candidates=(
                ("googleapis_channels", self.CHANNELS_URL),
                ("youtube.googleapis_channels", self.ALT_CHANNELS_URL),
            ),
            http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
        )
        if ch_data is None:
            err = ch_first or VendorNetworkError("YouTube channels API failed")
            if ch_errors:
                err.payload = {"official_errors": ch_errors}
            raise err
        ch_items = ch_data.get("items") or []
        if not ch_items:
            raise VendorFormatError(f"YouTube 频道未找到: {url!r}")
        ch_raw = ch_items[0]
        ch_sn = ch_raw.get("snippet") or {}
        ch_st = ch_raw.get("statistics") or {}
        channel_id = str(ch_raw.get("id") or "")
        uploads_id = (
            ((ch_raw.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        )
        if not uploads_id:
            raise VendorFormatError(f"YouTube 频道缺少 uploads 列表: {url!r}")
        pl_data, pl_errors, pl_first = await self._fetch_official_json(
            params={
                "part": "snippet",
                "playlistId": uploads_id,
                "maxResults": ps,
                "key": self._api_key,
            },
            candidates=(
                ("googleapis_playlistItems", self.PLAYLIST_ITEMS_URL),
                ("youtube.googleapis_playlistItems", self.ALT_PLAYLIST_ITEMS_URL),
            ),
            http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
        )
        if pl_data is None:
            err = pl_first or VendorNetworkError("YouTube playlistItems API failed")
            if pl_errors:
                err.payload = {"official_errors": pl_errors}
            raise err
        video_ids: list[str] = []
        for raw in pl_data.get("items") or []:
            rid = ((raw.get("snippet") or {}).get("resourceId") or {}).get("videoId")
            if rid and str(rid) not in video_ids:
                video_ids.append(str(rid))
            if len(video_ids) >= ps:
                break
        videos: list[TrendItem] = []
        if video_ids:
            video_data, vid_errors, vid_first = await self._fetch_official_json(
                params={
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(video_ids),
                    "key": self._api_key,
                },
                candidates=(
                    ("googleapis_videos", self.VIDEOS_URL),
                    ("youtube.googleapis_videos", self.ALT_VIDEOS_URL),
                ),
                http_timeout=_YOUTUBE_OFFICIAL_HTTP_TIMEOUT_S,
            )
            if video_data is None:
                err = vid_first or VendorNetworkError("YouTube videos API failed")
                if vid_errors:
                    err.payload = {"official_errors": vid_errors}
                raise err
            videos = self._parse_video_items(
                video_data,
                [],
                "30d",
                ps,
                require_keyword_match=False,
            )
        creator = {
            "name": ch_sn.get("title") or "",
            "profile_url": (
                f"https://www.youtube.com/channel/{channel_id}" if channel_id else url
            ),
            "follower_count": _coerce_int(ch_st.get("subscriberCount")),
            "bio": ch_sn.get("description") or "",
        }
        return {"creator": creator, "videos": videos}

    async def fetch_user(self, url: str, limit: int = 20) -> list[TrendItem]:
        payload = await self.fetch_creator(url, limit)
        return list(payload.get("videos") or [])

    async def fetch_single_source(
        self, url: str, *, with_comments: bool = False
    ) -> ResolvedSource | None:
        m = re.search(r"[?&]v=([\w-]{11})", url or "") or re.search(
            r"youtu\.be/([\w-]{11})", url or ""
        )
        if not m:
            raise VendorFormatError(f"unrecognized youtube url: {url!r}")
        vid = m.group(1)
        if not self._api_key:
            raise VendorAuthError("YouTube Data API key not configured")
        data = await self._get_json(
            self.VIDEOS_URL,
            params={
                "part": "snippet,statistics,contentDetails",
                "id": vid,
                "key": self._api_key,
            },
        )
        items = data.get("items") or []
        if not items:
            return None
        raw = items[0]
        sn = raw.get("snippet") or {}
        st = raw.get("statistics") or {}
        item = TrendItem(
            id=_new_item_id(),
            platform="youtube",
            external_id=vid,
            external_url=url,
            title=sn.get("title") or "",
            author=sn.get("channelTitle") or "",
            cover_url=(sn.get("thumbnails", {}).get("high") or {}).get("url"),
            duration_seconds=_parse_iso_duration((raw.get("contentDetails") or {}).get("duration")),
            description=sn.get("description"),
            like_count=_coerce_int(st.get("likeCount")),
            comment_count=_coerce_int(st.get("commentCount")),
            view_count=_coerce_int(st.get("viewCount")),
            publish_at=_parse_iso_ts(sn.get("publishedAt") or ""),
            fetched_at=_now(),
            engine_used="a",
            collector_name=self.name,
            raw_payload_json=json.dumps(raw, ensure_ascii=False),
        )
        comments: list[dict[str, Any]] = []
        if with_comments:
            with contextlib.suppress(VendorError):
                comments = await self._fetch_comments(vid)
        return ResolvedSource(item=item, comments=comments)

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        resolved = await self.fetch_single_source(url, with_comments=with_comments)
        return resolved.item if resolved is not None else None


def _parse_iso_ts(value: str) -> int:
    if not value:
        return 0
    try:
        from datetime import datetime

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return int(datetime.fromisoformat(value).timestamp())
    except Exception:
        return 0


def _rfc3339_utc(ts: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_duration(value: str | None) -> int | None:
    if not value or not value.startswith("PT"):
        return None
    h = re.search(r"(\d+)H", value)
    m = re.search(r"(\d+)M", value)
    s = re.search(r"(\d+)S", value)
    secs = (
        (int(h.group(1)) if h else 0) * 3600
        + (int(m.group(1)) if m else 0) * 60
        + (int(s.group(1)) if s else 0)
    )
    return secs or None


# --------------------------------------------------------------------------- #
# 3. RssHubCollector — Douyin / Xhs / Weibo (low-quality, no engagement)       #
# --------------------------------------------------------------------------- #


_RSSHUB_PATHS: dict[str, str] = {
    "douyin": "/douyin/user/{uid}",
    "xhs": "/xiaohongshu/user/{uid}",
    "weibo": "/weibo/search/hot",
    "bilibili": "/bilibili/popular/all",
    "youtube": "/youtube/trending/{region}",
}


class RssHubCollector(ApiCollectorBase):
    name = "rsshub"
    platform = "other"
    rate_limit_per_min = 30

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
        *,
        platform: str = "weibo",
        uid: str | None = None,
        region: str | None = None,
        http_timeout: float = 30.0,
    ) -> list[TrendItem]:
        path = _RSSHUB_PATHS.get(platform)
        if not path:
            raise VendorFormatError(f"no rsshub path for platform {platform!r}")
        if "{uid}" in path:
            if not uid:
                raise VendorFormatError(
                    f"rsshub path {path} requires uid for platform {platform!r}"
                )
            path = path.replace("{uid}", uid)
        if "{region}" in path:
            path = path.replace("{region}", (region or "US").strip().upper() or "US")
        url = self._rsshub_base + path
        text = await self._get_text(url, timeout=http_timeout)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise VendorFormatError(f"non-xml rss response from {url}: {exc}") from exc
        channel = root.find("channel")
        items_xml = (channel.findall("item") if channel is not None else []) or []
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for el in items_xml:
            title = (el.findtext("title") or "").strip()
            link = (el.findtext("link") or "").strip()
            desc = (el.findtext("description") or "").strip()
            pub_text = (el.findtext("pubDate") or "").strip()
            pub_ts = _parse_rfc822_ts(pub_text)
            if pub_ts and pub_ts < cutoff:
                continue
            matched = _matches_keywords(f"{title} {desc}", keywords)
            if keywords and not matched:
                continue
            ext_id = _hash_short(link or title)
            out.append(
                TrendItem(
                    id=_new_item_id(),
                    platform=platform
                    if platform in {"bilibili", "youtube", "douyin", "xhs", "ks", "weibo"}
                    else "other",
                    external_id=ext_id,
                    external_url=link,
                    title=title,
                    author=(el.findtext("author") or "").strip(),
                    description=desc,
                    publish_at=pub_ts,
                    fetched_at=_now(),
                    engine_used="a",
                    collector_name=self.name,
                    raw_payload_json=json.dumps(
                        {
                            "title": title,
                            "link": link,
                            "description": desc,
                            "pubDate": pub_text,
                        },
                        ensure_ascii=False,
                    ),
                    keywords_matched=matched,
                    data_quality="low",
                )
            )
            if len(out) >= limit:
                break
        return out

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        # RSS Hub does not expose single-item lookup; callers should use a
        # platform-specific collector instead.
        return None


def _parse_rfc822_ts(value: str) -> int:
    if not value:
        return 0
    try:
        from email.utils import parsedate_to_datetime

        return int(parsedate_to_datetime(value).timestamp())
    except Exception:
        return 0


def _hash_short(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 3b. YouTube oEmbed — lightweight metadata without yt-dlp subprocess         #
# --------------------------------------------------------------------------- #


_YOUTUBE_VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/)([\w-]{11})")


async def fetch_youtube_oembed_item(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TrendItem | None:
    """Resolve basic YouTube metadata via the public oEmbed endpoint."""

    match = _YOUTUBE_VIDEO_ID_RE.search(url or "")
    if not match:
        return None
    vid = match.group(1)
    try:
        if client is not None:
            resp = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
            )
        else:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as own:
                resp = await own.get(
                    "https://www.youtube.com/oembed",
                    params={"url": url, "format": "json"},
                )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    if not title:
        return None
    return TrendItem(
        id=_new_item_id(),
        platform="youtube",
        external_id=vid,
        external_url=url,
        title=title,
        author=str(data.get("author_name") or ""),
        cover_url=data.get("thumbnail_url"),
        duration_seconds=0,
        fetched_at=_now(),
        engine_used="a",
        collector_name="youtube_oembed",
        data_quality="medium",
    )


def youtube_item_from_url(url: str) -> TrendItem | None:
    """Build a minimal TrendItem from a YouTube watch URL without network I/O."""

    match = _YOUTUBE_VIDEO_ID_RE.search(url or "")
    if not match:
        return None
    vid = match.group(1)
    return TrendItem(
        id=_new_item_id(),
        platform="youtube",
        external_id=vid,
        external_url=url,
        title=url,
        author="",
        duration_seconds=0,
        fetched_at=_now(),
        engine_used="a",
        collector_name="youtube_url",
        data_quality="low",
    )


# --------------------------------------------------------------------------- #
# 4. UrlPasteCollector — yt-dlp -j fallback                                    #
# --------------------------------------------------------------------------- #


class UrlPasteCollector:
    """yt-dlp metadata fallback.

    Spawns ``yt-dlp -j {url}`` in a subprocess (run on a worker thread)
    and turns the resulting JSON into a single ``TrendItem``. Raises a
    ``VendorError(error_kind='dependency')`` when ``yt-dlp`` is not on
    ``PATH`` so the route layer renders the install hint from §15.
    """

    name = "ytdlp"
    platform = "other"

    def __init__(self, *, ytdlp_bin: str | None = None) -> None:
        self._bin = ytdlp_bin

    def _resolve_runner(self) -> list[str] | None:
        if self._bin:
            return [self._bin]
        from idea_research_inline.dep_bootstrap import resolve_ytdlp_runner

        return resolve_ytdlp_runner()

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        runner = self._resolve_runner()
        if runner is None:
            err = VendorError("yt-dlp not found in PATH or current Python runtime")
            err.error_kind = "dependency"
            raise err
        try:
            data = await asyncio.to_thread(self._run_sync, runner, url)
        except subprocess.TimeoutExpired as exc:
            err = VendorError(f"yt-dlp timeout for {url!r}")
            err.error_kind = "timeout"
            raise err from exc
        except subprocess.CalledProcessError as exc:
            err = VendorError(
                f"yt-dlp failed ({exc.returncode}): {exc.stderr[-200:] if exc.stderr else ''}"
            )
            err.error_kind = "format"
            raise err from exc
        if not data:
            return None
        platform = _platform_from_url(url) or "other"
        return TrendItem(
            id=_new_item_id(),
            platform=platform,  # type: ignore[arg-type]
            external_id=str(data.get("id") or _hash_short(url)),
            external_url=url,
            title=str(data.get("title") or ""),
            author=str(data.get("uploader") or data.get("channel") or ""),
            cover_url=data.get("thumbnail"),
            duration_seconds=_coerce_int(data.get("duration")),
            description=data.get("description"),
            like_count=_coerce_int(data.get("like_count")),
            comment_count=_coerce_int(data.get("comment_count")),
            view_count=_coerce_int(data.get("view_count")),
            publish_at=_coerce_int(data.get("timestamp")) or 0,
            fetched_at=_now(),
            engine_used="a",
            collector_name=self.name,
            raw_payload_json=json.dumps(data, ensure_ascii=False),
            data_quality="high",
        )

    async def fetch_trending(
        self, keywords: list[str], time_window: str = "24h", limit: int = 20
    ) -> list[TrendItem]:
        # yt-dlp does not produce a trending feed; collectors using
        # this engine in radar mode will simply yield zero items.
        return []

    def _run_sync(self, runner: list[str], url: str) -> dict[str, Any] | None:
        import tempfile

        from idea_research_inline.dep_bootstrap import run_ytdlp_subprocess

        platform = _platform_from_url(url) or "other"
        timeout_s = 120 if platform == "youtube" else 60
        args = [
            *runner,
            "-j",
            "--no-warnings",
            "--no-playlist",
            "--socket-timeout",
            "30",
        ]
        if platform == "youtube":
            args.extend(["--extractor-args", "youtube:player_client=android,web"])
        args.append(url)

        # Write JSON to a file instead of a pipe — large single-line payloads can
        # block on Windows when the parent uses capture_output in frozen runtimes.
        with tempfile.TemporaryDirectory(prefix="idea-ytdlp-") as td:
            out_path = Path(td) / "meta.json"
            with out_path.open("w", encoding="utf-8") as out_fh:
                proc = run_ytdlp_subprocess(
                    args,
                    timeout=timeout_s,
                    stdout=out_fh,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(
                    proc.returncode,
                    args,
                    None,
                    proc.stderr,
                )
            text = out_path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return None
            first = text.splitlines()[0]
            try:
                return json.loads(first)
            except json.JSONDecodeError as exc:
                err = VendorError(f"yt-dlp non-json output: {first[:120]!r}")
                err.error_kind = "format"
                raise err from exc


def _youtube_account_params_from_url(url: str) -> dict[str, str]:
    """Map a YouTube channel/home URL to channels.list query params."""

    raw = (url or "").strip()
    if re.search(r"[?&]v=([\w-]{11})|youtu\.be/([\w-]{11})", raw, re.I):
        raise VendorFormatError(
            "YouTube 对标请使用频道/主页链接（@handle、/channel/…），不要用单条视频链接"
        )
    clean = raw.split("?", 1)[0].rstrip("/")
    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", clean, re.I)
    if m:
        return {"id": m.group(1)}
    m = re.search(r"youtube\.com/@([\w.\-]+)", clean, re.I)
    if m:
        handle = m.group(1)
        return {"forHandle": handle if handle.startswith("@") else f"@{handle}"}
    m = re.search(r"youtube\.com/user/([\w.\-]+)", clean, re.I)
    if m:
        return {"forUsername": m.group(1)}
    raise VendorFormatError(f"无法识别的 YouTube 账号链接: {url!r}")


def _platform_from_url(url: str) -> str | None:
    if not url:
        return None
    u = url.lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xhs"
    if "kuaishou.com" in u:
        return "ks"
    if "weibo.com" in u or "weibo.cn" in u:
        return "weibo"
    return None


def trend_item_to_dict(item: TrendItem) -> dict[str, Any]:
    """Helper for tests / SQLite serialisation."""

    out = asdict(item)
    return out


__all__ = [
    "ApiCollectorBase",
    "BiliCollector",
    "CollectorError",
    "RssHubCollector",
    "UrlPasteCollector",
    "YouTubeCollector",
    "WINDOW_TO_SECONDS",
    "trend_item_to_dict",
]
