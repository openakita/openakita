"""Engine A collector tests — mock httpx (§6.1)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import httpx
import pytest
from idea_engine_api import (
    BiliCollector,
    RssHubCollector,
    UrlPasteCollector,
    YouTubeCollector,
    _parse_iso_duration,
    _parse_iso_ts,
)
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorError,
    VendorFormatError,
    VendorNetworkError,
    VendorRateLimitError,
    VendorTimeoutError,
)


def _client_with_handler(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# --------------------------------------------------------------------------- #
# BiliCollector                                                                #
# --------------------------------------------------------------------------- #


def test_bili_fetch_trending_parses_canonical_payload():
    import time as _t

    fresh = int(_t.time())
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "bvid": "BV1xx411",
                    "aid": 12345,
                    "title": "Cursor 评测：AI 编辑器的极限",
                    "desc": "上手 24h 实测",
                    "owner": {"name": "技术阿赵", "mid": 999},
                    "pic": "https://cover.example/x.jpg",
                    "duration": 600,
                    "stat": {
                        "like": 5000,
                        "reply": 200,
                        "share": 100,
                        "view": 80_000,
                    },
                    "pubdate": fresh - 60,
                }
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "popular" in str(request.url)
        return httpx.Response(200, json=payload)

    async def go() -> None:
        async with _client_with_handler(handler) as client:
            collector = BiliCollector(client=client)
            items = await collector.fetch_trending([], "30d", 5)
            assert len(items) == 1
            it = items[0]
            assert it.platform == "bilibili"
            assert it.external_id == "BV1xx411"
            assert it.like_count == 5000
            assert it.cover_url.startswith("https://cover")
            assert it.engine_used == "a"

    asyncio.run(go())


def test_bili_fetch_trending_filters_keywords_and_window():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "bvid": "BV1AAA",
                    "title": "完全无关",
                    "desc": "",
                    "stat": {"view": 10},
                    "pubdate": 1,  # very old
                },
                {
                    "bvid": "BV1BBB",
                    "title": "AI 工具盘点",
                    "desc": "",
                    "stat": {"view": 100},
                    "pubdate": 9_999_999_999,
                },
            ]
        },
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    async def go() -> None:
        async with _client_with_handler(handler) as client:
            collector = BiliCollector(client=client)
            items = await collector.fetch_trending(["AI"], "24h", 5)
            assert [it.external_id for it in items] == ["BV1BBB"]

    asyncio.run(go())


def test_bili_fetch_single_unknown_url_raises_format():
    async def go():
        async with _client_with_handler(lambda r: httpx.Response(200, json={})) as c:
            with pytest.raises(VendorFormatError):
                await BiliCollector(client=c).fetch_single("https://random.com")

    asyncio.run(go())


def test_bili_http_429_maps_to_rate_limit():
    async def go():
        def handler(request):
            return httpx.Response(429, text="too many")

        async with _client_with_handler(handler) as c:
            with pytest.raises(VendorRateLimitError):
                await BiliCollector(client=c).fetch_trending([], "24h", 5)

    asyncio.run(go())


def test_bili_http_403_maps_to_auth():
    async def go():
        async with _client_with_handler(lambda r: httpx.Response(403, text="nope")) as c:
            with pytest.raises(VendorAuthError):
                await BiliCollector(client=c).fetch_trending([], "24h", 5)

    asyncio.run(go())


def test_bili_http_500_maps_to_network():
    async def go():
        async with _client_with_handler(lambda r: httpx.Response(500, text="boom")) as c:
            with pytest.raises(VendorNetworkError):
                await BiliCollector(client=c).fetch_trending([], "24h", 5)

    asyncio.run(go())


def test_bili_timeout_maps_to_timeout(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    async def go():
        async with _client_with_handler(handler) as c:
            with pytest.raises(VendorTimeoutError):
                await BiliCollector(client=c).fetch_trending([], "24h", 5)

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# YouTubeCollector                                                             #
# --------------------------------------------------------------------------- #


def test_youtube_requires_api_key():
    async def go():
        async with _client_with_handler(lambda r: httpx.Response(200, json={})) as c:
            with pytest.raises(VendorAuthError):
                await YouTubeCollector(client=c).fetch_trending([], "24h", 5)

    asyncio.run(go())


def test_youtube_parses_video_payload():
    payload = {
        "items": [
            {
                "id": "abc12345678",
                "snippet": {
                    "title": "Cursor vs VS Code",
                    "description": "AI editor comparison",
                    "channelTitle": "Dev Reviews",
                    "channelId": "UCxx",
                    "publishedAt": "2099-12-01T12:00:00Z",
                    "thumbnails": {"high": {"url": "https://img.example/x"}},
                },
                "statistics": {
                    "likeCount": "1500",
                    "commentCount": "200",
                    "viewCount": "45000",
                },
                "contentDetails": {"duration": "PT12M34S"},
            }
        ]
    }

    def handler(request):
        assert "key=KEY" in str(request.url)
        return httpx.Response(200, json=payload)

    async def go():
        async with _client_with_handler(handler) as c:
            collector = YouTubeCollector(client=c, api_key="KEY")
            items = await collector.fetch_trending([], "30d", 5)
            assert len(items) == 1
            assert items[0].duration_seconds == 12 * 60 + 34
            assert items[0].view_count == 45000
            assert items[0].external_url.startswith("https://www.youtube.com/")

    asyncio.run(go())


def test_youtube_iso_helpers():
    assert _parse_iso_duration("PT1H2M3S") == 3723
    assert _parse_iso_ts("2024-12-01T00:00:00Z") > 0


# --------------------------------------------------------------------------- #
# RssHubCollector                                                              #
# --------------------------------------------------------------------------- #


_RSS_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss><channel>
<item>
  <title>AI 工具速评</title>
  <link>https://example.com/post/1</link>
  <author>weibo-user</author>
  <description>desc 1</description>
  <pubDate>Mon, 01 Dec 2099 12:00:00 +0000</pubDate>
</item>
<item>
  <title>无关</title>
  <link>https://example.com/post/2</link>
  <description>desc 2</description>
  <pubDate>Mon, 01 Dec 2099 13:00:00 +0000</pubDate>
</item>
</channel></rss>""".encode()


def test_rsshub_parses_xml_and_marks_low_quality():
    def handler(request):
        return httpx.Response(200, content=_RSS_XML)

    async def go():
        async with _client_with_handler(handler) as c:
            collector = RssHubCollector(client=c, rsshub_base="https://rsshub.app")
            items = await collector.fetch_trending(["AI"], "30d", 10, platform="weibo")
            assert len(items) == 1
            assert items[0].data_quality == "low"
            assert items[0].engine_used == "a"
            assert items[0].title == "AI 工具速评"

    asyncio.run(go())


def test_rsshub_invalid_xml_raises_format():
    def handler(request):
        return httpx.Response(200, content=b"not xml")

    async def go():
        async with _client_with_handler(handler) as c:
            with pytest.raises(VendorFormatError):
                await RssHubCollector(client=c).fetch_trending([], "24h", 5, platform="weibo")

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# UrlPasteCollector                                                            #
# --------------------------------------------------------------------------- #


def test_urlpaste_missing_binary_raises_dependency(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    async def go():
        with pytest.raises(VendorError) as ei:
            await UrlPasteCollector(ytdlp_bin="yt-dlp").fetch_single("https://b23.tv/x")
        assert ei.value.error_kind == "dependency"

    asyncio.run(go())


def test_urlpaste_parses_yt_dlp_json(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "yt-dlp")
    fake_json = json.dumps(
        {
            "id": "BV1xxx",
            "title": "Test",
            "uploader": "alice",
            "thumbnail": "https://t.example",
            "duration": 60,
            "like_count": 10,
            "comment_count": 2,
            "view_count": 100,
            "timestamp": 1_700_000_000,
        }
    )

    def fake_run(cmd, **kwargs):
        class _R:
            stdout = fake_json + "\n"
            returncode = 0

        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    async def go():
        item = await UrlPasteCollector().fetch_single("https://www.bilibili.com/video/BV1xxx")
        assert item is not None
        assert item.platform == "bilibili"
        assert item.title == "Test"
        assert item.view_count == 100

    asyncio.run(go())
