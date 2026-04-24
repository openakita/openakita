"""Host-level cross-plugin asset registry (the Asset Bus).

Plugins with ``assets.publish`` and/or ``assets.consume`` permissions can
exchange intermediate artifacts (video files, audio tracks, subtitle blobs,
preview URLs, etc.) without touching each other's private SQLite databases.

Design constraints (kept deliberately small for v1):

* **Single-process, single-file SQLite** at ``settings.data_dir / asset_bus.db``
  using WAL mode so multiple coroutines read concurrently while one writes.
* **No background sweeper task in v1.** ``sweep_expired`` exists and is fully
  tested but is invoked on demand (e.g. by tests, by a future scheduled job,
  or by an admin endpoint). v1 does NOT spin a periodic task.
* **Minimal surface, ACL by content.** Each row carries
  ``created_by_plugin`` plus a JSON ``shared_with`` array. Reads return
  ``None`` for non-permitted access so the bus does not leak existence.
* **Source paths are not validated.** Publishers can put any ``source_path``
  string they like; consumers must validate before opening the file. See
  ``docs/asset-bus.md`` for the security note.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets_bus (
    asset_id           TEXT PRIMARY KEY,
    asset_kind         TEXT NOT NULL,
    source_path        TEXT,
    preview_url        TEXT,
    duration_sec       REAL,
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_by_plugin  TEXT NOT NULL,
    shared_with_json   TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL,
    expires_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_bus_owner   ON assets_bus(created_by_plugin);
CREATE INDEX IF NOT EXISTS idx_asset_bus_kind    ON assets_bus(asset_kind);
CREATE INDEX IF NOT EXISTS idx_asset_bus_expires ON assets_bus(expires_at);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_at(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Materialise a Row into a JSON-serialisable dict the API can return."""
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    try:
        shared_with = json.loads(row["shared_with_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        shared_with = []
    return {
        "asset_id": row["asset_id"],
        "asset_kind": row["asset_kind"],
        "source_path": row["source_path"],
        "preview_url": row["preview_url"],
        "duration_sec": row["duration_sec"],
        "metadata": metadata,
        "created_by_plugin": row["created_by_plugin"],
        "shared_with": shared_with,
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


class AssetBus:
    """SQLite-backed cross-plugin asset registry.

    Methods are async and safe to call concurrently from the same event loop
    (aiosqlite serialises writes through its executor). The bus auto-creates
    its schema on first use, so callers do not have to remember to invoke
    :meth:`init` before the first publish/get — but doing so explicitly
    avoids paying the lock cost on the first request.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()
        self._initialized = False

    # ------------------------------------------------------------------ init

    async def init(self) -> None:
        """Open the connection and create the schema. Idempotent."""
        async with self._init_lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._db.executescript(SCHEMA_SQL)
            await self._db.commit()
            self._initialized = True
            logger.info("AssetBus initialised at %s", self._db_path)

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        await self.init()

    async def close(self) -> None:
        """Close the underlying connection. Safe to call repeatedly."""
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._initialized = False

    # --------------------------------------------------------------- publish

    async def publish(
        self,
        *,
        plugin_id: str,
        asset_kind: str,
        source_path: str | None = None,
        preview_url: str | None = None,
        duration_sec: float | None = None,
        metadata: dict[str, Any] | None = None,
        shared_with: list[str] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        """Insert a new asset row and return its asset_id."""
        if not plugin_id:
            raise ValueError("plugin_id is required")
        if not asset_kind:
            raise ValueError("asset_kind is required")
        await self._ensure_init()
        assert self._db is not None

        asset_id = uuid.uuid4().hex
        now = _iso_now()
        expires_at = (
            _iso_at(time.time() + ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None
        )
        await self._db.execute(
            """
            INSERT INTO assets_bus (
                asset_id, asset_kind, source_path, preview_url, duration_sec,
                metadata_json, created_by_plugin, shared_with_json,
                created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                asset_kind,
                source_path,
                preview_url,
                duration_sec,
                json.dumps(metadata or {}, ensure_ascii=False),
                plugin_id,
                json.dumps(shared_with or [], ensure_ascii=False),
                now,
                expires_at,
            ),
        )
        await self._db.commit()
        return asset_id

    # ------------------------------------------------------------------- get

    @staticmethod
    def _is_permitted(row: aiosqlite.Row, requester: str) -> bool:
        if row["created_by_plugin"] == requester:
            return True
        try:
            shared = json.loads(row["shared_with_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            shared = []
        if "*" in shared:
            return True
        return requester in shared

    async def get(
        self,
        asset_id: str,
        *,
        requester_plugin_id: str,
    ) -> dict[str, Any] | None:
        """Return the asset row as a dict if the requester is permitted, else None.

        Returning None for missing AND for forbidden assets is intentional:
        consumers must not be able to enumerate assets they cannot read.
        """
        if not requester_plugin_id:
            return None
        await self._ensure_init()
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT * FROM assets_bus WHERE asset_id = ?",
            (asset_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        if not self._is_permitted(row, requester_plugin_id):
            return None
        return _row_to_dict(row)

    # ------------------------------------------------------------- list/own

    async def list_owned(self, plugin_id: str) -> list[dict[str, Any]]:
        """Return all assets owned by ``plugin_id`` (most recent first)."""
        await self._ensure_init()
        assert self._db is not None
        cur = await self._db.execute(
            """
            SELECT * FROM assets_bus
             WHERE created_by_plugin = ?
             ORDER BY created_at DESC
            """,
            (plugin_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_dict(r) for r in rows]

    async def delete_owned(self, asset_id: str, plugin_id: str) -> bool:
        """Delete an asset only if the caller is its owner. Returns True iff a row was removed."""
        await self._ensure_init()
        assert self._db is not None
        cur = await self._db.execute(
            "DELETE FROM assets_bus WHERE asset_id = ? AND created_by_plugin = ?",
            (asset_id, plugin_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # -------------------------------------------------------------- sweepers

    async def sweep_expired(self, *, now: float | None = None) -> int:
        """Delete all rows whose ``expires_at`` is in the past. Returns count removed."""
        await self._ensure_init()
        assert self._db is not None
        cutoff = _iso_at(now if now is not None else time.time())
        cur = await self._db.execute(
            "DELETE FROM assets_bus WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def sweep_owner(self, plugin_id: str) -> int:
        """Delete every asset owned by ``plugin_id``.

        Called by the host on plugin uninstall to prevent orphan rows whose
        owner no longer exists. Returns the number of rows removed.
        """
        if not plugin_id:
            return 0
        await self._ensure_init()
        assert self._db is not None
        cur = await self._db.execute(
            "DELETE FROM assets_bus WHERE created_by_plugin = ?",
            (plugin_id,),
        )
        await self._db.commit()
        removed = cur.rowcount or 0
        if removed:
            logger.info(
                "AssetBus: removed %d asset(s) owned by '%s'", removed, plugin_id
            )
        return removed

    # --------------------------------------------------------------- counts

    async def count_all(self) -> int:
        """Return total number of rows. Useful for ops/health checks."""
        await self._ensure_init()
        assert self._db is not None
        cur = await self._db.execute("SELECT COUNT(*) FROM assets_bus")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0
