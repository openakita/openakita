"""Red-line tests for FinpulseTaskManager — assert the V1.0 contract.

Every path below has to hold for Phase 2+ to safely hang fetchers /
pipeline / agent-tools off the manager:

* All four tables plus ``assets_bus`` exist after ``init()``.
* ``assets_bus`` stays empty after every CRUD path in V1.0.
* ``update_task_safe`` raises ``ValueError`` on an unknown column.
* ``upsert_article`` deduplicates by ``url_hash`` and merges raw data.
* ``reset_ai_scores`` nulls every ai_score (triggers re-score on next
  interest-file change).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from finpulse_task_manager import DEFAULT_CONFIG, FinpulseTaskManager


@pytest.fixture()
def tm_path(tmp_path: Path) -> Path:
    return tmp_path / "fin_pulse.sqlite"


async def _init(tm_path: Path) -> FinpulseTaskManager:
    tm = FinpulseTaskManager(tm_path)
    await tm.init()
    return tm


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_init_creates_five_tables(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            names = set()
            async with tm._db.execute(  # type: ignore[union-attr]
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) as cur:
                async for row in cur:
                    names.add(row[0])
            assert {"tasks", "articles", "digests", "config", "assets_bus"}.issubset(names)
        finally:
            await tm.close()

    _run(_body())


def test_default_config_seeded(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            cfg = await tm.get_all_config()
            for key in (
                "fetch_timeout_sec",
                "llm_batch_size",
                "dedupe.use_llm",
                "newsnow.mode",
                "schedule.morning.cron",
                "source.wallstreetcn.enabled",
            ):
                assert key in cfg, key
            # NewsNow now ships enabled by default — the plugin pins the
            # public upstream URL and throttles it via
            # newsnow.min_interval_s so the open-source maintainer isn't
            # hammered.
            assert cfg["newsnow.mode"] == "public"
            assert cfg["newsnow.api_url"] == "https://newsnow.busiyi.world/api/s"
            assert cfg["newsnow.min_interval_s"] == "300"
            assert cfg["dedupe.use_llm"] == "false"
            # All defaults were persisted so double-init is idempotent.
            assert cfg == DEFAULT_CONFIG | cfg  # defaults are a subset
        finally:
            await tm.close()

    _run(_body())


def test_default_config_backfills_blank_newsnow_keys(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            await tm.set_configs(
                {
                    "newsnow.mode": "",
                    "newsnow.api_url": "",
                    "newsnow.min_interval_s": "",
                }
            )
        finally:
            await tm.close()

        tm2 = await _init(tm_path)
        try:
            cfg = await tm2.get_all_config()
            assert cfg["newsnow.mode"] == "public"
            assert cfg["newsnow.api_url"] == "https://newsnow.busiyi.world/api/s"
            assert cfg["newsnow.min_interval_s"] == "300"
        finally:
            await tm2.close()

    _run(_body())


def test_update_task_safe_rejects_unknown_column(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            task = await tm.create_task(mode="ingest", params={"sources": "*"})
            with pytest.raises(ValueError, match="not whitelisted"):
                await tm.update_task_safe(task["id"], nonexistent="boom")
            ok = await tm.update_task_safe(
                task["id"], status="running", pipeline_step="fetch"
            )
            assert ok is True
            refreshed = await tm.get_task(task["id"])
            assert refreshed is not None
            assert refreshed["status"] == "running"
            assert refreshed["pipeline_step"] == "fetch"
        finally:
            await tm.close()

    _run(_body())


def test_article_upsert_dedupes_by_url_hash(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            aid1, inserted1 = await tm.upsert_article(
                source_id="wallstreetcn",
                url="https://wallstreetcn.com/articles/100",
                url_hash="h0000000001",
                title="首发",
                fetched_at="2026-04-24T00:00:00Z",
                raw={"rank": 1},
            )
            assert inserted1 is True
            aid2, inserted2 = await tm.upsert_article(
                source_id="wallstreetcn",
                url="https://wallstreetcn.com/articles/100",
                url_hash="h0000000001",
                title="updated",
                fetched_at="2026-04-24T00:10:00Z",
                published_at="2026-04-24T00:05:00Z",
                raw={"rank": 1, "source_extra": "later"},
            )
            assert inserted2 is False
            assert aid2 == aid1
            row = await tm.get_article(aid1)
            assert row is not None
            assert row["title"] == "updated"
            assert row["raw"]["source_extra"] == "later"
        finally:
            await tm.close()

    _run(_body())


def test_article_list_window_falls_back_for_non_iso_published_at(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            await tm.upsert_article(
                source_id="wallstreetcn",
                url="https://wallstreetcn.com/articles/non-iso",
                url_hash="h_non_iso",
                title="刚抓到的非 ISO 时间新闻",
                fetched_at="2026-04-24T12:00:00Z",
                published_at="刚刚",
            )

            rows, total = await tm.list_articles(
                since="2026-04-24T00:00:00Z",
                limit=10,
            )

            assert total == 1
            assert rows[0]["title"] == "刚抓到的非 ISO 时间新闻"
        finally:
            await tm.close()

    _run(_body())


def test_article_list_window_uses_space_separated_published_at(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            await tm.upsert_article(
                source_id="nbs",
                url="https://stats.gov.cn/old-report",
                url_hash="h_old_space_time",
                title="旧发布日期不应出现在近 6 小时",
                fetched_at="2026-04-24T12:00:00Z",
                published_at="2023-12-31 09:30",
            )

            rows, total = await tm.list_articles(
                since="2026-04-24T06:00:00Z",
                limit=10,
            )

            assert total == 0
            assert rows == []
        finally:
            await tm.close()

    _run(_body())


def test_article_list_sort_supports_time_asc(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            await tm.upsert_article(
                source_id="cls",
                url="https://cls.example/old",
                url_hash="h_sort_old",
                title="old",
                fetched_at="2026-04-24T08:00:00Z",
            )
            await tm.upsert_article(
                source_id="cls",
                url="https://cls.example/new",
                url_hash="h_sort_new",
                title="new",
                fetched_at="2026-04-24T10:00:00Z",
            )

            rows, _ = await tm.list_articles(sort="time_asc", limit=10)

            assert [row["title"] for row in rows[:2]] == ["old", "new"]
        finally:
            await tm.close()

    _run(_body())


def test_reset_ai_scores_nulls_every_row(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            for i in range(3):
                await tm.upsert_article(
                    source_id="cls",
                    url=f"https://cls.example/{i}",
                    url_hash=f"h{i}",
                    title=f"t{i}",
                    fetched_at="2026-04-24T00:00:00Z",
                )
            # simulate a prior AI pass
            rows, _ = await tm.list_articles()
            for row in rows:
                await tm.update_article_ai(row["id"], ai_score=5.5)
            changed = await tm.reset_ai_scores()
            assert changed == 3
            rows2, _ = await tm.list_articles()
            for row in rows2:
                assert row["ai_score"] is None
        finally:
            await tm.close()

    _run(_body())


def test_assets_bus_stays_empty_in_v1(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            await tm.create_task(mode="daily_brief", params={})
            await tm.upsert_article(
                source_id="cls",
                url="https://cls.example/1",
                url_hash="h_assets",
                title="x",
                fetched_at="2026-04-24T00:00:00Z",
            )
            await tm.create_digest(
                session="morning",
                generated_at="2026-04-24T09:00:00Z",
                title="Morning",
            )
            assert await tm.count_assets_bus() == 0
            assert await tm.list_assets_bus() == []
        finally:
            await tm.close()

    _run(_body())


def test_digest_roundtrip(tm_path: Path) -> None:
    async def _body() -> None:
        tm = await _init(tm_path)
        try:
            did = await tm.create_digest(
                session="morning",
                generated_at="2026-04-24T09:00:00Z",
                title="早报",
                markdown_blob="# hello",
                html_blob="<h1>hello</h1>",
                push_results={"feishu": "ok"},
                stats={"count": 3},
            )
            digest = await tm.get_digest(did)
            assert digest is not None
            assert digest["title"] == "早报"
            assert digest["push_results"] == {"feishu": "ok"}
            assert digest["stats"] == {"count": 3}
            items, total = await tm.list_digests(session="morning")
            assert total == 1
            assert items[0]["id"] == did
            assert await tm.delete_digest(did) is True
            assert await tm.get_digest(did) is None
            assert await tm.delete_digest(did) is False
        finally:
            await tm.close()

    _run(_body())
