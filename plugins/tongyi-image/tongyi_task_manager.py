"""SQLite-backed task and config manager for the Tongyi Image plugin."""

from __future__ import annotations

import json
import uuid
import time
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_CONFIG: dict[str, str] = {
    "dashscope_api_key": "",
    "dashscope_base_url": "",
    "default_model": "wan27-pro",
    "default_size": "2K",
    "auto_download": "true",
    "output_dir": "",
    "poll_interval": "10",
    "watermark": "false",
}


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class TaskManager:
    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._create_tables()
        await self._seed_config()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        assert self._db
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                api_task_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                prompt TEXT,
                negative_prompt TEXT,
                model TEXT,
                mode TEXT,
                params_json TEXT,
                image_urls TEXT,
                local_image_paths TEXT,
                error_message TEXT,
                usage_json TEXT,
                asset_ids TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_mode ON tasks(mode);

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        # 兼容旧库：旧版本没有 asset_ids 列，ALTER TABLE ADD COLUMN
        # 在已有列时会失败，吞掉即可。
        try:
            await self._db.execute("ALTER TABLE tasks ADD COLUMN asset_ids TEXT")
        except Exception:
            pass
        await self._db.commit()

    async def _seed_config(self) -> None:
        assert self._db
        for k, v in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    async def get_config(self, key: str) -> str | None:
        assert self._db
        cur = await self._db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def get_all_config(self) -> dict[str, str]:
        assert self._db
        cur = await self._db.execute("SELECT key, value FROM config")
        rows = await cur.fetchall()
        return {r["key"]: r["value"] for r in rows}

    async def set_config(self, key: str, value: str) -> None:
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
        )
        await self._db.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        assert self._db
        for k, v in updates.items():
            await self._db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    async def create_task(
        self,
        *,
        prompt: str = "",
        negative_prompt: str = "",
        model: str = "",
        mode: str = "text2img",
        params: dict | None = None,
        api_task_id: str | None = None,
        status: str = "pending",
        image_urls: list[str] | None = None,
    ) -> dict:
        assert self._db
        task_id = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO tasks
               (id, api_task_id, status, prompt, negative_prompt, model, mode,
                params_json, image_urls, local_image_paths, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                api_task_id or "",
                status,
                prompt,
                negative_prompt,
                model,
                mode,
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(image_urls or []),
                json.dumps([]),
                now,
                now,
            ),
        )
        await self._db.commit()
        return await self.get_task(task_id)  # type: ignore

    async def get_task(self, task_id: str) -> dict | None:
        assert self._db
        cur = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

    # Whitelist of caller-facing keys → physical column names. Anything
    # outside this map is rejected to prevent SQL injection via column-name
    # interpolation in the UPDATE statement below.
    _UPDATABLE_COLUMNS: dict[str, str] = {
        "status": "status",
        "error_message": "error_message",
        "api_task_id": "api_task_id",
        "prompt": "prompt",
        "negative_prompt": "negative_prompt",
        "model": "model",
        "mode": "mode",
        "params": "params_json",
        "image_urls": "image_urls",
        "local_image_paths": "local_image_paths",
        "usage": "usage_json",
        "asset_ids": "asset_ids",
    }
    _JSON_ENCODED_KEYS: frozenset[str] = frozenset(
        {"params", "image_urls", "local_image_paths", "usage", "asset_ids"}
    )

    async def update_task(self, task_id: str, **updates: Any) -> None:
        assert self._db
        if not updates:
            return
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in updates.items():
            col = self._UPDATABLE_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(self._UPDATABLE_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in self._JSON_ENCODED_KEYS:
                vals.append(json.dumps(v, ensure_ascii=False))
            else:
                vals.append(v)
        sets.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
        await self._db.execute(sql, vals)
        await self._db.commit()

    async def delete_task(self, task_id: str) -> bool:
        assert self._db
        cur = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        mode: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        assert self._db
        where_clauses = []
        params: list[Any] = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if mode:
            where_clauses.append("mode = ?")
            params.append(mode)
        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur = await self._db.execute(f"SELECT COUNT(*) as cnt FROM tasks {where}", params)
        total = (await cur.fetchone())["cnt"]

        cur = await self._db.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = await cur.fetchall()
        return {"tasks": [self._row_to_dict(r) for r in rows], "total": total}

    async def get_running_tasks(self) -> list[dict]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM tasks WHERE status IN ('pending', 'running')"
        )
        rows = await cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict:
        d = dict(row)
        for jf in ("params_json", "image_urls", "local_image_paths", "usage_json", "asset_ids"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "") if jf.endswith("_json") else jf
            if key == "params_json":
                key = "params"
            try:
                d[key] = json.loads(val) if val else (
                    [] if ("urls" in jf or "paths" in jf or jf == "asset_ids") else {}
                )
            except (json.JSONDecodeError, TypeError):
                d[key] = {} if "json" in jf else []
        return d
