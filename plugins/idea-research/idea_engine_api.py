"""Engine A — safe collectors for idea-research (§6.1).

Four collectors backed by official APIs / RSS feeds, all driven by
``httpx.AsyncClient`` so they can be unit-tested by patching the
client. None of them require user-supplied cookies; the only optional
secret is a YouTube Data API v3 key.

Each collector exposes:

    async def fetch_trending(keywords, time_window, limit) -> list[TrendItem]
    async def fetch_single(url, with_comments=False) -> TrendItem | None

The base ``ApiCollectorBase`` enforces a tiny per-instance rate limit
plus uniform error mapping into ``VendorError`` subclasses (which carry
``error_kind`` already, so the pipeline / route layer can render the
bilingual hint from §15 without translating).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict
from typing import Any

import httpx
from idea_models import TrendItem
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
            raise VendorNetworkError(
                f"http error fetching {url}: {exc}", payload={"url": url}
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
            raise VendorNetworkError(
                f"http error fetching {url}: {exc}", payload={"url": url}
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


class BiliCollector(ApiCollectorBase):
    name = "bili_api"
    platform = "bilibili"
    rate_limit_per_min = 60

    POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular"
    VIEW_URL = "https://api.bilibili.com/x/web-interface/view"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        data = await self._get_json(
            self.POPULAR_URL, params={"ps": min(50, max(1, limit * 2)), "pn": 1}
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            raise VendorFormatError(f"bili popular bad payload: {data!r}"[:200])
        items_raw = (data.get("data") or {}).get("list") or []
        cutoff = _now() - _window_seconds(time_window)
        out: list[TrendItem] = []
        for raw in items_raw:
            pub = _coerce_int(raw.get("pubdate")) or 0
            if pub and pub < cutoff:
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
                cover_url=raw.get("pic"),
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

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        bv = _BILI_BV_RE.search(url or "")
        av = _BILI_AID_RE.search(url or "")
        params: dict[str, Any] = {}
        if bv:
            params["bvid"] = bv.group(0)
        elif av:
            params["aid"] = av.group(1)
        else:
            raise VendorFormatError(f"unrecognized bilibili url: {url!r}")
        data = await self._get_json(self.VIEW_URL, params=params)
        if not isinstance(data, dict) or data.get("code") != 0:
            return None
        d = data.get("data") or {}
        owner = d.get("owner") or {}
        stat = d.get("stat") or {}
        return TrendItem(
            id=_new_item_id(),
            platform="bilibili",
            external_id=str(d.get("bvid") or d.get("aid")),
            external_url=url,
            title=str(d.get("title") or ""),
            author=str(owner.get("name") or ""),
            cover_url=d.get("pic"),
            duration_seconds=_coerce_int(d.get("duration")),
            description=d.get("desc"),
            like_count=_coerce_int(stat.get("like")),
            comment_count=_coerce_int(stat.get("reply")),
            share_count=_coerce_int(stat.get("share")),
            view_count=_coerce_int(stat.get("view")),
            publish_at=_coerce_int(d.get("pubdate")) or 0,
            fetched_at=_now(),
            engine_used="a",
            collector_name=self.name,
            raw_payload_json=json.dumps(d, ensure_ascii=False),
        )


# --------------------------------------------------------------------------- #
# 2. YouTubeCollector — Data API v3                                            #
# --------------------------------------------------------------------------- #


class YouTubeCollector(ApiCollectorBase):
    name = "youtube_api"
    platform = "youtube"
    rate_limit_per_min = 100  # quota gates real ceiling

    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
        *,
        region_code: str = "CN",
    ) -> list[TrendItem]:
        if not self._api_key:
            raise VendorAuthError("YouTube Data API key not configured")
        params = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(50, max(1, limit * 2)),
            "key": self._api_key,
        }
        data = await self._get_json(self.VIDEOS_URL, params=params)
        items = data.get("items") or []
        if "error" in data:
            err = data["error"]
            kind = "quota" if err.get("code") == 403 else "auth"
            raise (VendorQuotaError(str(err)) if kind == "quota" else VendorAuthError(str(err)))
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
            if keywords and not matched:
                continue
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

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
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
        return TrendItem(
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
    "youtube": "/youtube/trending/CN",
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
        url = self._rsshub_base + path
        text = await self._get_text(url)
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
        self._bin = ytdlp_bin or shutil.which("yt-dlp") or "yt-dlp"

    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None:
        if shutil.which(self._bin) is None:
            err = VendorError("yt-dlp not found on PATH")
            err.error_kind = "dependency"
            raise err
        try:
            data = await asyncio.to_thread(self._run_sync, url)
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

    def _run_sync(self, url: str) -> dict[str, Any] | None:
        proc = subprocess.run(
            [self._bin, "-j", "--no-warnings", "--no-playlist", url],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (proc.stdout or "").strip().splitlines()
        if not out:
            return None
        first = out[0]
        try:
            return json.loads(first)
        except json.JSONDecodeError as exc:
            err = VendorError(f"yt-dlp non-json output: {first[:120]!r}")
            err.error_kind = "format"
            raise err from exc


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
