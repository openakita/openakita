from __future__ import annotations

from types import SimpleNamespace

import pytest

import openakita.runtime.context.continuity as continuity
from openakita.agent.context import ContextManager
from openakita.memory.storage import _SCHEMA_VERSION, MemoryStorage
from openakita.memory.unified_store import UnifiedStore
from openakita.runtime.context.continuity import (
    CompactionContribution,
    ContextEpoch,
    capture_workspace_snapshot,
    content_digest,
)
from openakita.sessions.session import SessionContext


def _manager() -> ContextManager:
    return ContextManager(SimpleNamespace(model="test-model"))


def test_context_epoch_is_stable_and_tracks_privileged_inputs() -> None:
    first = ContextEpoch.build(system_prompt="rules", tools=[{"name": "read"}], model="m")
    same = ContextEpoch.build(system_prompt="rules", tools=[{"name": "read"}], model="m")
    changed = ContextEpoch.build(system_prompt="new rules", tools=[{"name": "read"}], model="m")

    assert first.digest == same.digest
    assert first.digest != changed.digest


def test_continuity_storage_round_trip(tmp_path) -> None:
    store = UnifiedStore(tmp_path / "memory.db")
    epoch = ContextEpoch.build(system_prompt="rules", tools=[], model="m").to_dict()
    assert store.save_context_epoch("s1", epoch) == epoch["digest"]
    assert store.get_latest_context_epoch("s1")["digest"] == epoch["digest"]

    blob_id = store.save_tool_output_blob("s1", "shell", "large output" * 1000)
    assert store.get_tool_output_blob(blob_id) == "large output" * 1000
    assert store.get_tool_output_blob(blob_id, session_id="other") is None

    snapshot = capture_workspace_snapshot(tmp_path, session_id="s1")
    assert snapshot is not None
    snapshot_id = store.save_workspace_snapshot(snapshot.to_dict())
    loaded_snapshot = store.get_workspace_snapshot(snapshot_id)
    assert loaded_snapshot["root"] == str(tmp_path.resolve())
    assert loaded_snapshot["capture_status"] in {"not_a_repository", "git_unavailable"}
    assert loaded_snapshot["capture_error"]

    checkpoint = {
        "id": "checkpoint-1",
        "session_id": "s1",
        "status": "completed",
        "source_digest": "abc",
        "source_message_count": 2,
        "summary": "anchored summary",
        "recent_messages": [{"role": "user", "content": "recent"}],
        "projected_messages": [{"role": "user", "content": "projected"}],
        "tail_start_index": 1,
        "tokens_before": 100,
        "tokens_after": 20,
        "epoch_digest": epoch["digest"],
        "workspace_snapshot_id": snapshot_id,
        "contributions": [{"name": "plan"}],
        "model": "m",
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:00:01",
        "error": "",
    }
    store.save_compaction_checkpoint(checkpoint)
    loaded = store.get_latest_completed_compaction("s1")
    assert loaded["summary"] == "anchored summary"
    assert loaded["recent_messages"][0]["content"] == "recent"
    assert loaded["projected_messages"][0]["content"] == "projected"
    assert loaded["workspace_snapshot_id"] == snapshot_id


def test_workspace_snapshot_reports_missing_git(tmp_path, monkeypatch) -> None:
    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(continuity.subprocess, "run", missing_git)

    snapshot = capture_workspace_snapshot(tmp_path, session_id="s1")

    assert snapshot is not None
    assert snapshot.capture_status == "git_unavailable"
    assert snapshot.capture_error == "git executable was not found"
    assert snapshot.vcs == ""


def test_workspace_snapshot_reports_non_repository(tmp_path, monkeypatch) -> None:
    def not_a_repository(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository (or any parent up to mount point)",
        )

    monkeypatch.setattr(continuity.subprocess, "run", not_a_repository)

    snapshot = capture_workspace_snapshot(tmp_path, session_id="s1")

    assert snapshot is not None
    assert snapshot.capture_status == "not_a_repository"
    assert "not a git repository" in snapshot.capture_error
    assert snapshot.vcs == ""


def test_checkpoint_restore_requires_an_exact_source_prefix(tmp_path) -> None:
    store = UnifiedStore(tmp_path / "memory.db")
    manager = _manager()
    source = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
    ]
    epoch = ContextEpoch.build(system_prompt="rules", tools=[], model="test-model")
    store.save_compaction_checkpoint(
        {
            "id": "checkpoint-1",
            "session_id": "s1",
            "status": "completed",
            "source_digest": content_digest(source),
            "source_message_count": len(source),
            "summary": "old work is complete",
            "recent_messages": [{"role": "assistant", "content": "old answer"}],
            "projected_messages": [],
            "tail_start_index": 1,
            "tokens_before": 100,
            "tokens_after": 20,
            "epoch_digest": epoch.digest,
            "created_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T00:00:01",
        }
    )

    restored, checkpoint, changed = manager._restore_completed_checkpoint(
        [*source, {"role": "user", "content": "new request"}],
        session_id="s1",
        store=store,
        epoch=epoch,
    )
    assert checkpoint["id"] == "checkpoint-1"
    assert changed is False
    assert "old work is complete" in restored[0]["content"]
    assert restored[-1]["content"] == "new request"

    untouched, checkpoint, _ = manager._restore_completed_checkpoint(
        [{"role": "user", "content": "different"}],
        session_id="s1",
        store=store,
        epoch=epoch,
    )
    assert untouched == [{"role": "user", "content": "different"}]
    assert checkpoint is None


def test_checkpoint_lookup_failure_degrades_to_original_messages() -> None:
    manager = _manager()
    messages = [{"role": "user", "content": "continue"}]

    class LockedStore:
        def get_latest_completed_compaction(self, _session_id):
            raise RuntimeError("database is locked")

    restored, checkpoint, changed = manager._restore_completed_checkpoint(
        messages,
        session_id="s1",
        store=LockedStore(),
        epoch=ContextEpoch.build(system_prompt="", tools=[]),
    )
    assert restored is messages
    assert checkpoint is None
    assert changed is False


def test_recent_tail_selection_uses_token_budget(monkeypatch) -> None:
    manager = _manager()
    monkeypatch.setattr(manager, "_recent_tail_budget", lambda _limit: 700)
    groups = [[{"role": "user", "content": str(i) * 600}] for i in range(6)]

    early, recent = manager._select_recent_groups(groups, hard_limit=10_000)

    assert early
    assert 1 <= len(recent) < len(groups)
    assert recent[-1][0]["content"].startswith("5")


def test_old_tool_output_is_cold_stored_without_mutating_source(tmp_path) -> None:
    store = UnifiedStore(tmp_path / "memory.db")
    manager = _manager()
    raw = "x" * 12_000
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": "shell", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": raw}],
        },
        {"role": "user", "content": "z" * 1000},
    ]

    projected, refs = manager._cold_store_tool_outputs(
        messages, session_id="s1", store=store, protect_tokens=100
    )

    assert refs
    assert messages[1]["content"][0]["content"] == raw
    assert "memory://tool-output/" in projected[1]["content"][0]["content"]
    assert store.get_tool_output_blob(refs[0]) == raw


@pytest.mark.asyncio
async def test_contributors_are_ordered_and_bounded_by_contract() -> None:
    manager = _manager()

    class Contributor:
        async def contribute_to_compaction(self, **_kwargs):
            return CompactionContribution("plan", "next step", priority=80, max_tokens=20)

    manager.register_compaction_contributor(Contributor())
    epoch = ContextEpoch.build(system_prompt="", tools=[])
    values = await manager._gather_compaction_contributions(
        session_id="s1", messages=[], context_epoch=epoch
    )
    assert values == [CompactionContribution("plan", "next step", priority=80, max_tokens=20)]


@pytest.mark.asyncio
async def test_force_compaction_persists_completed_checkpoint(tmp_path, monkeypatch) -> None:
    store = UnifiedStore(tmp_path / "memory.db")
    manager = _manager()
    memory_manager = SimpleNamespace(store=store, _current_session_id="s1")
    session_context = SessionContext()
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i) * 5000} for i in range(8)
    ]

    async def summarize(*_args, **_kwargs):
        return "## Objective\n- persist continuity"

    monkeypatch.setattr(manager, "_summarize_messages_chunked", summarize)
    result = await manager.compress_if_needed(
        messages,
        system_prompt="rules",
        tools=[],
        max_tokens=6000,
        memory_manager=memory_manager,
        conversation_id="s1",
        force=True,
        session_context=session_context,
        working_directory=str(tmp_path),
        persist_checkpoint=True,
    )

    checkpoint = store.get_latest_completed_compaction("s1")
    assert checkpoint is not None
    assert checkpoint["summary"].startswith("## Objective")
    assert checkpoint["projected_messages"] == result
    assert session_context.latest_compaction_checkpoint()["id"] == checkpoint["id"]
    assert len(result) < len(messages)


def test_session_context_serializes_compaction_state() -> None:
    context = SessionContext()
    context.context_epoch = {"digest": "epoch"}
    context.append_compaction_checkpoint(
        {"id": "c1", "status": "completed", "summary": "summary", "workspace_snapshot_id": "w1"}
    )

    loaded = SessionContext.from_dict(context.to_dict())
    assert loaded.latest_compaction_checkpoint()["id"] == "c1"
    assert loaded.context_epoch["digest"] == "epoch"
    assert loaded.workspace_snapshot_id == "w1"


def test_schema_v5_migrates_context_continuity_tables(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    storage = MemoryStorage(db_path)
    storage._conn.execute("DROP TABLE compaction_checkpoints")
    storage._conn.execute("DROP TABLE context_epochs")
    storage._conn.execute("DROP TABLE tool_output_blobs")
    storage._conn.execute("DROP TABLE workspace_snapshots")
    storage._set_schema_version(5)
    storage.close()

    migrated = MemoryStorage(db_path)
    assert migrated._get_schema_version() == _SCHEMA_VERSION
    names = {
        row[0]
        for row in migrated._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "compaction_checkpoints",
        "context_epochs",
        "tool_output_blobs",
        "workspace_snapshots",
    } <= names
    snapshot_columns = {
        row[1]
        for row in migrated._conn.execute("PRAGMA table_info(workspace_snapshots)").fetchall()
    }
    assert {"capture_status", "capture_error"} <= snapshot_columns
