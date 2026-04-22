"""Unit tests for ``plugins/seedance-video/task_manager.py``.

Sprint 7 / A4 — exercise the SQL whitelist hardening and lifecycle invariants.
The tests run against an on-disk SQLite file under ``tmp_path`` so each test is
isolated from the others (no in-memory ``:memory:`` shared connection trickery).
"""

from __future__ import annotations

import json

import pytest

from task_manager import DEFAULT_CONFIG, TaskManager


@pytest.fixture
async def tm(tmp_path):
    db = tmp_path / "seedance.db"
    manager = TaskManager(db)
    await manager.init()
    try:
        yield manager
    finally:
        await manager.close()


# ── init / config ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_creates_db_file(tmp_path):
    db = tmp_path / "nested" / "seedance.db"
    manager = TaskManager(db)
    await manager.init()
    try:
        assert db.is_file()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_default_config_seeded(tm):
    cfg = await tm.get_all_config()
    for key, default in DEFAULT_CONFIG.items():
        assert cfg.get(key) == default


@pytest.mark.asyncio
async def test_get_config_unknown_key_returns_empty(tm):
    assert await tm.get_config("never_existed") == ""


@pytest.mark.asyncio
async def test_get_config_known_key(tm):
    assert await tm.get_config("auto_download") == "true"


@pytest.mark.asyncio
async def test_set_config_overrides_default(tm):
    await tm.set_config("auto_download", "false")
    assert await tm.get_config("auto_download") == "false"


@pytest.mark.asyncio
async def test_set_configs_batch(tm):
    await tm.set_configs(
        {"poll_interval": "30", "service_tier_default": "flex"}
    )
    cfg = await tm.get_all_config()
    assert cfg["poll_interval"] == "30"
    assert cfg["service_tier_default"] == "flex"


@pytest.mark.asyncio
async def test_set_config_persists_after_close(tmp_path):
    db = tmp_path / "seedance.db"
    m1 = TaskManager(db)
    await m1.init()
    await m1.set_config("ark_api_key", "sk-test-123")
    await m1.close()

    m2 = TaskManager(db)
    await m2.init()
    try:
        assert await m2.get_config("ark_api_key") == "sk-test-123"
    finally:
        await m2.close()


# ── create_task ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_task_minimal_defaults(tm):
    task = await tm.create_task()
    assert task["id"]
    assert task["status"] == "pending"
    assert task["mode"] == "t2v"
    assert task["model"] == "2.0"
    assert task["params"] == {}
    assert task["service_tier"] == "default"
    assert task["is_draft"] == 0


@pytest.mark.asyncio
async def test_create_task_custom_params(tm):
    task = await tm.create_task(
        prompt="hello",
        mode="i2v",
        model="lite",
        params={"foo": 1, "bar": [2, 3]},
        service_tier="flex",
        is_draft=True,
        callback_url="https://example.test/cb",
    )
    persisted = await tm.get_task(task["id"])
    assert persisted is not None
    assert persisted["prompt"] == "hello"
    assert persisted["mode"] == "i2v"
    assert persisted["model"] == "lite"
    assert persisted["params"] == {"foo": 1, "bar": [2, 3]}
    assert persisted["is_draft"] is True
    assert persisted["callback_url"] == "https://example.test/cb"


@pytest.mark.asyncio
async def test_create_task_explicit_id(tm):
    task = await tm.create_task(id="custom-id-1234")
    assert task["id"] == "custom-id-1234"
    assert (await tm.get_task("custom-id-1234"))["id"] == "custom-id-1234"


# ── get / list ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_missing_returns_none(tm):
    assert await tm.get_task("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_tasks_empty(tm):
    items, total = await tm.list_tasks()
    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_tasks_pagination(tm):
    for i in range(7):
        await tm.create_task(prompt=f"p{i}")
    page1, total = await tm.list_tasks(limit=3, offset=0)
    page2, _ = await tm.list_tasks(limit=3, offset=3)
    assert total == 7
    assert len(page1) == 3
    assert len(page2) == 3
    assert {t["id"] for t in page1}.isdisjoint({t["id"] for t in page2})


@pytest.mark.asyncio
async def test_list_tasks_filter_status(tm):
    a = await tm.create_task(status="pending")
    b = await tm.create_task(status="running")
    await tm.create_task(status="succeeded")

    pending, total = await tm.list_tasks(status="pending")
    assert total == 1
    assert pending[0]["id"] == a["id"]

    running, _ = await tm.list_tasks(status="running")
    assert running[0]["id"] == b["id"]


@pytest.mark.asyncio
async def test_list_tasks_filter_is_draft(tm):
    await tm.create_task(is_draft=True)
    await tm.create_task(is_draft=False)
    drafts, total = await tm.list_tasks(is_draft=True)
    assert total == 1
    assert drafts[0]["is_draft"] is True


@pytest.mark.asyncio
async def test_list_tasks_filter_service_tier(tm):
    await tm.create_task(service_tier="default")
    await tm.create_task(service_tier="flex")
    flex, total = await tm.list_tasks(service_tier="flex")
    assert total == 1
    assert flex[0]["service_tier"] == "flex"


@pytest.mark.asyncio
async def test_get_running_tasks(tm):
    await tm.create_task(status="pending")
    await tm.create_task(status="running")
    await tm.create_task(status="succeeded")
    running = await tm.get_running_tasks()
    statuses = {t["status"] for t in running}
    assert statuses == {"pending", "running"}


# ── update_task whitelist (A4 hardening) ─────────────────────────────────


@pytest.mark.asyncio
async def test_update_task_no_kwargs_returns_false(tm):
    task = await tm.create_task()
    assert await tm.update_task(task["id"]) is False


@pytest.mark.asyncio
async def test_update_task_value_injection_is_bind_safe(tm):
    """Values flow through aiosqlite bind params, so even a SQL-looking
    string never reaches the SQL grammar.  The malicious value lands in the
    cell verbatim and the tasks table survives.
    """
    task = await tm.create_task()
    payload = "running'; DROP TABLE tasks; --"
    ok = await tm.update_task(task["id"], status=payload)
    assert ok is True

    persisted = await tm.get_task(task["id"])
    assert persisted is not None
    assert persisted["status"] == payload  # stored verbatim, not executed
    assert await tm.create_task() is not None  # tasks table still alive


@pytest.mark.asyncio
async def test_update_task_rejects_non_whitelisted_column(tm):
    task = await tm.create_task()
    with pytest.raises(ValueError) as exc:
        await tm.update_task(task["id"], hax_column="x")
    msg = str(exc.value)
    assert "hax_column" in msg
    assert "whitelisted" in msg
    assert "status" in msg  # allowed-list shown for debuggability


@pytest.mark.asyncio
async def test_update_task_rejects_id_column(tm):
    """``id`` is intentionally not in the whitelist — re-keying a row would
    break referential integrity in the assets table.
    """
    task = await tm.create_task()
    with pytest.raises(ValueError):
        await tm.update_task(task["id"], id="other-id")


@pytest.mark.asyncio
async def test_update_task_rejects_created_at(tm):
    task = await tm.create_task()
    with pytest.raises(ValueError):
        await tm.update_task(task["id"], created_at=0)


@pytest.mark.asyncio
async def test_update_task_rejects_updated_at(tm):
    """``updated_at`` is auto-bumped by the manager itself."""
    task = await tm.create_task()
    with pytest.raises(ValueError):
        await tm.update_task(task["id"], updated_at=0)


@pytest.mark.asyncio
async def test_update_task_status(tm):
    task = await tm.create_task()
    assert await tm.update_task(task["id"], status="succeeded") is True
    assert (await tm.get_task(task["id"]))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_update_task_video_url_and_thumb(tm):
    task = await tm.create_task()
    await tm.update_task(
        task["id"],
        video_url="https://cdn.example/x.mp4",
        thumbnail_path="/tmp/x.jpg",
        local_video_path="/tmp/x.mp4",
    )
    persisted = await tm.get_task(task["id"])
    assert persisted["video_url"] == "https://cdn.example/x.mp4"
    assert persisted["thumbnail_path"] == "/tmp/x.jpg"
    assert persisted["local_video_path"] == "/tmp/x.mp4"


@pytest.mark.asyncio
async def test_update_task_params_json_encoded(tm):
    task = await tm.create_task()
    await tm.update_task(task["id"], params={"foo": 1, "lst": [1, 2]})
    persisted = await tm.get_task(task["id"])
    assert persisted["params"] == {"foo": 1, "lst": [1, 2]}


@pytest.mark.asyncio
async def test_update_task_is_draft_bool_to_int(tm):
    task = await tm.create_task(is_draft=False)
    await tm.update_task(task["id"], is_draft=True)
    persisted = await tm.get_task(task["id"])
    assert persisted["is_draft"] is True
    await tm.update_task(task["id"], is_draft=False)
    persisted = await tm.get_task(task["id"])
    assert persisted["is_draft"] is False


@pytest.mark.asyncio
async def test_update_task_bumps_updated_at(tm):
    task = await tm.create_task()
    original = (await tm.get_task(task["id"]))["updated_at"]
    import asyncio
    await asyncio.sleep(0.02)
    await tm.update_task(task["id"], status="running")
    bumped = (await tm.get_task(task["id"]))["updated_at"]
    assert bumped > original


@pytest.mark.asyncio
async def test_update_task_multiple_columns_atomic(tm):
    task = await tm.create_task()
    ok = await tm.update_task(
        task["id"],
        status="succeeded",
        video_url="https://x.test/v.mp4",
        revised_prompt="prettier prompt",
        last_frame_url="https://x.test/last.jpg",
    )
    assert ok is True
    persisted = await tm.get_task(task["id"])
    assert persisted["status"] == "succeeded"
    assert persisted["video_url"] == "https://x.test/v.mp4"
    assert persisted["revised_prompt"] == "prettier prompt"
    assert persisted["last_frame_url"] == "https://x.test/last.jpg"


@pytest.mark.asyncio
async def test_update_task_missing_id_returns_false(tm):
    assert await tm.update_task("nonexistent", status="x") is False


@pytest.mark.asyncio
async def test_update_task_whitelist_covers_all_existing_callsites(tm):
    """Sanity: every column update_task() is invoked with from plugin.py /
    long_video.py *must* be in the whitelist.  Catches regressions where a
    new feature adds an update key without registering it.
    """
    callsite_keys = {
        "status", "video_url", "revised_prompt", "last_frame_url",
        "error_message", "local_video_path",
    }
    assert callsite_keys <= set(TaskManager._UPDATABLE_COLUMNS)


@pytest.mark.asyncio
async def test_update_task_json_keys_subset(tm):
    """``_JSON_ENCODED_KEYS`` must be a subset of ``_UPDATABLE_COLUMNS``."""
    assert TaskManager._JSON_ENCODED_KEYS <= set(TaskManager._UPDATABLE_COLUMNS)


@pytest.mark.asyncio
async def test_update_task_params_value_is_valid_json_in_db(tm):
    task = await tm.create_task()
    await tm.update_task(task["id"], params={"k": "v"})
    # peek at the underlying row to confirm JSON encoding (not Python repr)
    cur = await tm._db.execute(
        "SELECT params_json FROM tasks WHERE id = ?", (task["id"],)
    )
    rows = await cur.fetchall()
    assert json.loads(rows[0][0]) == {"k": "v"}


# ── delete_task / cleanup ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_task_existing(tm):
    task = await tm.create_task()
    assert await tm.delete_task(task["id"]) is True
    assert await tm.get_task(task["id"]) is None


@pytest.mark.asyncio
async def test_delete_task_missing_returns_false(tm):
    assert await tm.delete_task("does-not-exist") is False


# ── assets ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_asset_defaults(tm):
    asset = await tm.create_asset(file_path="/tmp/a.jpg")
    assert asset["id"]
    assert asset["type"] == "image"
    assert asset["file_path"] == "/tmp/a.jpg"
    assert asset["sort_order"] == 0


@pytest.mark.asyncio
async def test_create_asset_custom(tm):
    task = await tm.create_task()
    asset = await tm.create_asset(
        task_id=task["id"], type="video", file_path="/tmp/v.mp4",
        original_name="orig.mp4", size_bytes=4096,
        width=1280, height=720, duration_sec=12.5, role="ref",
    )
    persisted = await tm.get_asset(asset["id"])
    assert persisted["task_id"] == task["id"]
    assert persisted["type"] == "video"
    assert persisted["original_name"] == "orig.mp4"
    assert persisted["width"] == 1280
    assert persisted["duration_sec"] == 12.5
    assert persisted["role"] == "ref"


@pytest.mark.asyncio
async def test_list_assets_filter_type(tm):
    await tm.create_asset(type="image", file_path="/i.jpg")
    await tm.create_asset(type="video", file_path="/v.mp4")
    images, total = await tm.list_assets(asset_type="image")
    assert total == 1
    assert images[0]["type"] == "image"


@pytest.mark.asyncio
async def test_list_assets_filter_task_id(tm):
    task_a = await tm.create_task()
    task_b = await tm.create_task()
    await tm.create_asset(task_id=task_a["id"], file_path="/a.jpg")
    await tm.create_asset(task_id=task_b["id"], file_path="/b.jpg")
    a_only, total = await tm.list_assets(task_id=task_a["id"])
    assert total == 1
    assert a_only[0]["task_id"] == task_a["id"]


@pytest.mark.asyncio
async def test_list_assets_pagination(tm):
    for i in range(5):
        await tm.create_asset(file_path=f"/f{i}.jpg")
    page1, total = await tm.list_assets(limit=2, offset=0)
    page2, _ = await tm.list_assets(limit=2, offset=2)
    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert {a["id"] for a in page1}.isdisjoint({a["id"] for a in page2})


@pytest.mark.asyncio
async def test_get_asset_missing(tm):
    assert await tm.get_asset("nope") is None


@pytest.mark.asyncio
async def test_delete_asset_existing(tm):
    asset = await tm.create_asset(file_path="/x.jpg")
    assert await tm.delete_asset(asset["id"]) is True
    assert await tm.get_asset(asset["id"]) is None


@pytest.mark.asyncio
async def test_delete_asset_missing(tm):
    assert await tm.delete_asset("ghost") is False


@pytest.mark.asyncio
async def test_count_asset_references_zero(tm):
    asset = await tm.create_asset(file_path="/x.jpg")
    # This asset has task_id=NULL; the count looks for non-null link rows.
    assert await tm.count_asset_references(asset["id"]) == 0


@pytest.mark.asyncio
async def test_count_asset_references_one(tm):
    task = await tm.create_task()
    asset = await tm.create_asset(task_id=task["id"], file_path="/y.jpg")
    assert await tm.count_asset_references(asset["id"]) == 1


# ── close ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path):
    manager = TaskManager(tmp_path / "x.db")
    await manager.init()
    await manager.close()
    # Calling twice must not raise.
    await manager.close()
