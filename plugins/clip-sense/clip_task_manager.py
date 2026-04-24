"""SQLite-backed task, transcript, and config manager for clip-sense.

Modeled after tongyi-image's tongyi_task_manager.py: aiosqlite, Row factory,
WAL mode, _UPDATABLE_COLUMNS whitelist.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite


DEFAULT_CONFIG: dict[str, str] = {
    "dashscope_api_key": "",
    "ffmpeg_path": "",
    "default_silence_threshold": "-40",
    "default_subtitle": "false",
    "default_output_format": "mp4",
    "poll_interval": "3",
}


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class TaskManager:
    """Async SQLite manager for clip-sense tasks, transcripts, and config."""

    def __init__(self, db_path: str | Path) -> None:
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

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def _create_tables(self) -> None:
        assert self._db
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                mode TEXT NOT NULL,
                source_video_path TEXT,
                source_duration_sec REAL,
                params_json TEXT,
                transcript_id TEXT,
                output_path TEXT,
                output_duration_sec REAL,
                subtitle_path TEXT,
                segments_json TEXT,
                cost_json TEXT,
                error_kind TEXT,
                error_message TEXT,
                error_hints_json TEXT,
                pipeline_step TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_mode ON tasks(mode);

            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                source_hash TEXT UNIQUE,
                source_path TEXT,
                source_name TEXT,
                duration_sec REAL,
                sentences_json TEXT,
                full_text TEXT,
                language TEXT,
                status TEXT DEFAULT 'pending',
                api_task_id TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transcripts_hash ON transcripts(source_hash);

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        await self._db.commit()

    async def _seed_config(self) -> None:
        assert self._db
        for k, v in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Config CRUD
    # ------------------------------------------------------------------

    async def get_config(self, key: str) -> str | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
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
    # Task CRUD
    # ------------------------------------------------------------------

    _UPDATABLE_TASK_COLUMNS: dict[str, str] = {
        "status": "status",
        "mode": "mode",
        "source_video_path": "source_video_path",
        "source_duration_sec": "source_duration_sec",
        "params": "params_json",
        "transcript_id": "transcript_id",
        "output_path": "output_path",
        "output_duration_sec": "output_duration_sec",
        "subtitle_path": "subtitle_path",
        "segments": "segments_json",
        "cost": "cost_json",
        "error_kind": "error_kind",
        "error_message": "error_message",
        "error_hints": "error_hints_json",
        "pipeline_step": "pipeline_step",
    }
    _TASK_JSON_KEYS: frozenset[str] = frozenset(
        {"params", "segments", "cost", "error_hints"}
    )

    async def create_task(
        self,
        *,
        mode: str,
        source_video_path: str = "",
        source_duration_sec: float | None = None,
        params: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> dict[str, Any]:
        assert self._db
        task_id = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO tasks
               (id, status, mode, source_video_path, source_duration_sec,
                params_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                status,
                mode,
                source_video_path,
                source_duration_sec,
                json.dumps(params or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self._db.commit()
        return await self.get_task(task_id)  # type: ignore[return-value]

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        row = await cur.fetchone()
        return self._task_row_to_dict(row) if row else None

    async def update_task(self, task_id: str, **updates: Any) -> None:
        assert self._db
        if not updates:
            return
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in updates.items():
            col = self._UPDATABLE_TASK_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(self._UPDATABLE_TASK_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in self._TASK_JSON_KEYS:
                vals.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
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
        cur = await self._db.execute(
            "DELETE FROM tasks WHERE id = ?", (task_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        mode: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        assert self._db
        where_parts: list[str] = []
        params: list[Any] = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if mode:
            where_parts.append("mode = ?")
            params.append(mode)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        cur = await self._db.execute(
            f"SELECT COUNT(*) as cnt FROM tasks {where}", params
        )
        total = (await cur.fetchone())["cnt"]

        cur = await self._db.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = await cur.fetchall()
        return {"tasks": [self._task_row_to_dict(r) for r in rows], "total": total}

    async def get_running_tasks(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM tasks WHERE status IN ('pending', 'running')"
        )
        rows = await cur.fetchall()
        return [self._task_row_to_dict(r) for r in rows]

    @staticmethod
    def _task_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("params_json", "segments_json", "cost_json", "error_hints_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        return d

    # ------------------------------------------------------------------
    # Transcript CRUD
    # ------------------------------------------------------------------

    _UPDATABLE_TRANSCRIPT_COLUMNS: dict[str, str] = {
        "status": "status",
        "source_path": "source_path",
        "source_name": "source_name",
        "duration_sec": "duration_sec",
        "sentences": "sentences_json",
        "full_text": "full_text",
        "language": "language",
        "api_task_id": "api_task_id",
        "error_message": "error_message",
    }
    _TRANSCRIPT_JSON_KEYS: frozenset[str] = frozenset({"sentences"})

    async def create_transcript(
        self,
        *,
        source_hash: str,
        source_path: str = "",
        source_name: str = "",
        duration_sec: float | None = None,
    ) -> dict[str, Any]:
        assert self._db
        tid = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO transcripts
               (id, source_hash, source_path, source_name, duration_sec,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (tid, source_hash, source_path, source_name, duration_sec, now, now),
        )
        await self._db.commit()
        return await self.get_transcript(tid)  # type: ignore[return-value]

    async def get_transcript(self, tid: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM transcripts WHERE id = ?", (tid,)
        )
        row = await cur.fetchone()
        return self._transcript_row_to_dict(row) if row else None

    async def get_transcript_by_hash(self, source_hash: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM transcripts WHERE source_hash = ?", (source_hash,)
        )
        row = await cur.fetchone()
        return self._transcript_row_to_dict(row) if row else None

    async def update_transcript(self, tid: str, **updates: Any) -> None:
        assert self._db
        if not updates:
            return
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in updates.items():
            col = self._UPDATABLE_TRANSCRIPT_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_transcript: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(self._UPDATABLE_TRANSCRIPT_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in self._TRANSCRIPT_JSON_KEYS:
                vals.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
            else:
                vals.append(v)
        sets.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(tid)
        sql = f"UPDATE transcripts SET {', '.join(sets)} WHERE id = ?"
        await self._db.execute(sql, vals)
        await self._db.commit()

    async def delete_transcript(self, tid: str) -> bool:
        assert self._db
        cur = await self._db.execute(
            "DELETE FROM transcripts WHERE id = ?", (tid,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_transcripts(
        self, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        assert self._db
        cur = await self._db.execute("SELECT COUNT(*) as cnt FROM transcripts")
        total = (await cur.fetchone())["cnt"]

        cur = await self._db.execute(
            "SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        )
        rows = await cur.fetchall()
        return {
            "transcripts": [self._transcript_row_to_dict(r) for r in rows],
            "total": total,
        }

    @staticmethod
    def _transcript_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        val = d.pop("sentences_json", None)
        try:
            d["sentences"] = json.loads(val) if val else None
        except (json.JSONDecodeError, TypeError):
            d["sentences"] = None
        return d
