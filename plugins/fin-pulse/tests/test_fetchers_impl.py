"""Phase 2b — per-source fetcher behaviour.

Every test stubs the network layer so the suite stays hermetic. httpx
ships ``MockTransport`` in the standard distribution, so we do not pull
in respx. Sources that need ``feedparser`` skip gracefully when the dep
is absent (the pipeline already classifies that as ``error_kind =
dependency``).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import httpx
import pytest

import finpulse_fetchers._http as http_mod
from finpulse_fetchers.base import NormalizedItem
from finpulse_fetchers.eastmoney import EastmoneyFetcher
from finpulse_fetchers.fed_fomc import FedFOMCFetcher
from finpulse_fetchers.newsnow import NewsNowFetcher
from finpulse_fetchers.rss import FEEDPARSER_AVAILABLE, GenericRSSFetcher, parse_feed
from finpulse_fetchers.yicai import YicaiFetcher
from finpulse_fetchers.nbd import NBDFetcher
from finpulse_fetchers.stcn import STCNFetcher

BS4_AVAILABLE: bool
try:
    importlib.import_module("bs4")
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """Rewire :func:`make_client` to return a MockTransport-backed client."""

    def _factory(
        *,
        timeout: float = 15.0,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        user_agent: str | None = None,
    ) -> httpx.AsyncClient:
        headers = {"User-Agent": user_agent or "test"}
        if extra_headers:
            headers.update(extra_headers)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(http_mod, "make_client", _factory)
    for mod_name in (
        "finpulse_fetchers.rss",
        "finpulse_fetchers.eastmoney",
        "finpulse_fetchers.sec_edgar",
        "finpulse_fetchers.pbc_omo",
        "finpulse_fetchers.fed_fomc",
        "finpulse_fetchers.newsnow",
        "finpulse_fetchers.yicai",
        "finpulse_fetchers.nbd",
        "finpulse_fetchers.stcn",
    ):
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "make_client"):
            monkeypatch.setattr(mod, "make_client", _factory)


# ── Yicai (第一财经) ──────────────────────────────────────────────────────


class TestYicaiFetcher:
    def test_parses_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {
                "title": "央行开展逆回购操作",
                "url": "/news/id-101.html",
                "CreatedDate": "2026-04-24 09:10:00",
                "NewsContent": "摘要内容",
            },
            {
                "title": "",
                "url": "/news/id-102.html",
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        items = _run(YicaiFetcher(config={}).fetch())

        assert len(items) == 1
        assert "yicai.com" in items[0].url
        assert items[0].title == "央行开展逆回购操作"

    def test_parses_current_news_title_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = [
            {
                "NewsTitle": "第一财经当前接口标题",
                "url": "/news/103167815.html",
                "CreateDate": "2026-05-07T10:56:10",
                "NewsNotes": "当前接口摘要",
            }
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        items = _run(YicaiFetcher(config={}).fetch())

        assert len(items) == 1
        assert items[0].title == "第一财经当前接口标题"
        assert items[0].published_at == "2026-05-07T10:56:10"
        assert items[0].summary == "当前接口摘要"


# ── NBD (每日经济新闻) ──────────────────────────────────────────────────


class TestNBDFetcher:
    def test_parses_html_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = (
            '<html><body>'
            '<a href="https://www.nbd.com.cn/articles/2026-04-24/001.html">'
            'A股三大指数高开</a>'
            '<span>2026-04-24 09:30:00</span>'
            '<a href="https://www.nbd.com.cn/articles/2026-04-24/002.html">'
            '上市公司公告</a>'
            '</body></html>'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(NBDFetcher(config={}).fetch())

        assert len(items) >= 2
        assert items[0].title == "A股三大指数高开"


# ── STCN (证券时报) ──────────────────────────────────────────────────────


class TestSTCNFetcher:
    def test_parses_html_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = (
            '<html><body>'
            '<a href="https://www.stcn.com/article/detail/abc123.html">'
            '证券市场要闻</a>'
            '<span>2026-04-24 10:00</span>'
            '</body></html>'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(STCNFetcher(config={}).fetch())

        assert len(items) >= 1
        assert items[0].title == "证券市场要闻"

    def test_parses_current_relative_article_links(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            '<html><body><li class="no-pr"><div class="tt">'
            '<a href="/article/detail/3897513.html" target="_blank">\n'
            "  4月300城宅地成交平均溢价率8.9%  "
            "</a></div>"
            '<span>2026-05-07 10:30</span>'
            "</li></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(STCNFetcher(config={}).fetch())

        assert len(items) == 1
        assert items[0].url == "https://www.stcn.com/article/detail/3897513.html"
        assert items[0].title == "4月300城宅地成交平均溢价率8.9%"


# ── EastMoney ────────────────────────────────────────────────────────────


class TestEastMoneyFetcher:
    def test_parses_html_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Covers the new ``finance.eastmoney.com/a/czqyw.html`` scrape.

        The old private ``np-listapi`` JSON endpoint now rejects every
        unsigned request (``Required String parameter 'mTypeAndCode' is
        not present``), and NewsNow has no ``eastmoney`` platform, so
        the fetcher falls back to scraping the rolling "证券聚焦" page
        directly. This asserts the anchor-based extractor.
        """
        html = (
            "<html><body><ul class='newsList'>"
            "<li><p class='title'><a "
            "href=\"//finance.eastmoney.com/a/202604240001.html\" "
            "target=\"_blank\" title=\"A股三大指数低开高走\">"
            "A股三大指数低开高走</a></p>"
            "<span class='time'>2026-04-24 09:10</span></li>"
            "<li><p class='title'><a "
            "href=\"https://finance.eastmoney.com/a/202604240002.html\" "
            "title=\"上市公司公告速览\">上市公司公告速览</a></p></li>"
            "<li><p class='title'><a href=\"\" title=\"\">drop me</a></p>"
            "</li>"
            "</ul></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(EastmoneyFetcher(config={}).fetch())

        assert len(items) >= 2
        titles = [it.title for it in items]
        assert "A股三大指数低开高走" in titles
        assert "上市公司公告速览" in titles
        # Protocol-relative URLs get upgraded to https.
        url_lookup = {it.title: it.url for it in items}
        assert url_lookup["A股三大指数低开高走"].startswith("https://")

    def test_uses_current_dynamic_json_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "code": "1",
            "message": "success",
            "data": {
                "list": [
                    {
                        "title": "首批科创债ETF上市首日成交活跃",
                        "uniqueUrl": "http://finance.eastmoney.com/a/202605073729522834.html",
                        "showTime": "2026-05-07 09:07:49",
                        "summary": "东方财富接口摘要",
                        "mediaName": "东方财富研究中心",
                    }
                ]
            },
        }
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        items = _run(EastmoneyFetcher(config={}).fetch())

        assert len(items) == 1
        assert "getNewsByColumns" in seen_urls[0]
        assert items[0].title == "首批科创债ETF上市首日成交活跃"
        assert items[0].published_at == "2026-05-07T09:07:49Z"
        assert items[0].extra["parser"] == "eastmoney_api"


# ── NewsNow ──────────────────────────────────────────────────────────────


class TestNewsNowFetcher:
    def test_off_mode_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = {"newsnow.mode": "off", "newsnow.api_url": "https://example.com/api/s"}
        items = _run(NewsNowFetcher(config=cfg).fetch())
        assert items == []

    def test_public_mode_iterates_source_defs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_pids: list[str] = []

        async def fake_fetch(*, platform_id: str, source_id: str, config: Any, timeout_sec: float) -> list[NormalizedItem]:
            called_pids.append(platform_id)
            return [NormalizedItem(source_id=source_id, title=f"from {platform_id}", url=f"https://x.com/{platform_id}")]

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", fake_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://custom.example.com/api/s",
        }
        items = _run(NewsNowFetcher(config=cfg).fetch())

        assert len(items) >= 1
        assert "wallstreetcn" in called_pids
        source_ids = {it.source_id for it in items}
        assert any(sid in source_ids for sid in ("wallstreetcn", "cls", "jin10"))


# ── Fed FOMC calendar gating ────────────────────────────────────────────


class TestFedFOMCCalendarGate:
    def test_returns_empty_when_today_not_in_calendar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate a calendar with no dates matching today.
        from finpulse_fetchers import fed_fomc as mod

        monkeypatch.setattr(mod, "_load_calendar", lambda: {"1900-01-01"})

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("fed_fomc must not hit the network on non-release days")

        _patch_transport(monkeypatch, handler)
        items = _run(
            FedFOMCFetcher(config={"fed_fomc.use_calendar_gate": "true"}).fetch()
        )
        assert items == []

    def test_empty_calendar_allows_scrape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty calendar means "gate disabled"; the fetcher proceeds.
        if not BS4_AVAILABLE:
            pytest.skip("bs4 not installed")
        from finpulse_fetchers import fed_fomc as mod

        monkeypatch.setattr(mod, "_load_calendar", lambda: set())
        html = (
            "<html><body>"
            "<a href='/newsevents/pressreleases/20260430-foo.htm'>FOMC Statement 20260430</a>"
            "</body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        _patch_transport(monkeypatch, handler)
        items = _run(FedFOMCFetcher(config={}).fetch())
        assert len(items) >= 1
        assert any("20260430" in it.url for it in items)


# ── Generic RSS ──────────────────────────────────────────────────────────


class TestGenericRSSFetcher:
    def test_no_feeds_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = _run(GenericRSSFetcher(config={"rss_generic.feeds": ""}).fetch())
        assert items == []

    @pytest.mark.skipif(
        not FEEDPARSER_AVAILABLE, reason="feedparser not installed"
    )
    def test_multi_feed_aggregation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _make_body(feed_id: int) -> str:
            return (
                "<?xml version='1.0'?><rss version='2.0'><channel>"
                f"<title>feed-{feed_id}</title>"
                "<item><title>Post</title>"
                f"<link>https://feed{feed_id}.example.com/p</link></item>"
                "</channel></rss>"
            )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "f1" in url:
                return httpx.Response(200, text=_make_body(1))
            return httpx.Response(200, text=_make_body(2))

        _patch_transport(monkeypatch, handler)
        cfg = {
            "rss_generic.feeds": (
                "https://f1.example/rss\nhttps://f2.example/rss"
            )
        }
        items = _run(GenericRSSFetcher(config=cfg).fetch())
        # Each feed's item has ``extra['feed_url']`` threaded through.
        urls = [item.url for item in items]
        assert "https://feed1.example.com/p" in urls
        assert "https://feed2.example.com/p" in urls
        assert all("feed_url" in item.extra for item in items)


# ── Parse feed direct ────────────────────────────────────────────────────


@pytest.mark.skipif(not FEEDPARSER_AVAILABLE, reason="feedparser not installed")
class TestParseFeed:
    def test_drops_entries_without_title_or_link(self) -> None:
        body = (
            "<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>"
            "<item><title>ok</title><link>https://e.com/1</link></item>"
            "<item><title></title><link>https://e.com/2</link></item>"
            "<item><title>no-link</title></item>"
            "</channel></rss>"
        )
        items = parse_feed("x", body)
        assert len(items) == 1
        assert items[0].url == "https://e.com/1"


class TestParseFeedStdlibFallback:
    """Stdlib ``xml.etree`` fallback parser that keeps nbs/fed_fomc/
    sec_edgar/rss_generic working when the optional ``feedparser`` dep
    is missing. Previously these sources surfaced the
    ``dependency · feedparser is required`` banner and produced zero
    rows — the regression reported by the user with five failing cards.
    """

    def test_rss_2_0_parses_without_feedparser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the fallback path regardless of whether feedparser is
        # actually installed in the dev env.
        import finpulse_fetchers.rss as rss_mod

        monkeypatch.setattr(rss_mod, "FEEDPARSER_AVAILABLE", False)

        body = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel>"
            "<title>x</title><link>https://e.com</link>"
            "<item>"
            "<title>Press Release</title>"
            "<link>https://e.com/a</link>"
            "<description>some &lt;b&gt;bold&lt;/b&gt; summary</description>"
            "<pubDate>Mon, 22 Apr 2024 09:00:00 GMT</pubDate>"
            "</item>"
            "<item>"
            "<title></title><link>https://e.com/skip</link>"
            "</item>"
            "</channel></rss>"
        )

        items = rss_mod.parse_feed("x", body)
        assert len(items) == 1
        assert items[0].title == "Press Release"
        assert items[0].url == "https://e.com/a"
        # HTML tags stripped, but text content retained.
        assert items[0].summary and "bold" in items[0].summary
        assert items[0].published_at and "2024" in items[0].published_at

    def test_atom_parses_without_feedparser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import finpulse_fetchers.rss as rss_mod

        monkeypatch.setattr(rss_mod, "FEEDPARSER_AVAILABLE", False)

        body = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<feed xmlns='http://www.w3.org/2005/Atom'>"
            "<title>SEC Filings</title>"
            "<entry>"
            "<title>8-K filed</title>"
            "<link href='https://sec.gov/8k/1' />"
            "<summary>material event</summary>"
            "<updated>2024-05-01T12:00:00Z</updated>"
            "</entry>"
            "</feed>"
        )

        items = rss_mod.parse_feed("sec_edgar", body)
        assert len(items) == 1
        assert items[0].title == "8-K filed"
        assert items[0].url == "https://sec.gov/8k/1"
        assert items[0].published_at == "2024-05-01T12:00:00Z"


# ── Smoke: every fetcher is importable & obeys BaseFetcher contract ──────


class TestFetcherContract:
    @pytest.mark.parametrize(
        "module_name, class_name",
        [
            ("finpulse_fetchers.eastmoney", "EastmoneyFetcher"),
            ("finpulse_fetchers.pbc_omo", "PbcOmoFetcher"),
            ("finpulse_fetchers.nbs", "NBSFetcher"),
            ("finpulse_fetchers.fed_fomc", "FedFOMCFetcher"),
            ("finpulse_fetchers.sec_edgar", "SecEdgarFetcher"),
            ("finpulse_fetchers.rss", "GenericRSSFetcher"),
            ("finpulse_fetchers.newsnow", "NewsNowFetcher"),
            ("finpulse_fetchers.yicai", "YicaiFetcher"),
            ("finpulse_fetchers.nbd", "NBDFetcher"),
            ("finpulse_fetchers.stcn", "STCNFetcher"),
        ],
    )
    def test_each_fetcher_defines_source_id(
        self, module_name: str, class_name: str
    ) -> None:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        assert cls.source_id, f"{class_name} missing source_id"
        assert callable(getattr(cls, "fetch", None)), (
            f"{class_name} must define async fetch()"
        )


# ── NormalizedItem cross-source dedupe hash ─────────────────────────────


class TestCrossSourceDedupe:
    def test_same_canonical_url_hashes_equal_across_sources(self) -> None:
        """Same article posted on wallstreetcn and newsnow:wallstreetcn-hot
        produces the same ``url_hash`` so :func:`upsert_article` collapses
        them into one row (the 2c dedupe behaviour).
        """
        a = NormalizedItem(
            source_id="wallstreetcn",
            title="t",
            url="https://wallstreetcn.com/articles/9?utm_source=x",
        )
        b = NormalizedItem(
            source_id="newsnow:wallstreetcn-hot",
            title="t",
            url="https://wallstreetcn.com/articles/9",
        )
        assert a.url_hash() == b.url_hash()
