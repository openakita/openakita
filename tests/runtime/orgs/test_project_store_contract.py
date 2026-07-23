"""Cross-backend contract suite for v2 ProjectStore (P-RC-9 P9.2e).

Every test is parametrised over the two backend implementations
of :class:`ProjectStoreProtocol` --
:class:`JsonProjectStore` (default) and
:class:`SqliteProjectStore` (cross-process safe via WAL +
``BEGIN IMMEDIATE``). The full P9.2 contract suite is **18
cases x 2 backends = 36 collected tests**. To stay within the
380-LOC commit guard the file lands in two commits:

* P9.2e -- cases 1..10 (read-back / IDs / recalc /
  delete) -> 20 collected tests.
* P9.2e2 (this commit) -- cases 11..18 (malformed input /
  schema / concurrent / perf) -> 16 collected tests.

Same pattern as ``tests/runtime/orgs/test_blackboard_contract.py``
(P-RC-9 P9.1d) and ``tests/runtime/orgs/test_store_contract.py``
(P-RC-3 P3.5). If either backend fails any case the G-RC-9.2
mini-gate is BLOCKED: the whole point of the Protocol-typed
factory in P9.2c2 is that the two backends are observationally
indistinguishable.

All 18 cases (after P9.2e2):

* read-back (empty / single / nested = 3)
* ID uniqueness (project + task = 2)
* recalc_progress (partial / complete / after-demote = 3)
* delete (leaf / subtree via recursion = 2)
* malformed input (cycle walk / orphan = 2)
* backend-agnostic schema (to_dict shape / payload round-trip = 2)
* concurrent inserts no corruption (1)
* large-tree perf smoke (sampled adds / all_tasks under 500ms / depth 100 walk = 3)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from openakita.orgs.project_models import (
    OrgProject,
    ProjectTask,
    TaskStatus,
)
from openakita.orgs.project_store import (
    JsonProjectStore,
    ProjectStoreProtocol,
    SqliteProjectStore,
)

BackendFactory = Callable[[Path], ProjectStoreProtocol]


def _json_factory(root: Path) -> ProjectStoreProtocol:
    org = root / "json_store"
    org.mkdir(parents=True, exist_ok=True)
    return JsonProjectStore(org)


def _sqlite_factory(root: Path) -> ProjectStoreProtocol:
    return SqliteProjectStore(root / "store.sqlite")


BACKENDS = [
    pytest.param(("json", _json_factory), id="json"),
    pytest.param(("sqlite", _sqlite_factory), id="sqlite"),
]


def _store(backend_spec, tmp_path: Path) -> ProjectStoreProtocol:
    _name, factory = backend_spec
    return factory(tmp_path)


# ---------------------------------------------------------------------------
# 1. empty read-back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_empty_store_lists_empty(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        assert store.list_projects() == []
        assert store.get_project("does-not-exist") is None
        assert store.all_tasks() == []
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 2. single project + task round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_create_project_round_trip(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="P1", org_id="o", description="d"))
        t = ProjectTask(title="Task 1", description="td")
        store.add_task(p.id, t)
        listed = store.list_projects()
        assert len(listed) == 1
        proj = listed[0]
        assert proj.name == "P1"
        assert len(proj.tasks) == 1
        assert proj.tasks[0].title == "Task 1"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 3. nested tree persists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_create_nested_tree_persists(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="Tree", org_id="o"))
        root = ProjectTask(title="root")
        store.add_task(p.id, root)
        for i in range(3):
            child = ProjectTask(title=f"c{i}", parent_task_id=root.id)
            store.add_task(p.id, child)
        tree = store.get_task_tree(root.id)
        assert tree["title"] == "root"
        assert len(tree["children"]) == 3
        assert sorted(c["title"] for c in tree["children"]) == ["c0", "c1", "c2"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 4 / 5. ID uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_project_ids_unique(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        ids = {store.create_project(OrgProject(name=f"P{i}", org_id="o")).id for i in range(20)}
        assert len(ids) == 20
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_task_ids_unique_within_project(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="U", org_id="o"))
        ids: set[str] = set()
        for i in range(30):
            t = ProjectTask(title=f"T{i}")
            store.add_task(p.id, t)
            ids.add(t.id)
        assert len(ids) == 30
        proj = store.get_project(p.id)
        assert proj is not None and len(proj.tasks) == 30
    finally:
        store.close()


# ---------------------------------------------------------------------------
# P4 阶段C: user acceptance auto-stamps completed_at on ACCEPTED, and
# delivery auto-stamps delivered_at -> the Gantt bar gets a real end.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_accept_and_deliver_stamp_timestamps(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="A", org_id="o"))
        task = ProjectTask(title="deliverable")
        store.add_task(p.id, task)
        # delivery -> delivered_at populated, completed_at still empty
        store.update_task(p.id, task.id, {"status": TaskStatus.DELIVERED.value})
        _t, _p = store.get_task(task.id)
        assert _t is not None
        assert _t.delivered_at  # auto-stamped on delivery
        assert not _t.completed_at  # acceptance not yet done
        # user 验收 -> ACCEPTED auto-stamps completed_at
        store.update_task(p.id, task.id, {"status": TaskStatus.ACCEPTED.value})
        _t2, _ = store.get_task(task.id)
        assert _t2 is not None
        assert _t2.status == TaskStatus.ACCEPTED
        assert _t2.completed_at  # acceptance finish time recorded
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 6 / 7 / 8. recalc_progress
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_recalc_partial(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="R", org_id="o"))
        root = ProjectTask(title="root")
        store.add_task(p.id, root)
        leaves = [ProjectTask(title=f"L{i}", parent_task_id=root.id) for i in range(4)]
        for leaf in leaves:
            store.add_task(p.id, leaf)
        store.update_task(p.id, leaves[0].id, {"status": TaskStatus.ACCEPTED.value})
        assert store.recalc_progress(root.id) == 25  # (100 + 0 + 0 + 0) // 4
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_recalc_complete(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="R", org_id="o"))
        root = ProjectTask(title="root")
        store.add_task(p.id, root)
        leaves = [ProjectTask(title=f"L{i}", parent_task_id=root.id) for i in range(3)]
        for leaf in leaves:
            store.add_task(p.id, leaf)
            store.update_task(p.id, leaf.id, {"status": TaskStatus.ACCEPTED.value})
        assert store.recalc_progress(root.id) == 100
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_recalc_after_demote(backend_spec, tmp_path: Path) -> None:
    """Re-running recalc after demoting a child yields the lower value."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="R", org_id="o"))
        root = ProjectTask(title="root")
        store.add_task(p.id, root)
        leaves = [ProjectTask(title=f"L{i}", parent_task_id=root.id) for i in range(2)]
        for leaf in leaves:
            store.add_task(p.id, leaf)
        for leaf in leaves:
            store.update_task(p.id, leaf.id, {"status": TaskStatus.ACCEPTED.value})
        assert store.recalc_progress(root.id) == 100
        store.update_task(
            p.id,
            leaves[1].id,
            {"status": TaskStatus.IN_PROGRESS.value, "progress_pct": 30},
        )
        assert store.recalc_progress(root.id) == 65  # (100 + 30) // 2
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 9 / 10. delete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_delete_leaf(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="D", org_id="o"))
        t1 = ProjectTask(title="keep")
        t2 = ProjectTask(title="drop")
        store.add_task(p.id, t1)
        store.add_task(p.id, t2)
        assert store.delete_task(p.id, t2.id) is True
        assert store.delete_task(p.id, "does-not-exist") is False
        proj = store.get_project(p.id)
        assert proj is not None and {t.title for t in proj.tasks} == {"keep"}
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_delete_subtree_via_recursion(backend_spec, tmp_path: Path) -> None:
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="DS", org_id="o"))
        root = ProjectTask(title="root")
        mid = ProjectTask(title="mid")
        store.add_task(p.id, root)
        store.add_task(p.id, mid)
        store.update_task(p.id, mid.id, {"parent_task_id": root.id})
        leaves = [ProjectTask(title=f"L{i}", parent_task_id=mid.id) for i in range(2)]
        for leaf in leaves:
            store.add_task(p.id, leaf)

        def _recursive_delete(task_id: str) -> int:
            removed = 0
            for child in list(store.get_subtasks(task_id)):
                removed += _recursive_delete(child.id)
            if store.delete_task(p.id, task_id):
                removed += 1
            return removed

        removed = _recursive_delete(mid.id)
        assert removed == 3  # mid + 2 leaves
        proj = store.get_project(p.id)
        assert proj is not None and [t.title for t in proj.tasks] == ["root"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 11 / 12. malformed input handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_cycle_in_parents_is_walked_safely(backend_spec, tmp_path: Path) -> None:
    """If a cycle ever creeps in (corrupted data), get_ancestors must terminate."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="C", org_id="o"))
        a = ProjectTask(title="a")
        b = ProjectTask(title="b")
        store.add_task(p.id, a)
        store.add_task(p.id, b)
        store.update_task(p.id, a.id, {"parent_task_id": b.id})
        store.update_task(p.id, b.id, {"parent_task_id": a.id})
        ancestors = store.get_ancestors(a.id)
        # Cycle guard kicks in after one hop (either {b} or {b, a}).
        assert len(ancestors) <= 2
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_orphan_task_remains_orphan(backend_spec, tmp_path: Path) -> None:
    """A task whose parent_task_id points at a missing id is still listed."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="O", org_id="o"))
        store.add_task(p.id, ProjectTask(title="orphan", parent_task_id="missing"))
        rows = store.all_tasks()
        assert len(rows) == 1
        assert rows[0]["parent_task_id"] == "missing"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 13 / 14. backend-agnostic schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_to_dict_shape_matches_canonical(backend_spec, tmp_path: Path) -> None:
    """Both backends emit the same OrgProject.to_dict() field set."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="S", org_id="o"))
        store.add_task(p.id, ProjectTask(title="t"))
        proj = store.list_projects()[0]
        d = proj.to_dict()
        expected = {
            "id",
            "org_id",
            "name",
            "description",
            "project_type",
            "status",
            "owner_node_id",
            "tasks",
            "created_at",
            "updated_at",
            "completed_at",
        }
        assert set(d.keys()) == expected
        t = d["tasks"][0]
        for required in (
            "id",
            "project_id",
            "title",
            "status",
            "depth",
            "progress_pct",
        ):
            assert required in t
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_payload_round_trip_via_from_dict(backend_spec, tmp_path: Path) -> None:
    """Project survives to_dict -> from_dict -> create_project unchanged."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="X", org_id="o"))
        store.add_task(p.id, ProjectTask(title="T1"))
        store.add_task(p.id, ProjectTask(title="T2"))
        d = store.list_projects()[0].to_dict()
        rebuilt = OrgProject.from_dict(d)
        assert rebuilt.name == "X"
        assert [t.title for t in rebuilt.tasks] == ["T1", "T2"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 15. concurrent inserts no corruption (v2 strict)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_concurrent_add_task_no_loss(backend_spec, tmp_path: Path) -> None:
    """2 threads x 5 add_task each -> exactly 10 tasks survive.

    v1 ProjectStore would race on the shared in-memory list under
    the same load (its lock only covers ``_save``). v2 takes the
    RLock across the whole read-modify-write window for the JSON
    backend and uses ``BEGIN IMMEDIATE`` for SQLite, so both
    backends are loss-free. Parallels the
    P9.1 blackboard contract case 12.
    """
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="C", org_id="o"))
        errors: list[BaseException] = []

        def worker(prefix: str) -> None:
            try:
                for i in range(5):
                    store.add_task(p.id, ProjectTask(title=f"{prefix}_{i}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c,)) for c in ("a", "b")]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)
        assert not errors
        proj = store.get_project(p.id)
        assert proj is not None
        assert len(proj.tasks) == 10
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 16 / 17 / 18. large-tree perf smoke (budget envelope)
# ---------------------------------------------------------------------------

_LARGE_N = 1000
_JSON_ADD_N = 250
# The 0.5 s target in the charter is for the all_tasks query path.
# Sequential JSON adds rewrite the entire file and are O(N**2), so use a
# representative smoke sample there; SQLite remains linear and covers 1000.
_PERF_QUERY_LIMIT_S = 1.0  # 500 ms target + 2x Windows CI slack
_PERF_ADD_LIMIT_S = 20.0


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_perf_add_tasks(backend_spec, tmp_path: Path) -> None:
    """Sequential task insertion completes within the backend's envelope."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="Big", org_id="o"))
        backend_name, _factory = backend_spec
        task_count = _JSON_ADD_N if backend_name == "json" else _LARGE_N
        t0 = time.perf_counter()
        for i in range(task_count):
            store.add_task(p.id, ProjectTask(title=f"t{i}"))
        elapsed = time.perf_counter() - t0
        assert elapsed < _PERF_ADD_LIMIT_S, (
            f"add_task x{task_count} took {elapsed:.2f}s; budget {_PERF_ADD_LIMIT_S}s"
        )
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_perf_all_tasks_under_500ms(backend_spec, tmp_path: Path) -> None:
    """Querying all_tasks across a 1000-task project is sub-500 ms (2x CI slack)."""
    store = _store(backend_spec, tmp_path)
    try:
        tasks = [ProjectTask(title=f"t{i}") for i in range(_LARGE_N)]
        store.create_project(OrgProject(name="Q", org_id="o", tasks=tasks))
        t0 = time.perf_counter()
        rows = store.all_tasks()
        elapsed = time.perf_counter() - t0
        assert len(rows) == _LARGE_N
        assert elapsed < _PERF_QUERY_LIMIT_S, (
            f"all_tasks took {elapsed:.3f}s (target {_PERF_QUERY_LIMIT_S}s)"
        )
    finally:
        store.close()


@pytest.mark.parametrize("backend_spec", BACKENDS)
def test_perf_deep_tree_walk(backend_spec, tmp_path: Path) -> None:
    """get_task_tree on a 100-deep chain walks without stack issues."""
    store = _store(backend_spec, tmp_path)
    try:
        p = store.create_project(OrgProject(name="D", org_id="o"))
        prev: str | None = None
        first_id: str | None = None
        for i in range(100):
            t = ProjectTask(title=f"d{i}", parent_task_id=prev)
            store.add_task(p.id, t)
            if first_id is None:
                first_id = t.id
            prev = t.id
        assert first_id is not None
        t0 = time.perf_counter()
        tree = store.get_task_tree(first_id)
        elapsed = time.perf_counter() - t0
        assert elapsed < _PERF_QUERY_LIMIT_S

        depth = 0
        node = tree
        while node:
            depth += 1
            children = node.get("children", [])
            node = children[0] if children else None
        assert depth == 100
    finally:
        store.close()
