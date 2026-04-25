"""Phase 2b-bis — :mod:`finpulse_fetchers.newsnow_base` helper coverage.

These tests stub the HTTP layer (the same pattern the rest of the suite
uses) and confirm that the shared NewsNow envelope parser honours the
public API contract:

* ``status in {"success", "cache"}`` → rows materialise
* any other status → :class:`ValueError` (so callers can fall back)
* blank titles / URLs → row is dropped
* ``mobileUrl`` serves as a fallback when ``url`` is empty
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import finpulse_fetchers._http as http_mod
from finpulse_fetchers import newsnow_base


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _noop_sleep(_seconds: float) -> None:
    """``asyncio.sleep`` replacement used to keep retry tests snappy."""
    return None


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def _factory(
        *,
        timeout: float = 15.0,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> httpx.AsyncClient:
        headers = {"User-Agent": "test"}
        if extra_headers:
            headers.update(extra_headers)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(http_mod, "make_client", _factory)
    mod = importlib.import_module("finpulse_fetchers.newsnow_base")
    monkeypatch.setattr(mod, "make_client", _factory)


class TestFetchFromNewsNow:
    def test_success_envelope_yields_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "status": "success",
            "items": [
                {
                    "title": "央行开展 5000 亿 MLF 操作",
                    "url": "https://wallstreetcn.com/articles/100",
                    "mobileUrl": "https://wallstreetcn.com/m/100",
                    "desc": "操作利率维持不变",
                },
                {
                    "title": "  ",  # blank → dropped
                    "url": "https://wallstreetcn.com/articles/101",
                },
                {
                    "title": "",  # empty → dropped
                    "url": "https://wallstreetcn.com/articles/102",
                },
                {
                    "title": "没有 url 但有 mobile 的",
                    "url": "",
                    "mobileUrl": "https://wallstreetcn.com/m/103",
                },
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert "id=wallstreetcn-hot" in str(request.url)
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)

        rows = _run(
            newsnow_base.fetch_from_newsnow(
                platform_id="wallstreetcn-hot",
                source_id="wallstreetcn",
                config={"newsnow.mode": "public"},
                timeout_sec=2.0,
            )
        )

        assert len(rows) == 2
        titles = [r.title for r in rows]
        assert "央行开展 5000 亿 MLF 操作" in titles
        assert "没有 url 但有 mobile 的" in titles
        first = rows[0]
        assert first.source_id == "wallstreetcn"
        assert first.extra.get("via") == "newsnow"
        assert first.extra.get("platform") == "wallstreetcn-hot"
        assert first.extra.get("rank") == 1

    def test_off_mode_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={"status": "success", "items": []})

        _patch_transport(monkeypatch, handler)

        rows = _run(
            newsnow_base.fetch_from_newsnow(
                platform_id="wallstreetcn-hot",
                source_id="wallstreetcn",
                config={"newsnow.mode": "off"},
                timeout_sec=2.0,
            )
        )
        assert rows == []
        assert called["n"] == 0, "off mode must not hit the network"

    def test_error_status_raises_so_caller_can_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "forbidden", "items": []})

        _patch_transport(monkeypatch, handler)

        with pytest.raises(ValueError):
            _run(
                newsnow_base.fetch_from_newsnow(
                    platform_id="cls-hot",
                    source_id="cls",
                    config={"newsnow.mode": "public"},
                    timeout_sec=2.0,
                )
            )

    def test_cache_status_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "status": "cache",
            "items": [
                {"title": "缓存也要接受", "url": "https://x.com/a"},
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        _patch_transport(monkeypatch, handler)
        rows = _run(
            newsnow_base.fetch_from_newsnow(
                platform_id="eastmoney",
                source_id="eastmoney",
                config={"newsnow.mode": "public"},
                timeout_sec=2.0,
            )
        )
        assert len(rows) == 1
        assert rows[0].title == "缓存也要接受"

    def test_cloudflare_challenge_raises_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 403 with a Cloudflare HTML body is the smoking gun that
        re-triggered the rework (the volunteer aggregator flags our old
        ``OpenAkita fin-pulse/1.0`` UA). Make sure we surface it as a
        distinct, classifiable error instead of swallowing it as empty.
        """
        body = "<html>Attention Required! | Cloudflare… bot-check challenge…</html>"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text=body)

        _patch_transport(monkeypatch, handler)

        with pytest.raises(newsnow_base.NewsNowTransportError) as exc_info:
            _run(
                newsnow_base.fetch_from_newsnow(
                    platform_id="wallstreetcn-hot",
                    source_id="wallstreetcn",
                    config={"newsnow.mode": "public"},
                    timeout_sec=2.0,
                )
            )
        assert exc_info.value.kind == "cloudflare_blocked"

    def test_invalid_source_id_short_circuits_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Invalid source id`` never recovers on retry — assert we only
        hit the network once and surface the hint for eastmoney-like
        platforms that never existed on the aggregator.
        """
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                500, json={"error": True, "message": "Invalid source id"}
            )

        _patch_transport(monkeypatch, handler)

        with pytest.raises(newsnow_base.NewsNowTransportError) as exc_info:
            _run(
                newsnow_base.fetch_from_newsnow(
                    platform_id="eastmoney",
                    source_id="eastmoney",
                    config={"newsnow.mode": "public"},
                    timeout_sec=2.0,
                )
            )
        assert exc_info.value.kind == "invalid_source_id"
        assert calls["n"] == 1, "invalid_source_id is permanent — no retry"

    def test_transient_5xx_retries_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cold-start 502/504 on the volunteer-run aggregator should be
        absorbed by a single transparent retry before we escalate to
        the fallback path. Keeps the drawer noise-free for blips.
        """
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "items": [{"title": "ok", "url": "https://x.com/a"}],
                },
            )

        _patch_transport(monkeypatch, handler)
        # Skip the 1.5s sleep in the retry so the test stays snappy.
        monkeypatch.setattr(newsnow_base.asyncio, "sleep", _noop_sleep)

        rows = _run(
            newsnow_base.fetch_from_newsnow(
                platform_id="cls-hot",
                source_id="cls",
                config={"newsnow.mode": "public"},
                timeout_sec=2.0,
            )
        )
        assert len(rows) == 1
        assert calls["n"] == 2

    def test_ms_timestamp_is_converted_to_iso(self) -> None:
        parsed = newsnow_base._parse_envelope(
            {
                "status": "success",
                "items": [
                    {
                        "title": "t",
                        "url": "https://x.com/a",
                        "pubDate": 1_714_000_000_000,
                    }
                ],
            },
            platform_id="p",
            source_id="s",
        )
        assert len(parsed) == 1
        assert parsed[0].published_at and "T" in parsed[0].published_at

    def test_non_sortable_time_falls_back_to_fetched_at(self) -> None:
        parsed = newsnow_base._parse_envelope(
            {
                "status": "success",
                "items": [
                    {
                        "title": "relative time",
                        "url": "https://x.com/relative",
                        "time": "刚刚",
                    }
                ],
            },
            platform_id="p",
            source_id="s",
        )
        assert len(parsed) == 1
        assert parsed[0].published_at is None

    def test_space_separated_timestamp_is_normalized(self) -> None:
        parsed = newsnow_base._parse_envelope(
            {
                "status": "success",
                "items": [
                    {
                        "title": "space time",
                        "url": "https://x.com/space",
                        "time": "2026-04-25 15:30",
                    }
                ],
            },
            platform_id="p",
            source_id="s",
        )
        assert len(parsed) == 1
        assert parsed[0].published_at == "2026-04-25T15:30:00"
