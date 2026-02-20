"""
统一记忆存储 (v2)

SQLite 为唯一结构化主存储，管理所有记忆数据:
- memories: 语义记忆 (含 FTS5 全文索引)
- episodes: 情节记忆
- scratchpad: 工作记忆草稿本
- conversation_turns: 对话原文索引
- extraction_queue: 提取重试队列
- embedding_cache: API Embedding 缓存 (可选)

设计原则:
- SQLite 是唯一真相源, 所有数据先写 SQLite
- FTS5 全文索引通过触发器自动同步
- 向后兼容 v1 schema, 自动迁移
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2


class MemoryStorage:
    """
    统一记忆存储管理器 (v2)

    Usage:
        storage = MemoryStorage(db_path="data/memory/openakita.db")
        storage.save_memory(memory_dict)
        results = storage.search_fts("代码风格")
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ======================================================================
    # Initialization & Migration
    # ======================================================================

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        current_version = self._get_schema_version()
        if current_version < _SCHEMA_VERSION:
            self._migrate_schema(current_version)
        else:
            self._create_tables()

        logger.debug(f"MemoryStorage initialized: {self._db_path} (schema v{_SCHEMA_VERSION})")

    def _get_schema_version(self) -> int:
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cur = self._conn.execute(
                "SELECT value FROM _schema_meta WHERE key = 'version'"
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _set_schema_version(self, version: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', ?)",
            (str(version),),
        )
        self._conn.commit()

    def _migrate_schema(self, from_version: int) -> None:
        """Migrate from old schema to current version."""
        logger.info(f"[MemoryStorage] Migrating schema v{from_version} → v{_SCHEMA_VERSION}")

        self._create_tables()

        if from_version < 2:
            self._migrate_v1_to_v2()

        self._set_schema_version(_SCHEMA_VERSION)
        logger.info("[MemoryStorage] Schema migration complete")

    def _migrate_v1_to_v2(self) -> None:
        """Add v2 columns to existing memories table."""
        new_columns = [
            ("subject", "TEXT DEFAULT ''"),
            ("predicate", "TEXT DEFAULT ''"),
            ("confidence", "REAL DEFAULT 0.5"),
            ("decay_rate", "REAL DEFAULT 0.1"),
            ("last_accessed_at", "TEXT"),
            ("superseded_by", "TEXT"),
            ("source_episode_id", "TEXT"),
        ]
        for col_name, col_def in new_columns:
            try:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def _create_tables(self) -> None:
        c = self._conn

        # --- memories (v2: with subject/predicate/confidence/decay) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'FACT',
                priority TEXT NOT NULL DEFAULT 'SHORT_TERM',
                source TEXT DEFAULT '',
                importance_score REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                metadata TEXT DEFAULT '{}',
                subject TEXT DEFAULT '',
                predicate TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5,
                decay_rate REAL DEFAULT 0.1,
                last_accessed_at TEXT,
                superseded_by TEXT,
                source_episode_id TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_priority ON memories(priority)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject)")

        # --- FTS5 full-text index ---
        try:
            c.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content, subject, predicate, tags,
                    content=memories, content_rowid=rowid,
                    tokenize='unicode61'
                )
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"[MemoryStorage] FTS5 creation skipped: {e}")

        # FTS5 sync triggers
        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
                VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
                VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
                VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
                INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
                VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
            END""",
        ]:
            try:
                c.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass

        # --- episodes ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                goal TEXT DEFAULT '',
                outcome TEXT DEFAULT 'completed',
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                action_nodes TEXT DEFAULT '[]',
                entities TEXT DEFAULT '[]',
                tools_used TEXT DEFAULT '[]',
                linked_memory_ids TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                importance_score REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'session_end'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(started_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome)")

        # --- scratchpad ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS scratchpad (
                user_id TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                active_projects TEXT DEFAULT '[]',
                current_focus TEXT DEFAULT '',
                open_questions TEXT DEFAULT '[]',
                next_steps TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)

        # --- conversation_turns ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_results TEXT,
                has_tool_calls BOOLEAN DEFAULT FALSE,
                timestamp TEXT NOT NULL,
                token_estimate INTEGER,
                episode_id TEXT,
                extracted BOOLEAN DEFAULT FALSE,
                UNIQUE(session_id, turn_index)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON conversation_turns(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_tool ON conversation_turns(has_tool_calls)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_extracted ON conversation_turns(extracted)")

        # --- extraction_queue ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS extraction_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                tool_results TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                last_attempted_at TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_eq_status ON extraction_queue(status)")

        # --- attachments (文件/媒体记忆) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                episode_id TEXT DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                original_filename TEXT DEFAULT '',
                mime_type TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                local_path TEXT DEFAULT '',
                url TEXT DEFAULT '',
                direction TEXT DEFAULT 'inbound',
                description TEXT DEFAULT '',
                transcription TEXT DEFAULT '',
                extracted_text TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                linked_memory_ids TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_session ON attachments(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_mime ON attachments(mime_type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_direction ON attachments(direction)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_created ON attachments(created_at)")

        # FTS5 for attachment search
        try:
            c.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS attachments_fts USING fts5(
                    description, transcription, extracted_text, filename, tags,
                    content=attachments, content_rowid=rowid,
                    tokenize='unicode61'
                )
            """)
        except sqlite3.OperationalError:
            pass

        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS attachments_fts_ai AFTER INSERT ON attachments BEGIN
                INSERT INTO attachments_fts(rowid, description, transcription, extracted_text, filename, tags)
                VALUES (new.rowid, new.description, new.transcription, new.extracted_text, new.filename, new.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS attachments_fts_ad AFTER DELETE ON attachments BEGIN
                INSERT INTO attachments_fts(attachments_fts, rowid, description, transcription, extracted_text, filename, tags)
                VALUES ('delete', old.rowid, old.description, old.transcription, old.extracted_text, old.filename, old.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS attachments_fts_au AFTER UPDATE ON attachments BEGIN
                INSERT INTO attachments_fts(attachments_fts, rowid, description, transcription, extracted_text, filename, tags)
                VALUES ('delete', old.rowid, old.description, old.transcription, old.extracted_text, old.filename, old.tags);
                INSERT INTO attachments_fts(rowid, description, transcription, extracted_text, filename, tags)
                VALUES (new.rowid, new.description, new.transcription, new.extracted_text, new.filename, new.tags);
            END""",
        ]:
            try:
                c.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass

        # --- embedding_cache (for API embedding backend) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER DEFAULT 1024,
                created_at TEXT NOT NULL
            )
        """)

        # --- schema meta ---
        c.execute(
            "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
        )

        c.commit()

    # ======================================================================
    # Semantic Memory CRUD
    # ======================================================================

    def save_memory(self, memory: dict) -> None:
        if not self._conn:
            return
        now = datetime.now().isoformat()
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, content, type, priority, source, importance_score,
                 access_count, tags, created_at, updated_at, expires_at, metadata,
                 subject, predicate, confidence, decay_rate,
                 last_accessed_at, superseded_by, source_episode_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.get("id", ""),
                    memory.get("content", ""),
                    memory.get("type", "FACT"),
                    memory.get("priority", "SHORT_TERM"),
                    memory.get("source", ""),
                    memory.get("importance_score", 0.5),
                    memory.get("access_count", 0),
                    json.dumps(memory.get("tags", []), ensure_ascii=False),
                    memory.get("created_at", now),
                    now,
                    memory.get("expires_at"),
                    json.dumps(memory.get("metadata", {}), ensure_ascii=False),
                    memory.get("subject", ""),
                    memory.get("predicate", ""),
                    memory.get("confidence", 0.5),
                    memory.get("decay_rate", 0.1),
                    memory.get("last_accessed_at"),
                    memory.get("superseded_by"),
                    memory.get("source_episode_id"),
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save memory to SQLite: {e}")

    def save_memories_batch(self, memories: list[dict]) -> None:
        if not self._conn or not memories:
            return
        now = datetime.now().isoformat()
        try:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO memories
                (id, content, type, priority, source, importance_score,
                 access_count, tags, created_at, updated_at, expires_at, metadata,
                 subject, predicate, confidence, decay_rate,
                 last_accessed_at, superseded_by, source_episode_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        m.get("id", ""),
                        m.get("content", ""),
                        m.get("type", "FACT"),
                        m.get("priority", "SHORT_TERM"),
                        m.get("source", ""),
                        m.get("importance_score", 0.5),
                        m.get("access_count", 0),
                        json.dumps(m.get("tags", []), ensure_ascii=False),
                        m.get("created_at", now),
                        now,
                        m.get("expires_at"),
                        json.dumps(m.get("metadata", {}), ensure_ascii=False),
                        m.get("subject", ""),
                        m.get("predicate", ""),
                        m.get("confidence", 0.5),
                        m.get("decay_rate", 0.1),
                        m.get("last_accessed_at"),
                        m.get("superseded_by"),
                        m.get("source_episode_id"),
                    )
                    for m in memories
                ],
            )
            self._conn.commit()
            logger.debug(f"Batch saved {len(memories)} memories to SQLite")
        except Exception as e:
            logger.error(f"Failed to batch save memories: {e}")

    def load_all(self) -> list[dict]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC"
            )
            return self._rows_to_dicts(cursor)
        except Exception as e:
            logger.error(f"Failed to load memories from SQLite: {e}")
            return []

    def get_memory(self, memory_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            )
            rows = self._rows_to_dicts(cursor)
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    def delete_memory(self, memory_id: str) -> bool:
        if not self._conn:
            return False
        try:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            return False

    def update_memory(self, memory_id: str, updates: dict) -> bool:
        """Update specific fields of a memory."""
        if not self._conn or not updates:
            return False
        allowed = {
            "content", "type", "priority", "source", "importance_score",
            "access_count", "tags", "subject", "predicate", "confidence",
            "decay_rate", "last_accessed_at", "superseded_by",
            "source_episode_id", "updated_at", "metadata",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False

        if "tags" in filtered and isinstance(filtered["tags"], list):
            filtered["tags"] = json.dumps(filtered["tags"], ensure_ascii=False)
        if "metadata" in filtered and isinstance(filtered["metadata"], dict):
            filtered["metadata"] = json.dumps(filtered["metadata"], ensure_ascii=False)

        filtered.setdefault("updated_at", datetime.now().isoformat())
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [memory_id]

        try:
            self._conn.execute(
                f"UPDATE memories SET {set_clause} WHERE id = ?", values
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            return False

    def query(
        self,
        *,
        memory_type: str | None = None,
        priority: str | None = None,
        source: str | None = None,
        min_importance: float | None = None,
        subject: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        if not self._conn:
            return []

        conditions: list[str] = []
        params: list[Any] = []

        if memory_type:
            conditions.append("type = ?")
            params.append(memory_type)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if min_importance is not None:
            conditions.append("importance_score >= ?")
            params.append(min_importance)
        if subject:
            conditions.append("subject = ?")
            params.append(subject)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        try:
            cursor = self._conn.execute(
                f"SELECT * FROM memories WHERE {where} "
                f"ORDER BY importance_score DESC, created_at DESC "
                f"LIMIT ? OFFSET ?",
                params,
            )
            return self._rows_to_dicts(cursor)
        except Exception as e:
            logger.error(f"Failed to query memories: {e}")
            return []

    def count(self, memory_type: str | None = None) -> int:
        if not self._conn:
            return 0
        try:
            if memory_type:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE type = ?", (memory_type,)
                )
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM memories")
            return cur.fetchone()[0]
        except Exception:
            return 0

    # ======================================================================
    # FTS5 Search
    # ======================================================================

    def search_fts(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search using FTS5 with BM25 ranking."""
        if not self._conn or not query.strip():
            return []
        try:
            safe_query = self._sanitize_fts_query(query)
            cursor = self._conn.execute(
                """
                SELECT m.*, bm25(memories_fts) AS rank
                FROM memories_fts fts
                JOIN memories m ON m.rowid = fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, limit),
            )
            return self._rows_to_dicts(cursor)
        except Exception as e:
            logger.debug(f"FTS5 search failed (query={query!r}): {e}")
            return []

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Make user input safe for FTS5 MATCH."""
        special = set('"*(){}[]^~:')
        cleaned = "".join(c if c not in special else " " for c in query)
        tokens = cleaned.split()
        if not tokens:
            return '""'
        return " OR ".join(tokens)

    def rebuild_fts_index(self) -> None:
        """Rebuild FTS5 index from scratch (after migration)."""
        if not self._conn:
            return
        try:
            self._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            self._conn.commit()
            logger.info("[MemoryStorage] FTS5 index rebuilt")
        except Exception as e:
            logger.warning(f"[MemoryStorage] FTS5 rebuild failed: {e}")

    # ======================================================================
    # Episode CRUD
    # ======================================================================

    def save_episode(self, episode: dict) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                (id, session_id, summary, goal, outcome, started_at, ended_at,
                 action_nodes, entities, tools_used, linked_memory_ids, tags,
                 importance_score, access_count, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.get("id", ""),
                    episode.get("session_id", ""),
                    episode.get("summary", ""),
                    episode.get("goal", ""),
                    episode.get("outcome", "completed"),
                    episode.get("started_at", ""),
                    episode.get("ended_at", ""),
                    json.dumps(episode.get("action_nodes", []), ensure_ascii=False),
                    json.dumps(episode.get("entities", []), ensure_ascii=False),
                    json.dumps(episode.get("tools_used", []), ensure_ascii=False),
                    json.dumps(episode.get("linked_memory_ids", []), ensure_ascii=False),
                    json.dumps(episode.get("tags", []), ensure_ascii=False),
                    episode.get("importance_score", 0.5),
                    episode.get("access_count", 0),
                    episode.get("source", "session_end"),
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save episode: {e}")

    def get_episode(self, episode_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
            rows = self._rows_to_dicts(cur, json_fields=["action_nodes", "entities", "tools_used", "linked_memory_ids", "tags"])
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get episode {episode_id}: {e}")
            return None

    def search_episodes(
        self,
        *,
        session_id: str | None = None,
        entity: str | None = None,
        tool: str | None = None,
        outcome: str | None = None,
        days: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if not self._conn:
            return []
        conditions: list[str] = []
        params: list[Any] = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if entity:
            conditions.append("entities LIKE ?")
            params.append(f"%{entity}%")
        if tool:
            conditions.append("tools_used LIKE ?")
            params.append(f"%{tool}%")
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        if days:
            cutoff = datetime.now().isoformat()[:10]
            conditions.append("started_at >= date(?, ?)")
            params.extend([cutoff, f"-{days} days"])

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        try:
            cur = self._conn.execute(
                f"SELECT * FROM episodes WHERE {where} ORDER BY started_at DESC LIMIT ?",
                params,
            )
            return self._rows_to_dicts(cur, json_fields=["action_nodes", "entities", "tools_used", "linked_memory_ids", "tags"])
        except Exception as e:
            logger.error(f"Failed to search episodes: {e}")
            return []

    # ======================================================================
    # Scratchpad CRUD
    # ======================================================================

    def get_scratchpad(self, user_id: str = "default") -> dict | None:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute(
                "SELECT * FROM scratchpad WHERE user_id = ?", (user_id,)
            )
            rows = self._rows_to_dicts(cur, json_fields=["active_projects", "open_questions", "next_steps"])
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get scratchpad: {e}")
            return None

    def save_scratchpad(self, scratchpad: dict) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO scratchpad
                (user_id, content, active_projects, current_focus,
                 open_questions, next_steps, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scratchpad.get("user_id", "default"),
                    scratchpad.get("content", ""),
                    json.dumps(scratchpad.get("active_projects", []), ensure_ascii=False),
                    scratchpad.get("current_focus", ""),
                    json.dumps(scratchpad.get("open_questions", []), ensure_ascii=False),
                    json.dumps(scratchpad.get("next_steps", []), ensure_ascii=False),
                    scratchpad.get("updated_at", datetime.now().isoformat()),
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save scratchpad: {e}")

    # ======================================================================
    # Conversation Turns
    # ======================================================================

    def save_turn(
        self,
        session_id: str,
        turn_index: int,
        role: str,
        content: str | None,
        tool_calls: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        timestamp: str | None = None,
        token_estimate: int | None = None,
    ) -> None:
        if not self._conn:
            return
        ts = timestamp or datetime.now().isoformat()
        has_tools = bool(tool_calls)
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO conversation_turns
                (session_id, turn_index, role, content, tool_calls, tool_results,
                 has_tool_calls, timestamp, token_estimate, extracted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                """,
                (
                    session_id,
                    turn_index,
                    role,
                    content,
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                    json.dumps(tool_results, ensure_ascii=False) if tool_results else None,
                    has_tools,
                    ts,
                    token_estimate,
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save turn: {e}")

    def get_unextracted_turns(self, limit: int = 100) -> list[dict]:
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT * FROM conversation_turns WHERE extracted = FALSE "
                "ORDER BY timestamp ASC LIMIT ?",
                (limit,),
            )
            return self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
        except Exception as e:
            logger.error(f"Failed to get unextracted turns: {e}")
            return []

    def mark_turns_extracted(self, session_id: str, turn_indices: list[int]) -> None:
        if not self._conn or not turn_indices:
            return
        placeholders = ",".join("?" * len(turn_indices))
        try:
            self._conn.execute(
                f"UPDATE conversation_turns SET extracted = TRUE "
                f"WHERE session_id = ? AND turn_index IN ({placeholders})",
                [session_id] + turn_indices,
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark turns extracted: {e}")

    def get_session_turns(self, session_id: str) -> list[dict]:
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT * FROM conversation_turns WHERE session_id = ? ORDER BY turn_index",
                (session_id,),
            )
            return self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
        except Exception as e:
            logger.error(f"Failed to get session turns: {e}")
            return []

    def get_max_turn_index(self, session_id: str) -> int:
        """返回下一个可用的 turn_index（用于续接，避免覆盖历史数据）"""
        if not self._conn:
            return 0
        try:
            cur = self._conn.execute(
                "SELECT MAX(turn_index) FROM conversation_turns WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            return (row[0] if row[0] is not None else -1) + 1
        except Exception:
            return 0

    def get_recent_turns(self, session_id: str, limit: int = 20) -> list[dict]:
        """按 turn_index 倒序获取最近 N 轮对话"""
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT role, content, timestamp, tool_calls, tool_results "
                "FROM conversation_turns "
                "WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?",
                (session_id, limit),
            )
            rows = self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
            rows.reverse()
            return rows
        except Exception:
            return []

    # ======================================================================
    # Extraction Queue
    # ======================================================================

    def enqueue_extraction(
        self,
        session_id: str,
        turn_index: int,
        content: str,
        tool_calls: list[dict] | None = None,
        tool_results: list[dict] | None = None,
    ) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO extraction_queue
                (session_id, turn_index, content, tool_calls, tool_results, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_index,
                    content,
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                    json.dumps(tool_results, ensure_ascii=False) if tool_results else None,
                    datetime.now().isoformat(),
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to enqueue extraction: {e}")

    def dequeue_extraction(self, batch_size: int = 10) -> list[dict]:
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT * FROM extraction_queue WHERE status = 'pending' "
                "AND retry_count < max_retries "
                "ORDER BY created_at ASC LIMIT ?",
                (batch_size,),
            )
            rows = self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"UPDATE extraction_queue SET status = 'processing', "
                    f"last_attempted_at = ?, retry_count = retry_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    [datetime.now().isoformat()] + ids,
                )
                self._conn.commit()
            return rows
        except Exception as e:
            logger.error(f"Failed to dequeue extraction: {e}")
            return []

    def complete_extraction(self, queue_id: int, success: bool = True) -> None:
        if not self._conn:
            return
        status = "completed" if success else "failed"
        try:
            self._conn.execute(
                "UPDATE extraction_queue SET status = ? WHERE id = ?",
                (status, queue_id),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to complete extraction {queue_id}: {e}")

    # ======================================================================
    # Embedding Cache (for API embedding backend)
    # ======================================================================

    def get_cached_embedding(self, content_hash: str) -> bytes | None:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute(
                "SELECT embedding FROM embedding_cache WHERE content_hash = ?",
                (content_hash,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def save_cached_embedding(
        self, content_hash: str, embedding: bytes, model: str, dimensions: int = 1024
    ) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO embedding_cache
                (content_hash, embedding, model, dimensions, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (content_hash, embedding, model, dimensions, datetime.now().isoformat()),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to cache embedding: {e}")

    # ======================================================================
    # Attachments (文件/媒体记忆)
    # ======================================================================

    def save_attachment(self, data: dict) -> None:
        if not self._conn:
            return
        try:
            tags_val = data.get("tags", [])
            if isinstance(tags_val, list):
                tags_val = json.dumps(tags_val, ensure_ascii=False)
            linked_val = data.get("linked_memory_ids", [])
            if isinstance(linked_val, list):
                linked_val = json.dumps(linked_val, ensure_ascii=False)

            self._conn.execute(
                """INSERT OR REPLACE INTO attachments
                   (id, session_id, episode_id, filename, original_filename,
                    mime_type, file_size, local_path, url, direction,
                    description, transcription, extracted_text, tags,
                    linked_memory_ids, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["id"],
                    data.get("session_id", ""),
                    data.get("episode_id", ""),
                    data.get("filename", ""),
                    data.get("original_filename", ""),
                    data.get("mime_type", ""),
                    data.get("file_size", 0),
                    data.get("local_path", ""),
                    data.get("url", ""),
                    data.get("direction", "inbound"),
                    data.get("description", ""),
                    data.get("transcription", ""),
                    data.get("extracted_text", ""),
                    tags_val,
                    linked_val,
                    data.get("created_at", datetime.now().isoformat()),
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save attachment {data.get('id')}: {e}")

    def get_attachment(self, attachment_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            )
            rows = self._rows_to_dicts(cursor, json_fields=["linked_memory_ids"])
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get attachment {attachment_id}: {e}")
        return None

    def search_attachments(
        self,
        query: str = "",
        mime_type: str | None = None,
        direction: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if not self._conn:
            return []
        try:
            if query:
                safe_query = self._sanitize_fts_query(query)
                results = []
                try:
                    cursor = self._conn.execute(
                        """SELECT a.* FROM attachments a
                           JOIN attachments_fts f ON a.rowid = f.rowid
                           WHERE attachments_fts MATCH ?
                           ORDER BY rank
                           LIMIT ?""",
                        (safe_query, limit * 3),
                    )
                    results = self._rows_to_dicts(cursor, json_fields=["linked_memory_ids"])
                except sqlite3.OperationalError:
                    pass

                if not results:
                    like_q = f"%{query}%"
                    cursor = self._conn.execute(
                        """SELECT * FROM attachments
                           WHERE description LIKE ? OR filename LIKE ?
                                 OR transcription LIKE ? OR extracted_text LIKE ?
                           ORDER BY created_at DESC LIMIT ?""",
                        (like_q, like_q, like_q, like_q, limit * 3),
                    )
                    results = self._rows_to_dicts(cursor, json_fields=["linked_memory_ids"])
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM attachments ORDER BY created_at DESC LIMIT ?",
                    (limit * 3,),
                )
                results = self._rows_to_dicts(cursor, json_fields=["linked_memory_ids"])

            if mime_type:
                results = [r for r in results if r.get("mime_type", "").startswith(mime_type)]
            if direction:
                results = [r for r in results if r.get("direction") == direction]
            if session_id:
                results = [r for r in results if r.get("session_id") == session_id]

            return results[:limit]
        except Exception as e:
            logger.error(f"Failed to search attachments: {e}")
            return []

    def delete_attachment(self, attachment_id: str) -> bool:
        if not self._conn:
            return False
        try:
            self._conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to delete attachment {attachment_id}: {e}")
            return False

    def get_session_attachments(self, session_id: str) -> list[dict]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(
                "SELECT * FROM attachments WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            return self._rows_to_dicts(cursor, json_fields=["linked_memory_ids"])
        except Exception as e:
            logger.error(f"Failed to get session attachments: {e}")
            return []

    # ======================================================================
    # Export / Import / Cleanup
    # ======================================================================

    def export_json(self, output_path: str | Path) -> int:
        memories = self.load_all()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported {len(memories)} memories to {output_path}")
        return len(memories)

    def import_from_json(self, json_path: str | Path) -> int:
        json_path = Path(json_path)
        if not json_path.exists():
            logger.warning(f"Import file not found: {json_path}")
            return 0
        try:
            with open(json_path, encoding="utf-8") as f:
                memories = json.load(f)
            if not isinstance(memories, list):
                logger.error(f"Invalid memories format in {json_path}")
                return 0
            self.save_memories_batch(memories)
            logger.info(f"Imported {len(memories)} memories from {json_path}")
            return len(memories)
        except Exception as e:
            logger.error(f"Failed to import memories from {json_path}: {e}")
            return 0

    def cleanup_expired(self) -> int:
        if not self._conn:
            return 0
        now = datetime.now().isoformat()
        try:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            self._conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Cleaned up {count} expired memories")
            return count
        except Exception as e:
            logger.error(f"Failed to cleanup expired memories: {e}")
            return 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ======================================================================
    # Helpers
    # ======================================================================

    def _rows_to_dicts(
        self, cursor: sqlite3.Cursor, json_fields: list[str] | None = None
    ) -> list[dict]:
        columns = [desc[0] for desc in cursor.description]
        auto_json = {"tags", "metadata"}
        if json_fields:
            auto_json.update(json_fields)

        results = []
        for row in cursor.fetchall():
            d = dict(zip(columns, row, strict=False))
            for jf in auto_json:
                if jf in d and isinstance(d[jf], str):
                    try:
                        d[jf] = json.loads(d[jf])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results
