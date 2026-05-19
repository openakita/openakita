"""Cross-backend contract suite for v2 ProjectStore (P-RC-9 P9.2e).

Every test is parametrised over the two backend implementations
of :class:`ProjectStoreProtocol` --
:class:`JsonProjectStore` (default) and
:class:`SqliteProjectStore` (cross-process safe via WAL +
``BEGIN IMMEDIATE``). The full P9.2 contract suite is **18
cases x 2 backends = 36 collected tests**. To stay within the
380-LOC commit guard the file lands in two commits:

* P9.2e (this commit) -- cases 1..10 (read-back / IDs /
  recalc / delete) -> 20 collected tests.
* P9.2e2 -- cases 11..18 (malformed input / schema /
  concurrent / perf) -> 16 collected tests.

Same pattern as ``tests/runtime/orgs/test_blackboard_contract.py``
(P-RC-9 P9.1d) and ``tests/runtime/orgs/test_store_contract.py``
(P-RC-3 P3.5). If either backend fails any case the G-RC-9.2
mini-gate is BLOCKED: the whole point of the Protocol-typed
factory in P9.2c2 is that the two backends are observationally
indistinguishable.

10 cases this commit:

* read-back (empty / single / nested = 3)
* ID uniqueness (project + task = 2)
* recalc_progress (partial / complete / after-demote = 3)
* delete (leaf / subtree via recursion = 2)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from openakita.runtime.orgs.project_models import (
    OrgProject,
    ProjectTask,
    TaskStatus,
)
from openakita.runtime.orgs.project_store import (
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
