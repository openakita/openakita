"""Cross-backend contract suite for the v2 org store (P-RC-3 G-RC-3 gate).

Every test is parametrised over the two backends -- :class:Json
OrgStore (the default) and :class:SqliteOrgStore (P-RC-3 P3.4).
If either backend fails any case here, the G-RC-3 gate is BLOCKED:
the whole point of the pluggable factory in P3.6 is that the two
backends are observationally indistinguishable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from openakita.runtime.models import OrgV2, new_org_id
from openakita.runtime.orgs.sqlite_store import SqliteOrgStore
from openakita.runtime.orgs.store import JsonOrgStore, OrgNotFound

# Each "backend factory" returns a freshly opened store rooted under
# `tmp_path`. The closer is called by the fixture so we exercise the
# reopen-after-close case generically.
BackendFactory = Callable[[Path], object]
BackendCloser = Callable[[object], None]


def _json_factory(root: Path) -> JsonOrgStore:
    return JsonOrgStore(path=root / "orgs.json")


def _sqlite_factory(root: Path) -> SqliteOrgStore:
    return SqliteOrgStore(path=root / "orgs.sqlite")


def _json_close(store: object) -> None:
    pass  # JSON store has no explicit close; cache invalidates on reopen.


def _sqlite_close(store: object) -> None:
    store.close()  # type: ignore[attr-defined]


BACKENDS = [
    pytest.param(("json", _json_factory, _json_close), id="json"),
    pytest.param(("sqlite", _sqlite_factory, _sqlite_close), id="sqlite"),
]


def _mk_org(name: str = "Test", org_id: str | None = None) -> OrgV2:
    return OrgV2(
        id=org_id or new_org_id(),
        name=name,
        template_id="content_ops",
        description=None,
        nodes=[],
        edges=[],
    )


# --- contract cases -----------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_list_empty(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        assert store.list() == []
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_create_then_get(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        org = _mk_org("Alpha")
        store.create(org)
        assert store.get(org.id).name == "Alpha"
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_create_then_list_contains(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        a = _mk_org("A")
        b = _mk_org("B")
        store.create(a)
        store.create(b)
        assert {o.id for o in store.list()} == {a.id, b.id}
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_patch_fields(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        org = _mk_org("X")
        store.create(org)
        patched = store.patch(org.id, name="Y", description="d")
        assert patched.name == "Y"
        assert patched.description == "d"
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_delete_then_get_raises(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        org = _mk_org()
        store.create(org)
        store.delete(org.id)
        with pytest.raises(OrgNotFound):
            store.get(org.id)
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_delete_missing_raises(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        with pytest.raises(OrgNotFound):
            store.delete("org_missing")
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_create_duplicate_raises(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    try:
        org = _mk_org()
        store.create(org)
        with pytest.raises(ValueError, match="already exists"):
            store.create(org)
    finally:
        closer(store)


@pytest.mark.parametrize("backend", BACKENDS)
def test_idempotent_reopen(backend, tmp_path: Path) -> None:
    _name, factory, closer = backend
    store = factory(tmp_path)
    org = _mk_org("Persisted")
    store.create(org)
    closer(store)
    fresh = factory(tmp_path)
    try:
        assert {o.id for o in fresh.list()} == {org.id}
    finally:
        closer(fresh)


@pytest.mark.parametrize("backend", BACKENDS)
def test_concurrent_writes_smoke(backend, tmp_path: Path) -> None:
    """4 threads x 5 orgs = 20 rows visible to a fresh reader.

    The threads share one store instance per backend -- this is the
    contract both backends support (the JSON store's RLock + the
    SQLite store's BEGIN IMMEDIATE serialise writes inside the
    process). Cross-process concurrent writes are explicitly out of
    scope for the JSON backend and covered separately by the SQLite
    store's own multi-connection test.
    """
    _name, factory, closer = backend
    n_threads = 4
    n_per_thread = 5
    store = factory(tmp_path)
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(n_per_thread):
                store.create(
                    _mk_org(f"{prefix}_{i}", org_id=f"org_{prefix}_{i}")
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(chr(ord("a") + i),))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert not errors, f"workers errored: {errors}"
    assert len(store.list()) == n_threads * n_per_thread
    closer(store)
