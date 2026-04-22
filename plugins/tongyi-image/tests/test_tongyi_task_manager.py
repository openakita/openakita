"""Unit tests for ``tongyi_task_manager.TaskManager``.

Covers Sprint 1's hardening (A4: SQL column whitelist) plus the regular
CRUD surface so the deeper migration in A1+ has a safety net.

Each test gets a fresh tmp_path SQLite file via a function-scoped fixture
— every test owns its own DB, so order does not matter and parallel-mode
``pytest -n`` would be safe later (we don't enable it here).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from tongyi_task_manager import (  # noqa: E402
    DEFAULT_CONFIG,
    TaskManager,
    _now_iso,
    _short_id,
)

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def tm(tmp_path: Path):
    """Yield a fresh TaskManager backed by a per-test SQLite file."""
    db = tmp_path / "tongyi.db"
    mgr = TaskManager(db)
    await mgr.init()
    try:
        yield mgr
    finally:
        await mgr.close()


# ── helpers ───────────────────────────────────────────────────────────


def test_short_id_is_12_hex_chars() -> None:
    """_short_id must produce a 12-char hex slug — collisions across
    create_task() calls would silently overwrite rows otherwise."""
    sid = _short_id()
    assert len(sid) == 12
    assert all(c in "0123456789abcdef" for c in sid)
    # crude collision guard: 1000 ids should all be unique
    ids = {_short_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_now_iso_format() -> None:
    """Timestamps must be local-time YYYY-MM-DD HH:MM:SS so the UI's
    `Date.parse` reads them without timezone surprises."""
    s = _now_iso()
    assert len(s) == 19
    assert s[4] == "-" and s[7] == "-" and s[10] == " "
    assert s[13] == ":" and s[16] == ":"


# ── lifecycle ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_creates_tables_and_seeds_config(tmp_path: Path) -> None:
    db = tmp_path / "init.db"
    mgr = TaskManager(db)
    await mgr.init()
    try:
        cfg = await mgr.get_all_config()
        # All DEFAULT_CONFIG keys must be present after first init.
        for key in DEFAULT_CONFIG:
            assert key in cfg
        assert cfg["default_model"] == "wan27-pro"
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_double_init_is_idempotent(tmp_path: Path) -> None:
    """A second TaskManager() over the same file must not crash on
    ``CREATE TABLE`` / re-seed; existing values must be preserved."""
    db = tmp_path / "twice.db"
    mgr1 = TaskManager(db)
    await mgr1.init()
    await mgr1.set_config("dashscope_api_key", "user_key")
    await mgr1.close()

    mgr2 = TaskManager(db)
    await mgr2.init()
    try:
        # Re-seed must use INSERT OR IGNORE — existing user value preserved.
        assert await mgr2.get_config("dashscope_api_key") == "user_key"
    finally:
        await mgr2.close()


@pytest.mark.asyncio
async def test_close_is_safe_without_init(tmp_path: Path) -> None:
    """``close()`` on a never-initialised TaskManager must not raise."""
    mgr = TaskManager(tmp_path / "noinit.db")
    await mgr.close()  # no-op
    await mgr.close()  # double-close also no-op


# ── config CRUD ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_and_get_config(tm: TaskManager) -> None:
    await tm.set_config("dashscope_api_key", "sk-xyz")
    assert await tm.get_config("dashscope_api_key") == "sk-xyz"
    # Overwrite (REPLACE) works.
    await tm.set_config("dashscope_api_key", "sk-new")
    assert await tm.get_config("dashscope_api_key") == "sk-new"


@pytest.mark.asyncio
async def test_get_config_missing_returns_none(tm: TaskManager) -> None:
    assert await tm.get_config("never_set") is None


@pytest.mark.asyncio
async def test_set_configs_bulk(tm: TaskManager) -> None:
    await tm.set_configs({"poll_interval": "5", "watermark": "true"})
    cfg = await tm.get_all_config()
    assert cfg["poll_interval"] == "5"
    assert cfg["watermark"] == "true"


# ── task CRUD ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_task_returns_full_record(tm: TaskManager) -> None:
    task = await tm.create_task(
        prompt="A red apple", model="wan27-pro", mode="text2img",
        params={"size": "1024*1024"},
    )
    assert task is not None
    assert isinstance(task["id"], str) and len(task["id"]) == 12
    assert task["prompt"] == "A red apple"
    assert task["model"] == "wan27-pro"
    assert task["status"] == "pending"  # default
    assert task["params"] == {"size": "1024*1024"}
    assert task["image_urls"] == []
    assert task["local_image_paths"] == []
    assert task["created_at"] == task["updated_at"]


@pytest.mark.asyncio
async def test_create_task_with_image_urls(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X", image_urls=["http://a/1.png", "http://a/2.png"])
    assert task["image_urls"] == ["http://a/1.png", "http://a/2.png"]


@pytest.mark.asyncio
async def test_create_task_with_explicit_status(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X", status="running")
    assert task["status"] == "running"


@pytest.mark.asyncio
async def test_get_task_missing_returns_none(tm: TaskManager) -> None:
    assert await tm.get_task("nonexistent_id") is None


@pytest.mark.asyncio
async def test_delete_task(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="bye")
    assert await tm.delete_task(task["id"]) is True
    assert await tm.get_task(task["id"]) is None
    # Re-deleting a missing row returns False, not crash.
    assert await tm.delete_task(task["id"]) is False
    assert await tm.delete_task("never_existed") is False


# ── update_task: A4 SQL column whitelist (Sprint 1 hardening) ────────


@pytest.mark.asyncio
async def test_update_task_simple_field(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X")
    await tm.update_task(task["id"], status="succeeded")
    updated = await tm.get_task(task["id"])
    assert updated["status"] == "succeeded"
    # updated_at must advance even when only one column changes.
    assert updated["updated_at"] >= task["updated_at"]


@pytest.mark.asyncio
async def test_update_task_multiple_fields(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X")
    await tm.update_task(
        task["id"],
        status="running", api_task_id="api-123",
        error_message="", model="qwen-pro",
    )
    updated = await tm.get_task(task["id"])
    assert updated["status"] == "running"
    assert updated["api_task_id"] == "api-123"
    assert updated["model"] == "qwen-pro"


@pytest.mark.asyncio
async def test_update_task_json_fields_are_encoded(tm: TaskManager) -> None:
    """JSON-encoded keys (``params`` / ``image_urls`` / ``local_image_paths``
    / ``usage``) must be json.dumps()'d before persisting and json.loads()'d
    on read — guards against accidental column rename breaking the round-trip."""
    task = await tm.create_task(prompt="X")
    await tm.update_task(
        task["id"],
        params={"size": "2K", "n": 4},
        image_urls=["http://x/1.png"],
        local_image_paths=["/tmp/a.png", "/tmp/b.png"],
        usage={"image_count": 1, "tokens": 100},
    )
    updated = await tm.get_task(task["id"])
    assert updated["params"] == {"size": "2K", "n": 4}
    assert updated["image_urls"] == ["http://x/1.png"]
    assert updated["local_image_paths"] == ["/tmp/a.png", "/tmp/b.png"]
    assert updated["usage"] == {"image_count": 1, "tokens": 100}


@pytest.mark.asyncio
async def test_update_task_rejects_unknown_column(tm: TaskManager) -> None:
    """A4: column names go through a whitelist — anything else MUST raise
    ValueError instead of being interpolated into the UPDATE SQL.

    This is the SQL-injection regression test: if someone reverts the
    whitelist back to f"{k} = ?", an unsanitised key like ``status; DROP
    TABLE tasks --`` would pass through silently."""
    task = await tm.create_task(prompt="X")
    with pytest.raises(ValueError, match="not whitelisted"):
        await tm.update_task(task["id"], not_a_real_column="x")


@pytest.mark.asyncio
async def test_update_task_rejects_sql_injection_payload(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X")
    payload = "status; DROP TABLE tasks --"
    with pytest.raises(ValueError, match="not whitelisted"):
        await tm.update_task(task["id"], **{payload: "x"})
    # Tasks table must still exist after the rejected update.
    assert await tm.get_task(task["id"]) is not None


@pytest.mark.asyncio
async def test_update_task_rejects_physical_column_name(tm: TaskManager) -> None:
    """Even valid physical column names must be rejected if they're not
    in the caller-facing key set — e.g. ``params_json`` is the physical
    column but the caller-facing key is ``params``.  Using the physical
    name should fail to avoid bypassing the JSON-encode step."""
    task = await tm.create_task(prompt="X")
    with pytest.raises(ValueError, match="not whitelisted"):
        await tm.update_task(task["id"], params_json='{"size":"2K"}')


@pytest.mark.asyncio
async def test_update_task_with_no_updates_is_noop(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X")
    # Empty kwargs must NOT issue a malformed UPDATE.
    await tm.update_task(task["id"])
    same = await tm.get_task(task["id"])
    assert same["updated_at"] == task["updated_at"]


@pytest.mark.asyncio
async def test_whitelist_keys_are_stable() -> None:
    """Sanity guard against accidental whitelist shrinking — UI / worker
    callers depend on these caller-facing keys."""
    expected = {
        "status", "error_message", "api_task_id",
        "prompt", "negative_prompt", "model", "mode",
        "params", "image_urls", "local_image_paths", "usage",
    }
    assert set(TaskManager._UPDATABLE_COLUMNS) == expected
    # JSON-encoded keys must be a subset of whitelisted keys.
    assert set(TaskManager._UPDATABLE_COLUMNS) >= TaskManager._JSON_ENCODED_KEYS


# ── list / filter / paginate ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tasks_filters_by_status(tm: TaskManager) -> None:
    a = await tm.create_task(prompt="a", status="running")
    b = await tm.create_task(prompt="b", status="succeeded")
    c = await tm.create_task(prompt="c", status="succeeded")

    out = await tm.list_tasks(status="succeeded")
    assert out["total"] == 2
    ids = {t["id"] for t in out["tasks"]}
    assert ids == {b["id"], c["id"]}

    out_running = await tm.list_tasks(status="running")
    assert out_running["total"] == 1
    assert out_running["tasks"][0]["id"] == a["id"]


@pytest.mark.asyncio
async def test_list_tasks_filters_by_mode(tm: TaskManager) -> None:
    await tm.create_task(prompt="x", mode="text2img")
    await tm.create_task(prompt="y", mode="img_edit")
    out = await tm.list_tasks(mode="img_edit")
    assert out["total"] == 1
    assert out["tasks"][0]["mode"] == "img_edit"


@pytest.mark.asyncio
async def test_list_tasks_combined_filters(tm: TaskManager) -> None:
    await tm.create_task(prompt="a", status="running", mode="text2img")
    await tm.create_task(prompt="b", status="succeeded", mode="text2img")
    await tm.create_task(prompt="c", status="succeeded", mode="img_edit")
    out = await tm.list_tasks(status="succeeded", mode="text2img")
    assert out["total"] == 1
    assert out["tasks"][0]["prompt"] == "b"


@pytest.mark.asyncio
async def test_list_tasks_pagination(tm: TaskManager) -> None:
    for i in range(5):
        await tm.create_task(prompt=f"t{i}")
        # Sleep a hair so created_at differs between rows — without this
        # sqlite returns rows in insertion order anyway, but the assertion
        # below is more meaningful when timestamps actually differ.
        await asyncio.sleep(0.001)

    page1 = await tm.list_tasks(limit=2, offset=0)
    page2 = await tm.list_tasks(limit=2, offset=2)
    page3 = await tm.list_tasks(limit=2, offset=4)
    assert page1["total"] == page2["total"] == page3["total"] == 5
    assert len(page1["tasks"]) == 2
    assert len(page2["tasks"]) == 2
    assert len(page3["tasks"]) == 1
    # Ordering is created_at DESC — page1 has the most recent.
    seen_ids = (
        [t["id"] for t in page1["tasks"]]
        + [t["id"] for t in page2["tasks"]]
        + [t["id"] for t in page3["tasks"]]
    )
    assert len(set(seen_ids)) == 5


@pytest.mark.asyncio
async def test_list_tasks_empty_returns_zero(tm: TaskManager) -> None:
    out = await tm.list_tasks()
    assert out == {"tasks": [], "total": 0}


@pytest.mark.asyncio
async def test_get_running_tasks(tm: TaskManager) -> None:
    a = await tm.create_task(prompt="a", status="pending")
    b = await tm.create_task(prompt="b", status="running")
    await tm.create_task(prompt="c", status="succeeded")
    await tm.create_task(prompt="d", status="failed")

    running = await tm.get_running_tasks()
    ids = {t["id"] for t in running}
    assert ids == {a["id"], b["id"]}


# ── _row_to_dict: defensive JSON decoding ─────────────────────────────


@pytest.mark.asyncio
async def test_row_to_dict_handles_corrupt_json(tm: TaskManager) -> None:
    """Persisted JSON cells written by an older / forked build may be
    invalid; the loader must NOT crash the whole row — it returns the
    matching empty container instead so the UI still renders something."""
    task = await tm.create_task(prompt="X")
    # Force-corrupt the JSON columns directly via the underlying connection.
    assert tm._db is not None
    await tm._db.execute(
        "UPDATE tasks SET image_urls = ?, params_json = ?, local_image_paths = ?, usage_json = ? WHERE id = ?",
        ("not-a-json[", "also bad{", "][broken", "{nope", task["id"]),
    )
    await tm._db.commit()

    row = await tm.get_task(task["id"])
    assert row is not None
    # Sequence-shaped fields fall back to [], dict-shaped fields fall back to {}.
    assert row["image_urls"] == []
    assert row["local_image_paths"] == []
    assert row["params"] == {}
    assert row["usage"] == {}


@pytest.mark.asyncio
async def test_row_to_dict_handles_null_json(tm: TaskManager) -> None:
    task = await tm.create_task(prompt="X")
    assert tm._db is not None
    await tm._db.execute(
        "UPDATE tasks SET image_urls = NULL, usage_json = NULL WHERE id = ?",
        (task["id"],),
    )
    await tm._db.commit()
    row = await tm.get_task(task["id"])
    assert row["image_urls"] == []
    assert row["usage"] == {}


# ── default config invariants ─────────────────────────────────────────


def test_default_config_has_safe_empty_values() -> None:
    """Empty-string defaults for credentials prevent leaking literal keys
    in shipped binaries; non-empty defaults must remain to avoid forcing
    every user to fill in obvious fields."""
    assert DEFAULT_CONFIG["dashscope_api_key"] == ""
    assert DEFAULT_CONFIG["output_dir"] == ""
    # Sensible defaults that should NOT be empty.
    assert DEFAULT_CONFIG["default_model"]
    assert DEFAULT_CONFIG["default_size"]
    assert DEFAULT_CONFIG["poll_interval"]
    assert DEFAULT_CONFIG["auto_download"] in {"true", "false"}
    assert DEFAULT_CONFIG["watermark"] in {"true", "false"}


# ── round-trip: create → update → get → list ──────────────────────────


@pytest.mark.asyncio
async def test_full_lifecycle_round_trip(tm: TaskManager) -> None:
    """A representative end-to-end story so a regression that breaks
    one stage is loud rather than subtly off-by-one."""
    task = await tm.create_task(
        prompt="cat in space", model="wan27-pro",
        params={"size": "1024*1024", "n": 2},
    )
    await tm.update_task(task["id"], status="running", api_task_id="dash-001")
    await tm.update_task(
        task["id"],
        status="succeeded",
        image_urls=["http://x/1.png", "http://x/2.png"],
        local_image_paths=["/data/1.png", "/data/2.png"],
        usage={"image_count": 2},
    )

    final = await tm.get_task(task["id"])
    assert final["status"] == "succeeded"
    assert final["api_task_id"] == "dash-001"
    assert final["image_urls"] == ["http://x/1.png", "http://x/2.png"]
    assert final["local_image_paths"] == ["/data/1.png", "/data/2.png"]
    assert final["usage"] == {"image_count": 2}
    assert final["params"] == {"size": "1024*1024", "n": 2}

    listed = await tm.list_tasks(status="succeeded")
    assert listed["total"] == 1
    assert listed["tasks"][0]["id"] == task["id"]


# ── _row_to_dict invariants (no async) ────────────────────────────────


def test_no_residual_physical_column_names_leak_into_dict() -> None:
    """Quick guard: the dict returned by _row_to_dict must NOT carry
    physical ``*_json`` columns alongside the caller-facing keys, or
    the API response double-stringifies the JSON payload."""
    # Build a fake aiosqlite.Row-shaped dict; _row_to_dict accepts any
    # mapping because it only uses dict() and pop().
    fake_row = {
        "id": "abc", "status": "ok",
        "params_json": json.dumps({"size": "2K"}),
        "image_urls": json.dumps(["http://a"]),
        "local_image_paths": json.dumps([]),
        "usage_json": json.dumps({"n": 1}),
        "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    out = TaskManager._row_to_dict(fake_row)  # type: ignore[arg-type]
    assert "params_json" not in out
    assert "usage_json" not in out
    assert out["params"] == {"size": "2K"}
    assert out["usage"] == {"n": 1}
    assert out["image_urls"] == ["http://a"]
    assert out["local_image_paths"] == []
