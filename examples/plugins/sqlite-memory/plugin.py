"""sqlite-memory: in-process SQLite memory backend with full-text search."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend:
    """MemoryBackendProtocol implementation backed by a single SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._session_id: str = ""
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}',
                created_at  REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL
            )
        """)
        self._conn.commit()

    async def store(self, memory: dict) -> str:
        mem_id = str(uuid.uuid4())
        content = memory.get("content", "") or json.dumps(memory, ensure_ascii=False)
        metadata = json.dumps(
            {k: v for k, v in memory.items() if k != "content"},
            ensure_ascii=False,
        )
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO memories (id, content, metadata, created_at) VALUES (?, ?, ?, ?)",
            (mem_id, content, metadata, time.time()),
        )
        self._conn.commit()
        logger.info("[sqlite-memory] stored id=%s len=%d", mem_id, len(content))
        return mem_id

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        assert self._conn is not None
        pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT id, content, metadata, created_at FROM memories "
            "WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (pattern, limit),
        ).fetchall()
        results = []
        for row in rows:
            meta = json.loads(row[2]) if row[2] else {}
            results.append({
                "id": row[0],
                "content": row[1],
                "metadata": meta,
                "created_at": row[3],
            })
        logger.info("[sqlite-memory] search q=%r found=%d", query, len(results))
        return results

    async def delete(self, memory_id: str) -> bool:
        assert self._conn is not None
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        deleted = cur.rowcount > 0
        logger.info("[sqlite-memory] delete id=%s ok=%s", memory_id, deleted)
        return deleted

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        results = await self.search(query, limit=20)
        if not results:
            return ""
        lines: list[str] = []
        total = 0
        for r in results:
            text = r["content"]
            est_tokens = len(text) // 3
            if total + est_tokens > max_tokens:
                break
            lines.append(text)
            total += est_tokens
        return "\n---\n".join(lines)

    async def start_session(self, session_id: str) -> None:
        self._session_id = session_id
        logger.info("[sqlite-memory] start_session session_id=%s", session_id)

    async def end_session(self) -> None:
        logger.info("[sqlite-memory] end_session session_id=%s", self._session_id)
        self._session_id = ""

    async def record_turn(self, role: str, content: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (self._session_id, role, content, time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        cfg = api.get_config()
        data_dir = api.get_data_dir()
        db_path = cfg.get("db_path") if cfg else None
        if not db_path and data_dir:
            db_path = str(Path(data_dir) / "memory.db")
        if not db_path:
            db_path = ":memory:"

        self._backend = SQLiteMemoryBackend(db_path)
        api.register_memory_backend(self._backend)
        api.log(f"SQLite memory backend loaded, db={db_path}")

    def on_unload(self) -> None:
        if hasattr(self, "_backend"):
            self._backend.close()

