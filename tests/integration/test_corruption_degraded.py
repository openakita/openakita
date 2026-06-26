"""Integration tests: corrupted SQLite files put subsystems into degraded mode.

These tests construct a corrupted database file via direct
``write_bytes`` (so no fixture holds a file handle, which on Windows
would otherwise produce a sharing-violation rather than the corruption
error we want). Each subsystem is exercised directly (no full backend
spin-up) to keep the suite fast and isolated.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from openakita.storage.degraded import registry


@pytest.fixture(autouse=True)
def _isolate_registry():
    registry.clear()
    yield
    registry.clear()


def _write_corrupt(path: Path, size: int = 200) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


# ---------------------------------------------------------------------------
# token_tracking
# ---------------------------------------------------------------------------


def test_token_tracking_writer_dead_on_corrupted_db(tmp_path):
    """``_writer_loop`` exits cleanly + flips ``_writer_dead`` on bad DB."""
    from openakita.core import token_tracking as tt

    bad = tmp_path / "agent.db"
    _write_corrupt(bad, size=200)

    # Reset module state (the writer-dead Event is process-global).
    tt._writer_dead.clear()
    tt._drop_warned.clear()
    tt._writer_stop.clear()
    tt._initialized = False

    tt.init_token_tracking(str(bad))
    # Let the daemon thread reach safe_open_sync → SQLiteUnavailable → return.
    for _ in range(20):
        if tt._writer_dead.is_set():
            break
        time.sleep(0.05)

    assert tt._writer_dead.is_set(), "writer thread should mark itself dead"
    assert registry.is_degraded("token_tracking")
    snap_entry = next(e for e in registry.snapshot() if e["subsystem"] == "token_tracking")
    assert snap_entry["reason"] in {"corrupted", "open_failed"}


def test_token_tracking_record_usage_silently_drops_after_dead(tmp_path):
    from openakita.core import token_tracking as tt

    bad = tmp_path / "agent2.db"
    _write_corrupt(bad)

    tt._writer_dead.clear()
    tt._drop_warned.clear()
    tt._writer_stop.clear()
    tt._initialized = False

    tt.init_token_tracking(str(bad))
    # Wait for writer death.
    for _ in range(20):
        if tt._writer_dead.is_set():
            break
        time.sleep(0.05)
    assert tt._writer_dead.is_set()

    # record_usage should not raise nor enqueue anything once dead.
    qsize_before = tt._write_queue.qsize()
    tt.record_usage(
        model="gpt-4",
        endpoint_name="openai",
        input_tokens=10,
        output_tokens=5,
    )
    tt.record_usage(
        model="gpt-4",
        endpoint_name="openai",
        input_tokens=10,
        output_tokens=5,
    )
    qsize_after = tt._write_queue.qsize()
    assert qsize_after == qsize_before, "dead writer must not enqueue records"
    assert tt._drop_warned.is_set(), "first drop should set the one-shot warn flag"


# ---------------------------------------------------------------------------
# feedback_store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_store_raises_503_on_corruption(tmp_path, monkeypatch):
    from openakita.api.routes import feedback_store

    bad = tmp_path / "feedback.db"
    _write_corrupt(bad, size=200)

    # Patch the resolver so the test DB path is the corrupted file.
    monkeypatch.setattr(feedback_store, "_DB_PATH", bad)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await feedback_store._get_conn()
    assert ei.value.status_code == 503
    assert "feedback_store_degraded" in str(ei.value.detail)
    assert registry.is_degraded("feedback")


@pytest.mark.asyncio
async def test_feedback_store_get_all_records_returns_503(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from openakita.api.routes import feedback_store

    bad = tmp_path / "feedback2.db"
    _write_corrupt(bad)
    monkeypatch.setattr(feedback_store, "_DB_PATH", bad)

    with pytest.raises(HTTPException) as ei:
        await feedback_store.get_all_records()
    assert ei.value.status_code == 503


# ---------------------------------------------------------------------------
# AssetBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_bus_init_degrades_on_corruption(tmp_path):
    from openakita.plugins.asset_bus import AssetBus

    bad = tmp_path / "asset_bus.db"
    _write_corrupt(bad)

    bus = AssetBus(bad)
    await bus.init()

    assert bus._degraded
    assert bus._db is None
    assert registry.is_degraded("asset_bus")


@pytest.mark.asyncio
async def test_asset_bus_publish_returns_placeholder_when_degraded(tmp_path):
    from openakita.plugins.asset_bus import AssetBus

    bad = tmp_path / "asset_bus_pub.db"
    _write_corrupt(bad)

    bus = AssetBus(bad)
    await bus.init()
    assert bus._degraded

    # publish returns a hex string (placeholder) and does NOT raise.
    asset_id = await bus.publish(plugin_id="testplug", asset_kind="video")
    assert isinstance(asset_id, str) and len(asset_id) >= 16

    # get() returns None (we cannot fetch the placeholder back).
    got = await bus.get(asset_id, requester_plugin_id="testplug")
    assert got is None

    # list_owned / count_all return empty / 0 — no crash.
    assert await bus.list_owned("testplug") == []
    assert await bus.count_all() == 0
    assert await bus.delete_owned(asset_id, "testplug") is False
    assert await bus.sweep_expired() == 0
    assert await bus.sweep_owner("testplug") == 0


@pytest.mark.asyncio
async def test_asset_bus_quiesce_idempotent(tmp_path):
    from openakita.plugins.asset_bus import AssetBus

    db = tmp_path / "asset_bus_quiesce.db"
    bus = AssetBus(db)
    await bus.init()
    assert bus._initialized
    assert not bus._degraded

    await bus.quiesce()
    assert bus._degraded
    assert bus._db is None

    # Idempotency — second call doesn't blow up.
    await bus.quiesce()
    assert bus._degraded


# ---------------------------------------------------------------------------
# storage/database.Database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_database_connect_raises_sqlite_unavailable_on_corruption(tmp_path):
    from openakita.storage.database import Database
    from openakita.storage.safe_sqlite import SQLiteUnavailable

    bad = tmp_path / "test_db.db"
    _write_corrupt(bad, size=200)

    db = Database(db_path=bad)
    with pytest.raises(SQLiteUnavailable) as ei:
        await db.connect()
    assert ei.value.reason == "corrupted"


@pytest.mark.asyncio
async def test_database_quiesce_idempotent(tmp_path):
    from openakita.storage.database import Database

    db = Database(db_path=tmp_path / "ok.db")
    await db.connect()
    assert db._connection is not None

    await db.quiesce()
    assert db._connection is None
    # Second call must not raise.
    await db.quiesce()


# ---------------------------------------------------------------------------
# MemoryStorage refactor — equivalence with previous behaviour
# ---------------------------------------------------------------------------


def test_memory_storage_init_still_raises_memorystorageunavailable_on_corruption(tmp_path):
    """The refactor must not change the externally-observed exception type."""
    from openakita.memory.exceptions import MemoryStorageUnavailable
    from openakita.memory.storage import MemoryStorage

    bad = tmp_path / "memory.db"
    _write_corrupt(bad)

    with pytest.raises(MemoryStorageUnavailable) as ei:
        MemoryStorage(bad, _register=False)
    assert ei.value.reason == "schema_corrupt"


def test_memory_storage_quiesce_async_wrapper(tmp_path):
    from openakita.memory.storage import MemoryStorage

    storage = MemoryStorage(tmp_path / "memory_ok.db", _register=False)
    assert storage._conn is not None

    asyncio.run(storage.quiesce())
    assert storage._conn is None


# ---------------------------------------------------------------------------
# Concurrent registration smoke (mirrors the daemon-thread / async / HTTP mix)
# ---------------------------------------------------------------------------


def test_concurrent_register_from_3_subsystems():
    """Token_tracking (thread) + asset_bus (event loop) + feedback (HTTP) all racing."""
    registry.clear()

    def thread_register():
        registry.register("token_tracking", "corrupted")

    async def async_register():
        registry.register("asset_bus", "corrupted")

    def http_register():
        registry.register("feedback", "corrupted")

    t1 = threading.Thread(target=thread_register)
    t2 = threading.Thread(target=lambda: asyncio.run(async_register()))
    t3 = threading.Thread(target=http_register)
    for t in (t1, t2, t3):
        t.start()
    for t in (t1, t2, t3):
        t.join()

    names = sorted(item["subsystem"] for item in registry.snapshot())
    assert names == ["asset_bus", "feedback", "token_tracking"]
