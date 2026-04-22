from __future__ import annotations

from pathlib import Path
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


def test_playbookrun_init_parses_spec(make_task, profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookRun, PlaybookState

    factory, _ = agent_factory_mock
    task = make_task(agent_profile_id="claude-code-pair")
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    assert run.task is task
    assert run.profile_id == "claude-code-pair"
    assert run.state == PlaybookState.INITIALIZING
    assert run.wt_info is None
    assert run._loop_iter == 0
    assert run.run_id.startswith("run-")
    assert len(run.run_id) == 12  # "run-" + 8 hex


def test_effective_path_no_reset_returns_source(tmp_path, make_task,
                                                profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookDocumentSpec, PlaybookRun

    factory, _ = agent_factory_mock
    src = tmp_path / "plan.md"
    src.write_text("- [ ] a\n")
    task = make_task(documents=[{"filename": str(src), "reset_on_completion": False}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    doc = PlaybookDocumentSpec(filename=str(src), reset_on_completion=False)
    assert run._effective_path(doc, loop_iter=0) == str(src)


def test_effective_path_reset_creates_working_copy(tmp_path, make_task,
                                                   profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookDocumentSpec, PlaybookRun

    factory, _ = agent_factory_mock
    src = tmp_path / "nightly.md"
    src.write_text("- [ ] generated\n")
    task = make_task(task_id="tsk-1",
                     documents=[{"filename": str(src), "reset_on_completion": True}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    doc = PlaybookDocumentSpec(filename=str(src), reset_on_completion=True)
    wc = Path(run._effective_path(doc, loop_iter=2))
    assert wc.exists()
    assert wc.parent.name == "tsk-1-loop2"
    assert wc.parent.parent.name == "Runs"
    assert wc.read_text() == "- [ ] generated\n"
    # Second call is idempotent: returns the same path, does NOT re-copy.
    wc.write_text("- [x] mutated\n")
    wc2 = Path(run._effective_path(doc, loop_iter=2))
    assert wc2 == wc
    assert wc2.read_text() == "- [x] mutated\n"


def test_reset_docs_filters_by_flag(make_task, profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    task = make_task(documents=[
        {"filename": "/a.md", "reset_on_completion": False},
        {"filename": "/b.md", "reset_on_completion": True},
        {"filename": "/c.md", "reset_on_completion": True},
    ])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    reset = run._reset_docs()
    assert [d.filename for d in reset] == ["/b.md", "/c.md"]


def test_refresh_doc_snapshot_counts_checkboxes(tmp_path, make_task,
                                                profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookDocumentSpec, PlaybookRun

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n- [x] b\n- [ ] c\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    doc = PlaybookDocumentSpec(filename=str(md))
    run._refresh_doc_snapshot(doc, md)
    snap = run._doc_snapshots[str(md)]
    assert snap == {"filename": str(md), "total": 3, "checked": 1, "stalled": False}


def test_refresh_doc_snapshot_preserves_stalled_flag(tmp_path, make_task,
                                                     profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookDocumentSpec, PlaybookRun

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    doc = PlaybookDocumentSpec(filename=str(md))
    run._doc_snapshots[str(md)] = {"filename": str(md), "total": 1,
                                   "checked": 0, "stalled": True}
    run._refresh_doc_snapshot(doc, md)
    assert run._doc_snapshots[str(md)]["stalled"] is True


def test_refresh_all_handles_missing_file(tmp_path, make_task,
                                          profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    missing = tmp_path / "nope.md"
    task = make_task(documents=[{"filename": str(missing)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    run._refresh_all_doc_snapshots(loop_iter=0)
    snap = run._doc_snapshots[str(missing)]
    assert snap == {"filename": str(missing), "total": 0, "checked": 0, "stalled": False}


def test_docs_snapshot_returns_list_in_doc_order(tmp_path, make_task,
                                                 profile_store_mock, agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    a = tmp_path / "a.md"
    a.write_text("- [ ] x\n")
    b = tmp_path / "b.md"
    b.write_text("- [x] y\n")
    task = make_task(documents=[{"filename": str(a)}, {"filename": str(b)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    run._refresh_all_doc_snapshots(loop_iter=0)
    snap = run._docs_snapshot()
    assert [d["filename"] for d in snap] == [str(a), str(b)]
    assert snap[0]["checked"] == 0
    assert snap[1]["checked"] == 1


@pytest.mark.asyncio
async def test_broadcast_emits_full_state(tmp_path, make_task, monkeypatch,
                                          profile_store_mock, agent_factory_mock):
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import PlaybookRun, PlaybookState

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n- [x] b\n")
    task = make_task(task_id="T1", documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    run.state = PlaybookState.RUNNING
    run._refresh_all_doc_snapshots(loop_iter=0)

    captured = AsyncMock()
    monkeypatch.setattr(ap, "broadcast_event", captured)
    await run._broadcast(active_doc=str(md), delta=1, loop_iter=3)

    captured.assert_awaited_once()
    event_name, payload = captured.await_args.args
    assert event_name == "autorun:state"
    assert payload["task_id"] == "T1"
    assert payload["run_id"].startswith("run-")
    assert payload["state"] == "running"
    assert payload["active_doc"] == str(md)
    assert payload["delta"] == 1
    assert payload["loop_iter"] == 3
    assert payload["error"] is None
    assert len(payload["docs"]) == 1
    assert payload["docs"][0]["checked"] == 1
    assert payload["docs"][0]["total"] == 2


@pytest.mark.asyncio
async def test_broadcast_defaults_to_none_fields(tmp_path, make_task, monkeypatch,
                                                 profile_store_mock, agent_factory_mock):
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    captured = AsyncMock()
    monkeypatch.setattr(ap, "broadcast_event", captured)
    await run._broadcast()

    _, payload = captured.await_args.args
    assert payload["active_doc"] is None
    assert payload["delta"] is None
    assert payload["error"] is None


def _make_flipping_agent(path: Path):
    """Return an AsyncMock agent whose execute_task_from_message flips one
    unchecked box per call (simulates the agent editing the file itself)."""
    async def _flip(_prompt):
        text = path.read_text()
        # Maestro regex parity: replace first "- [ ]" (or "* [ ]", with optional
        # whitespace inside the brackets) with "- [x]"
        import re
        text2, n = re.subn(r"^(\s*[-*]\s*)\[\s*\]", r"\1[x]", text, count=1, flags=re.MULTILINE)
        if n:
            path.write_text(text2)

    agent = AsyncMock()
    agent.execute_task_from_message = AsyncMock(side_effect=_flip)
    agent.shutdown = AsyncMock()
    return agent


@pytest.mark.asyncio
async def test_run_doc_pass_flips_all_boxes(tmp_path, make_task, monkeypatch,
                                            profile_store_mock, agent_factory_mock):
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import PlaybookRun, PlaybookState

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n- [ ] b\n- [ ] c\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    run.state = PlaybookState.RUNNING
    run._refresh_all_doc_snapshots(loop_iter=0)
    monkeypatch.setattr(ap, "broadcast_event", AsyncMock())

    agent = _make_flipping_agent(md)
    progressed = await run._run_doc_pass(agent, loop_iter=0)

    assert progressed is True
    assert agent.execute_task_from_message.await_count == 3
    assert md.read_text().count("[x]") == 3
    assert md.read_text().count("[ ]") == 0


@pytest.mark.asyncio
async def test_run_doc_pass_breaks_on_stall(tmp_path, make_task, monkeypatch,
                                            profile_store_mock, agent_factory_mock):
    """Agent that never flips a box — must break after MAX_CONSECUTIVE_NO_CHANGES."""
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import (
        MAX_CONSECUTIVE_NO_CHANGES,
        PlaybookRun,
        PlaybookState,
    )

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n- [ ] b\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    run.state = PlaybookState.RUNNING
    run._refresh_all_doc_snapshots(loop_iter=0)
    monkeypatch.setattr(ap, "broadcast_event", AsyncMock())

    noop_agent = AsyncMock()
    noop_agent.execute_task_from_message = AsyncMock(return_value=None)  # never edits

    progressed = await run._run_doc_pass(noop_agent, loop_iter=0)

    assert progressed is False
    assert noop_agent.execute_task_from_message.await_count == MAX_CONSECUTIVE_NO_CHANGES
    assert run._doc_snapshots[str(md)]["stalled"] is True


@pytest.mark.asyncio
async def test_run_doc_pass_honors_stopping(tmp_path, make_task, monkeypatch,
                                            profile_store_mock, agent_factory_mock):
    """If state flips to STOPPING mid-loop, the pass returns immediately."""
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import PlaybookRun, PlaybookState

    factory, _ = agent_factory_mock
    md = tmp_path / "x.md"
    md.write_text("- [ ] a\n- [ ] b\n")
    task = make_task(documents=[{"filename": str(md)}])
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    run.state = PlaybookState.STOPPING
    run._refresh_all_doc_snapshots(loop_iter=0)
    monkeypatch.setattr(ap, "broadcast_event", AsyncMock())

    agent = _make_flipping_agent(md)
    progressed = await run._run_doc_pass(agent, loop_iter=0)

    assert progressed is False
    agent.execute_task_from_message.assert_not_awaited()
    assert md.read_text().count("[ ]") == 2  # unchanged


@pytest.mark.asyncio
async def test_maybe_create_worktree_disabled(make_task, profile_store_mock,
                                              agent_factory_mock):
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    task = make_task(worktree={"enabled": False})
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)
    assert await run._maybe_create_worktree() is None


@pytest.mark.asyncio
async def test_maybe_create_worktree_enabled(make_task, monkeypatch,
                                             profile_store_mock, agent_factory_mock):
    from openakita.scheduler import autorun_playbook as ap
    from openakita.scheduler.autorun_playbook import PlaybookRun

    factory, _ = agent_factory_mock
    task = make_task(worktree={"enabled": True, "project_root": "/abs/repo"})
    run = PlaybookRun(task, executor=MagicMock(),
                     profile_store=profile_store_mock, agent_factory=factory)

    sentinel = MagicMock(name="WorktreeInfo")
    captured = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(ap, "create_agent_worktree", captured)

    wt = await run._maybe_create_worktree()
    assert wt is sentinel
    captured.assert_awaited_once_with(agent_id=run.run_id, project_root="/abs/repo")
