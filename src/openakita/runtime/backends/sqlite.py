"""SQLite checkpoint backend.

The default backend for the v2 runtime. Single SQLite file per concern
per org (ADR-0010), WAL mode, autocommit, with one row per checkpoint.
The state envelope is stored as a zlib-compressed canonical JSON blob
produced by :func:`encode_state` so writes are small even for large
TaskLedgers.

This backend is sync-on-disk under ``asyncio.to_thread`` for the same
reasons as the event store — write rate is low, and we avoid a hard
``aiosqlite`` dependency at the leaf layer. The two threading-mode
choices match: ``check_same_thread=False`` plus an internal RLock.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..checkpoint import (
    BaseCheckpointer,
    Checkpoint,
    CheckpointId,
    CheckpointMetadata,
    CheckpointStatus,
    CommandId,
    decode_state,
    encode_state,
)

__all__ = ["SqliteCheckpointer"]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id   TEXT PRIMARY KEY,
    parent_id       TEXT,
    command_id      TEXT NOT NULL,
    org_id          TEXT NOT NULL,
    superstep       INTEGER NOT NULL,
    status          TEXT NOT NULL,
    n_stalls        INTEGER NOT NULL,
    n_turns         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    state_blob      BLOB NOT NULL,
    pending_writes  BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ck_command_id_pk
    ON checkpoints (command_id, checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_ck_org_id_pk
    ON checkpoints (org_id, checkpoint_id);
"""


class SqliteCheckpointer(BaseCheckpointer):
    """Thread-safe SQLite-backed checkpoint store."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def aput(self, checkpoint: Checkpoint) -> CheckpointMetadata:
        return await asyncio.to_thread(self._put_sync, checkpoint)

    def _put_sync(self, checkpoint: Checkpoint) -> CheckpointMetadata:
        m = checkpoint.metadata
        blob = encode_state(checkpoint.state)
        pw = encode_state({"$schema_version": 1, "writes": checkpoint.pending_writes})
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO checkpoints
                    (checkpoint_id, parent_id, command_id, org_id,
                     superstep, status, n_stalls, n_turns,
                     created_at, state_blob, pending_writes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    parent_id=excluded.parent_id,
                    command_id=excluded.command_id,
                    org_id=excluded.org_id,
                    superstep=excluded.superstep,
                    status=excluded.status,
                    n_stalls=excluded.n_stalls,
                    n_turns=excluded.n_turns,
                    created_at=excluded.created_at,
                    state_blob=excluded.state_blob,
                    pending_writes=excluded.pending_writes
                """,
                (
                    m.checkpoint_id,
                    m.parent_id,
                    m.command_id,
                    m.org_id,
                    m.superstep,
                    m.status.value,
                    m.n_stalls,
                    m.n_turns,
                    m.created_at.isoformat(),
                    blob,
                    pw,
                ),
            )
        return m

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def aget(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        return await asyncio.to_thread(self._get_sync, checkpoint_id)

    def _get_sync(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    async def aget_latest(self, command_id: CommandId) -> Checkpoint | None:
        return await asyncio.to_thread(self._get_latest_sync, command_id)

    def _get_latest_sync(self, command_id: CommandId) -> Checkpoint | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE command_id=?
                ORDER BY checkpoint_id DESC
                LIMIT 1
                """,
                (command_id,),
            ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    async def alist(
        self,
        command_id: CommandId,
        *,
        limit: int = 64,
    ) -> AsyncIterator[CheckpointMetadata]:
        rows = await asyncio.to_thread(self._list_metadata_sync, command_id, limit)
        for row in rows:
            yield self._row_to_metadata(row)

    def _list_metadata_sync(
        self, command_id: CommandId, limit: int
    ) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT checkpoint_id, parent_id, command_id, org_id,
                       superstep, status, n_stalls, n_turns, created_at,
                       NULL AS state_blob, NULL AS pending_writes
                FROM checkpoints
                WHERE command_id=?
                ORDER BY checkpoint_id DESC
                LIMIT ?
                """,
                (command_id, int(limit)),
            ).fetchall()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def adelete_command(self, command_id: CommandId) -> int:
        return await asyncio.to_thread(self._delete_command_sync, command_id)

    def _delete_command_sync(self, command_id: CommandId) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM checkpoints WHERE command_id=?",
                (command_id,),
            )
            return int(cur.rowcount or 0)

    # ------------------------------------------------------------------
    # Row → record helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_metadata(row: tuple[Any, ...]) -> CheckpointMetadata:
        from datetime import datetime

        (
            checkpoint_id,
            parent_id,
            command_id,
            org_id,
            superstep,
            status,
            n_stalls,
            n_turns,
            created_at,
            *_rest,
        ) = row
        return CheckpointMetadata(
            checkpoint_id=checkpoint_id,
            parent_id=parent_id,
            command_id=command_id,
            org_id=org_id,
            superstep=int(superstep),
            status=CheckpointStatus(status),
            n_stalls=int(n_stalls),
            n_turns=int(n_turns),
            created_at=datetime.fromisoformat(created_at),
        )

    @classmethod
    def _row_to_checkpoint(cls, row: tuple[Any, ...]) -> Checkpoint:
        meta = cls._row_to_metadata(row)
        state = decode_state(row[9])
        pending_payload = decode_state(row[10])
        pending = list(pending_payload.get("writes", []))
        return Checkpoint(metadata=meta, state=state, pending_writes=pending)
