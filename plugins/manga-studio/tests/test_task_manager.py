"""Phase 1 — MangaTaskManager CRUD across 4 tables + whitelist guards."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from manga_task_manager import MangaTaskManager


@pytest.fixture
async def tm(tmp_path: Path) -> MangaTaskManager:
    mgr = MangaTaskManager(tmp_path / "manga.db")
    await mgr.init()
    yield mgr
    await mgr.close()


# ─── Characters ───────────────────────────────────────────────────────────


async def test_create_character_returns_unique_id_and_persists(tm) -> None:
    a = await tm.create_character(
        name="Aoi Takahashi",
        role_type="main",
        gender="female",
        appearance={"hair": "silver short", "outfit": "cardigan"},
        description="forensic linguist",
    )
    b = await tm.create_character(name="Hiroshi", role_type="support")
    assert a != b
    row = await tm.get_character(a)
    assert row is not None
    assert row["name"] == "Aoi Takahashi"
    assert row["role_type"] == "main"
    assert row["appearance"] == {"hair": "silver short", "outfit": "cardigan"}


async def test_create_character_rejects_invalid_role(tm) -> None:
    with pytest.raises(ValueError, match="invalid role_type"):
        await tm.create_character(name="x", role_type="bogus")


async def test_list_characters_filter_by_role(tm) -> None:
    main_id = await tm.create_character(name="A", role_type="main")
    await tm.create_character(name="B", role_type="support")
    narrator_id = await tm.create_character(name="N", role_type="narrator")
    mains = await tm.list_characters(role_type="main")
    narrators = await tm.list_characters(role_type="narrator")
    assert {r["id"] for r in mains} == {main_id}
    assert {r["id"] for r in narrators} == {narrator_id}


async def test_update_character_safe_rejects_non_writable(tm) -> None:
    cid = await tm.create_character(name="A")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_character_safe(cid, id="hijack")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_character_safe(cid, created_at=0.0)


async def test_update_character_safe_validates_role(tm) -> None:
    cid = await tm.create_character(name="A")
    with pytest.raises(ValueError, match="invalid role_type"):
        await tm.update_character_safe(cid, role_type="bogus")


async def test_update_character_auto_encodes_json(tm) -> None:
    cid = await tm.create_character(name="A")
    await tm.update_character_safe(
        cid,
        ref_images_json=[{"oss_url": "https://x/a.png", "role": "front"}],
        appearance_json={"hair": "red"},
    )
    row = await tm.get_character(cid)
    assert row is not None
    assert row["ref_images"] == [{"oss_url": "https://x/a.png", "role": "front"}]
    assert row["appearance"] == {"hair": "red"}


async def test_delete_character(tm) -> None:
    cid = await tm.create_character(name="A")
    assert await tm.delete_character(cid) is True
    assert await tm.get_character(cid) is None
    assert await tm.delete_character(cid) is False


# ─── Series ──────────────────────────────────────────────────────────────


async def test_create_series_with_default_characters(tm) -> None:
    c1 = await tm.create_character(name="A")
    c2 = await tm.create_character(name="B")
    sid = await tm.create_series(
        title="校园物语",
        visual_style="shoujo",
        ratio="9:16",
        backend_pref="direct",
        default_characters=[c1, c2],
    )
    row = await tm.get_series(sid)
    assert row is not None
    assert row["title"] == "校园物语"
    assert row["default_characters"] == [c1, c2]
    assert row["total_episodes"] == 0


async def test_create_series_rejects_invalid_backend(tm) -> None:
    with pytest.raises(ValueError, match="invalid backend_pref"):
        await tm.create_series(title="x", backend_pref="bogus")


async def test_update_series_safe_validates_backend(tm) -> None:
    sid = await tm.create_series(title="x")
    with pytest.raises(ValueError, match="invalid backend_pref"):
        await tm.update_series_safe(sid, backend_pref="bogus")


async def test_list_series_orders_by_created_desc(tm) -> None:
    a = await tm.create_series(title="A")
    b = await tm.create_series(title="B")
    rows = await tm.list_series()
    assert [r["id"] for r in rows] == [b, a]


# ─── Episodes ────────────────────────────────────────────────────────────


async def test_create_episode_under_series(tm) -> None:
    sid = await tm.create_series(title="S")
    ep1 = await tm.create_episode(series_id=sid, episode_no=1, title="Pilot", story="...")
    ep2 = await tm.create_episode(series_id=sid, episode_no=2, title="Two")
    row = await tm.get_episode(ep1)
    assert row is not None
    assert row["series_id"] == sid
    listed = await tm.list_episodes(series_id=sid)
    assert [r["id"] for r in listed] == [ep1, ep2]  # by episode_no ASC


async def test_create_standalone_episode(tm) -> None:
    ep = await tm.create_episode(story="standalone story")
    row = await tm.get_episode(ep)
    assert row is not None
    assert row["series_id"] is None


async def test_update_episode_safe_writes_storyboard_json(tm) -> None:
    ep = await tm.create_episode(story="x")
    storyboard = [
        {"index": 1, "duration": 5, "scene_description": "..."},
        {"index": 2, "duration": 4, "scene_description": "..."},
    ]
    await tm.update_episode_safe(ep, storyboard_json=storyboard, duration_sec=9.0)
    row = await tm.get_episode(ep)
    assert row is not None
    assert row["storyboard"] == storyboard
    assert row["duration_sec"] == 9.0


async def test_update_episode_safe_rejects_non_writable(tm) -> None:
    ep = await tm.create_episode()
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_episode_safe(ep, id="hijack")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_episode_safe(ep, series_id="hijack")  # creation-time


# ─── Tasks ───────────────────────────────────────────────────────────────


async def test_create_task_returns_unique_id(tm) -> None:
    a = await tm.create_task(mode="quick", backend="direct")
    b = await tm.create_task(mode="episode", backend="runninghub")
    assert a != b
    row = await tm.get_task(a)
    assert row is not None
    assert row["status"] == "pending"
    assert row["current_step"] == "setup"
    assert row["progress"] == 0


async def test_create_task_rejects_invalid_backend(tm) -> None:
    with pytest.raises(ValueError, match="invalid backend"):
        await tm.create_task(mode="quick", backend="bogus")


async def test_update_task_safe_writable_columns(tm) -> None:
    tid = await tm.create_task(mode="quick")
    ok = await tm.update_task_safe(tid, status="running", current_step="animate", progress=42)
    assert ok is True
    row = await tm.get_task(tid)
    assert row is not None
    assert row["status"] == "running"
    assert row["current_step"] == "animate"
    assert row["progress"] == 42


async def test_update_task_safe_rejects_non_writable(tm) -> None:
    tid = await tm.create_task(mode="quick")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_task_safe(tid, id="hijack")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_task_safe(tid, mode="episode")  # creation-time only


async def test_update_task_safe_validates_status(tm) -> None:
    tid = await tm.create_task(mode="quick")
    with pytest.raises(ValueError, match="invalid status"):
        await tm.update_task_safe(tid, status="bogus")


async def test_update_task_safe_validates_progress_range(tm) -> None:
    tid = await tm.create_task(mode="quick")
    with pytest.raises(ValueError, match="progress out of range"):
        await tm.update_task_safe(tid, progress=101)
    with pytest.raises(ValueError, match="progress out of range"):
        await tm.update_task_safe(tid, progress=-1)
    with pytest.raises(ValueError, match="progress must be"):
        await tm.update_task_safe(tid, progress="not a number")


async def test_update_task_safe_auto_encodes_json(tm) -> None:
    tid = await tm.create_task(mode="quick")
    await tm.update_task_safe(
        tid,
        cost_breakdown_json={"total": 0.42, "items": []},
        error_hints_json={"cause": "network"},
    )
    row = await tm.get_task(tid)
    assert row is not None
    assert row["cost_breakdown"] == {"total": 0.42, "items": []}
    assert row["error_hints"] == {"cause": "network"}


async def test_list_tasks_filter_by_status_and_episode(tm) -> None:
    ep = await tm.create_episode()
    a = await tm.create_task(mode="quick", episode_id=ep)
    await tm.update_task_safe(a, status="succeeded")
    b = await tm.create_task(mode="episode", episode_id=ep)
    await tm.update_task_safe(b, status="failed")
    c = await tm.create_task(mode="quick")  # different episode
    succ = await tm.list_tasks(status="succeeded")
    assert {r["id"] for r in succ} == {a}
    by_ep = await tm.list_tasks(episode_id=ep)
    assert {r["id"] for r in by_ep} == {a, b}
    everything = await tm.list_tasks()
    assert {r["id"] for r in everything} == {a, b, c}


async def test_find_pending_tasks_returns_only_in_flight(tm) -> None:
    pending = await tm.create_task(mode="quick")
    running = await tm.create_task(mode="quick")
    await tm.update_task_safe(running, status="running")
    done = await tm.create_task(mode="quick")
    await tm.update_task_safe(done, status="succeeded")
    rows = list(await tm.find_pending_tasks())
    ids = {r["id"] for r in rows}
    assert ids == {pending, running}


async def test_cleanup_expired_only_finished(tm) -> None:
    fresh = await tm.create_task(mode="quick")
    old_done = await tm.create_task(mode="quick")
    old_running = await tm.create_task(mode="quick")
    await tm.update_task_safe(old_done, status="succeeded")
    await tm.update_task_safe(old_running, status="running")
    # Backdate the two "old" tasks beyond the 30d window.
    long_ago = time.time() - 90 * 86400
    conn = tm._conn  # type: ignore[attr-defined]
    await conn.execute(
        "UPDATE tasks SET created_at = ? WHERE id IN (?, ?)",
        (long_ago, old_done, old_running),
    )
    await conn.commit()
    n = await tm.cleanup_expired_tasks(retention_days=30)
    assert n == 1  # only the succeeded one is removed
    assert await tm.get_task(fresh) is not None
    assert await tm.get_task(old_done) is None
    assert await tm.get_task(old_running) is not None  # running preserved


# ─── Bulk helpers ────────────────────────────────────────────────────────


async def test_count_per_table_and_status(tm) -> None:
    await tm.create_character(name="A")
    await tm.create_character(name="B")
    sid = await tm.create_series(title="x")
    await tm.create_episode(series_id=sid)
    t1 = await tm.create_task(mode="quick")
    await tm.update_task_safe(t1, status="succeeded")
    assert await tm.count("characters") == 2
    assert await tm.count("series") == 1
    assert await tm.count("episodes") == 1
    assert await tm.count("tasks") == 1
    assert await tm.count("tasks", status="succeeded") == 1
    assert await tm.count("tasks", status="failed") == 0


async def test_count_unknown_table_raises(tm) -> None:
    with pytest.raises(ValueError, match="unknown table"):
        await tm.count("bogus")


async def test_count_status_filter_limited_to_tasks(tm) -> None:
    with pytest.raises(ValueError, match="status filter"):
        await tm.count("characters", status="succeeded")


# ─── Lifecycle ───────────────────────────────────────────────────────────


async def test_async_context_manager(tmp_path: Path) -> None:
    db = tmp_path / "ctx.db"
    async with MangaTaskManager(db) as mgr:
        assert mgr._db is not None  # type: ignore[attr-defined]
        await mgr.create_character(name="x")
    # After context exit DB should be closed.
    fresh = MangaTaskManager(db)
    await fresh.init()
    chars = await fresh.list_characters()
    assert len(chars) == 1  # persisted across instances
    await fresh.close()


async def test_init_called_twice_is_noop(tm) -> None:
    await tm.init()
    await tm.init()  # must not error / re-create schema
    assert await tm.count("characters") == 0


async def test_use_before_init_raises(tmp_path: Path) -> None:
    mgr = MangaTaskManager(tmp_path / "raw.db")
    with pytest.raises(RuntimeError, match="must be called first"):
        await mgr.create_character(name="x")
