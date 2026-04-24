"""Engine B crawler tests — mock Playwright + CookiesVault (§6.2 / §6.3)."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
from idea_engine_crawler import (
    BiliLoggedCrawler,
    CookiesVault,
    DouyinCrawler,
    KsCrawler,
    PageResponse,
    PlaywrightDriver,
    PlaywrightUnavailable,
    WeiboCrawler,
    XhsCrawler,
)
from idea_research_inline.vendor_client import VendorError

# --------------------------------------------------------------------------- #
# Helpers — fake driver                                                        #
# --------------------------------------------------------------------------- #


def _make_driver(pages: dict[str, PageResponse]) -> PlaywrightDriver:
    """Return a PlaywrightDriver whose fetch is short-circuited."""

    async def fake_fetch(*, url: str, **kwargs: Any) -> PageResponse:
        if url in pages:
            return pages[url]
        # Allow URL prefix match (user-page tests use dynamic URLs).
        for k, v in pages.items():
            if url.startswith(k):
                return v
        return PageResponse(url=url, status=200, html="", json_payloads=[])

    return PlaywrightDriver(override_fetch=fake_fetch)


# --------------------------------------------------------------------------- #
# CookiesVault                                                                 #
# --------------------------------------------------------------------------- #


def test_cookies_vault_save_load_roundtrip(tmp_path: Path):
    vault = CookiesVault(tmp_path / "cookies.db")

    async def go() -> None:
        encrypted = await vault.save(
            "douyin",
            {"sessionid_ss": "x", "s_v_web_id": "y", "ttwid": "z"},
            expires_at=9999999999,
        )
        loaded = await vault.load("douyin")
        assert loaded is not None
        assert loaded.cookies["sessionid_ss"] == "x"
        assert loaded.expires_at == 9999999999
        # Encryption is best-effort; must succeed if cryptography is present.
        statuses = await vault.list_status()
        assert any(s["platform"] == "douyin" for s in statuses)
        # delete -> 1 row
        deleted = await vault.delete("douyin")
        assert deleted == 1
        assert await vault.load("douyin") is None
        # Sanity: encryption flag is consistent.
        assert isinstance(encrypted, bool)

    asyncio.run(go())


def test_cookies_vault_falls_back_when_cryptography_missing(tmp_path: Path, monkeypatch):
    """Simulate `import cryptography` failing -> fallback to plain text."""

    real_import = importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("cryptography"):
            raise ImportError("simulated missing cryptography")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    sys.modules.pop("cryptography", None)
    sys.modules.pop("cryptography.fernet", None)
    monkeypatch.setitem(sys.modules, "cryptography", None)

    vault = CookiesVault(tmp_path / "cookies.db")

    async def go() -> None:
        encrypted = await vault.save("douyin", {"k": "v"})
        # Fallback path → encryption_ready False, plain bytes stored.
        assert encrypted is False
        assert vault.encryption_ready is False
        assert vault.warn_messages
        loaded = await vault.load("douyin")
        assert loaded is not None
        assert loaded.cookies == {"k": "v"}

    asyncio.run(go())


def test_cookies_vault_status_marks_expired(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")

    async def go() -> None:
        await vault.save("xhs", {"web_session": "x", "xsecappid": "y", "a1": "z"}, expires_at=1)
        statuses = await vault.list_status()
        s = next(s for s in statuses if s["platform"] == "xhs")
        assert s["expired"] is True

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# CrawlerBase guards                                                           #
# --------------------------------------------------------------------------- #


def test_crawler_without_risk_acknowledged_raises_auth(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    driver = _make_driver({})
    crawler = DouyinCrawler(driver=driver, vault=vault, risk_acknowledged=False)

    async def go() -> None:
        with pytest.raises(VendorError) as ei:
            await crawler.fetch_trending([], "24h", 5)
        assert ei.value.error_kind == "auth"

    asyncio.run(go())


def test_crawler_without_cookies_raises_cookies_expired(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    driver = _make_driver({})
    crawler = XhsCrawler(driver=driver, vault=vault, risk_acknowledged=True)

    async def go() -> None:
        with pytest.raises(VendorError) as ei:
            await crawler.fetch_trending([], "24h", 5)
        assert ei.value.error_kind == "cookies_expired"

    asyncio.run(go())


def test_crawler_with_expired_cookies_raises(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    driver = _make_driver({})
    crawler = WeiboCrawler(driver=driver, vault=vault, risk_acknowledged=True)

    async def go() -> None:
        await vault.save("weibo", {"SUB": "x", "SUBP": "y"}, expires_at=1)
        with pytest.raises(VendorError) as ei:
            await crawler.fetch_trending([], "24h", 5)
        assert ei.value.error_kind == "cookies_expired"

    asyncio.run(go())


def test_crawler_blocked_html_raises(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    blocked = PageResponse(
        url=DouyinCrawler.listing_url,
        status=200,
        html="<html>请输入验证码</html>",
        json_payloads=[],
    )
    driver = _make_driver({DouyinCrawler.listing_url: blocked})
    crawler = DouyinCrawler(driver=driver, vault=vault, risk_acknowledged=True)

    async def go() -> None:
        await vault.save(
            "douyin",
            {"sessionid_ss": "x", "s_v_web_id": "y", "ttwid": "z"},
        )
        with pytest.raises(VendorError) as ei:
            await crawler.fetch_trending([], "24h", 5)
        assert ei.value.error_kind == "crawler_blocked"

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# Per-platform happy paths (5 platforms)                                       #
# --------------------------------------------------------------------------- #


def _ack_save(vault: CookiesVault, platform: str, cookies: dict[str, str]) -> None:
    asyncio.run(vault.save(platform, cookies))


def test_douyin_happy_path(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(
        vault,
        "douyin",
        {"sessionid_ss": "1", "s_v_web_id": "2", "ttwid": "3"},
    )
    payload = {
        "aweme_list": [
            {
                "aweme_id": "100",
                "desc": "AI 工具实测：颠覆体验",
                "author": {"nickname": "tester", "sec_uid": "secU"},
                "video": {"duration": 60_000, "cover": {"url_list": ["https://t/c.jpg"]}},
                "statistics": {
                    "digg_count": 5_000,
                    "comment_count": 100,
                    "share_count": 50,
                    "play_count": 90_000,
                },
                "create_time": 1_700_000_000,
            }
        ]
    }
    driver = _make_driver(
        {
            DouyinCrawler.listing_url: PageResponse(
                url=DouyinCrawler.listing_url,
                status=200,
                html="<div></div>",
                json_payloads=[payload],
            )
        }
    )
    crawler = DouyinCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_trending(["AI"], "30d", 5))
    assert len(items) == 1
    assert items[0].external_id == "100"
    assert items[0].like_count == 5000
    assert items[0].engine_used == "b"


def test_xhs_happy_path(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(
        vault,
        "xhs",
        {"web_session": "x", "xsecappid": "y", "a1": "z"},
    )
    payload = {
        "data": {
            "items": [
                {
                    "id": "n1",
                    "note_id": "n1",
                    "title": "AI 笔记",
                    "user": {"nickname": "测评师", "user_id": "uX"},
                    "interact_info": {
                        "liked_count": 1000,
                        "comment_count": 50,
                        "shared_count": 10,
                    },
                    "cover": {"url": "https://x/c.jpg"},
                    "time": 1_700_000_000,
                }
            ]
        }
    }
    driver = _make_driver(
        {
            XhsCrawler.listing_url: PageResponse(
                url=XhsCrawler.listing_url,
                status=200,
                html="<div></div>",
                json_payloads=[payload],
            )
        }
    )
    crawler = XhsCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_trending(["AI"], "30d", 5))
    assert len(items) == 1
    assert items[0].platform == "xhs"
    assert items[0].comment_count == 50


def test_ks_happy_path(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(
        vault,
        "ks",
        {"did": "1", "kpf": "x", "kpn": "y", "clientid": "z"},
    )
    payload = {
        "data": {
            "feeds": [
                {
                    "photoId": "p1",
                    "id": "p1",
                    "caption": "AI 工具速评",
                    "user": {"name": "kol", "id": "u1"},
                    "duration": 30_000,
                    "likeCount": 800,
                    "commentCount": 20,
                    "viewCount": 50_000,
                    "timestamp": 1_700_000_000,
                    "coverUrl": "https://k/c.jpg",
                }
            ]
        }
    }
    driver = _make_driver(
        {
            KsCrawler.listing_url: PageResponse(
                url=KsCrawler.listing_url,
                status=200,
                html="<div></div>",
                json_payloads=[payload],
            )
        }
    )
    crawler = KsCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_trending(["AI"], "30d", 5))
    assert len(items) == 1
    assert items[0].platform == "ks"
    assert items[0].duration_seconds == 30


def test_bili_logged_happy_path(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(
        vault,
        "bilibili",
        {"SESSDATA": "1", "bili_jct": "x", "DedeUserID": "999"},
    )
    payload = {
        "data": {
            "list": [
                {
                    "bvid": "BVAA",
                    "title": "AI 编辑器横评",
                    "owner": {"name": "kol", "mid": 1},
                    "stat": {"like": 100, "reply": 10, "share": 2, "view": 9000},
                    "pic": "https://b/c.jpg",
                    "duration": 120,
                    "pubdate": 1_700_000_000,
                }
            ]
        }
    }
    driver = _make_driver(
        {
            BiliLoggedCrawler.listing_url: PageResponse(
                url=BiliLoggedCrawler.listing_url,
                status=200,
                html="<div></div>",
                json_payloads=[payload],
            )
        }
    )
    crawler = BiliLoggedCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_trending(["AI"], "30d", 5))
    assert len(items) == 1
    assert items[0].platform == "bilibili"
    assert items[0].view_count == 9000


def test_weibo_happy_path(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(vault, "weibo", {"SUB": "x", "SUBP": "y"})
    payload = {
        "data": {
            "cards": [
                {
                    "mid": "M1",
                    "id": "M1",
                    "text_raw": "AI 工具上线 一夜爆火",
                    "user": {"screen_name": "kol", "id": 9},
                    "attitudes_count": 500,
                    "comments_count": 100,
                    "reposts_count": 20,
                    "created_at": "Mon Dec 01 12:00:00 +0800 2099",
                }
            ]
        }
    }
    driver = _make_driver(
        {
            WeiboCrawler.listing_url: PageResponse(
                url=WeiboCrawler.listing_url,
                status=200,
                html="<div></div>",
                json_payloads=[payload],
            )
        }
    )
    crawler = WeiboCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_trending(["AI"], "30d", 5))
    assert len(items) == 1
    assert items[0].platform == "weibo"
    assert items[0].like_count == 500


# --------------------------------------------------------------------------- #
# fetch_user                                                                   #
# --------------------------------------------------------------------------- #


def test_douyin_fetch_user_returns_videos(tmp_path: Path):
    vault = CookiesVault(tmp_path / "v.db")
    _ack_save(
        vault,
        "douyin",
        {"sessionid_ss": "1", "s_v_web_id": "2", "ttwid": "3"},
    )
    user_url = "https://www.douyin.com/user/MS4wAAA"
    payload = {
        "aweme_list": [
            {
                "aweme_id": str(i),
                "desc": f"video {i}",
                "author": {"nickname": "x"},
                "video": {"duration": 1000, "cover": {"url_list": [None]}},
                "statistics": {
                    "digg_count": i,
                    "comment_count": 0,
                    "share_count": 0,
                    "play_count": 1000,
                },
                "create_time": 1_700_000_000 + i,
            }
            for i in range(3)
        ]
    }
    driver = _make_driver(
        {user_url: PageResponse(url=user_url, status=200, html="", json_payloads=[payload])}
    )
    crawler = DouyinCrawler(driver=driver, vault=vault, risk_acknowledged=True)
    items = asyncio.run(crawler.fetch_user(user_url, max_videos=2))
    assert [it.external_id for it in items] == ["0", "1"]


# --------------------------------------------------------------------------- #
# Driver lazy import                                                           #
# --------------------------------------------------------------------------- #


def test_playwright_driver_raises_when_module_missing(monkeypatch):
    """Driver.fetch with no override + no playwright installed → dependency."""

    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    driver = PlaywrightDriver()

    async def go():
        with pytest.raises(PlaywrightUnavailable) as ei:
            await driver._ensure_browser()
        assert ei.value.error_kind == "dependency"

    asyncio.run(go())
