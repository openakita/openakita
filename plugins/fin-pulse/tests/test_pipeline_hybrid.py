"""Pipeline-level coverage for the refactored source dispatch.

Tests pin three user-visible behaviours:

1. Each ``summary.by_source[id]`` carries a ``via`` field.
2. ``summary.totals`` exposes ``sources_total`` and ``sources_ok``.
3. The ``no_sources_enabled`` early return flips the task to ``"skipped"``.
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

import finpulse_pipeline as pipeline_mod
from finpulse_fetchers import SOURCE_REGISTRY
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_pipeline import ingest
from finpulse_task_manager import FinpulseTaskManager


def _disable_all_sources() -> dict[str, str]:
    from finpulse_models import SOURCE_DEFS

    cfg: dict[str, str] = {}
    for sid in SOURCE_DEFS:
        cfg[f"source.{sid}.enabled"] = "false"
    for sid in SOURCE_REGISTRY:
        cfg[f"source.{sid}.enabled"] = "false"
    return cfg


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _DirectStub(BaseFetcher):
    def __init__(
        self,
        *,
        source_id: str,
        items: list[NormalizedItem],
        via: str = "direct",
    ) -> None:
        super().__init__(config={})
        self.source_id = source_id  # type: ignore[assignment]
        self._items = items
        self._last_via = via

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        return list(self._items)


class TestIngestVia:
    def test_summary_by_source_contains_via(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.eastmoney.enabled"] = "true"
        overrides["source.yicai.enabled"] = "true"
        _run(tm.set_configs(overrides))

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            if source_id == "eastmoney":
                return _DirectStub(
                    source_id="eastmoney",
                    items=[
                        NormalizedItem(
                            source_id="eastmoney",
                            title="EastMoney News",
                            url="https://eastmoney.com/a/1",
                        )
                    ],
                    via="direct",
                )
            if source_id == "yicai":
                return _DirectStub(
                    source_id="yicai",
                    items=[
                        NormalizedItem(
                            source_id="yicai",
                            title="Yicai News",
                            url="https://yicai.com/n/1",
                        )
                    ],
                    via="direct",
                )
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, since_hours=24))
        assert summary["ok"] is True
        by_source = summary["by_source"]
        assert by_source["eastmoney"]["via"] == "direct"
        assert by_source["yicai"]["via"] == "direct"

        totals = summary["totals"]
        assert totals["sources_total"] >= 2
        assert totals["sources_ok"] >= 2

    def test_explicit_newsnow_source_runs_when_channels_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.wallstreetcn.enabled"] = "true"
        overrides["newsnow.mode"] = "public"
        overrides["newsnow.min_interval_s"] = "0"
        _run(tm.set_configs(overrides))

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            if source_id == "newsnow":
                return _DirectStub(
                    source_id="newsnow",
                    items=[
                        NormalizedItem(
                            source_id="wallstreetcn",
                            title="NewsNow headline",
                            url="https://wallstreetcn.com/a/newsnow",
                        )
                    ],
                    via="newsnow",
                )
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, sources=["newsnow"], since_hours=24))

        assert summary["ok"] is True
        assert summary["totals"]["fetched"] == 1
        assert summary["by_source"]["newsnow"]["via"] == "newsnow"

    def test_newsnow_channel_reports_expand_summary_by_subsource(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.xueqiu.enabled"] = "true"
        overrides["source.xueqiu-hotstock.enabled"] = "true"
        overrides["newsnow.mode"] = "public"
        overrides["newsnow.min_interval_s"] = "0"
        _run(tm.set_configs(overrides))

        class _NewsNowStub(_DirectStub):
            async def fetch(self, **_: Any) -> list[NormalizedItem]:
                self._channel_reports = [
                    {"source_id": "xueqiu", "count": 1, "error": None},
                    {"source_id": "xueqiu-hotstock", "count": 1, "error": None},
                ]
                return [
                    NormalizedItem(
                        source_id="xueqiu",
                        title="Xueqiu News",
                        url="https://xueqiu.com/a/news",
                    ),
                    NormalizedItem(
                        source_id="xueqiu-hotstock",
                        title="Xueqiu Hot Stock",
                        url="https://xueqiu.com/a/hot",
                    ),
                ]

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            if source_id == "newsnow":
                return _NewsNowStub(source_id="newsnow", items=[], via="newsnow")
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, sources=["newsnow"], since_hours=24))

        assert summary["ok"] is True
        assert "newsnow" not in summary["by_source"]
        assert summary["by_source"]["xueqiu"]["fetched"] == 1
        assert summary["by_source"]["xueqiu-hotstock"]["fetched"] == 1
        assert summary["totals"]["sources_total"] == 2
        assert summary["totals"]["sources_ok"] == 2

    def test_newsnow_empty_channel_reason_reaches_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.fastbull.enabled"] = "true"
        overrides["newsnow.mode"] = "public"
        overrides["newsnow.min_interval_s"] = "0"
        _run(tm.set_configs(overrides))

        class _NewsNowEmptyStub(_DirectStub):
            async def fetch(self, **_: Any) -> list[NormalizedItem]:
                self._channel_reports = [
                    {
                        "source_id": "fastbull",
                        "count": 0,
                        "error": None,
                        "empty_reason": "newsnow:empty_payload",
                    },
                ]
                return []

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            if source_id == "newsnow":
                return _NewsNowEmptyStub(source_id="newsnow", items=[], via="newsnow")
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, sources=["newsnow"], since_hours=24))

        assert summary["by_source"]["fastbull"]["fetched"] == 0
        assert (
            summary["by_source"]["fastbull"]["via_reason"]
            == "newsnow:empty_payload"
        )

    def test_explicit_direct_source_does_not_pull_newsnow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        overrides = _disable_all_sources()
        overrides["source.eastmoney.enabled"] = "true"
        overrides["source.wallstreetcn.enabled"] = "true"
        overrides["newsnow.mode"] = "public"
        overrides["newsnow.min_interval_s"] = "0"
        _run(tm.set_configs(overrides))

        called: list[str] = []

        def fake_get_fetcher(
            source_id: str, *, config: dict[str, str] | None = None
        ) -> Any:
            called.append(source_id)
            if source_id == "eastmoney":
                return _DirectStub(
                    source_id="eastmoney",
                    items=[
                        NormalizedItem(
                            source_id="eastmoney",
                            title="EastMoney only",
                            url="https://eastmoney.com/a/only",
                        )
                    ],
                    via="direct",
                )
            if source_id == "newsnow":
                raise AssertionError("explicit direct ingest must not fetch newsnow")
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, sources=["eastmoney"], since_hours=24))

        assert summary["ok"] is True
        assert summary["totals"]["fetched"] == 1
        assert "newsnow" not in called
        assert "newsnow" not in summary["by_source"]


class TestIngestNoSourcesEnabled:
    def test_returns_skipped_and_flips_task_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tm = FinpulseTaskManager(tmp_path / "pipe.db")
        _run(tm.init())
        _run(tm.set_configs(_disable_all_sources()))

        task = _run(tm.create_task(mode="ingest", params={"since_hours": 24}, status="running"))

        summary = _run(ingest(tm, since_hours=24, task_id=task["id"]))
        assert summary["ok"] is False
        assert summary["reason"] == "no_sources_enabled"
        assert summary["totals"]["sources_total"] == 0

        reloaded = _run(tm.get_task(task["id"]))
        assert reloaded is not None
        assert reloaded.get("status") == "skipped"
