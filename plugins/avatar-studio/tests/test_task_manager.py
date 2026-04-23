"""Phase 1 — AvatarTaskManager CRUD + whitelist guard + figures + cleanup."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from avatar_task_manager import AvatarTaskManager


@pytest.fixture
async def tm(tmp_path: Path) -> AvatarTaskManager:
    mgr = AvatarTaskManager(tmp_path / "avatar.db")
    await mgr.init()
    yield mgr
    await mgr.close()


# ─── Task CRUD ─────────────────────────────────────────────────────────


async def test_create_task_returns_unique_id_and_persists(tm: AvatarTaskManager) -> None:
    a = await tm.create_task(mode="photo_speak", prompt="hello", params={"resolution": "480P"})
    b = await tm.create_task(mode="photo_speak", prompt="world")
    assert a != b
    row = await tm.get_task(a)
    assert row is not None
    assert row["status"] == "pending"
    assert row["mode"] == "photo_speak"
    assert row["params"] == {"resolution": "480P"}  # auto-decoded


async def test_list_tasks_orders_by_created_desc_with_filters(tm: AvatarTaskManager) -> None:
    a = await tm.create_task(mode="photo_speak")
    await tm.update_task_safe(a, status="succeeded")
    b = await tm.create_task(mode="video_reface")
    await tm.update_task_safe(b, status="failed")
    all_rows = await tm.list_tasks()
    assert [r["id"] for r in all_rows] == [b, a]
    only_succ = await tm.list_tasks(status="succeeded")
    assert len(only_succ) == 1 and only_succ[0]["id"] == a
    only_reface = await tm.list_tasks(mode="video_reface")
    assert len(only_reface) == 1 and only_reface[0]["id"] == b


async def test_update_task_safe_writable_columns(tm: AvatarTaskManager) -> None:
    tid = await tm.create_task(mode="photo_speak")
    ok = await tm.update_task_safe(
        tid,
        status="running",
        dashscope_id="ds_abc",
        dashscope_endpoint="submit_s2v",
        audio_duration_sec=5.5,
    )
    assert ok is True
    row = await tm.get_task(tid)
    assert row is not None
    assert row["status"] == "running"
    assert row["dashscope_id"] == "ds_abc"
    assert row["audio_duration_sec"] == 5.5
    assert row["updated_at"] >= row["created_at"]


async def test_update_task_safe_rejects_non_writable_column(tm: AvatarTaskManager) -> None:
    tid = await tm.create_task(mode="photo_speak")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_task_safe(tid, id="hijack")
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_task_safe(tid, created_at=0.0)
    with pytest.raises(ValueError, match="non-writable"):
        await tm.update_task_safe(tid, mode="video_reface")  # mode is creation-time only


async def test_update_task_safe_validates_status(tm: AvatarTaskManager) -> None:
    tid = await tm.create_task(mode="photo_speak")
    with pytest.raises(ValueError, match="invalid status"):
        await tm.update_task_safe(tid, status="bogus")


async def test_update_task_safe_auto_encodes_json_columns(tm: AvatarTaskManager) -> None:
    tid = await tm.create_task(mode="photo_speak")
    await tm.update_task_safe(
        tid,
        cost_breakdown_json={"total": 0.42, "items": []},
        error_hints_json=["check network"],
        asset_paths_json={"image": "/tmp/a.png"},
    )
    row = await tm.get_task(tid)
    assert row is not None
    assert row["cost_breakdown"] == {"total": 0.42, "items": []}
    assert row["error_hints"] == ["check network"]
    assert row["asset_paths"] == {"image": "/tmp/a.png"}


async def test_delete_task(tm: AvatarTaskManager) -> None:
    tid = await tm.create_task(mode="photo_speak")
    assert await tm.delete_task(tid) is True
    assert await tm.get_task(tid) is None
    assert await tm.delete_task(tid) is False


async def test_cleanup_expired_only_finished(tm: AvatarTaskManager) -> None:
    fresh = await tm.create_task(mode="photo_speak")
    old_done = await tm.create_task(mode="photo_speak")
    old_running = await tm.create_task(mode="photo_speak")
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
    n = await tm.cleanup_expired(retention_days=30)
    assert n == 1  # only the succeeded one is removed; running is preserved
    assert await tm.get_task(fresh) is not None
    assert await tm.get_task(old_done) is None
    assert await tm.get_task(old_running) is not None


# ─── Voices (cloned only) ─────────────────────────────────────────────


async def test_voice_crud(tm: AvatarTaskManager) -> None:
    vid = await tm.create_custom_voice(
        label="my-voice",
        source_audio_path="/tmp/sample.wav",
        dashscope_voice_id="cust_ds_001",
        gender="female",
    )
    voices = await tm.list_voices()
    assert any(v["id"] == vid for v in voices)
    assert await tm.delete_custom_voice(vid) is True
    assert await tm.delete_custom_voice(vid) is False


# ─── Figures ─────────────────────────────────────────────────────────


async def test_figure_crud_with_detect_flags(tm: AvatarTaskManager) -> None:
    fid = await tm.create_figure(
        label="actor 01",
        image_path="/tmp/01.png",
        preview_url="/api/plugins/avatar-studio/uploads/figures/01.png",
        detect_pass=True,
        detect_humanoid=True,
    )
    rows = await tm.list_figures()
    assert any(r["id"] == fid for r in rows)
    figure = next(r for r in rows if r["id"] == fid)
    assert figure["detect_pass"] == 1
    assert figure["detect_humanoid"] == 1
    assert await tm.delete_figure(fid) is True


async def test_figure_default_status_is_pending_until_updated(
    tm: AvatarTaskManager,
) -> None:
    """New figure rows must surface as ``pending`` so the UI can show a
    spinner — the bug we just fixed was that rows defaulted to
    ``detect_pass=0`` and the UI mis-classified that as 'pending' only
    by accident, with no way to distinguish a never-checked figure from
    a failed one. ``detect_status`` now carries the verdict explicitly."""
    fid = await tm.create_figure(
        label="actor 02",
        image_path="/tmp/02.png",
        preview_url="/api/plugins/avatar-studio/uploads/figures/02.png",
    )
    figure = await tm.get_figure(fid)
    assert figure is not None
    assert figure["detect_status"] == "pending"
    assert figure["detect_pass"] == 0

    pending = await tm.list_pending_figures()
    assert any(r["id"] == fid for r in pending)

    assert await tm.update_figure_detect(
        fid, status="pass", message="OK", humanoid=True
    ) is True
    figure = await tm.get_figure(fid)
    assert figure["detect_status"] == "pass"
    assert figure["detect_pass"] == 1
    assert figure["detect_humanoid"] == 1
    assert figure["detect_message"] == "OK"

    pending = await tm.list_pending_figures()
    assert all(r["id"] != fid for r in pending)


async def test_figure_update_detect_rejects_unknown_status(
    tm: AvatarTaskManager,
) -> None:
    fid = await tm.create_figure(
        label="actor 03", image_path="/tmp/03.png", preview_url="/p/03.png",
    )
    with pytest.raises(ValueError, match="unknown detect_status"):
        await tm.update_figure_detect(fid, status="bogus")


# ─── Bulk helpers ─────────────────────────────────────────────────────


async def test_count_helper(tm: AvatarTaskManager) -> None:
    a = await tm.create_task(mode="photo_speak")
    await tm.create_task(mode="video_reface")
    await tm.update_task_safe(a, status="running")
    assert await tm.count("tasks") == 2
    assert await tm.count("tasks", status="running") == 1
    assert await tm.count("voices") == 0
    with pytest.raises(ValueError, match="unknown table"):
        await tm.count("not_a_table")


async def test_find_pending_dashscope_ids(tm: AvatarTaskManager) -> None:
    a = await tm.create_task(mode="photo_speak")
    b = await tm.create_task(mode="video_reface")
    await tm.update_task_safe(
        a, status="running", dashscope_id="ds_a", dashscope_endpoint="submit_s2v"
    )
    await tm.update_task_safe(b, status="succeeded", dashscope_id="ds_b")
    pending = list(await tm.find_pending_dashscope_ids())
    assert len(pending) == 1
    task_id, ds_id, endpoint = pending[0]
    assert task_id == a
    assert ds_id == "ds_a"
    assert endpoint == "submit_s2v"


async def test_lifecycle_via_async_context(tmp_path: Path) -> None:
    db = tmp_path / "ctx.db"
    async with AvatarTaskManager(db) as mgr:
        await mgr.create_task(mode="photo_speak")
        assert await mgr.count("tasks") == 1
    # After exit the connection is closed; a fresh manager can re-open.
    async with AvatarTaskManager(db) as mgr2:
        assert await mgr2.count("tasks") == 1
