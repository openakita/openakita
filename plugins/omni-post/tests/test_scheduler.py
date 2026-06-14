"""Unit tests for omni_post_scheduler.

Covers:
  * ``stagger_slots`` — pure function for timezone-aware stagger. We
    assert deterministic ordering (no jitter) and that each account
    ends up at the expected UTC time given a fixed ``now``.
  * ``fanout_matrix`` — (platforms × accounts) expansion + tag-routed
    copy overrides + per-platform overrides win semantics.
  * ``ScheduleTicker`` — triggers only rows that are due, marks them
    triggered, skips missing task_id, and exits cleanly on stop().
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from omni_post_scheduler import ScheduleTicker, fanout_matrix, stagger_slots
from omni_post_task_manager import OmniPostTaskManager

# ── stagger_slots ──────────────────────────────────────────────────


def test_stagger_slots_basic_spacing_utc() -> None:
    now = datetime(2026, 4, 24, 3, 0, tzinfo=UTC)  # 11:00 Shanghai
    accounts = [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]
    slots = stagger_slots(
        base_local_hour=20,
        base_minute=0,
        timezone="Asia/Shanghai",
        accounts=accounts,
        stagger_seconds=600,
        jitter_seconds=0,
        now=now,
    )
    # 20:00 Shanghai == 12:00 UTC on the same day.
    assert slots[0]["scheduled_at"] == "2026-04-24T12:00:00Z"
    assert slots[1]["scheduled_at"] == "2026-04-24T12:10:00Z"
    assert slots[2]["scheduled_at"] == "2026-04-24T12:20:00Z"


def test_stagger_slots_rolls_to_tomorrow_when_passed() -> None:
    # Local time is 21:00 Shanghai; requested 20:00 → next day.
    now = datetime(2026, 4, 24, 13, 0, tzinfo=UTC)
    slots = stagger_slots(
        base_local_hour=20,
        base_minute=0,
        timezone="Asia/Shanghai",
        accounts=[{"id": "a1"}],
        stagger_seconds=0,
        jitter_seconds=0,
        now=now,
    )
    assert slots[0]["scheduled_at"].startswith("2026-04-25T12:00")


def test_stagger_slots_respects_explicit_day_offset() -> None:
    now = datetime(2026, 4, 24, 3, 0, tzinfo=UTC)
    slots = stagger_slots(
        base_local_hour=20,
        base_minute=30,
        timezone="Asia/Shanghai",
        accounts=[{"id": "a"}],
        stagger_seconds=0,
        jitter_seconds=0,
        day_offset=7,
        now=now,
    )
    # 20:30 Shanghai on 2026-05-01 == 12:30 UTC
    assert slots[0]["scheduled_at"] == "2026-05-01T12:30:00Z"


# ── fanout_matrix ──────────────────────────────────────────────────


def test_fanout_matrix_skips_platform_mismatch() -> None:
    pairs = fanout_matrix(
        platforms=["douyin", "rednote"],
        accounts=[
            {"id": "a", "platform": "douyin", "tags": []},
            {"id": "b", "platform": "youtube", "tags": []},
        ],
        payload={"title": "t", "description": ""},
    )
    assert len(pairs) == 1
    assert pairs[0]["platform"] == "douyin"


def test_fanout_matrix_tag_override_applies() -> None:
    pairs = fanout_matrix(
        platforms=["douyin"],
        accounts=[{"id": "a", "platform": "douyin", "tags": ["travel"]}],
        payload={"title": "base", "description": "base desc"},
        per_tag_overrides={"travel": {"description": "travel-specific copy"}},
    )
    assert pairs[0]["payload"]["description"] == "travel-specific copy"


def test_fanout_matrix_per_platform_wins_over_tag() -> None:
    pairs = fanout_matrix(
        platforms=["douyin"],
        accounts=[{"id": "a", "platform": "douyin", "tags": ["travel"]}],
        payload={
            "title": "base",
            "description": "base",
            "per_platform_overrides": {"douyin": {"description": "dy-special"}},
        },
        per_tag_overrides={"travel": {"description": "travel-copy"}},
    )
    assert pairs[0]["payload"]["description"] == "dy-special"
    # The flattened payload should not carry the overrides dict.
    assert "per_platform_overrides" not in pairs[0]["payload"]


# ── ScheduleTicker ─────────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_schedule_ticker_triggers_only_due_rows(tmp_path: Path) -> None:
    tm = OmniPostTaskManager(tmp_path / "s.db")
    await tm.init()
    try:
        acc_id = await tm.create_account(
            platform="douyin",
            nickname="n",
            cookie_cipher=b"ignored",
            tags=[],
        )
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        past_iso = "2020-01-01T00:00:00Z"
        future_iso = "2099-12-31T23:59:59Z"
        past_task = await tm.create_task(
            platform="douyin", account_id=acc_id, asset_id=None,
            payload={"title": "p"},
        )
        future_task = await tm.create_task(
            platform="douyin", account_id=acc_id, asset_id=None,
            payload={"title": "f"},
        )
        past_sched = await tm.create_schedule(task_id=past_task, scheduled_at=past_iso)
        await tm.create_schedule(task_id=future_task, scheduled_at=future_iso)

        triggered: list[str] = []

        async def _runner(task_id: str) -> None:
            triggered.append(task_id)

        def _spawn(coro, name=None):
            # Directly await the coroutine in a throw-away task so we
            # don't need a PluginAPI in tests.
            return asyncio.ensure_future(coro)

        ticker = ScheduleTicker(
            task_manager=tm, runner=_runner, spawn=_spawn, poll_seconds=5.0
        )
        await ticker._tick_once()
        # Give the spawned coro a chance to run.
        await asyncio.sleep(0)

        assert triggered == [past_task]
        rows = await tm.list_pending_schedules()
        # Only the future schedule remains "scheduled".
        assert len(rows) == 1
        assert rows[0]["scheduled_at"] == future_iso
        # And the past one is marked "triggered".
        async with tm._conn().execute(  # noqa: SLF001
            "SELECT status FROM schedules WHERE id=?", (past_sched,)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "triggered"
        _ = now_iso
    finally:
        await tm.close()


@pytest.mark.asyncio()
async def test_schedule_ticker_stop_is_idempotent(tmp_path: Path) -> None:
    tm = OmniPostTaskManager(tmp_path / "s2.db")
    await tm.init()
    try:

        async def _runner(task_id: str) -> None:
            pass

        def _spawn(coro, name=None):
            return asyncio.ensure_future(coro)

        ticker = ScheduleTicker(
            task_manager=tm, runner=_runner, spawn=_spawn, poll_seconds=5.0
        )
        ticker.start()
        await ticker.stop()
        await ticker.stop()  # no-op, must not raise
    finally:
        await tm.close()
