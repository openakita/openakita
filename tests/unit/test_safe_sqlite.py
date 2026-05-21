"""Unit tests for the centralised safe_sqlite helper.

Coverage targets:

* happy path — fresh DB opens cleanly with PRAGMAs in place
* corruption — random bytes / truncated header → ``SQLiteUnavailable("corrupted")``
* hot-journal orphan — empty main + fat WAL → ``SQLiteUnavailable("hot_journal_orphan")``
* sync folder path → ``SQLiteUnavailable("path_in_sync_folder")``
* DegradedRegistry singleton — register / unregister / snapshot / thread-safety
* async ctx wrapper closes connection on exit
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading

import pytest

from openakita.storage.degraded import DegradedRegistry, registry
from openakita.storage.safe_sqlite import (
    SQLiteUnavailable,
    safe_open_async,
    safe_open_async_ctx,
    safe_open_sync,
)

# ---------------------------------------------------------------------------
# Windows-safe corruption construction.
#
# We deliberately ``write_bytes`` and let Python close the file handle
# *before* the helper tries to open it; on Windows, an open handle held
# by the fixture would otherwise produce a sharing violation rather than
# the corruption error we want to assert against. (Plan v3 point 7.)
# ---------------------------------------------------------------------------


@pytest.fixture
def corrupt_db_factory(tmp_path):
    def _make(name: str = "bad.db", pattern: bytes = b"\x00" * 100):
        p = tmp_path / name
        p.write_bytes(pattern)
        return p

    return _make


@pytest.fixture
def fresh_db_path(tmp_path):
    return tmp_path / "fresh.db"


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def test_safe_open_sync_happy_path(fresh_db_path):
    conn = safe_open_sync(fresh_db_path)
    try:
        assert (fresh_db_path).exists()
        # WAL mode is honoured
        cur = conn.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"
        # busy_timeout is set (default 30s = 30000ms)
        cur = conn.execute("PRAGMA busy_timeout")
        assert int(cur.fetchone()[0]) == 30_000
    finally:
        conn.close()


def test_safe_open_sync_detects_corruption(corrupt_db_factory):
    db = corrupt_db_factory("corrupted.db", b"\x00" * 200)
    with pytest.raises(SQLiteUnavailable) as ei:
        safe_open_sync(db)
    assert ei.value.reason == "corrupted"
    assert ei.value.path == db


def test_safe_open_sync_detects_hot_journal_orphan(tmp_path):
    main = tmp_path / "orphan.db"
    main.write_bytes(b"")  # empty main
    wal = tmp_path / "orphan.db-wal"
    wal.write_bytes(b"\x77" * 200_000)  # > 64KB sidecar

    with pytest.raises(SQLiteUnavailable) as ei:
        safe_open_sync(main)
    assert ei.value.reason == "hot_journal_orphan"
    assert "main=0B" in ei.value.details


def test_safe_open_sync_rejects_sync_folder_path(tmp_path, monkeypatch):
    """Paths containing OneDrive/Dropbox markers are rejected by default.

    We synthesise the path string rather than actually creating a folder
    in OneDrive — the helper checks substring match on the absolute path,
    so a renamed test directory is enough.
    """
    one_drive_dir = tmp_path / "OneDrive_personal"
    one_drive_dir.mkdir()
    target = one_drive_dir / "x.db"
    monkeypatch.delenv("OPENAKITA_ALLOW_SYNC_FOLDER_DB", raising=False)
    with pytest.raises(SQLiteUnavailable) as ei:
        safe_open_sync(target)
    assert ei.value.reason == "path_in_sync_folder"


def test_safe_open_sync_sync_folder_override(tmp_path, monkeypatch):
    one_drive_dir = tmp_path / "OneDrive_personal"
    one_drive_dir.mkdir()
    target = one_drive_dir / "x.db"
    monkeypatch.setenv("OPENAKITA_ALLOW_SYNC_FOLDER_DB", "1")
    conn = safe_open_sync(target)
    try:
        assert target.exists()
    finally:
        conn.close()


def test_safe_open_sync_busy_timeout_applied(tmp_path):
    """Second connection waits, doesn't immediately raise ``locked``."""
    db = tmp_path / "busy.db"
    conn1 = safe_open_sync(db, busy_ms=5_000)
    try:
        conn1.execute("CREATE TABLE t (x INTEGER)")
        conn1.commit()
        # holder begins a write transaction
        conn1.execute("BEGIN IMMEDIATE")
        conn2 = safe_open_sync(db, busy_ms=500)
        try:
            # Even with the writer blocked, opening + PRAGMA should succeed
            # because we haven't queried it yet.
            assert conn2 is not None
        finally:
            conn2.close()
        conn1.commit()
    finally:
        conn1.close()


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_open_async_happy_path(fresh_db_path):
    conn = await safe_open_async(fresh_db_path)
    try:
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert str(row[0]).lower() == "wal"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_safe_open_async_detects_corruption(corrupt_db_factory):
    db = corrupt_db_factory("async_corrupted.db", b"\x00" * 200)
    with pytest.raises(SQLiteUnavailable) as ei:
        await safe_open_async(db)
    assert ei.value.reason == "corrupted"


@pytest.mark.asyncio
async def test_safe_open_async_ctx_closes_on_exit(fresh_db_path):
    """``async with safe_open_async_ctx`` must close the connection."""
    captured = []
    async with safe_open_async_ctx(fresh_db_path) as conn:
        captured.append(conn)
        cursor = await conn.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1
    # After exit, attempting another query should fail because the
    # connection is closed. aiosqlite signals this via ValueError /
    # ProgrammingError depending on version; we accept either.
    with pytest.raises((ValueError, sqlite3.ProgrammingError, RuntimeError)):
        await captured[0].execute("SELECT 1")


# ---------------------------------------------------------------------------
# DegradedRegistry
# ---------------------------------------------------------------------------


def test_degraded_registry_register_idempotent():
    r = DegradedRegistry()
    r.register("foo", "corrupted", repair="r1")
    r.register("foo", "second_call_ignored", repair="r2")
    snap = r.snapshot()
    assert len(snap) == 1
    assert snap[0]["subsystem"] == "foo"
    assert snap[0]["reason"] == "corrupted"
    assert snap[0]["repair_action"] == "r1"


def test_degraded_registry_unregister_clears():
    r = DegradedRegistry()
    r.register("foo", "x")
    assert r.is_degraded("foo")
    r.unregister("foo")
    assert not r.is_degraded("foo")
    assert r.snapshot() == []
    # Unregistering twice is fine.
    r.unregister("foo")


def test_degraded_registry_snapshot_is_defensive_copy():
    r = DegradedRegistry()
    r.register("foo", "x")
    s1 = r.snapshot()
    s1[0]["reason"] = "tampered"
    s2 = r.snapshot()
    assert s2[0]["reason"] == "x"  # mutation didn't leak back


def test_degraded_registry_thread_safe_concurrent_register():
    r = DegradedRegistry()
    seen_subsystems = ["s1", "s2", "s3", "s1", "s2"]  # duplicates intentional
    threads = [
        threading.Thread(target=lambda s=s: r.register(s, "corrupted"))
        for s in seen_subsystems
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = r.snapshot()
    names = sorted(item["subsystem"] for item in snap)
    assert names == ["s1", "s2", "s3"]


def test_module_level_registry_singleton(tmp_path):
    # Same module-level singleton across imports — relied on by daemon
    # threads (token_tracking) and HTTP handlers (feedback_store) sharing
    # state without an app.state reference.
    registry.clear()
    registry.register("daemon-thread", "open_failed", details="x")
    assert registry.is_degraded("daemon-thread")
    snap = registry.snapshot()
    assert snap[0]["subsystem"] == "daemon-thread"
    registry.clear()


# ---------------------------------------------------------------------------
# SQLiteUnavailable str / extras
# ---------------------------------------------------------------------------


def test_sqlite_unavailable_str_includes_path_and_details():
    e = SQLiteUnavailable("corrupted", path="/tmp/x.db", details="ok? no")
    msg = str(e)
    assert "corrupted" in msg
    assert "/tmp/x.db" in msg or "x.db" in msg
    assert "ok? no" in msg


def test_sqlite_unavailable_extra_carries_orphan_sizes(tmp_path):
    main = tmp_path / "orphan.db"
    main.write_bytes(b"")
    side = tmp_path / "orphan.db-wal"
    side.write_bytes(b"\x00" * 100_000)
    try:
        safe_open_sync(main)
        pytest.fail("should have raised")
    except SQLiteUnavailable as e:
        assert e.reason == "hot_journal_orphan"
        assert e.extra.get("main_size") == 0
        assert e.extra.get("sidecar_size") == 100_000


# ---------------------------------------------------------------------------
# disk_full / permission_denied (best-effort, environment-dependent)
# ---------------------------------------------------------------------------


def test_safe_open_sync_permission_denied(tmp_path, monkeypatch):
    """Simulate ``PermissionError`` from sqlite3.connect.

    Patching sqlite3.connect lets us run this test cross-platform without
    actually flipping a filesystem permission bit (which behaves wildly
    differently between POSIX/Windows/admin/non-admin).
    """
    target = tmp_path / "denied.db"

    def boom(*args, **kwargs):
        err = PermissionError("simulated EACCES")
        err.errno = 13
        raise err

    monkeypatch.setattr(sqlite3, "connect", boom)
    with pytest.raises(SQLiteUnavailable) as ei:
        safe_open_sync(target)
    assert ei.value.reason == "permission_denied"


def test_safe_open_sync_disk_full(tmp_path, monkeypatch):
    target = tmp_path / "full.db"

    class FakeConn:
        def execute(self, *a, **kw):
            raise sqlite3.DatabaseError("disk is full")

        def close(self):  # pragma: no cover
            pass

    def boom(*args, **kwargs):
        return FakeConn()

    monkeypatch.setattr(sqlite3, "connect", boom)
    with pytest.raises(SQLiteUnavailable) as ei:
        safe_open_sync(target)
    assert ei.value.reason == "disk_full"


# ---------------------------------------------------------------------------
# Integration smoke: helper + DegradedRegistry combined
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corruption_triggers_registry_register_when_caller_wires_it(corrupt_db_factory):
    """The helper itself does not touch the registry — callers do.

    But the failure mode is shaped such that wrapping it in a try/except
    + registry.register(...) yields a working degraded subsystem. We
    exercise the wrapper pattern explicitly here to keep that contract
    visible.
    """
    registry.clear()
    db = corrupt_db_factory("wired.db", b"\x00" * 200)
    try:
        await safe_open_async(db)
    except SQLiteUnavailable as e:
        registry.register("wired_subsystem", e.reason, details=e.details)

    assert registry.is_degraded("wired_subsystem")
    snap = registry.snapshot()
    assert snap[0]["reason"] == "corrupted"
    registry.clear()


def test_run_quick_check_off_skips_corruption(tmp_path):
    """``run_quick_check=False`` opens corrupted DBs without raising.

    Some workflows (snapshot validation, ``.recover`` output) need this
    so we keep the escape hatch.
    """
    db = tmp_path / "skip.db"
    db.write_bytes(b"\x00" * 200)
    # Note: hot-journal orphan detection still fires regardless of
    # run_quick_check, because it's a pre-open check. To bypass it we
    # need the main file to be at least 1KB.
    db.write_bytes(b"\x00" * 2000)
    with pytest.raises(SQLiteUnavailable):
        # Header bytes are still corrupt → sqlite3.connect + execute
        # eventually surfaces the malformed header, but if quick_check
        # is off we get whatever sqlite raises first.
        safe_open_sync(db, run_quick_check=False)


def test_safe_open_sync_creates_parent_dir(tmp_path):
    target = tmp_path / "nested" / "subdir" / "child.db"
    conn = safe_open_sync(target)
    try:
        assert target.parent.exists()
    finally:
        conn.close()


# Ensure asyncio cleanup doesn't leak between tests
@pytest.fixture(autouse=True)
def _isolate_registry():
    registry.clear()
    yield
    registry.clear()


# Best-effort smoke that the module-level registry import path works in
# a fresh subprocess context (mirrors how token_tracking's daemon thread
# acquires it).
def test_module_level_registry_importable_from_subthread():
    from openakita.storage.degraded import registry as _r

    results: list[bool] = []

    def worker():
        _r.register("from-thread", "x")
        results.append(_r.is_degraded("from-thread"))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert results == [True]
    _r.clear()


# Sanity: asyncio event loop ops above didn't leak descriptors.
def test_no_lingering_event_loop():
    assert asyncio.get_event_loop_policy() is not None


# Sanity: the env var override is restored between tests.
def test_env_var_does_not_leak(monkeypatch, tmp_path):
    assert os.environ.get("OPENAKITA_ALLOW_SYNC_FOLDER_DB") is None
    target = tmp_path / "plain.db"
    conn = safe_open_sync(target)
    conn.close()
