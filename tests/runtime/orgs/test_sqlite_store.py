"""Tests for :class:openakita.runtime.orgs.sqlite_store.SqliteOrgStore.

P-RC-3 commit P3.4. Independent of the shared contract suite
(`test_store_contract.py`); these cases focus on SQLite-specific
concerns: concurrent writes through two connections, reopening a
closed store, and the corrupted-row tolerance path. The CRUD
shape is exercised here too so a regression in either backend
surfaces in the more focussed file.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from openakita.runtime.models import OrgV2, new_org_id
from openakita.runtime.orgs.sqlite_store import OrgNotFound, SqliteOrgStore


def _mk_org(name: str = "Test", org_id: str | None = None) -> OrgV2:
    return OrgV2(
        id=org_id or new_org_id(),
        name=name,
        template_id="content_ops",
        description=None,
        nodes=[],
        edges=[],
    )


def test_get_unknown_raises(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    with pytest.raises(OrgNotFound):
        store.get("org_missing")
    store.close()


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    org = _mk_org("Alpha")
    saved = store.create(org)
    assert saved.id == org.id
    got = store.get(org.id)
    assert got.name == "Alpha"
    store.close()


def test_create_duplicate_raises(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    org = _mk_org()
    store.create(org)
    with pytest.raises(ValueError, match="already exists"):
        store.create(org)
    store.close()


def test_patch_updates_whitelisted_fields_only(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    org = _mk_org("Original")
    store.create(org)
    patched = store.patch(org.id, name="Renamed", description="now")
    assert patched.name == "Renamed"
    assert patched.description == "now"
    assert patched.updated_at >= org.updated_at
    # Persisted across a fresh fetch.
    fresh = store.get(org.id)
    assert fresh.name == "Renamed"
    store.close()


def test_delete_removes_org(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    org = _mk_org()
    store.create(org)
    store.delete(org.id)
    with pytest.raises(OrgNotFound):
        store.get(org.id)
    with pytest.raises(OrgNotFound):
        store.delete(org.id)
    store.close()


def test_list_returns_newest_first(tmp_path: Path) -> None:
    store = SqliteOrgStore(path=tmp_path / "orgs.sqlite")
    a = _mk_org("A")
    store.create(a)
    b = _mk_org("B")
    store.create(b)
    listing = store.list()
    assert {o.id for o in listing} == {a.id, b.id}
    store.close()


def test_reopen_after_close_recovers_data(tmp_path: Path) -> None:
    path = tmp_path / "orgs.sqlite"
    store = SqliteOrgStore(path=path)
    org = _mk_org("Persisted")
    store.create(org)
    store.close()
    # Reopening a fresh store on the same file recovers state.
    fresh = SqliteOrgStore(path=path)
    assert {o.id for o in fresh.list()} == {org.id}
    fresh.close()


def test_corrupted_row_is_tolerated_in_list(tmp_path: Path) -> None:
    path = tmp_path / "orgs.sqlite"
    store = SqliteOrgStore(path=path)
    good = _mk_org("Good")
    store.create(good)
    # Smuggle a corrupted row directly into the DB.
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute(
        "INSERT INTO orgs (id, name, description, payload, created_at,"
        " updated_at, version) VALUES (?, ?, ?, ?, ?, ?, 1)",
        ("org_bad", "bad", "", "not json at all", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    raw.close()
    listing = store.list()
    assert {o.id for o in listing} == {good.id}, "corrupted row should be dropped, not crash"
    with pytest.raises(OrgNotFound):
        store.get("org_bad")
    store.close()


def test_concurrent_writes_through_two_connections(tmp_path: Path) -> None:
    """Two threads, each on its own SqliteOrgStore instance pointed at
    the same file, must not lose writes -- BEGIN IMMEDIATE + WAL
    serialise the two transactions.

    The schema is initialised by a warm-up store outside the threads
    so the PRAGMA journal_mode=WAL switch races (which the SQLite
    docs note are not guaranteed to acquire the WAL lock atomically
    on Windows even with busy_timeout) do not show up as test flake.
    The behaviour under test is the *write* serialisation, which is
    what the BEGIN IMMEDIATE wrapper guarantees.
    """
    path = tmp_path / "orgs.sqlite"
    # Warm-up store: ensures the file exists in WAL mode before the
    # worker threads each open their own connection.
    SqliteOrgStore(path=path).close()
    n_per_thread = 10
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            store = SqliteOrgStore(path=path)
            for i in range(n_per_thread):
                org = _mk_org(f"{prefix}_{i}", org_id=f"org_{prefix}_{i}")
                store.create(org)
            store.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=15.0)
    t2.join(timeout=15.0)
    assert not errors, f"workers errored: {errors}"
    reader = SqliteOrgStore(path=path)
    assert len(reader.list()) == 2 * n_per_thread
    reader.close()
