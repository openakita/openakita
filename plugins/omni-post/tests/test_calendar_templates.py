"""Tests for the Tab-4 calendar queries and Tab-5 template library.

We drive the real SQLite-backed task manager so we catch schema drift
and index problems. These are cheap — pytest boots a new temp DB per
test via the shared ``tm`` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from omni_post_task_manager import OmniPostTaskManager


@pytest.fixture()
async def tm(tmp_path: Path) -> OmniPostTaskManager:
    mgr = OmniPostTaskManager(tmp_path / "cal.db")
    await mgr.init()
    try:
        yield mgr
    finally:
        await mgr.close()


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


async def _mk_scheduled(
    tm: OmniPostTaskManager,
    *,
    trace: str,
    scheduled_at: str,
    platform: str = "douyin",
) -> str:
    tid = await tm.create_task(
        platform=platform,
        account_id=f"acc-{trace}",
        asset_id=None,
        payload={"title": trace},
        client_trace_id=trace,
    )
    await tm.update_task_safe(tid, {"scheduled_at": scheduled_at})
    return tid


@pytest.mark.asyncio()
async def test_calendar_returns_tasks_inside_window(tm: OmniPostTaskManager) -> None:
    t1 = await _mk_scheduled(tm, trace="a", scheduled_at="2026-05-01T08:00:00+00:00")
    t2 = await _mk_scheduled(tm, trace="b", scheduled_at="2026-05-01T21:00:00+00:00")
    await _mk_scheduled(tm, trace="c", scheduled_at="2026-05-03T08:00:00+00:00")

    items = await tm.list_scheduled_tasks_in_range(
        from_iso="2026-05-01T00:00:00+00:00",
        to_iso="2026-05-01T23:59:59+00:00",
    )
    ids = {r["id"] for r in items}
    assert ids == {t1, t2}
    assert items == sorted(items, key=lambda r: r["scheduled_at"])  # ordered


@pytest.mark.asyncio()
async def test_calendar_filters_by_platform(tm: OmniPostTaskManager) -> None:
    await _mk_scheduled(tm, trace="a", scheduled_at="2026-05-01T08:00:00+00:00", platform="douyin")
    await _mk_scheduled(tm, trace="b", scheduled_at="2026-05-01T09:00:00+00:00", platform="rednote")
    items = await tm.list_scheduled_tasks_in_range(
        from_iso="2026-05-01T00:00:00+00:00",
        to_iso="2026-05-01T23:59:59+00:00",
        platform="rednote",
    )
    assert [r["platform"] for r in items] == ["rednote"]


@pytest.mark.asyncio()
async def test_reschedule_pending_task(tm: OmniPostTaskManager) -> None:
    tid = await _mk_scheduled(
        tm, trace="r", scheduled_at="2026-05-01T08:00:00+00:00"
    )
    ok = await tm.reschedule_task(
        task_id=tid,
        new_scheduled_at="2026-05-02T10:00:00+00:00",
    )
    assert ok is True
    row = await tm.get_task(tid)
    assert row["scheduled_at"] == "2026-05-02T10:00:00+00:00"


@pytest.mark.asyncio()
async def test_reschedule_refuses_running_task(tm: OmniPostTaskManager) -> None:
    tid = await _mk_scheduled(
        tm, trace="run", scheduled_at="2026-05-01T08:00:00+00:00"
    )
    await tm.update_task_safe(tid, {"status": "running"})
    ok = await tm.reschedule_task(
        task_id=tid,
        new_scheduled_at="2026-05-02T10:00:00+00:00",
    )
    assert ok is False
    row = await tm.get_task(tid)
    assert row["scheduled_at"] == "2026-05-01T08:00:00+00:00"


# ---------------------------------------------------------------------------
# Template library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_template_crud_roundtrip(tm: OmniPostTaskManager) -> None:
    tid = await tm.create_template(
        name="daily-hook",
        kind="caption",
        body={"text": "Hi {{topic}}!", "per_platform": {"douyin": {"text": "抖音 Hi"}}},
        tags=["daily"],
    )
    [row] = await tm.list_templates()
    assert row["id"] == tid
    assert row["body"]["text"].startswith("Hi")
    assert row["tags"] == ["daily"]

    ok = await tm.update_template(tid, name="daily-hook-v2", tags=["daily", "hot"])
    assert ok
    [row2] = await tm.list_templates()
    assert row2["name"] == "daily-hook-v2"
    assert sorted(row2["tags"]) == ["daily", "hot"]

    assert await tm.delete_template(tid) is True
    assert await tm.list_templates() == []


@pytest.mark.asyncio()
async def test_template_kind_filter_and_validation(tm: OmniPostTaskManager) -> None:
    await tm.create_template(name="c1", kind="caption", body={"text": "a"})
    await tm.create_template(name="t1", kind="topic", body={"hashtags": ["#a"]})

    captions = await tm.list_templates(kind="caption")
    assert [t["name"] for t in captions] == ["c1"]

    with pytest.raises(ValueError):
        await tm.create_template(name="bad", kind="not-a-real-kind")
    with pytest.raises(ValueError):
        await tm.create_template(name="   ", kind="caption")


@pytest.mark.asyncio()
async def test_template_update_empty_payload_is_noop(tm: OmniPostTaskManager) -> None:
    tid = await tm.create_template(name="x", kind="caption")
    # No fields changed — should return False cleanly, not crash.
    assert await tm.update_template(tid) is False
