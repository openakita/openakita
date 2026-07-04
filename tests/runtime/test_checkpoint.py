"""Tests for :mod:`openakita.runtime.checkpoint` and the in-memory backend.

Phase 1 commit 6. Asserts:

* Checkpoint / metadata round-trip through JSON;
* envelope encoding adds $schema_version when missing;
* decode rejects missing version with a clear diagnosis;
* decode rejects unknown future version with a clear diagnosis;
* MemoryCheckpointer get/list/latest/delete behave as the contract
  promises;
* alist respects the limit parameter and returns newest-first order;
* adelete_command removes every checkpoint for that command and
  returns the count.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openakita.runtime.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    CheckpointMetadata,
    CheckpointSchemaError,
    CheckpointStatus,
    MemoryCheckpointer,
    decode_state,
    encode_state,
    make_checkpoint_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(
    *,
    cid: str | None = None,
    command_id: str = "cmd_a",
    superstep: int = 0,
    status: CheckpointStatus = CheckpointStatus.RUNNING,
    parent_id: str | None = None,
) -> CheckpointMetadata:
    return CheckpointMetadata(
        checkpoint_id=cid or make_checkpoint_id(),
        parent_id=parent_id,
        command_id=command_id,
        org_id="org_x",
        superstep=superstep,
        status=status,
        n_stalls=0,
        n_turns=superstep,
        created_at=datetime.now(UTC),
    )


def _ck(state: dict | None = None, **kwargs) -> Checkpoint:
    return Checkpoint(
        metadata=_meta(**kwargs),
        state=state or {"supervisor": {"task_ledger": {}}},
        pending_writes=[],
    )


# ---------------------------------------------------------------------------
# Records round-trip
# ---------------------------------------------------------------------------


def test_metadata_round_trip() -> None:
    m = _meta(superstep=4, status=CheckpointStatus.DONE)
    rebuilt = CheckpointMetadata.from_jsonable(m.to_jsonable())
    assert rebuilt == m


def test_checkpoint_round_trip() -> None:
    ck = _ck(
        state={"supervisor": {"x": 1}, "channels": {"y": []}, "nodes": {}},
    )
    rebuilt = Checkpoint.from_jsonable(ck.to_jsonable())
    assert rebuilt.metadata == ck.metadata
    assert rebuilt.state == ck.state


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_encode_state_adds_schema_version_when_missing() -> None:
    payload = {"supervisor": {"task_ledger": {}}}
    blob = encode_state(payload)
    decoded = decode_state(blob)
    assert decoded["$schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert decoded["supervisor"] == payload["supervisor"]


def test_encode_state_keeps_existing_schema_version() -> None:
    payload = {"$schema_version": 1, "supervisor": {}}
    decoded = decode_state(encode_state(payload))
    assert decoded["$schema_version"] == 1


def test_decode_state_rejects_missing_version() -> None:
    import json
    import zlib

    raw = json.dumps({"supervisor": {}}).encode("utf-8")
    blob = zlib.compress(raw)
    with pytest.raises(CheckpointSchemaError) as info:
        decode_state(blob)
    assert "missing" in str(info.value).lower()


def test_decode_state_rejects_future_version() -> None:
    import json
    import zlib

    raw = json.dumps({"$schema_version": 99}).encode("utf-8")
    blob = zlib.compress(raw)
    with pytest.raises(CheckpointSchemaError) as info:
        decode_state(blob)
    assert "99" in str(info.value)


# ---------------------------------------------------------------------------
# MemoryCheckpointer
# ---------------------------------------------------------------------------


async def test_memory_put_get() -> None:
    store = MemoryCheckpointer()
    ck = _ck()
    meta = await store.aput(ck)
    assert meta == ck.metadata
    fetched = await store.aget(meta.checkpoint_id)
    assert fetched is not None
    # Memory backend normalises the envelope through encode/decode so
    # reads carry the $schema_version (matches SQLite/JsonFile).
    assert fetched.state == {"$schema_version": 1, **ck.state}


async def test_memory_get_returns_none_for_unknown_id() -> None:
    store = MemoryCheckpointer()
    fetched = await store.aget("nope")
    assert fetched is None


async def test_memory_aget_latest_returns_most_recent_per_command() -> None:
    store = MemoryCheckpointer()
    a1 = _ck(command_id="cmd_a", superstep=0)
    a2 = _ck(command_id="cmd_a", superstep=1)
    b1 = _ck(command_id="cmd_b", superstep=0)
    await store.aput(a1)
    await store.aput(b1)
    await store.aput(a2)

    latest_a = await store.aget_latest("cmd_a")
    assert latest_a is not None
    assert latest_a.metadata.checkpoint_id == a2.metadata.checkpoint_id

    latest_b = await store.aget_latest("cmd_b")
    assert latest_b is not None
    assert latest_b.metadata.checkpoint_id == b1.metadata.checkpoint_id

    latest_missing = await store.aget_latest("cmd_missing")
    assert latest_missing is None


async def test_memory_alist_newest_first_with_limit() -> None:
    store = MemoryCheckpointer()
    cks = []
    for i in range(5):
        ck = _ck(command_id="cmd_a", superstep=i)
        cks.append(ck)
        await store.aput(ck)

    seen = []
    async for m in store.alist("cmd_a", limit=3):
        seen.append(m.checkpoint_id)
    expected = [cks[i].metadata.checkpoint_id for i in (4, 3, 2)]
    assert seen == expected


async def test_memory_adelete_command_removes_all_for_command() -> None:
    store = MemoryCheckpointer()
    for i in range(3):
        await store.aput(_ck(command_id="cmd_a", superstep=i))
    await store.aput(_ck(command_id="cmd_b", superstep=0))

    n = await store.adelete_command("cmd_a")
    assert n == 3
    assert await store.aget_latest("cmd_a") is None
    # cmd_b unaffected
    assert await store.aget_latest("cmd_b") is not None


async def test_memory_total_grows_with_puts() -> None:
    store = MemoryCheckpointer()
    assert store.total() == 0
    await store.aput(_ck())
    await store.aput(_ck())
    assert store.total() == 2


# ---------------------------------------------------------------------------
# Checkpoint id minting
# ---------------------------------------------------------------------------


def test_checkpoint_id_is_lex_sortable_by_time() -> None:
    """ULID-like ids must sort lexicographically by time at millisecond
    granularity so SQLite indexes return them in append order without
    an explicit timestamp sort.

    Within the same millisecond the suffix is random, so we only assert
    that the *time prefix* is monotonically non-decreasing across the
    sequence and that ids generated at least 2ms apart sort correctly.
    """
    import time

    ids: list[str] = []
    for _ in range(8):
        ids.append(make_checkpoint_id())
        time.sleep(0.002)  # ensure each id is in a distinct millisecond bucket
    assert ids == sorted(ids)
    prefixes = [i.split("_", 1)[0] for i in ids]
    assert prefixes == sorted(prefixes)
