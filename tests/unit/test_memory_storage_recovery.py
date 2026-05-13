import inspect
import sqlite3
import time
from pathlib import Path

import pytest

from openakita.api.routes import health, memory_repair
from openakita.api.routes.memory_repair import _file_repair_lock, _memory_dir
from openakita.memory.exceptions import MemoryStorageUnavailable
from openakita.memory.manager import MemoryManager
from openakita.memory.noop_store import NoopUnifiedStore
from openakita.memory.storage import MemoryStorage
from openakita.memory.unified_store import UnifiedStore


def _write_corrupt_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a sqlite database")


def test_corrupt_db_raises_memory_storage_unavailable(tmp_path: Path):
    db = tmp_path / "openakita.db"
    _write_corrupt_db(db)

    with pytest.raises(MemoryStorageUnavailable) as exc:
        MemoryStorage(db)

    assert exc.value.reason in {"schema_corrupt", "unknown_db_error"}


def test_memory_manager_enters_degraded_mode_for_corrupt_db(tmp_path: Path):
    data_dir = tmp_path / "memory"
    _write_corrupt_db(data_dir / "openakita.db")
    memory_md = tmp_path / "MEMORY.md"

    manager = MemoryManager(data_dir=data_dir, memory_md_path=memory_md)

    assert manager.degraded is True
    assert isinstance(manager.store, NoopUnifiedStore)
    assert manager._memories == {}


def test_noop_unified_store_covers_public_unified_store_methods():
    missing = []
    for name, _member in inspect.getmembers(UnifiedStore, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        if not hasattr(NoopUnifiedStore, name):
            missing.append(name)

    assert missing == []


def test_migration_helpers_can_run_without_committing(tmp_path: Path):
    db = tmp_path / "openakita.db"
    storage = MemoryStorage(db)
    try:
        conn = sqlite3.connect(str(db), isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            storage._create_tables(conn, commit=False, include_fts=False)
            assert conn.in_transaction
            storage._set_schema_version(3, conn=conn, commit=False)
            assert conn.in_transaction
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    finally:
        storage.close()


def test_snapshot_uses_backup_api_and_prunes(tmp_path: Path):
    db = tmp_path / "openakita.db"
    storage = MemoryStorage(db)
    try:
        snap = storage.create_snapshot_incremental(keep=1)
        assert snap is not None
        assert snap.exists()
        with sqlite3.connect(str(snap)) as conn:
            assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        storage.close()


def test_stale_repair_lock_is_ignored(monkeypatch, tmp_path: Path):
    class Settings:
        data_dir = tmp_path / "data"

    monkeypatch.setattr(memory_repair, "settings", Settings())
    lock = _memory_dir() / ".repair.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("stale", encoding="utf-8")
    old = time.time() - 3600
    lock.touch()
    import os

    os.utime(lock, (old, old))

    with _file_repair_lock():
        assert lock.exists()

    assert not lock.exists()


def test_last_shutdown_marker_from_previous_clean_stop_is_clean(monkeypatch, tmp_path: Path):
    class Settings:
        project_root = tmp_path

    monkeypatch.setattr("openakita.config.settings", Settings())
    marker = tmp_path / "data" / "memory" / ".last_clean_shutdown"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        '{"ts": 1000, "pid": 123, "version": "x", "spawn_started_at": 500}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAKITA_SPAWN_STARTED_AT_MS", "2000")

    assert health._read_last_shutdown_marker()["status"] == "clean"
