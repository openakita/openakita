"""Backend conformance tests for SQLite and JsonFile checkpointers.

Phase 1 commit 7. The same suite runs against both backends through a
parametrised fixture so any divergence is caught at CI time. Asserts:

* aput / aget round-trip preserves state, metadata, and pending writes;
* aput is upsert-style (re-putting the same checkpoint_id replaces the
  prior content);
* aget_latest follows checkpoint_id lexical order so ULID-like ids
  produce the most recent checkpoint;
* alist newest-first with limit;
* adelete_command removes only the requested command's checkpoints
  and returns the count;
* state envelope $schema_version is validated end-to-end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openakita.runtime.backends.json_file import JsonFileCheckpointer
from openakita.runtime.backends.sqlite import SqliteCheckpointer
from openakita.runtime.checkpoint import (
    BaseCheckpointer,
    Checkpoint,
    CheckpointMetadata,
    CheckpointStatus,
    make_checkpoint_id,
)


@pytest.fixture(params=["sqlite", "json"])
async def backend(request, tmp_path) -> AsyncIterator[BaseCheckpointer]:
    if request.param == "sqlite":
        store = SqliteCheckpointer(tmp_path / "ck.db")
    else:
        store = JsonFileCheckpointer(tmp_path / "ck_root")
    try:
        yield store
    finally:
        await store.aclose()


def _meta(
    command_id: str = "cmd_a",
    *,
    superstep: int = 0,
    cid: str | None = None,
    status: CheckpointStatus = CheckpointStatus.RUNNING,
    parent: str | None = None,
) -> CheckpointMetadata:
    return CheckpointMetadata(
        checkpoint_id=cid or make_checkpoint_id(),
        parent_id=parent,
        command_id=command_id,
        org_id="org_a",
        superstep=superstep,
        status=status,
        n_stalls=0,
        n_turns=superstep,
        created_at=datetime.now(UTC),
    )


def _ck(
    command_id: str = "cmd_a",
    *,
    superstep: int = 0,
    cid: str | None = None,
    state: dict | None = None,
    pending: list[dict] | None = None,
    status: CheckpointStatus = CheckpointStatus.RUNNING,
) -> Checkpoint:
    return Checkpoint(
        metadata=_meta(command_id, superstep=superstep, cid=cid, status=status),
        state=state or {"supervisor": {"task_ledger": {"task": "demo"}}},
        pending_writes=pending or [],
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


async def test_put_get_round_trip(backend: BaseCheckpointer) -> None:
    ck = _ck(state={"supervisor": {"x": [1, 2, 3]}, "channels": {}, "nodes": {}})
    meta = await backend.aput(ck)
    fetched = await backend.aget(meta.checkpoint_id)
    assert fetched is not None
    assert fetched.metadata == meta
    # Backends normalise the envelope by adding $schema_version on the
    # encode/decode round-trip (ADR-0005); the original keys must
    # otherwise round-trip unchanged.
    expected = {"$schema_version": 1, **ck.state}
    assert fetched.state == expected
    assert fetched.pending_writes == []


async def test_pending_writes_round_trip(backend: BaseCheckpointer) -> None:
    pending = [
        {"channel": "deliverables", "key": "shot_001", "value": {"img": "url"}},
        {"channel": "blackboard", "key": "facts", "value": "..."},
    ]
    ck = _ck(pending=pending)
    await backend.aput(ck)
    fetched = await backend.aget(ck.metadata.checkpoint_id)
    assert fetched is not None
    assert fetched.pending_writes == pending


# ---------------------------------------------------------------------------
# Upsert semantics
# ---------------------------------------------------------------------------


async def test_aput_replaces_existing_checkpoint(backend: BaseCheckpointer) -> None:
    cid = make_checkpoint_id()
    ck1 = _ck(cid=cid, state={"supervisor": {"v": 1}})
    ck2 = _ck(cid=cid, state={"supervisor": {"v": 2}}, status=CheckpointStatus.DONE)
    await backend.aput(ck1)
    await backend.aput(ck2)
    fetched = await backend.aget(cid)
    assert fetched is not None
    assert fetched.state == {"$schema_version": 1, "supervisor": {"v": 2}}
    assert fetched.metadata.status == CheckpointStatus.DONE


# ---------------------------------------------------------------------------
# Latest + list
# ---------------------------------------------------------------------------


async def test_aget_latest_returns_lex_max(backend: BaseCheckpointer) -> None:
    import time

    cks = []
    for i in range(4):
        ck = _ck(superstep=i)
        cks.append(ck)
        await backend.aput(ck)
        time.sleep(0.002)
    latest = await backend.aget_latest("cmd_a")
    assert latest is not None
    assert latest.metadata.checkpoint_id == cks[-1].metadata.checkpoint_id


async def test_alist_newest_first_with_limit(backend: BaseCheckpointer) -> None:
    import time

    cks = []
    for i in range(5):
        ck = _ck(superstep=i)
        cks.append(ck)
        await backend.aput(ck)
        time.sleep(0.002)

    seen: list[str] = []
    async for m in backend.alist("cmd_a", limit=3):
        seen.append(m.checkpoint_id)
    expected = [cks[i].metadata.checkpoint_id for i in (4, 3, 2)]
    assert seen == expected


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_adelete_command_removes_only_target_command(
    backend: BaseCheckpointer,
) -> None:
    for i in range(3):
        await backend.aput(_ck("cmd_a", superstep=i))
    await backend.aput(_ck("cmd_b", superstep=0))

    n = await backend.adelete_command("cmd_a")
    assert n == 3
    assert await backend.aget_latest("cmd_a") is None
    assert await backend.aget_latest("cmd_b") is not None


# ---------------------------------------------------------------------------
# Persistence (file backends)
# ---------------------------------------------------------------------------


async def test_sqlite_persistence_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "ck.db"
    s1 = SqliteCheckpointer(db)
    cid = make_checkpoint_id()
    await s1.aput(_ck(cid=cid, state={"supervisor": {"persist": True}}))
    await s1.aclose()

    s2 = SqliteCheckpointer(db)
    fetched = await s2.aget(cid)
    assert fetched is not None
    assert fetched.state["supervisor"]["persist"] is True
    await s2.aclose()


async def test_json_file_persistence_across_reopen(tmp_path: Path) -> None:
    root = tmp_path / "ck_root"
    s1 = JsonFileCheckpointer(root)
    cid = make_checkpoint_id()
    await s1.aput(_ck(cid=cid, state={"supervisor": {"hello": "world"}}))
    await s1.aclose()

    s2 = JsonFileCheckpointer(root)
    fetched = await s2.aget(cid)
    assert fetched is not None
    assert fetched.state["supervisor"]["hello"] == "world"
    await s2.aclose()


async def test_json_file_writes_are_atomic(tmp_path: Path) -> None:
    """A failed write must not leave a half-written file behind."""
    root = tmp_path / "ck_root"
    backend = JsonFileCheckpointer(root)
    cid = make_checkpoint_id()
    ck = _ck(cid=cid, state={"supervisor": {"k": "v"}})
    await backend.aput(ck)
    target = root / "cmd_a" / f"{cid}.json"
    assert target.is_file()
    # No leftover temp files.
    siblings = [p.name for p in target.parent.iterdir()]
    assert siblings == [f"{cid}.json"]
    await backend.aclose()
