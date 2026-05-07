"""Unified NewsNow fetcher coverage — verifies the NewsNowFetcher iterates
all ``kind=newsnow`` sources from SOURCE_DEFS and correctly handles
per-channel errors without blocking other channels.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from finpulse_fetchers import newsnow_base
from finpulse_fetchers.base import NormalizedItem
from finpulse_fetchers.newsnow import NewsNowFetcher


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_items(source_id: str, n: int = 2) -> list[NormalizedItem]:
    return [
        NormalizedItem(
            source_id=source_id,
            title=f"{source_id} item {i}",
            url=f"https://example.com/{source_id}/{i}",
        )
        for i in range(n)
    ]


class TestNewsNowUnified:
    def test_iterates_enabled_newsnow_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_ids: list[str] = []

        async def fake_fetch(**kwargs: Any) -> list[NormalizedItem]:
            pid = kwargs["platform_id"]
            sid = kwargs["source_id"]
            called_ids.append(pid)
            return _make_items(sid, 1)

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(newsnow_base, "fetch_from_newsnow", fake_fetch)
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", fake_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {"newsnow.mode": "public", "newsnow.api_url": "https://x.example/api/s"}
        items = _run(NewsNowFetcher(config=cfg).fetch())

        assert len(items) >= 1
        assert "wallstreetcn" in called_ids
        assert "cls" in called_ids
        assert "xueqiu" in called_ids
        assert "xueqiu-hotstock" in called_ids

    def test_can_limit_to_one_newsnow_subsource(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_ids: list[str] = []

        async def fake_fetch(**kwargs: Any) -> list[NormalizedItem]:
            called_ids.append(kwargs["platform_id"])
            return _make_items(kwargs["source_id"], 1)

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(newsnow_base, "fetch_from_newsnow", fake_fetch)
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", fake_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://x.example/api/s",
            "_newsnow.only_sources": "xueqiu",
        }
        items = _run(NewsNowFetcher(config=cfg).fetch())

        assert called_ids == ["xueqiu"]
        assert {item.source_id for item in items} == {"xueqiu"}

    def test_one_channel_failure_does_not_block_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        async def flaky_fetch(**kwargs: Any) -> list[NormalizedItem]:
            nonlocal call_count
            call_count += 1
            pid = kwargs["platform_id"]
            if pid == "wallstreetcn":
                raise newsnow_base.NewsNowTransportError("http_500", "test error")
            return _make_items(kwargs["source_id"], 1)

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(newsnow_base, "fetch_from_newsnow", flaky_fetch)
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", flaky_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {"newsnow.mode": "public", "newsnow.api_url": "https://x.example/api/s"}
        items = _run(NewsNowFetcher(config=cfg).fetch())

        assert call_count >= 2
        assert len(items) >= 1
        assert all(i.source_id != "wallstreetcn" for i in items)

    def test_fanout_timeout_returns_partial_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow NewsNow channel must not discard completed channels.

        This pins the reliability fix for "拉取全部": the public
        aggregator can stall on one platform, but the drawer should still
        show the sources that completed plus per-channel timeout rows.
        """

        async def slow_fetch(**kwargs: Any) -> list[NormalizedItem]:
            sid = kwargs["source_id"]
            if sid == "cls":
                await asyncio.sleep(4)
            return _make_items(sid, 1)

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(newsnow_base, "fetch_from_newsnow", slow_fetch)
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", slow_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://x.example/api/s",
            "_newsnow.only_sources": "wallstreetcn,cls",
            "newsnow.channel_concurrency": "2",
            "newsnow.total_budget_sec": "3",
        }
        fetcher = NewsNowFetcher(config=cfg)
        items = _run(fetcher.fetch())

        assert {item.source_id for item in items} == {"wallstreetcn"}
        reports = {row["source_id"]: row for row in fetcher._channel_reports}
        assert reports["wallstreetcn"]["count"] == 1
        assert reports["wallstreetcn"]["error"] is None
        assert reports["cls"]["count"] == 0
        assert reports["cls"]["error"] == "timeout"

    def test_successful_empty_channel_carries_empty_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def empty_fetch(**kwargs: Any) -> list[NormalizedItem]:
            return []

        import finpulse_fetchers.newsnow as nn_mod
        monkeypatch.setattr(newsnow_base, "fetch_from_newsnow", empty_fetch)
        monkeypatch.setattr(nn_mod, "fetch_from_newsnow", empty_fetch)
        monkeypatch.setattr(nn_mod, "jittered_sleep", lambda *a, **k: asyncio.sleep(0))

        cfg = {
            "newsnow.mode": "public",
            "newsnow.api_url": "https://x.example/api/s",
            "_newsnow.only_sources": "fastbull",
        }
        fetcher = NewsNowFetcher(config=cfg)
        items = _run(fetcher.fetch())

        assert items == []
        assert fetcher._channel_reports == [
            {
                "source_id": "fastbull",
                "count": 0,
                "error": None,
                "empty_reason": "newsnow:empty_payload",
            }
        ]

    def test_off_mode_returns_empty(self) -> None:
        cfg = {"newsnow.mode": "off"}
        items = _run(NewsNowFetcher(config=cfg).fetch())
        assert items == []
