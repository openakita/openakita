"""Regression coverage for command-level project task completion."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.orgs.runtime import OrgRuntime


class _ProjectStore:
    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self.tasks = tasks

    def all_tasks(
        self, *, chain_id: str | None = None, assignee: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            task
            for task in self.tasks
            if (chain_id is None or task.get("chain_id") == chain_id)
            and (assignee is None or task.get("assignee_node_id") == assignee)
        ]

    def update_task(self, project_id: str, task_id: str, updates: dict[str, Any]) -> None:
        task = next(task for task in self.tasks if task["id"] == task_id)
        task.update(updates)

    def find_task_by_chain(self, chain_id: str) -> dict[str, Any] | None:
        return next(
            (task for task in self.tasks if task.get("chain_id") == chain_id),
            None,
        )


class _ProjectRegistry:
    def __init__(self, store: _ProjectStore) -> None:
        self.store = store

    def for_org(self, org_id: str) -> _ProjectStore:
        return self.store


@pytest.mark.asyncio
async def test_stage_finish_does_not_close_command_level_root_task() -> None:
    command_task = {
        "id": "task-command",
        "project_id": "project-1",
        "chain_id": "cmd-1",
        "assignee_node_id": "root-1",
        "status": "in_progress",
        "progress_pct": 0,
    }
    store = _ProjectStore([command_task])
    runtime = object.__new__(OrgRuntime)
    runtime._contract_project_store = _ProjectRegistry(store)
    runtime._contract_blackboard = None

    await runtime._contract_event_tap(
        "agent_run_finished",
        {
            "org_id": "org-1",
            "command_id": "cmd-1",
            "node_id": "root-1",
            "chain_id": "turn-chain-1",
            "output_len": 100,
        },
    )

    assert command_task["status"] == "in_progress"
    assert command_task["progress_pct"] == 0


@pytest.mark.asyncio
async def test_stage_finish_still_closes_matching_non_command_task() -> None:
    child_task = {
        "id": "task-child",
        "project_id": "project-1",
        "chain_id": "child-chain",
        "assignee_node_id": "child-1",
        "status": "in_progress",
        "progress_pct": 0,
    }
    store = _ProjectStore([child_task])
    runtime = object.__new__(OrgRuntime)
    runtime._contract_project_store = _ProjectRegistry(store)
    runtime._contract_blackboard = None

    await runtime._contract_event_tap(
        "agent_run_finished",
        {
            "org_id": "org-1",
            "command_id": "cmd-1",
            "node_id": "child-1",
            "output_len": 100,
        },
    )

    assert str(child_task["status"]) == "delivered"
    assert child_task["progress_pct"] == 100


@pytest.mark.asyncio
async def test_command_terminal_closes_root_task_with_final_artifact(tmp_path) -> None:
    final_file = tmp_path / "final.md"
    final_file.write_text("final result", encoding="utf-8")
    command_task = {
        "id": "task-command",
        "project_id": "project-1",
        "chain_id": "cmd-1",
        "assignee_node_id": "root-1",
        "status": "in_progress",
        "progress_pct": 0,
    }
    store = _ProjectStore([command_task])
    runtime = object.__new__(OrgRuntime)
    runtime._contract_project_store = _ProjectRegistry(store)
    runtime._root_final_artifact = {"cmd-1": ("root-1", str(final_file))}

    async def _render(**kwargs: Any) -> None:
        return None

    runtime._maybe_render_root_pdf = _render

    runtime.finalize_command_project("org-1", "cmd-1", ok=True)
    await asyncio.sleep(0)

    assert str(command_task["status"]) == "delivered"
    assert command_task["progress_pct"] == 100
    assert command_task["file_attachments"] == [
        {
            "filename": "final.md",
            "file_path": str(final_file),
            "file_size": len("final result"),
        }
    ]
