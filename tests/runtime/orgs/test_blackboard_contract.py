"""Cross-backend contract suite for OrgBlackboard (P-RC-9 P9.1d).

Every test is parametrised over the two
:class:`BlackboardBackendProtocol` implementations --
:class:`JsonFileBlackboardBackend` (default) and
:class:`SqliteBlackboardBackend` (multi-process safe). If either
backend fails any case here, the G-RC-9.1 mini-gate is
BLOCKED: the whole point of the Protocol-typed factory in
P9.1b2 is that the two backends are observationally
indistinguishable.

Same pattern as ``tests/runtime/orgs/test_store_contract.py``
(P-RC-3 P3.5) -- two BACKENDS fixtures, every test takes
``backend`` as a param and constructs a fresh backend under
``tmp_path``.

12 contract cases covering:

* empty / round-trip reads (1 + 3 per scope = 4)
* eviction (cap + importance-ordering = 2)
* duplicate detection (1)
* ttl expiry skip on read (1)
* delete by id (found + missing = 2)
* clear wipes all scopes (1)
* all_for_scope enumerates owners (1)
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openakita.orgs.blackboard import (
    MAX_ORG_MEMORIES,
    BlackboardBackendProtocol,
    JsonFileBlackboardBackend,
    OrgBlackboard,
    SqliteBlackboardBackend,
)
from openakita.orgs.memory_models import (
    MemoryScope,
    MemoryType,
    OrgMemoryEntry,
)

BackendFactory = Callable[[Path, str], BlackboardBackendProtocol]


def _json_factory(root: Path, org_id: str) -> BlackboardBackendProtocol:
    return JsonFileBlackboardBackend(root / "json_bb", org_id)


def _sqlite_factory(root: Path, org_id: str) -> BlackboardBackendProtocol:
    return SqliteBlackboardBackend(root / "bb.sqlite", org_id)


BACKENDS = [
    pytest.param(("json", _json_factory), id="json"),
    pytest.param(("sqlite", _sqlite_factory), id="sqlite"),
]


def _bb(backend: BlackboardBackendProtocol, tmp_path: Path, org_id: str) -> OrgBlackboard:
    return OrgBlackboard(tmp_path, org_id, backend=backend)


def _make_entry(*, content: str, importance: float = 0.5, **overrides) -> OrgMemoryEntry:
    base = {
        "org_id": "org_contract",
        "scope": MemoryScope.ORG,
        "scope_owner": "org_contract",
        "memory_type": MemoryType.FACT,
        "content": content,
        "source_node": "node_x",
        "importance": importance,
    }
    base.update(overrides)
    return OrgMemoryEntry(**base)


# ---------------------------------------------------------------------------
# 1. empty read returns []
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_read_empty_returns_empty_list(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        assert bb.read_org() == []
        assert bb.read_department("eng") == []
        assert bb.read_node("node_x") == []
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 2 / 3 / 4. write -> read round-trip per scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_round_trip_org(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_org("hello", "node_a", MemoryType.FACT, tags=["t1"])
        rows = bb.read_org()
        assert len(rows) == 1
        assert rows[0].content == "hello"
        assert rows[0].memory_type == MemoryType.FACT
        assert rows[0].tags == ["t1"]
        assert rows[0].scope == MemoryScope.ORG
    finally:
        backend.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_round_trip_dept(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_department("eng", "dept fact", "node_b", MemoryType.DECISION)
        rows = bb.read_department("eng")
        assert len(rows) == 1
        assert rows[0].scope == MemoryScope.DEPARTMENT
        assert rows[0].scope_owner == "eng"
        assert rows[0].memory_type == MemoryType.DECISION
    finally:
        backend.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_round_trip_node(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_node("node_c", "private note", MemoryType.PROGRESS, tags=["a", "b"])
        rows = bb.read_node("node_c")
        assert len(rows) == 1
        assert rows[0].scope == MemoryScope.NODE
        assert rows[0].scope_owner == "node_c"
        assert sorted(rows[0].tags) == ["a", "b"]
    finally:
        backend.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_node_and_dept_carry_attachments(backend_spec, tmp_path: Path) -> None:
    """Cross-session replay fix: node/dept tiers accept ``attachments`` so a
    deliverable record mirrored into the node tier keeps its downloadable file."""
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    att = [{"filename": "report.md", "path": "/x/report.md", "size_bytes": 1234}]
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_node("node_c", "节点 node_c 完成交付（500 字）", tags=["deliverable"], attachments=att)
        bb.write_department(
            "eng", "节点 node_c 完成交付（500 字）", "node_c", tags=["deliverable"], attachments=att
        )
        n = bb.read_node("node_c")
        d = bb.read_department("eng")
        assert len(n) == 1 and n[0].attachments and n[0].attachments[0]["filename"] == "report.md"
        assert len(d) == 1 and d[0].attachments and d[0].attachments[0]["size_bytes"] == 1234
    finally:
        backend.close()


def test_json_node_tier_replays_across_fresh_instances(tmp_path: Path) -> None:
    """The on-disk JSON backend (production default) replays node-tier records
    in a brand-new instance over the same dir -- i.e. ``/memory?scope=node``
    survives a backend restart / new session."""
    org_dir = tmp_path / "orgs" / "org_x"
    bb1 = OrgBlackboard(org_dir, "org_x")  # default JSON backend
    bb1.write_node("data-analyst", "节点 data-analyst 完成交付（800 字）", tags=["deliverable"])
    # A fresh instance (mimics a new process/session) reads the same disk.
    bb2 = OrgBlackboard(org_dir, "org_x")
    rows = bb2.query(scope=MemoryScope.NODE)
    assert any(r.scope_owner == "data-analyst" for r in rows)
    assert any("完成交付" in r.content for r in rows)


# ---------------------------------------------------------------------------
# 5 / 6. eviction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_eviction_caps_org_at_max(backend_spec, tmp_path: Path) -> None:
    """Writing more than MAX_ORG_MEMORIES keeps the cap honoured."""
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        # Write 220 rows with unique content (dup-detect is per content).
        for i in range(MAX_ORG_MEMORIES + 20):
            bb.write_org(f"row {i}", "node_a", importance=0.01 + (i / 1000.0))
        rows = bb.read_org(limit=10_000)
        assert len(rows) <= MAX_ORG_MEMORIES
    finally:
        backend.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_eviction_keeps_top_importance(backend_spec, tmp_path: Path) -> None:
    """After eviction, the highest-importance row survives."""
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        # Write 30 rows; the last has importance 1.0 (the max).
        for i in range(30):
            bb.write_org(f"row {i}", "node_a", importance=i / 30.0)
        bb.write_org("THE TOP ROW", "node_a", importance=1.0)
        rows = bb.read_org(limit=1)
        assert len(rows) == 1
        assert rows[0].content == "THE TOP ROW"
        assert rows[0].importance == 1.0
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 7. duplicate detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_is_duplicate_detects_prefix_match(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        first = bb.write_org("same content body", "node_a", MemoryType.FACT)
        second = bb.write_org("same content body", "node_a", MemoryType.FACT)
        assert first is not None
        assert second is None
        assert len(bb.read_org()) == 1
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 8. ttl expiry skip on read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_ttl_expired_skipped_on_read(backend_spec, tmp_path: Path) -> None:
    """Entry with past created_at + small ttl_hours is filtered on read."""
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        # Inject an already-expired entry directly through the backend.
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        expired = _make_entry(content="stale row", importance=0.99)
        expired.ttl_hours = 1
        expired.created_at = past
        backend.append(
            MemoryScope.ORG, "org_contract", expired, max_entries=MAX_ORG_MEMORIES
        )
        fresh = _make_entry(content="fresh row", importance=0.5)
        backend.append(
            MemoryScope.ORG, "org_contract", fresh, max_entries=MAX_ORG_MEMORIES
        )
        bb = _bb(backend, tmp_path, "org_contract")
        rows = bb.read_org(limit=999)
        contents = [r.content for r in rows]
        assert "fresh row" in contents
        assert "stale row" not in contents
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 9 / 10. delete_by_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_delete_by_id_removes_entry(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        target = bb.write_org("to be deleted", "node_a", MemoryType.FACT)
        assert target is not None
        assert bb.delete_entry(target.id) is True
        assert bb.read_org() == []
    finally:
        backend.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_delete_by_id_missing_returns_false(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        assert bb.delete_entry("mem_doesnotexist") is False
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 11. clear wipes all scopes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_clear_wipes_all_scopes(backend_spec, tmp_path: Path) -> None:
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_org("o", "node_a", MemoryType.FACT)
        bb.write_department("eng", "d", "node_b", MemoryType.FACT)
        bb.write_node("node_c", "n", MemoryType.FACT)
        bb.clear()
        assert backend.all_for_scope(MemoryScope.ORG) == []
        assert backend.all_for_scope(MemoryScope.DEPARTMENT) == []
        assert backend.all_for_scope(MemoryScope.NODE) == []
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# 12. all_for_scope enumerates owners + concurrent-writes safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_all_for_scope_and_concurrent_writes(backend_spec, tmp_path: Path) -> None:
    """all_for_scope returns every owner''s rows; concurrent writes don''t corrupt."""
    _name, factory = backend_spec
    backend = factory(tmp_path, "org_contract")
    try:
        bb = _bb(backend, tmp_path, "org_contract")
        bb.write_department("eng", "eng row", "n1", MemoryType.FACT)
        bb.write_department("ops", "ops row", "n1", MemoryType.FACT)
        # Both owners visible via all_for_scope (no owner filter).
        rows = backend.all_for_scope(MemoryScope.DEPARTMENT)
        owners = sorted(r.scope_owner for r in rows)
        assert owners == ["eng", "ops"]
        # Owner-narrowed query returns only eng.
        eng_rows = backend.all_for_scope(MemoryScope.DEPARTMENT, owner="eng")
        assert [r.scope_owner for r in eng_rows] == ["eng"]
        # 2 simulated tasks doing concurrent writes don''t corrupt state.
        errors: list[BaseException] = []

        def worker(prefix: str) -> None:
            try:
                for i in range(5):
                    bb.write_node(
                        "shared_node",
                        f"{prefix}_{i}",
                        MemoryType.PROGRESS,
                        importance=0.5,
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(c,)) for c in ("a", "b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        assert not errors
        node_rows = bb.read_node("shared_node", limit=999)
        assert len(node_rows) == 10  # 2 workers * 5 writes each
    finally:
        backend.close()
