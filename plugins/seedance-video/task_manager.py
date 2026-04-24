"""Task manager — SQLite-backed CRUD for Seedance generation tasks and assets."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    ark_task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    prompt TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 't2v',
    model TEXT NOT NULL DEFAULT '2.0',
    params_json TEXT NOT NULL DEFAULT '{}',
    video_url TEXT,
    local_video_path TEXT,
    thumbnail_path TEXT,
    service_tier TEXT NOT NULL DEFAULT 'default',
    is_draft INTEGER NOT NULL DEFAULT 0,
    draft_parent_id TEXT,
    callback_url TEXT,
    revised_prompt TEXT,
    last_frame_url TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    original_name TEXT,
    size_bytes INTEGER,
    width INTEGER,
    height INTEGER,
    duration_sec REAL,
    validated_at REAL,
    validation_result_json TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assets_task ON assets(task_id);
"""

DEFAULT_CONFIG = {
    "ark_api_key": "",
    "output_dir": "",
    "assets_dir": "",
    "cache_dir": "",
    "output_subdir_mode": "date",
    "output_naming_rule": "{date}_{prompt_prefix}",
    "auto_download": "true",
    "service_tier_default": "default",
    "callback_url": "",
    "poll_interval": "15",
    "llm_optimize_level": "professional",
}


class TaskManager:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._init_default_config()
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _init_default_config(self) -> None:
        assert self._db
        for key, val in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, val),
            )

    # ── Config ──

    async def get_config(self, key: str) -> str:
        assert self._db
        row = await self._db.execute_fetchall(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        if row:
            return row[0][0]
        return DEFAULT_CONFIG.get(key, "")

    async def get_all_config(self) -> dict[str, str]:
        assert self._db
        rows = await self._db.execute_fetchall("SELECT key, value FROM config")
        return {r[0]: r[1] for r in rows}

    async def set_config(self, key: str, value: str) -> None:
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        assert self._db
        for k, v in updates.items():
            await self._db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
        await self._db.commit()
        if "ark_api_key" in updates:
            saved = await self.get_config("ark_api_key")
            logger.info("API key saved, verify read-back matches: %s", saved == updates["ark_api_key"])

    # ── Tasks ──

    async def create_task(self, **kwargs: Any) -> dict:
        assert self._db
        task_id = kwargs.get("id") or str(uuid.uuid4())[:12]
        now = time.time()
        params = {
            "id": task_id,
            "ark_task_id": kwargs.get("ark_task_id", ""),
            "status": kwargs.get("status", "pending"),
            "prompt": kwargs.get("prompt", ""),
            "mode": kwargs.get("mode", "t2v"),
            "model": kwargs.get("model", "2.0"),
            "params_json": json.dumps(kwargs.get("params", {})),
            "service_tier": kwargs.get("service_tier", "default"),
            "is_draft": 1 if kwargs.get("is_draft") else 0,
            "draft_parent_id": kwargs.get("draft_parent_id"),
            "callback_url": kwargs.get("callback_url"),
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(params.keys())
        placeholders = ", ".join(["?"] * len(params))
        await self._db.execute(
            f"INSERT INTO tasks ({cols}) VALUES ({placeholders})",
            tuple(params.values()),
        )
        await self._db.commit()
        return {**params, "params": kwargs.get("params", {})}

    async def get_task(self, task_id: str) -> dict | None:
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(
        self,
        status: str | None = None,
        is_draft: bool | None = None,
        service_tier: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict], int]:
        assert self._db
        wheres: list[str] = []
        args: list[Any] = []
        if status:
            wheres.append("status = ?")
            args.append(status)
        if is_draft is not None:
            wheres.append("is_draft = ?")
            args.append(1 if is_draft else 0)
        if service_tier:
            wheres.append("service_tier = ?")
            args.append(service_tier)
        where_sql = " WHERE " + " AND ".join(wheres) if wheres else ""

        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM tasks{where_sql}", args
        )
        total = count_rows[0][0] if count_rows else 0

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        tasks = [self._row_to_task(r) for r in rows]
        return tasks, total

    # Whitelist of caller-facing keys → physical column names.  Anything outside
    # this map is rejected to prevent SQL injection via column-name interpolation
    # in the UPDATE statement below (Sprint 7 / A4 hardening).
    _UPDATABLE_COLUMNS: dict[str, str] = {
        "ark_task_id": "ark_task_id",
        "status": "status",
        "prompt": "prompt",
        "mode": "mode",
        "model": "model",
        "params": "params_json",
        "video_url": "video_url",
        "local_video_path": "local_video_path",
        "thumbnail_path": "thumbnail_path",
        "service_tier": "service_tier",
        "is_draft": "is_draft",
        "draft_parent_id": "draft_parent_id",
        "callback_url": "callback_url",
        "revised_prompt": "revised_prompt",
        "last_frame_url": "last_frame_url",
        "error_message": "error_message",
    }
    _JSON_ENCODED_KEYS: frozenset[str] = frozenset({"params"})

    async def update_task(self, task_id: str, **kwargs: Any) -> bool:
        assert self._db
        if not kwargs:
            return False
        sets: list[str] = []
        args: list[Any] = []
        for k, v in kwargs.items():
            col = self._UPDATABLE_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(self._UPDATABLE_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in self._JSON_ENCODED_KEYS:
                args.append(json.dumps(v, ensure_ascii=False))
            elif k == "is_draft":
                args.append(1 if v else 0)
            else:
                args.append(v)
        sets.append("updated_at = ?")
        args.append(time.time())
        args.append(task_id)
        result = await self._db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args
        )
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def delete_task(self, task_id: str) -> bool:
        assert self._db
        result = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def get_running_tasks(self) -> list[dict]:
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE status IN ('pending', 'running') ORDER BY created_at"
        )
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row: Any) -> dict:
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json", "{}"))
        d["is_draft"] = bool(d.get("is_draft"))
        return d

    # ── Assets ──

    async def create_asset(self, **kwargs: Any) -> dict:
        assert self._db
        asset_id = kwargs.get("id") or str(uuid.uuid4())[:12]
        now = time.time()
        params = {
            "id": asset_id,
            "task_id": kwargs.get("task_id"),
            "type": kwargs.get("type", "image"),
            "file_path": kwargs.get("file_path", ""),
            "role": kwargs.get("role", ""),
            "sort_order": kwargs.get("sort_order", 0),
            "original_name": kwargs.get("original_name"),
            "size_bytes": kwargs.get("size_bytes"),
            "width": kwargs.get("width"),
            "height": kwargs.get("height"),
            "duration_sec": kwargs.get("duration_sec"),
            "created_at": now,
        }
        cols = ", ".join(params.keys())
        placeholders = ", ".join(["?"] * len(params))
        await self._db.execute(
            f"INSERT INTO assets ({cols}) VALUES ({placeholders})",
            tuple(params.values()),
        )
        await self._db.commit()
        return params

    async def list_assets(
        self,
        asset_type: str | None = None,
        task_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict], int]:
        assert self._db
        wheres: list[str] = []
        args: list[Any] = []
        if asset_type:
            wheres.append("type = ?")
            args.append(asset_type)
        if task_id:
            wheres.append("task_id = ?")
            args.append(task_id)
        where_sql = " WHERE " + " AND ".join(wheres) if wheres else ""

        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM assets{where_sql}", args
        )
        total = count_rows[0][0] if count_rows else 0
        rows = await self._db.execute_fetchall(
            f"SELECT * FROM assets{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        return [dict(r) for r in rows], total

    async def get_asset(self, asset_id: str) -> dict | None:
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM assets WHERE id = ?", (asset_id,)
        )
        return dict(rows[0]) if rows else None

    async def delete_asset(self, asset_id: str) -> bool:
        assert self._db
        result = await self._db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def count_asset_references(self, asset_id: str) -> int:
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT COUNT(*) FROM assets WHERE id = ? AND task_id IS NOT NULL",
            (asset_id,),
        )
        return rows[0][0] if rows else 0

