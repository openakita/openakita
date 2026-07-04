"""Hash-chained, append-only event store.

Implements ADR-0006's persistence companion to :mod:`stream`: the bus
delivers events to live consumers, and the store is the durable record
the supervisor (ADR-0004) and the data-migration scripts (ADR-0010) use
for audit and post-mortem.

Each entry is content-addressed by a SHA-256 chain. The chain is:

    h_0 = SHA256(b"")                          # genesis
    h_i = SHA256(h_{i-1} || canonical(event_i))

Anyone in possession of the latest hash can detect any retroactive edit
to the log by replaying. The contract is the same shape as SINT
Protocol's evidence ledger and Cortex's audit log; we keep ours
intentionally small.

Storage is SQLite by default. The interface is async; we use synchronous
``sqlite3`` under ``asyncio.to_thread`` rather than pulling
``aiosqlite`` into a leaf module — simpler, dependency-free at this
layer, fast enough for our write rate (a few hundred events per
command).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .stream import StreamEvent

__all__ = [
    "EventStore",
    "StoredEvent",
    "EventStoreError",
    "ChainBrokenError",
    "canonical_event_bytes",
    "GENESIS_HASH",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EventStoreError(Exception):
    """Base class for event store failures."""


class ChainBrokenError(EventStoreError):
    """Raised by :meth:`EventStore.verify` if the hash chain is invalid."""

    def __init__(self, sequence: int, expected: str, actual: str) -> None:
        super().__init__(
            f"hash chain broken at sequence {sequence}: "
            f"expected {expected[:12]}... got {actual[:12]}..."
        )
        self.sequence = sequence
        self.expected = expected
        self.actual = actual


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------

GENESIS_HASH = hashlib.sha256(b"").hexdigest()


def canonical_event_bytes(event_payload: dict[str, Any]) -> bytes:
    """Return canonical JSON bytes for ``event_payload``.

    Sorted keys, no whitespace, ``ensure_ascii=False`` so non-ASCII
    payloads (Chinese task descriptions, plugin tool names) hash to
    stable bytes regardless of caller locale.
    """
    return json.dumps(
        event_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_jsonable"):
        return value.to_jsonable()
    raise TypeError(f"unsupported type for canonical JSON: {type(value).__name__}")


def chain_hash(prev_hash: str, event_payload: dict[str, Any]) -> str:
    """Compute the next hash in the chain."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical_event_bytes(event_payload))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredEvent:
    """A row from the event store, including chain metadata."""

    sequence: int
    chain_hash: str
    prev_hash: str
    command_id: str
    org_id: str
    channel: str
    type: str
    payload: dict[str, Any]
    appended_at: datetime

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "chain_hash": self.chain_hash,
            "prev_hash": self.prev_hash,
            "command_id": self.command_id,
            "org_id": self.org_id,
            "channel": self.channel,
            "type": self.type,
            "payload": dict(self.payload),
            "appended_at": self.appended_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_store (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_hash  TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    command_id  TEXT NOT NULL,
    org_id      TEXT NOT NULL,
    channel     TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     BLOB NOT NULL,
    appended_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_store_command_seq
    ON event_store (command_id, sequence);
CREATE INDEX IF NOT EXISTS idx_event_store_org_seq
    ON event_store (org_id, sequence);
"""


class EventStore:
    """SQLite-backed hash-chained event store.

    Thread-safe via an internal :class:`threading.RLock`; async callers
    invoke the synchronous methods through ``asyncio.to_thread``. The
    store is intentionally synchronous on the disk side because a single
    command writes events at a low rate (tens per turn) and SQLite WAL
    mode is already non-blocking for readers.

    Args:
        path: SQLite file path, or ``":memory:"`` for ephemeral stores
            (tests, parity harnesses).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # autocommit; we manage transactions
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        command_id: str,
        org_id: str,
        channel: str,
        type: str,
        payload: dict[str, Any],
    ) -> StoredEvent:
        """Append one event. Returns the stored record with chain metadata."""
        with self._lock:
            prev = self._latest_hash_unlocked()
            event_payload = {
                "command_id": command_id,
                "org_id": org_id,
                "channel": channel,
                "type": type,
                "payload": payload,
            }
            new_hash = chain_hash(prev, event_payload)
            blob = canonical_event_bytes(payload)
            now = datetime.now(UTC).isoformat()
            cur = self._conn.execute(
                """
                INSERT INTO event_store
                    (chain_hash, prev_hash, command_id, org_id,
                     channel, type, payload, appended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_hash, prev, command_id, org_id, channel, type, blob, now),
            )
            seq = int(cur.lastrowid or 0)
            return StoredEvent(
                sequence=seq,
                chain_hash=new_hash,
                prev_hash=prev,
                command_id=command_id,
                org_id=org_id,
                channel=channel,
                type=type,
                payload=payload,
                appended_at=datetime.fromisoformat(now),
            )

    def append_stream_event(self, event: StreamEvent) -> StoredEvent:
        """Persist a :class:`StreamEvent` produced by :class:`StreamBus`.

        The fields stored are the persistent subset of the envelope.
        Identifiers like ``event_id`` and ``superstep`` are folded into
        the payload so they remain auditable but do not need their own
        columns.
        """
        payload = {
            "event_id": event.event_id,
            "superstep": event.superstep,
            "correlation_id": event.correlation_id,
            "emitted_at": event.emitted_at.isoformat(),
            "data": dict(event.payload),
        }
        return self.append(
            command_id=event.command_id,
            org_id=event.org_id,
            channel=event.channel,
            type=event.type,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Read / iterate
    # ------------------------------------------------------------------

    def latest_hash(self) -> str:
        with self._lock:
            return self._latest_hash_unlocked()

    def _latest_hash_unlocked(self) -> str:
        row = self._conn.execute(
            "SELECT chain_hash FROM event_store ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def count(self, *, command_id: str | None = None) -> int:
        with self._lock:
            if command_id is None:
                row = self._conn.execute("SELECT COUNT(*) FROM event_store").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM event_store WHERE command_id=?",
                    (command_id,),
                ).fetchone()
            return int(row[0])

    def iter_command(self, command_id: str) -> Iterable[StoredEvent]:
        """Yield stored events for ``command_id`` in append order."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sequence, chain_hash, prev_hash, command_id, org_id,
                       channel, type, payload, appended_at
                FROM event_store
                WHERE command_id = ?
                ORDER BY sequence ASC
                """,
                (command_id,),
            ).fetchall()
        for row in rows:
            yield self._row_to_stored(row)

    def iter_all(self) -> Iterable[StoredEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sequence, chain_hash, prev_hash, command_id, org_id,
                       channel, type, payload, appended_at
                FROM event_store
                ORDER BY sequence ASC
                """
            ).fetchall()
        for row in rows:
            yield self._row_to_stored(row)

    @staticmethod
    def _row_to_stored(row: tuple[Any, ...]) -> StoredEvent:
        seq, ch_hash, prev, cmd, org, channel, ty, payload_blob, appended = row
        return StoredEvent(
            sequence=int(seq),
            chain_hash=ch_hash,
            prev_hash=prev,
            command_id=cmd,
            org_id=org,
            channel=channel,
            type=ty,
            payload=json.loads(payload_blob.decode("utf-8")),
            appended_at=datetime.fromisoformat(appended),
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> None:
        """Replay the chain and raise :class:`ChainBrokenError` on mismatch.

        Used by the migration script (ADR-0010) before archiving and by
        debug endpoints. O(n) over the entire log; intended to be a
        rare audit operation rather than a hot path.
        """
        prev = GENESIS_HASH
        for ev in self.iter_all():
            event_payload = {
                "command_id": ev.command_id,
                "org_id": ev.org_id,
                "channel": ev.channel,
                "type": ev.type,
                "payload": ev.payload,
            }
            expected = chain_hash(prev, event_payload)
            if expected != ev.chain_hash or prev != ev.prev_hash:
                raise ChainBrokenError(ev.sequence, expected, ev.chain_hash)
            prev = ev.chain_hash
