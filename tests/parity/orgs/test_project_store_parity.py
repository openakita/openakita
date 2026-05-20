"""Parity fixtures for ProjectStore v2-baseline (P-RC-9 P9.9δ-2a; was P9.2d v1 oracle).

Each :class:`ParityCase` runs a scripted sequence against the v2
``openakita.runtime.orgs.project_store.JsonProjectStore`` (the
SQLite backend is contract-tested directly in
``tests/runtime/orgs/test_project_store_contract.py``) and
asserts the normalised :class:`ParityResult` equals the captured
golden dict in ``_golden_project_store.json``.

Per P-RC-9-P9.9 δ-2a (audit §6 Option B): this file shipped 6
v1-vs-v2 oracle cases in P9.2d. The v1 import / runner was
removed in δ-2a; the golden dicts were captured from the v2
output at HEAD ``a3a5fde6`` (close of δ-1).

Ignore set: ULID-prefix IDs (``id`` / ``project_id`` /
``parent_task_id`` / ``chain_id``) + timestamps (``created_at``
/ ``updated_at`` / ``started_at`` / ``delivered_at`` /
``completed_at``). The golden dict already has those keys
stripped via :func:`_norm_task` / :func:`_project_summary`.

Sentinel discipline (P-RC-9 §7.1): sentinel #2 stays ACTIVE
through G-RC-9.9; semantics shift from oracle-equality to
v2-baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.parity.harness import ParityCase, ParityResult

_GOLDEN: dict[str, dict] = json.loads(
    (Path(__file__).parent / "_golden_project_store.json").read_text(encoding="utf-8")
)


_ID_FIELDS = frozenset({"id", "project_id", "parent_task_id", "chain_id", "delegated_by"})
_TIME_FIELDS = frozenset({"created_at", "updated_at", "started_at", "delivered_at", "completed_at"})


def _norm_task(task_dict: dict, parent_idx: int | None) -> dict:
    out: dict = {}
    for k, v in task_dict.items():
        if k in _ID_FIELDS or k in _TIME_FIELDS:
            continue
        out[k] = v
    out["parent_idx"] = parent_idx
    return out


def _flatten_project(proj_dict: dict) -> list[dict]:
    tasks = proj_dict.get("tasks", [])
    id_to_idx: dict[str, int] = {t["id"]: i for i, t in enumerate(tasks) if "id" in t}
    out: list[dict] = []
    for t in tasks:
        parent_idx = id_to_idx.get(t.get("parent_task_id"))
        out.append(_norm_task(t, parent_idx))
    return out


def _project_summary(proj_dict: dict) -> dict:
    out: dict = {}
    for k, v in proj_dict.items():
        if k in _ID_FIELDS or k in _TIME_FIELDS or k == "tasks":
            continue
        out[k] = v
    out["tasks"] = _flatten_project(proj_dict)
    return out


def _list_projects_dict(store) -> list[dict]:
    return [p.to_dict() for p in store.list_projects()]


def _ps_v2(case: ParityCase, org_dir: Path) -> ParityResult:
    from openakita.runtime.orgs.project_models import (
        OrgProject,
        ProjectTask,
        TaskStatus,
    )
    from openakita.runtime.orgs.project_store import JsonProjectStore

    store = JsonProjectStore(org_dir)
    return _drive(store, OrgProject, ProjectTask, TaskStatus, case)


def _drive(store, Project, Task, TS, case: ParityCase) -> ParityResult:  # noqa: N803
    op = case.inputs["op"]
    if op == "create_empty":
        store.create_project(Project(name="Empty", org_id="o", description="d"))
        return ParityResult(
            final_message="empty",
            success=True,
            extras={
                "list_summary": [_project_summary(p) for p in _list_projects_dict(store)],
            },
        )

    if op == "create_single_task":
        p = store.create_project(Project(name="Single", org_id="o"))
        store.add_task(p.id, Task(title="T1", description="task one"))
        listed = _list_projects_dict(store)
        return ParityResult(
            final_message="single",
            success=True,
            extras={
                "list_summary": [_project_summary(x) for x in listed],
                "all_tasks_count": len(store.all_tasks()),
            },
        )

    if op == "create_nested_tree":
        p = store.create_project(Project(name="Tree", org_id="o"))
        prev: str | None = None
        for i in range(5):
            t = Task(title=f"depth-{i}", parent_task_id=prev)
            store.add_task(p.id, t)
            prev = t.id
        listed = _list_projects_dict(store)
        root = next(t for t in listed[0]["tasks"] if t.get("parent_task_id") is None)
        tree = store.get_task_tree(root["id"])

        def _depth(n: dict) -> int:
            if not n.get("children"):
                return 1
            return 1 + max(_depth(c) for c in n["children"])

        return ParityResult(
            final_message="nested",
            success=True,
            extras={
                "list_summary": [_project_summary(x) for x in listed],
                "tree_depth": _depth(tree),
            },
        )

    if op == "recalc_progress_partial":
        p = store.create_project(Project(name="Partial", org_id="o"))
        root = Task(title="root")
        store.add_task(p.id, root)
        leaves = [Task(title=f"L{i}", parent_task_id=root.id) for i in range(4)]
        for leaf in leaves:
            store.add_task(p.id, leaf)
        store.update_task(p.id, leaves[0].id, {"status": TS.ACCEPTED.value})
        new_pct = store.recalc_progress(root.id)
        listed = _list_projects_dict(store)
        return ParityResult(
            final_message="partial",
            success=True,
            extras={
                "recalc_value": new_pct,
                "list_summary": [_project_summary(x) for x in listed],
            },
        )

    if op == "recalc_progress_complete":
        p = store.create_project(Project(name="Complete", org_id="o"))
        root = Task(title="root")
        store.add_task(p.id, root)
        leaves = [Task(title=f"L{i}", parent_task_id=root.id) for i in range(3)]
        for leaf in leaves:
            store.add_task(p.id, leaf)
        for leaf in leaves:
            store.update_task(p.id, leaf.id, {"status": TS.ACCEPTED.value})
        new_pct = store.recalc_progress(root.id)
        listed = _list_projects_dict(store)
        return ParityResult(
            final_message="complete",
            success=True,
            extras={
                "recalc_value": new_pct,
                "list_summary": [_project_summary(x) for x in listed],
            },
        )

    if op == "delete_subtree":
        p = store.create_project(Project(name="Sub", org_id="o"))
        root = Task(title="root")
        store.add_task(p.id, root)
        mid = Task(title="mid", parent_task_id=root.id)
        store.add_task(p.id, mid)
        leaves = [Task(title=f"L{i}", parent_task_id=mid.id) for i in range(2)]
        for leaf in leaves:
            store.add_task(p.id, leaf)

        def _delete_recursive(task_id: str) -> int:
            removed = 0
            for child in list(store.get_subtasks(task_id)):
                removed += _delete_recursive(child.id)
            if store.delete_task(p.id, task_id):
                removed += 1
            return removed

        removed = _delete_recursive(mid.id)
        listed = _list_projects_dict(store)
        return ParityResult(
            final_message="delsub",
            success=True,
            extras={
                "removed_count": removed,
                "list_summary": [_project_summary(x) for x in listed],
                "remaining_root_count": len(store.all_tasks(root_only=True)),
            },
        )

    raise KeyError(f"unknown op: {op}")


CASES: list[ParityCase] = [
    ParityCase(
        id="project_create_empty",
        kind="project_store",
        inputs={"op": "create_empty"},
    ),
    ParityCase(
        id="project_create_single_task",
        kind="project_store",
        inputs={"op": "create_single_task"},
    ),
    ParityCase(
        id="project_create_nested_tree",
        kind="project_store",
        inputs={"op": "create_nested_tree"},
    ),
    ParityCase(
        id="project_recalc_progress_partial",
        kind="project_store",
        inputs={"op": "recalc_progress_partial"},
    ),
    ParityCase(
        id="project_recalc_progress_complete",
        kind="project_store",
        inputs={"op": "recalc_progress_complete"},
    ),
    ParityCase(
        id="project_delete_subtree",
        kind="project_store",
        inputs={"op": "delete_subtree"},
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_project_store_parity(case: ParityCase, tmp_path: Path) -> None:
    """v2-baseline JsonProjectStore contract (P-RC-9 P9.9δ-2a, 6 cases)."""
    v2_dir = tmp_path / "v2"
    v2_dir.mkdir()
    v2 = _ps_v2(case, v2_dir)
    expected = _GOLDEN[case.id]
    actual = dict(v2.to_compare())
    actual["tool_sequence"] = [list(t) for t in actual.get("tool_sequence", [])]
    assert actual == expected, f"v2-baseline drift on {case.id}: {actual} != {expected}"
