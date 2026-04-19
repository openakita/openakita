"""BaseTaskManager — SQLite-backed task & asset persistence for AI media plugins.

Pulled out of ``plugins/seedance-video/task_manager.py`` and
``plugins/tongyi-image/task_manager.py`` (which were ~80% identical).

Plugins subclass this and add their own columns through
``extra_task_columns()`` and ``extra_asset_columns()``.  All async methods
use ``aiosqlite`` so they cooperate with the host event loop.

Cancellation is built-in (audit3_cc fix): ``cancel_task()`` sets status to
``cancelled`` and returns the row so callers can also notify the vendor.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    """Standard task lifecycle.  Plugins may add custom values via subclass."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def is_terminal(cls, value: str) -> bool:
        return value in {cls.SUCCEEDED.value, cls.FAILED.value, cls.CANCELLED.value}


@dataclass
class TaskRecord:
    """Lightweight DTO returned by ``get_task()`` / ``list_tasks()``.

    ``params`` and ``result`` are JSON-decoded dicts.  ``extra`` carries
    plugin-specific columns the base does not know about.
    """

    id: str
    vendor_task_id: str | None
    status: str
    prompt: str
    params: dict[str, Any]
    result: dict[str, Any]
    error_message: str | None
    created_at: float
    updated_at: float
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "vendor_task_id": self.vendor_task_id,
            "status": self.status,
            "prompt": self.prompt,
            "params": dict(self.params),
            "result": dict(self.result),
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **self.extra,
        }


_BASE_TASK_COLUMNS: list[tuple[str, str]] = [
    ("id", "TEXT PRIMARY KEY"),
    ("vendor_task_id", "TEXT"),
    ("status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("prompt", "TEXT NOT NULL DEFAULT ''"),
    ("params_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("result_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("error_message", "TEXT"),
    ("created_at", "REAL NOT NULL"),
    ("updated_at", "REAL NOT NULL"),
]

_BASE_ASSET_COLUMNS: list[tuple[str, str]] = [
    ("id", "TEXT PRIMARY KEY"),
    ("task_id", "TEXT"),
    ("type", "TEXT NOT NULL"),
    ("file_path", "TEXT NOT NULL"),
    ("size_bytes", "INTEGER"),
    ("created_at", "REAL NOT NULL"),
]


class BaseTaskManager:
    """SQLite task / asset / config persistence.

    Subclass and override:

    - ``extra_task_columns()``     -> list[(name, sql_type)]
    - ``extra_asset_columns()``    -> list[(name, sql_type)]
    - ``default_config()``         -> dict[str, str]
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    # ── overridable ──────────────────────────────────────────────────────

    def extra_task_columns(self) -> list[tuple[str, str]]:
        """Plugins add custom task columns here."""
        return []

    def extra_asset_columns(self) -> list[tuple[str, str]]:
        """Plugins add custom asset columns here."""
        return []

    def default_config(self) -> dict[str, str]:
        """Plugins seed config defaults (string values only)."""
        return {}

    # ── init ────────────────────────────────────────────────────────────

    async def init(self) -> None:
        """Create schema if missing.  Idempotent."""
        import aiosqlite

        if self._initialized:
            return

        task_cols = _BASE_TASK_COLUMNS + list(self.extra_task_columns())
        asset_cols = _BASE_ASSET_COLUMNS + list(self.extra_asset_columns())

        task_sql = ",\n  ".join(f"{n} {t}" for n, t in task_cols)
        asset_sql = ",\n  ".join(f"{n} {t}" for n, t in asset_cols)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"CREATE TABLE IF NOT EXISTS tasks (\n  {task_sql}\n)")
            await db.execute(f"CREATE TABLE IF NOT EXISTS assets (\n  {asset_sql}\n)")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_assets_task ON assets(task_id)")

            for k, v in self.default_config().items():
                await db.execute(
                    "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )

            await db.commit()
        self._initialized = True

    # ── tasks ───────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        prompt: str = "",
        params: dict[str, Any] | None = None,
        status: str = TaskStatus.PENDING.value,
        task_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new task row.  Returns task id."""
        import aiosqlite

        await self.init()
        tid = task_id or _new_id("t_")
        now = time.time()
        extra = extra or {}

        cols = ["id", "status", "prompt", "params_json", "result_json", "created_at", "updated_at"]
        vals: list[Any] = [
            tid, status, prompt, json.dumps(params or {}, ensure_ascii=False),
            "{}", now, now,
        ]
        for k, v in extra.items():
            cols.append(k)
            vals.append(v)

        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO tasks ({col_list}) VALUES ({placeholders})",
                vals,
            )
            await db.commit()
        return tid

    async def get_task(self, task_id: str) -> TaskRecord | None:
        import aiosqlite

        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        import aiosqlite

        await self.init()
        sql = "SELECT * FROM tasks"
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(limit, 500)), max(0, offset)])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, args) as cur:
                rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        vendor_task_id: str | None = None,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Patch one row.  Returns True if a row was actually changed."""
        import aiosqlite

        await self.init()
        sets: list[str] = ["updated_at = ?"]
        args: list[Any] = [time.time()]
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if vendor_task_id is not None:
            sets.append("vendor_task_id = ?")
            args.append(vendor_task_id)
        if result is not None:
            sets.append("result_json = ?")
            args.append(json.dumps(result, ensure_ascii=False))
        if error_message is not None:
            sets.append("error_message = ?")
            args.append(error_message)
        if extra:
            for k, v in extra.items():
                sets.append(f"{k} = ?")
                args.append(v)
        args.append(task_id)

        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
                args,
            )
            await db.commit()
            return cur.rowcount > 0

    async def cancel_task(self, task_id: str) -> TaskRecord | None:
        """Mark a task cancelled (audit3_cc).

        Returns the **post-update** record, or ``None`` if the task does not
        exist.  Already-terminal tasks are returned unchanged (caller should
        not double-cancel).
        """
        rec = await self.get_task(task_id)
        if rec is None:
            return None
        if TaskStatus.is_terminal(rec.status):
            return rec
        await self.update_task(task_id, status=TaskStatus.CANCELLED.value)
        return await self.get_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        import aiosqlite

        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            await db.execute("UPDATE assets SET task_id = NULL WHERE task_id = ?", (task_id,))
            await db.commit()
            return cur.rowcount > 0

    # ── assets ──────────────────────────────────────────────────────────

    async def add_asset(
        self,
        *,
        task_id: str | None,
        asset_type: str,
        file_path: str,
        size_bytes: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        import aiosqlite

        await self.init()
        aid = _new_id("a_")
        now = time.time()
        cols = ["id", "task_id", "type", "file_path", "size_bytes", "created_at"]
        vals: list[Any] = [aid, task_id, asset_type, file_path, size_bytes, now]
        if extra:
            for k, v in extra.items():
                cols.append(k)
                vals.append(v)
        placeholders = ", ".join("?" for _ in cols)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO assets ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            await db.commit()
        return aid

    async def list_assets(
        self,
        *,
        task_id: str | None = None,
        asset_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        import aiosqlite

        await self.init()
        sql = "SELECT * FROM assets"
        args: list[Any] = []
        clauses: list[str] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            args.append(task_id)
        if asset_type:
            clauses.append("type = ?")
            args.append(asset_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, args) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── config ──────────────────────────────────────────────────────────

    async def get_config(self) -> dict[str, str]:
        import aiosqlite

        await self.init()
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute("SELECT key, value FROM config") as cur,
        ):
            rows = await cur.fetchall()
        return dict(rows)

    async def set_config(self, updates: dict[str, str]) -> None:
        import aiosqlite

        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            for k, v in updates.items():
                await db.execute(
                    "INSERT INTO config (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, str(v)),
                )
            await db.commit()


# ── helpers ──────────────────────────────────────────────────────────────


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _row_to_record(row: Any) -> TaskRecord:
    """aiosqlite.Row → TaskRecord."""
    d = dict(row) if hasattr(row, "keys") else {}
    base_keys = {c[0] for c in _BASE_TASK_COLUMNS}
    extra = {k: v for k, v in d.items() if k not in base_keys and not k.endswith("_json")}
    try:
        params = json.loads(d.get("params_json") or "{}")
    except (TypeError, ValueError):
        params = {}
    try:
        result = json.loads(d.get("result_json") or "{}")
    except (TypeError, ValueError):
        result = {}
    return TaskRecord(
        id=d.get("id") or "",
        vendor_task_id=d.get("vendor_task_id"),
        status=d.get("status") or TaskStatus.PENDING.value,
        prompt=d.get("prompt") or "",
        params=params,
        result=result,
        error_message=d.get("error_message"),
        created_at=float(d.get("created_at") or 0.0),
        updated_at=float(d.get("updated_at") or 0.0),
        extra=extra,
    )
