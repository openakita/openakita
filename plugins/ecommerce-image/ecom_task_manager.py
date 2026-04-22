"""Unified task manager with SQLite persistence, state machine, and progress tracking.

Handles tasks, assets, and plugin configuration. Supports batch parent-child
relationships and atomic progress increments.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task state machine
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelling", "partial_success"},
    "cancelling": {"cancelled", "partial_success"},
    "succeeded": {"running"},
    "failed": {"running"},
    "cancelled": {"running"},
    "partial_success": {"running"},
}


def _validate_transition(old_status: str, new_status: str) -> bool:
    return new_status in VALID_TRANSITIONS.get(old_status, set())


# Whitelist of columns that may be mutated via update_task(**fields).
# Anything else is silently dropped (with a warning) to defend against any
# caller — including future plugins or LLM-generated handlers — accidentally
# attempting to inject column names.  ``status`` is allowed here so callers
# that already validated the transition (e.g. update_task_status) can pass it
# through; ``updated_at`` is set internally by update_task itself.
_UPDATABLE_COLS: frozenset[str] = frozenset({
    "feature_id",
    "module",
    "task_type",
    "api_task_id",
    "api_provider",
    "status",
    "prompt",
    "params_json",
    "image_urls",
    "local_paths",
    "video_url",
    "local_video_path",
    "last_frame_url",
    "progress_current",
    "progress_total",
    "failed_at_step",
    "model",
    "execution_mode",
    "batch_parent_id",
    "error_message",
    "revised_prompt",
})


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    feature_id        TEXT NOT NULL,
    module            TEXT NOT NULL,
    task_type         TEXT NOT NULL,
    api_task_id       TEXT,
    api_provider      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    prompt            TEXT,
    params_json       TEXT NOT NULL DEFAULT '{}',
    image_urls        TEXT,
    local_paths       TEXT,
    video_url         TEXT,
    local_video_path  TEXT,
    last_frame_url    TEXT,
    progress_current  INTEGER DEFAULT 0,
    progress_total    INTEGER DEFAULT 1,
    failed_at_step    INTEGER,
    model             TEXT,
    execution_mode    TEXT,
    batch_parent_id   TEXT,
    error_message     TEXT,
    revised_prompt    TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_module ON tasks(module);
CREATE INDEX IF NOT EXISTS idx_tasks_feature ON tasks(feature_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(batch_parent_id);

CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    original_name   TEXT,
    size_bytes      INTEGER DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TaskManager:
    """Async SQLite-backed task, asset, and config manager."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add columns introduced after first release. SQLite has no IF NOT EXISTS for ADD COLUMN."""
        async with self._db.execute("PRAGMA table_info(tasks)") as cur:
            existing = {row[1] for row in await cur.fetchall()}
        for col, ddl in (
            ("local_paths", "TEXT"),
            ("local_video_path", "TEXT"),
        ):
            if col not in existing:
                try:
                    await self._db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
                except Exception as e:
                    logger.warning("Failed to add column %s: %s", col, e)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Tasks ──

    async def create_task(self, *, feature_id: str, module: str, task_type: str,
                          api_provider: str, status: str = "pending",
                          api_task_id: str = "", prompt: str = "",
                          model: str = "", execution_mode: str = "",
                          params: dict | None = None, revised_prompt: str = "",
                          progress_current: int = 0, progress_total: int = 1,
                          batch_parent_id: str = "") -> dict:
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        params_json = json.dumps(params or {}, ensure_ascii=False)
        await self._db.execute(
            """INSERT INTO tasks (id, feature_id, module, task_type, api_task_id,
               api_provider, status, prompt, params_json, model, execution_mode,
               revised_prompt, progress_current, progress_total, batch_parent_id,
               created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, feature_id, module, task_type, api_task_id,
             api_provider, status, prompt, params_json, model, execution_mode,
             revised_prompt, progress_current, progress_total, batch_parent_id,
             now, now),
        )
        await self._db.commit()
        return await self.get_task(task_id)  # type: ignore

    async def get_task(self, task_id: str) -> dict | None:
        async with self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
            if row:
                d = dict(row)
                if d.get("params_json"):
                    try:
                        d["params"] = json.loads(d["params_json"])
                    except Exception:
                        d["params"] = {}
                return d
        return None

    async def list_tasks(self, *, module: str | None = None, feature_id: str | None = None,
                         status: str | None = None, offset: int = 0, limit: int = 20) -> tuple[list[dict], int]:
        conditions: list[str] = ["batch_parent_id IS NULL OR batch_parent_id = ''"]
        args: list[Any] = []
        if module:
            conditions.append("module = ?")
            args.append(module)
        if feature_id:
            conditions.append("feature_id = ?")
            args.append(feature_id)
        if status:
            conditions.append("status = ?")
            args.append(status)
        where = " AND ".join(conditions)

        count_sql = f"SELECT COUNT(*) FROM tasks WHERE {where}"
        async with self._db.execute(count_sql, args) as cur:
            total = (await cur.fetchone())[0]

        query_sql = f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        async with self._db.execute(query_sql, args + [limit, offset]) as cur:
            rows = await cur.fetchall()

        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("params_json"):
                try:
                    d["params"] = json.loads(d["params_json"])
                except Exception:
                    d["params"] = {}
            tasks.append(d)
        return tasks, total

    async def get_running_tasks(self, *, api_provider: str | None = None) -> list[dict]:
        conditions = ["status IN ('running', 'cancelling')"]
        args: list[Any] = []
        if api_provider:
            conditions.append("api_provider = ?")
            args.append(api_provider)
        where = " AND ".join(conditions)
        async with self._db.execute(
            f"SELECT * FROM tasks WHERE {where}", args,
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        # Drop unknown columns to keep the dynamic SET clause SQL-injection-free.
        # We log so misspellings (e.g. "image_url" instead of "image_urls") are
        # discoverable rather than silently lost.
        clean: dict[str, Any] = {}
        dropped: list[str] = []
        for k, v in fields.items():
            if k in _UPDATABLE_COLS:
                clean[k] = v
            else:
                dropped.append(k)
        if dropped:
            logger.warning(
                "update_task(%s): dropping unknown columns: %s", task_id, dropped,
            )
        if not clean:
            return
        clean["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in clean)
        values = list(clean.values()) + [task_id]
        await self._db.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()

    async def update_task_status(self, task_id: str, new_status: str, **extra: Any) -> None:
        task = await self.get_task(task_id)
        if not task:
            return
        old_status = task["status"]
        if old_status == new_status:
            return
        if not _validate_transition(old_status, new_status):
            logger.warning(
                "Invalid transition %s -> %s for task %s", old_status, new_status, task_id,
            )
            return
        await self.update_task(task_id, status=new_status, **extra)

    async def delete_task(self, task_id: str) -> None:
        await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.execute("DELETE FROM tasks WHERE batch_parent_id = ?", (task_id,))
        await self._db.commit()

    async def delete_mock_tasks(self) -> int:
        """Remove tasks created by local mock (api_task_id starts with mock-)."""
        async with self._db.execute(
            "SELECT id FROM tasks WHERE api_task_id LIKE 'mock-%'",
        ) as cur:
            rows = await cur.fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return 0
        ph = ",".join("?" * len(ids))
        await self._db.execute(
            f"DELETE FROM tasks WHERE id IN ({ph}) OR batch_parent_id IN ({ph})",
            ids + ids,
        )
        await self._db.commit()
        return len(ids)

    async def increment_progress(self, task_id: str) -> int:
        """Atomic progress increment. Returns new progress_current value."""
        await self._db.execute(
            "UPDATE tasks SET progress_current = progress_current + 1, updated_at = ? WHERE id = ?",
            (time.time(), task_id),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT progress_current FROM tasks WHERE id = ?", (task_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_children(self, parent_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM tasks WHERE batch_parent_id = ? ORDER BY created_at", (parent_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def recompute_batch_parent_status(self, parent_id: str) -> None:
        """Derive parent status from children: all-ok -> succeeded, all-fail -> failed, mix -> partial_success."""
        children = await self.get_children(parent_id)
        if not children:
            return
        statuses = {c["status"] for c in children}
        if statuses == {"succeeded"}:
            await self.update_task_status(parent_id, "succeeded")
        elif statuses == {"failed"}:
            await self.update_task_status(parent_id, "failed")
        elif "running" in statuses or "pending" in statuses:
            pass  # still in progress
        elif statuses <= {"succeeded", "failed", "cancelled"}:
            await self.update_task_status(parent_id, "partial_success")

    # ── Assets ──

    async def create_asset(self, *, type: str, file_path: str,
                           original_name: str | None = None,
                           size_bytes: int = 0) -> dict:
        asset_id = uuid.uuid4().hex[:12]
        now = time.time()
        await self._db.execute(
            "INSERT INTO assets (id, type, file_path, original_name, size_bytes, created_at) VALUES (?,?,?,?,?,?)",
            (asset_id, type, file_path, original_name, size_bytes, now),
        )
        await self._db.commit()
        return {"id": asset_id, "type": type, "file_path": file_path, "original_name": original_name}

    async def get_asset(self, asset_id: str) -> dict | None:
        async with self._db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Config ──

    async def get_config(self, key: str) -> str | None:
        async with self._db.execute("SELECT value FROM config WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_config(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value),
        )
        await self._db.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        for k, v in updates.items():
            await self._db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v),
            )
        await self._db.commit()

    async def get_all_config(self) -> dict[str, str]:
        async with self._db.execute("SELECT key, value FROM config") as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}
