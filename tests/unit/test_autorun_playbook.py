from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def make_task():
    """Build a mock ScheduledTask with metadata['playbook'] preset."""
    def _make(
        *,
        task_id: str = "tsk-abc",
        documents: list[dict] | None = None,
        prompt: str = "Do the next task.",
        loop_enabled: bool = False,
        max_loops: int | None = None,
        worktree: dict | None = None,
        agent_profile_id: str = "default",
    ):
        t = MagicMock()
        t.task_id = task_id
        t.agent_profile_id = agent_profile_id
        t.metadata = {
            "playbook": {
                "documents": documents or [{"filename": "/tmp/x.md", "reset_on_completion": False}],
                "prompt": prompt,
                "loop_enabled": loop_enabled,
                "max_loops": max_loops,
                "worktree": worktree or {"enabled": False},
            }
        }
        return t
    return _make


@pytest.fixture
def profile_store_mock():
    """Mock ProfileStore whose .get(profile_id) returns a truthy sentinel."""
    store = MagicMock()
    store.get = MagicMock(return_value=MagicMock(profile_id="default"))
    return store


@pytest.fixture
def agent_factory_mock():
    """Mock AgentFactory whose .create(profile) returns an AsyncMock agent.

    The agent mock exposes `execute_task_from_message` and `shutdown` as
    AsyncMock so tests can await them and inspect call_count. Individual
    tests that want the agent to flip checkboxes can set `.side_effect`
    on the method.
    """
    agent = AsyncMock()
    agent.execute_task_from_message = AsyncMock(return_value=None)
    agent.shutdown = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.create = AsyncMock(return_value=agent)
    return factory, agent


def test_playbook_spec_from_metadata_full():
    from openakita.scheduler.autorun_playbook import (
        MAX_CONSECUTIVE_NO_CHANGES,
        PlaybookDocumentSpec,
        PlaybookSpec,
        PlaybookState,
        PlaybookWorktreeSpec,
    )

    meta = {
        "playbook": {
            "documents": [
                {"filename": "/abs/backlog.md", "reset_on_completion": False},
                {"filename": "/abs/nightly.md", "reset_on_completion": True},
            ],
            "prompt": "do the next task",
            "loop_enabled": True,
            "max_loops": 3,
            "worktree": {
                "enabled": True,
                "branch_name_template": "autorun/{task_id}-{loop}",
                "create_pr_on_completion": False,
                "pr_target_branch": "main",
                "keep_on_failure": True,
                "project_root": "/abs/repo",
            },
        }
    }
    spec = PlaybookSpec.from_metadata(meta)
    assert spec.prompt == "do the next task"
    assert spec.loop_enabled is True
    assert spec.max_loops == 3
    assert spec.documents == (
        PlaybookDocumentSpec(filename="/abs/backlog.md", reset_on_completion=False),
        PlaybookDocumentSpec(filename="/abs/nightly.md", reset_on_completion=True),
    )
    assert spec.worktree == PlaybookWorktreeSpec(
        enabled=True,
        branch_name_template="autorun/{task_id}-{loop}",
        create_pr_on_completion=False,
        pr_target_branch="main",
        keep_on_failure=True,
        project_root="/abs/repo",
    )
    assert PlaybookState.INITIALIZING.value == "initializing"
    assert PlaybookState.RUNNING.value == "running"
    assert PlaybookState.STOPPING.value == "stopping"
    assert PlaybookState.COMPLETING.value == "completing"
    assert MAX_CONSECUTIVE_NO_CHANGES == 2


def test_playbook_spec_from_metadata_minimal():
    from openakita.scheduler.autorun_playbook import PlaybookSpec, PlaybookWorktreeSpec
    spec = PlaybookSpec.from_metadata({
        "playbook": {
            "documents": [{"filename": "/tmp/a.md"}],
            "prompt": "p",
        }
    })
    assert spec.loop_enabled is False
    assert spec.max_loops is None
    assert spec.worktree == PlaybookWorktreeSpec()
