"""Task manager — SQLite-backed CRUD for transcribe-archive jobs.

Mirrors the seedance-video / bgm-suggester / storyboard pattern so a
plugin author who has read one of those can drop into this one.  Key
shared invariants:

* WAL journal mode — concurrent reads while a writer is running.
* ``_UPDATABLE_COLUMNS`` allow-list — the same Sprint 7 / A4 hardening
  that protects seedance-video from SQL injection via column-name
  interpolation in the UPDATE statement.
* JSON-encoded blob columns (``params_json`` / ``result_json``) — keeps
  the schema flat while letting per-task payloads grow without
  migrations.
* No FOREIGN KEYs to other plugins' tables — every plugin owns its own
  database file (``data.own`` permission), never reaches into another's.

Schema columns (the *minimum* for a transcribe job):

* ``id``                 — UUID4 prefix (12 chars, like seedance).
* ``status``             — pending / running / succeeded / failed /
                            cancelled (matches ``contrib.TaskStatus``).
* ``audio_path``         — local file path the user uploaded.
* ``language``           — BCP-47 tag (``zh`` / ``en`` / ``zh-CN``).
* ``provider_id``        — which adapter ran the job (``stub`` /
                            ``whisper`` / ``scribe`` / ...).
* ``params_json``        — provider knobs (chunk size, overlap, …).
* ``result_json``        — `TranscriptResult.to_dict()` once finished.
* ``verification_json``  — D2.10 envelope rendered at success time so
                            the API can serve it without recomputing.
* ``error_message``      — short human-readable reason on failure.
* ``created_at`` / ``updated_at`` — float seconds since epoch.

Why no separate ``words`` / ``chunks`` tables: a transcript is small
(< 1 MB even for a 3-hour podcast) and only ever read as a whole;
querying individual words from SQL would cost more than parsing the
JSON every time.  When a future plugin needs word-level search we'll
either add an FTS5 virtual table or swap to a vector store — neither
benefits from a relational schema today.
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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    audio_path TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh',
    provider_id TEXT NOT NULL DEFAULT 'stub',
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    verification_json TEXT,
    error_message TEXT,
    chunks_total INTEGER NOT NULL DEFAULT 0,
    chunks_done INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
"""


# Defaults that the host's setup-center can tweak via the ``/config`` route.
# Keys MUST be strings (config table is TEXT-only); callers cast as needed.
DEFAULT_CONFIG: dict[str, str] = {
    "default_provider": "stub",
    "default_language": "zh",
    "chunk_duration_sec": "60",
    "chunk_overlap_sec": "5",
    "cache_dir": "",  # empty → engine uses ``data_dir / 'transcribe_cache'``
    "max_concurrent_chunks": "3",  # parallelism per task
    "max_concurrent_tasks": "2",   # parallelism across tasks
    # contrib.asr providers — drop-in replacement for the old per-vendor
    # whisper / scribe credentials. Phase 2-05 of the overhaul playbook.
    "asr_region": "cn",
    "dashscope_api_key": "",
    "whisper_local_binary": "whisper-cli",
    "whisper_local_model": "base",
    # legacy cloud knobs (kept for backwards-compat with existing UIs):
    "whisper_api_key": "",
    "whisper_model": "whisper-1",
    "scribe_api_key": "",
    "scribe_model_id": "scribe_v1",
}


class TranscribeTaskManager:
    """Async SQLite wrapper.

    Lifecycle: construct → ``await init()`` → use → ``await close()``
    in ``Plugin.on_unload``.  Calling any CRUD method before ``init()``
    raises ``AssertionError`` (we'd rather crash loudly than silently
    swallow the bug).
    """

    # Allow-list of caller-facing keys → physical column names (Sprint 7
    # / A4 hardening — copied from seedance-video).  NEVER add a key here
    # whose value is a column name supplied by user input.
    _UPDATABLE_COLUMNS: dict[str, str] = {
        "status": "status",
        "audio_path": "audio_path",
        "language": "language",
        "provider_id": "provider_id",
        "params": "params_json",
        "result": "result_json",
        "verification": "verification_json",
        "error_message": "error_message",
        "chunks_total": "chunks_total",
        "chunks_done": "chunks_done",
    }
    _JSON_ENCODED_KEYS: frozenset[str] = frozenset({"params", "result", "verification"})

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

    # ── config ────────────────────────────────────────────────────────

    async def _init_default_config(self) -> None:
        assert self._db
        for key, val in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, val),
            )

    async def get_config(self, key: str) -> str:
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        if rows:
            return rows[0][0]
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
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (k, v),
            )
        await self._db.commit()

    # ── tasks ─────────────────────────────────────────────────────────

    async def create_task(self, **kwargs: Any) -> dict:
        assert self._db
        task_id = kwargs.get("id") or str(uuid.uuid4())[:12]
        now = time.time()
        params = {
            "id": task_id,
            "status": kwargs.get("status", "pending"),
            "audio_path": kwargs.get("audio_path", ""),
            "language": kwargs.get("language", "zh"),
            "provider_id": kwargs.get("provider_id", "stub"),
            "params_json": json.dumps(
                kwargs.get("params", {}), ensure_ascii=False,
            ),
            "chunks_total": int(kwargs.get("chunks_total", 0)),
            "chunks_done": int(kwargs.get("chunks_done", 0)),
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
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict], int]:
        assert self._db
        wheres: list[str] = []
        args: list[Any] = []
        if status:
            wheres.append("status = ?")
            args.append(status)
        where_sql = " WHERE " + " AND ".join(wheres) if wheres else ""

        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM tasks{where_sql}", args
        )
        total = count_rows[0][0] if count_rows else 0

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where_sql} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        return [self._row_to_task(r) for r in rows], total

    async def update_task(self, task_id: str, **kwargs: Any) -> bool:
        """Update one or more whitelisted columns.

        Sprint 7 / A4 hardening: only keys present in
        :attr:`_UPDATABLE_COLUMNS` are accepted; everything else raises
        :class:`ValueError` so a programmer error surfaces in tests
        rather than enabling a SQL-injection foothold.
        """
        assert self._db
        if not kwargs:
            return False
        sets: list[str] = []
        args: list[Any] = []
        for k, v in kwargs.items():
            col = self._UPDATABLE_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task: column {k!r} is not whitelisted "
                    f"(allowed: {sorted(self._UPDATABLE_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in self._JSON_ENCODED_KEYS:
                args.append(json.dumps(v, ensure_ascii=False))
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
        result = await self._db.execute(
            "DELETE FROM tasks WHERE id = ?", (task_id,)
        )
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def get_running_tasks(self) -> list[dict]:
        """Used by ``on_load`` to resume / fail-mark tasks that were
        running when the host last crashed."""
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE status IN ('pending', 'running') "
            "ORDER BY created_at"
        )
        return [self._row_to_task(r) for r in rows]

    @staticmethod
    def _row_to_task(row: Any) -> dict:
        d = dict(row)
        # Decode JSON blobs lazily — failures swallowed and replaced
        # with empty equivalents because a corrupt blob from an old
        # plugin version must not break ``GET /tasks/{id}`` for the
        # whole UI.
        for key, default in (
            ("params_json", {}),
            ("result_json", None),
            ("verification_json", None),
        ):
            raw = d.pop(key, None)
            new_key = key.removesuffix("_json")
            if raw is None or raw == "":
                d[new_key] = default
                continue
            try:
                d[new_key] = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning(
                    "transcribe-archive: corrupt %s for task %s; "
                    "returning default", key, d.get("id"),
                )
                d[new_key] = default
        return d


__all__ = [
    "DEFAULT_CONFIG",
    "SCHEMA_SQL",
    "TranscribeTaskManager",
]
