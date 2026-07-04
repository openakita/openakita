"""Tests for :mod:`openakita.runtime.event_store`.

Phase 1 commit 5. Asserts:

* genesis hash equals SHA-256 of empty bytes;
* canonical bytes are stable across key order and Unicode;
* append computes a chain over (prev_hash || canonical(payload));
* verify() detects retroactive edits;
* iter_command() filters by command_id and yields in append order;
* StreamEvent → StoredEvent persistence preserves event_id and
  superstep in the payload (so audit can correlate live and stored
  events).
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from openakita.runtime.event_store import (
    GENESIS_HASH,
    ChainBrokenError,
    EventStore,
    canonical_event_bytes,
    chain_hash,
)
from openakita.runtime.stream import StreamBus

# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def test_genesis_hash_matches_sha256_of_empty() -> None:
    assert hashlib.sha256(b"").hexdigest() == GENESIS_HASH


def test_canonical_bytes_are_key_order_independent() -> None:
    a = canonical_event_bytes({"b": 2, "a": 1})
    b = canonical_event_bytes({"a": 1, "b": 2})
    assert a == b


def test_canonical_bytes_preserve_non_ascii() -> None:
    """Chinese task descriptions must not be mojibake'd into \\u escapes,
    because the migration script's manifest hashing depends on byte
    stability across locales."""
    payload = {"task": "10秒竖屏短视频"}
    out = canonical_event_bytes(payload)
    assert "10秒竖屏短视频".encode() in out


# ---------------------------------------------------------------------------
# Append + chain
# ---------------------------------------------------------------------------


def test_append_links_chain_from_genesis() -> None:
    store = EventStore(":memory:")
    e1 = store.append(
        command_id="cmd_1",
        org_id="org_a",
        channel="updates",
        type="node_started",
        payload={"node_id": "node_x"},
    )
    assert e1.prev_hash == GENESIS_HASH

    expected = chain_hash(
        GENESIS_HASH,
        {
            "command_id": "cmd_1",
            "org_id": "org_a",
            "channel": "updates",
            "type": "node_started",
            "payload": {"node_id": "node_x"},
        },
    )
    assert e1.chain_hash == expected

    e2 = store.append(
        command_id="cmd_1",
        org_id="org_a",
        channel="updates",
        type="node_progress",
        payload={"step": 1},
    )
    assert e2.prev_hash == e1.chain_hash
    assert e2.sequence == e1.sequence + 1


def test_count_total_and_per_command() -> None:
    store = EventStore(":memory:")
    store.append(command_id="cmd_a", org_id="o", channel="u", type="t", payload={})
    store.append(command_id="cmd_b", org_id="o", channel="u", type="t", payload={})
    store.append(command_id="cmd_a", org_id="o", channel="u", type="t", payload={})
    assert store.count() == 3
    assert store.count(command_id="cmd_a") == 2
    assert store.count(command_id="cmd_b") == 1


def test_iter_command_returns_only_matching_in_order() -> None:
    store = EventStore(":memory:")
    for i in range(3):
        store.append(
            command_id="cmd_a", org_id="o", channel="u", type=f"t{i}", payload={}
        )
        store.append(
            command_id="cmd_b", org_id="o", channel="u", type=f"x{i}", payload={}
        )
    seen = [e.type for e in store.iter_command("cmd_a")]
    assert seen == ["t0", "t1", "t2"]


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def test_verify_passes_on_clean_chain() -> None:
    store = EventStore(":memory:")
    for i in range(5):
        store.append(
            command_id="cmd_a", org_id="o", channel="u", type=f"t{i}", payload={"i": i}
        )
    store.verify()


def test_verify_detects_retroactive_payload_edit(tmp_path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    for i in range(3):
        store.append(
            command_id="cmd_a", org_id="o", channel="u", type=f"t{i}", payload={"i": i}
        )
    store.close()

    # Tamper with a row directly through sqlite3.
    raw = sqlite3.connect(db)
    raw.execute(
        "UPDATE event_store SET payload=? WHERE sequence=2",
        (b'{"i":99}',),
    )
    raw.commit()
    raw.close()

    store2 = EventStore(db)
    with pytest.raises(ChainBrokenError) as info:
        store2.verify()
    assert info.value.sequence == 2


# ---------------------------------------------------------------------------
# StreamEvent integration
# ---------------------------------------------------------------------------


async def test_append_stream_event_preserves_event_id_and_superstep() -> None:
    bus = StreamBus(strict=True)
    event = await bus.emit(
        "lifecycle",
        "node_idle",
        {"node_id": "nx"},
        command_id="cmd_a",
        org_id="org_b",
        superstep=4,
        correlation_id="cor_x",
    )
    store = EventStore(":memory:")
    stored = store.append_stream_event(event)
    assert stored.command_id == "cmd_a"
    assert stored.channel == "lifecycle"
    assert stored.type == "node_idle"
    assert stored.payload["event_id"] == event.event_id
    assert stored.payload["superstep"] == 4
    assert stored.payload["correlation_id"] == "cor_x"
    assert stored.payload["data"] == {"node_id": "nx"}


# ---------------------------------------------------------------------------
# Persistence across reopen
# ---------------------------------------------------------------------------


def test_chain_persists_across_reopen(tmp_path) -> None:
    db = tmp_path / "evlog.db"
    s1 = EventStore(db)
    s1.append(command_id="cmd", org_id="o", channel="u", type="t1", payload={})
    s1.append(command_id="cmd", org_id="o", channel="u", type="t2", payload={})
    h_before = s1.latest_hash()
    s1.close()

    s2 = EventStore(db)
    assert s2.latest_hash() == h_before
    s2.append(command_id="cmd", org_id="o", channel="u", type="t3", payload={})
    s2.verify()
    s2.close()
