"""Phase 4b tests — hot_radar pipeline + dispatch wiring.

Focuses on the integration between :func:`evaluate_radar`,
:func:`run_hot_radar`, and :class:`DispatchService`. We seed a tiny
SQLite via :class:`FinpulseTaskManager`, populate a handful of
articles, compile radar rules, and assert that:

* Matched titles end up in ``hits`` with ``matched_terms`` populated.
* ``run_hot_radar`` dispatches through the stubbed api exactly once.
* Cooldowns suppress a follow-up firing with the same hits set.
* Empty hits skip dispatch entirely.
* Task status is flipped to ``succeeded`` with the result JSON.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from finpulse_dispatch import DispatchService
from finpulse_pipeline import evaluate_radar, run_hot_radar
from finpulse_task_manager import FinpulseTaskManager


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class _StubAPI:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send_message(self, *, channel: str, chat_id: str, text: str) -> None:
        self.sent.append((channel, chat_id, text))


def _run(coro):
    return asyncio.run(coro)


async def _seed(tm: FinpulseTaskManager) -> list[str]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    titles = [
        ("wallstreetcn", "https://example.com/a1", "美联储加息 25 基点，美股小幅下挫"),
        ("xueqiu", "https://example.com/a2", "马斯克再度减持特斯拉股份"),
        ("cls", "https://example.com/a3", "欧央行维持利率不变，后续路径不明"),
        ("eastmoney", "https://example.com/a4", "财报季：苹果 Q2 营收超预期"),
        ("wallstreetcn", "https://example.com/a5", "广告：理财秘籍课程限时折扣"),
    ]
    ids: list[str] = []
    for src, url, title in titles:
        aid, _new = await tm.upsert_article(
            source_id=src,
            url=url,
            url_hash=_url_hash(url),
            title=title,
            fetched_at=now,
            summary="",
            content="",
            published_at=now,
            raw={},
        )
        ids.append(aid)
    return ids


def _make_tm(tmp_path: Path) -> FinpulseTaskManager:
    return FinpulseTaskManager(tmp_path / "fp.db")


# ── evaluate_radar ──────────────────────────────────────────────────


def test_evaluate_radar_filters_by_rules(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        try:
            await _seed(tm)
            rules = "+美联储\n!广告\n"
            result = await evaluate_radar(tm, rules_text=rules, since_hours=72)
            assert result["ok"] is True
            titles = [h["title"] for h in result["hits"]]
            assert any("美联储" in t for t in titles)
            assert not any("广告" in t for t in titles)
            assert result["meta"]["matched"] == len(result["hits"])
        finally:
            await tm.close()

    _run(_body())


def test_evaluate_radar_empty_rules_matches_all(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        try:
            await _seed(tm)
            result = await evaluate_radar(tm, rules_text="", since_hours=72)
            assert result["ok"] is True
            assert result["meta"]["matched"] == 5
        finally:
            await tm.close()

    _run(_body())


def test_evaluate_radar_respects_block_tokens(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        try:
            await _seed(tm)
            result = await evaluate_radar(
                tm,
                rules_text="美联储\n欧央行\n\n[GLOBAL_FILTER]\n广告\n",
                since_hours=72,
            )
            assert result["ok"] is True
            titles = [h["title"] for h in result["hits"]]
            assert not any("广告" in t for t in titles)
            assert any("美联储" in t for t in titles)
            assert any("欧央行" in t for t in titles)
        finally:
            await tm.close()

    _run(_body())


# ── run_hot_radar ───────────────────────────────────────────────────


def test_run_hot_radar_dispatches_on_hits(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        api = _StubAPI()
        dispatch = DispatchService(api, inter_chunk_delay=0.0)
        try:
            await _seed(tm)
            task = await tm.create_task(mode="hot_radar", params={}, status="running")
            result = await run_hot_radar(
                tm,
                dispatch,
                rules_text="+美联储\n",
                targets=[{"channel": "feishu", "chat_id": "u1"}],
                since_hours=72,
                task_id=task["id"],
            )
            assert result["ok"] is True
            assert len(result["hits"]) == 1
            assert len(api.sent) == 1
            assert api.sent[0][0] == "feishu"
            assert "美联储" in api.sent[0][2]
            row = await tm.get_task(task["id"])
            assert row["status"] == "succeeded"
            assert row["result"]["meta"]["matched"] == 1
        finally:
            await tm.close()

    _run(_body())


def test_run_hot_radar_cooldown_suppresses_repeat(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        api = _StubAPI()
        dispatch = DispatchService(api, inter_chunk_delay=0.0)
        try:
            await _seed(tm)
            first = await run_hot_radar(
                tm,
                dispatch,
                rules_text="+美联储\n",
                targets=[{"channel": "feishu", "chat_id": "u1"}],
                since_hours=72,
                cooldown_s=600.0,
            )
            assert first["dispatched"][0]["sent_chunks"] == 1

            second = await run_hot_radar(
                tm,
                dispatch,
                rules_text="+美联储\n",
                targets=[{"channel": "feishu", "chat_id": "u1"}],
                since_hours=72,
                cooldown_s=600.0,
            )
            assert second["dispatched"][0]["skipped"] == "cooldown"
            assert len(api.sent) == 1
        finally:
            await tm.close()

    _run(_body())


def test_run_hot_radar_no_hits_skips_dispatch(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        api = _StubAPI()
        dispatch = DispatchService(api, inter_chunk_delay=0.0)
        try:
            await _seed(tm)
            result = await run_hot_radar(
                tm,
                dispatch,
                rules_text="+关键词绝对不出现\n",
                targets=[{"channel": "feishu", "chat_id": "u1"}],
                since_hours=72,
            )
            assert result["ok"] is True
            assert result["hits"] == []
            assert result["dispatched"] == []
            assert api.sent == []
        finally:
            await tm.close()

    _run(_body())


def test_run_hot_radar_fan_out_to_multiple_targets(tmp_path: Path) -> None:
    async def _body() -> None:
        tm = _make_tm(tmp_path)
        await tm.init()
        api = _StubAPI()
        dispatch = DispatchService(api, inter_chunk_delay=0.0)
        try:
            await _seed(tm)
            result = await run_hot_radar(
                tm,
                dispatch,
                rules_text="+美联储\n",
                targets=[
                    {"channel": "feishu", "chat_id": "u1"},
                    {"channel": "dingtalk", "chat_id": "u2"},
                ],
                since_hours=72,
            )
            channels = [d["channel"] for d in result["dispatched"]]
            assert channels == ["feishu", "dingtalk"]
            assert len(api.sent) == 2
        finally:
            await tm.close()

    _run(_body())
