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

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .exceptions import MemoryStorageUnavailable
from .types import normalize_tags

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 6

# Process-level singleton registry: same db_path → same MemoryStorage instance
_instance_registry: dict[str, MemoryStorage] = {}
_instance_lock = threading.Lock()


def get_shared_storage(db_path: str | Path) -> MemoryStorage:
    """Get or create a process-level shared MemoryStorage for the given db_path."""
    key = str(Path(db_path).resolve())
    with _instance_lock:
        inst = _instance_registry.get(key)
        if inst is not None and inst._conn is not None:
            return inst
        inst = MemoryStorage(db_path, _register=False)
        _instance_registry[key] = inst
        return inst


def checkpoint_and_close_all_storages(timeout_mode: bool = True) -> None:
    """Best-effort WAL checkpoint + close for all process-level storages."""
    with _instance_lock:
        storages = list(_instance_registry.values())
    for storage in storages:
        try:
            storage.checkpoint_and_close(truncate=timeout_mode)
        except Exception as e:
            logger.warning(
                "[MemoryStorage] Checkpoint/close failed for %s: %s", storage._db_path, e
            )


def _is_db_locked(e: Exception) -> bool:
    return isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower()


def _is_corruption_error(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        "malformed" in msg
        or "corrupt" in msg
        or "not a database" in msg
        or "database disk image is malformed" in msg
    )


class MemoryStorage:
    """
    统一记忆存储管理器 (v2)

    Usage:
        storage = MemoryStorage(db_path="data/memory/openakita.db")
        storage.save_memory(memory_dict)
        results = storage.search_fts("代码风格")
    """

    _BUSY_TIMEOUT_MS = 30_000

    def __init__(self, db_path: str | Path, *, _register: bool = True) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._write_lock = threading.RLock()
        self._lock = self._write_lock  # backward compat alias
        self._init_db()
        if _register:
            key = str(self._db_path.resolve())
            with _instance_lock:
                _instance_registry.setdefault(key, self)

    # ======================================================================
    # Initialization & Migration
    # ======================================================================

    def _init_db(self) -> None:
        # `path_in_sync_folder` is a domain concern for the memory subsystem
        # (we surface it through MemoryStorageUnavailable so memory_repair
        # can recognise it), so we keep the pre-check here even though
        # safe_open_sync would also catch it.
        if self._is_sync_folder_path() and os.environ.get("OPENAKITA_ALLOW_SYNC_FOLDER_DB") != "1":
            raise MemoryStorageUnavailable(
                "path_in_sync_folder",
                details=str(self._db_path),
            )

        # Delegate the actual open + PRAGMA + quick_check matrix to the
        # shared safe_sqlite helper. This consolidates the connection-open
        # path with token_tracking / asset_bus / feedback_store etc., so
        # any future safety tweak (e.g. fsync, journal mode bump, retry
        # heuristic) only has to land in one file.
        from openakita.storage.safe_sqlite import SQLiteUnavailable, safe_open_sync

        try:
            conn = safe_open_sync(
                self._db_path,
                want_wal=True,
                busy_ms=self._BUSY_TIMEOUT_MS,
                run_quick_check=True,
                foreign_keys=True,
                check_same_thread=False,
            )
        except SQLiteUnavailable as e:
            # Map safe_sqlite reasons to the historical MemoryStorageUnavailable
            # reason vocabulary so callers (memory_repair UI, telemetry events,
            # tests) keep working unchanged. "corrupted" → "schema_corrupt"
            # is the only renaming; everything else passes through.
            mapped_reason = "schema_corrupt" if e.reason == "corrupted" else e.reason
            raise MemoryStorageUnavailable(
                mapped_reason,
                details=e.details or str(e),
                extra=dict(e.extra),
            ) from e

        self._conn = conn
        try:
            current_version = self._get_schema_version()
            if current_version > _SCHEMA_VERSION:
                raise MemoryStorageUnavailable(
                    "schema_too_new",
                    details=f"database schema v{current_version} is newer than supported v{_SCHEMA_VERSION}",
                    extra={"schema_version": current_version, "supported_version": _SCHEMA_VERSION},
                )
            if current_version < _SCHEMA_VERSION:
                self._migrate_schema(current_version)
            else:
                self._create_tables()
        except MemoryStorageUnavailable:
            self._cleanup_failed_init(conn)
            raise
        except sqlite3.DatabaseError as e:
            self._cleanup_failed_init(conn)
            if _is_corruption_error(e):
                raise MemoryStorageUnavailable("schema_corrupt", details=str(e)) from e
            msg = str(e).lower()
            if "disk i/o error" in msg or "disk is full" in msg:
                raise MemoryStorageUnavailable("disk_full", details=str(e)) from e
            raise MemoryStorageUnavailable("unknown_db_error", details=str(e)) from e
        except OSError as e:
            self._cleanup_failed_init(conn)
            if getattr(e, "errno", None) == 13:
                raise MemoryStorageUnavailable("permission_denied", details=str(e)) from e
            if getattr(e, "errno", None) == 28:
                raise MemoryStorageUnavailable("disk_full", details=str(e)) from e
            raise MemoryStorageUnavailable("filesystem_error", details=str(e)) from e
        except Exception as e:
            self._cleanup_failed_init(conn)
            logger.error(f"[MemoryStorage] Schema init failed: {e}", exc_info=True)
            raise

        logger.debug(f"MemoryStorage initialized: {self._db_path} (schema v{_SCHEMA_VERSION})")

    def _cleanup_failed_init(self, conn: sqlite3.Connection | None) -> None:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._conn = None

    def quick_check_or_raise(self) -> None:
        """Run ``PRAGMA quick_check`` and translate failures to MemoryStorageUnavailable.

        Implementation now delegates to the shared safe_sqlite helper so the
        memory subsystem and every other SQLite caller share one
        quick_check implementation. The thrown exception type stays
        ``MemoryStorageUnavailable`` so existing memory_repair / telemetry
        consumers don't need to change.
        """
        if self._conn is None:
            raise MemoryStorageUnavailable("not_open", details="database connection is not open")
        from openakita.storage.safe_sqlite import (
            SQLiteUnavailable,
            quick_check_or_raise_sync,
        )

        try:
            quick_check_or_raise_sync(self._conn, path=self._db_path)
        except SQLiteUnavailable as e:
            mapped_reason = "schema_corrupt" if e.reason == "corrupted" else e.reason
            raise MemoryStorageUnavailable(
                mapped_reason,
                details=e.details or str(e),
                extra=dict(e.extra),
            ) from e

    def _get_schema_version(self) -> int:
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cur = self._conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'")
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.DatabaseError as e:
            if _is_corruption_error(e):
                raise MemoryStorageUnavailable("schema_corrupt", details=str(e)) from e
            logger.warning(f"[MemoryStorage] Could not read schema version, assuming v0: {e}")
            return 0
        except Exception as e:
            logger.warning(f"[MemoryStorage] Could not read schema version, assuming v0: {e}")
            return 0

    # ----------------------------------------------------------------
    # 通用 meta 键值（复用 _schema_meta 表，避免再开一张 _meta 表）
    # 用于持久化"一次性 sentinel"，如 legacy_json_backfill_done 等。
    # ----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        """读取 _schema_meta 里的一个键。不存在返回 None。"""
        if self._conn is None or not key:
            return None
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cur = self._conn.execute("SELECT value FROM _schema_meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.debug(f"[MemoryStorage] get_meta({key!r}) failed: {e}")
            return None

    def set_meta(self, key: str, value: str) -> None:
        """写 _schema_meta 里的一个键。"""
        if self._conn is None or not key:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
                self._conn.commit()
            except Exception as e:
                logger.warning(f"[MemoryStorage] set_meta({key!r}) failed: {e}")

    def _set_schema_version(
        self,
        version: int,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        c = conn or self._conn
        c.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', ?)",
            (str(version),),
        )
        if commit:
            c.commit()

    def _migrate_schema(self, from_version: int) -> None:
        """Migrate from old schema to current version.

        All DDL + DML run inside a single transaction so the database
        never ends up in a half-migrated state.  If anything fails the
        transaction is rolled back and the old schema version is preserved.
        """
        logger.info(f"[MemoryStorage] Migrating schema v{from_version} → v{_SCHEMA_VERSION}")

        mig_conn: sqlite3.Connection | None = None
        try:
            self._backup_before_migration(from_version)
            mig_conn = sqlite3.connect(str(self._db_path), isolation_level=None)
            mig_conn.execute(f"PRAGMA busy_timeout={self._BUSY_TIMEOUT_MS}")
            mig_conn.execute("PRAGMA foreign_keys=ON")
            mig_conn.execute("BEGIN IMMEDIATE")

            self._create_tables(mig_conn, commit=False, include_fts=False)

            if from_version < 2:
                self._migrate_v1_to_v2(mig_conn, commit=False)
            if from_version < 3:
                self._migrate_v2_to_v3(mig_conn, commit=False)
            if from_version < 4:
                self._migrate_v3_to_v4(mig_conn, commit=False)
            if from_version < 5:
                self._migrate_v4_to_v5(mig_conn, commit=False)

            self._set_schema_version(_SCHEMA_VERSION, conn=mig_conn, commit=False)
            mig_conn.execute("COMMIT")
            self._create_fts_objects()
            logger.info("[MemoryStorage] Schema migration complete")
        except Exception:
            try:
                if mig_conn is not None:
                    mig_conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            if mig_conn is not None:
                try:
                    mig_conn.close()
                except Exception:
                    pass

    def _backup_before_migration(self, from_version: int) -> Path | None:
        """Create a best-effort SQLite backup before schema/data migration."""
        if from_version >= _SCHEMA_VERSION or not self._db_path.exists():
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._db_path.with_name(
            f"{self._db_path.name}.bak.v{from_version}_to_v{_SCHEMA_VERSION}.{timestamp}"
        )
        try:
            with sqlite3.connect(str(backup_path)) as dst:
                self._conn.backup(dst)
            logger.info(f"[MemoryStorage] Pre-migration backup created: {backup_path}")
            self._prune_backups(
                pattern=f"{self._db_path.name}.bak.v*_to_v*.*",
                keep=3,
            )
            return backup_path
        except Exception as e:
            logger.warning(f"[MemoryStorage] SQLite backup API failed: {e}")
            try:
                shutil.copy2(self._db_path, backup_path)
                logger.info(f"[MemoryStorage] Pre-migration file backup created: {backup_path}")
                self._prune_backups(
                    pattern=f"{self._db_path.name}.bak.v*_to_v*.*",
                    keep=3,
                )
                return backup_path
            except Exception as copy_error:
                logger.warning(f"[MemoryStorage] Pre-migration backup skipped: {copy_error}")
                return None

    def _migrate_v1_to_v2(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """Add v2 columns to existing memories table."""
        c = conn or self._conn
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
                c.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
        if commit:
            c.commit()

    def _migrate_v2_to_v3(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """Add owner columns without making legacy desktop memories disappear."""
        c = conn or self._conn
        for col, default in [("user_id", "'default'"), ("workspace_id", "'default'")]:
            try:
                c.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

        # Owner-only updates do not change FTS-indexed content. Older databases may have an
        # empty external-content FTS table; firing the generic UPDATE trigger during this
        # migration can make SQLite report a malformed FTS image.
        c.execute("DROP TRIGGER IF EXISTS memories_fts_au")
        # 旧版本把用户事实、测试数据和系统经验都写到 global/空 owner。
        # 不直接激活到当前用户，避免格式不规范或互相冲突的旧记忆污染检索；
        # 前端会提示用户通过安全导入流程整理这些 legacy_quarantine 记录。
        c.execute(
            """
            UPDATE memories
            SET scope = 'legacy_quarantine',
                scope_owner = '',
                user_id = 'legacy',
                workspace_id = COALESCE(NULLIF(workspace_id, ''), 'default')
            WHERE (scope IS NULL OR scope = 'global')
              AND (scope_owner IS NULL OR scope_owner = '')
              AND (user_id IS NULL OR user_id = '' OR user_id = 'default')
            """
        )
        try:
            c.execute("""CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
                VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
                INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
                VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
            END""")
        except sqlite3.OperationalError:
            pass
        if commit:
            c.commit()

    def _migrate_v3_to_v4(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """Phase 0: 把 legacy_quarantine 里的 lifecycle 后台合成产物迁出到
        pending_consolidation，让真历史旧数据和后台合成数据物理分桶。

        分流规则（保守优先）：
        - source IN ('daily_consolidation', 'experience_synthesis')
          → pending_consolidation（lifecycle 自己写的）
        - 其余 → 留在 legacy_quarantine（视为真历史 v1/v2 数据）

        所有被迁移的记录写入 _memory_scope_audit 表，便于排查和回滚。
        """
        c = conn or self._conn

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS _memory_scope_audit (
                memory_id TEXT NOT NULL,
                old_scope TEXT NOT NULL,
                new_scope TEXT NOT NULL,
                old_user_id TEXT DEFAULT '',
                new_user_id TEXT DEFAULT '',
                reason TEXT NOT NULL,
                migrated_at TEXT NOT NULL,
                migration_version TEXT NOT NULL DEFAULT 'v3_to_v4'
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_scope_audit_memory ON _memory_scope_audit(memory_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_scope_audit_version ON _memory_scope_audit(migration_version)"
        )

        # session 租户索引表（lifecycle 反查租户用），即使本次 migration
        # 不写入数据，也要保证表结构存在，避免 LifecycleManager / MemoryManager
        # 启动期就抛 no such table。
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS session_tenants (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                last_updated_at TEXT NOT NULL
            )
            """
        )

        # 关闭 FTS update 触发器，避免 owner-only 字段更新触发 FTS 重建
        c.execute("DROP TRIGGER IF EXISTS memories_fts_au")
        now = datetime.now().isoformat()

        # 先记审计：哪些行将被改 scope
        c.execute(
            """
            INSERT INTO _memory_scope_audit
                (memory_id, old_scope, new_scope, old_user_id, new_user_id, reason, migrated_at, migration_version)
            SELECT
                id, scope, 'pending_consolidation',
                user_id, user_id,
                'v3_to_v4_source_lifecycle', ?, 'v3_to_v4'
            FROM memories
            WHERE scope = 'legacy_quarantine'
              AND source IN ('daily_consolidation', 'experience_synthesis')
            """,
            (now,),
        )

        cur_update = c.execute(
            """
            UPDATE memories
            SET scope = 'pending_consolidation',
                updated_at = ?
            WHERE scope = 'legacy_quarantine'
              AND source IN ('daily_consolidation', 'experience_synthesis')
            """,
            (now,),
        )
        moved = cur_update.rowcount if cur_update.rowcount is not None else 0
        if moved:
            logger.info(
                "[MemoryStorage] v3→v4 split: moved %d rows from legacy_quarantine to "
                "pending_consolidation (audit recorded)",
                moved,
            )

        # v4 backfill：从 conversation_turns 里出现过的所有 session_id 反推登记
        # session_tenants，避免 v4 升级后旧 unextracted turn 被 lifecycle 误落
        # 到 pending_consolidation（再被 SHORT_TERM 自清规则 3 天后删掉）。
        #
        # 推断策略：
        # - session_id 形如 ``ns__chat_id__user_id[__thread]``（IM 通道的
        #   conversation_safe_id 标准形式）→ 取第 3 段当 user_id，workspace 仍
        #   默认 default（workspace_id 的真值要等 Phase 2a 才会出现）。
        # - 不符合此格式（如 desktop CLI 的 ``YYYYMMDD_HHMMSS_xxx`` 单段）→
        #   登记成 (default, default)。Phase 0 的语义已经接受 default 作为
        #   合法的单用户身份，lifecycle 会正确归属到该用户。
        # - user_id 段落是 ``default / anonymous / system / legacy / ''`` 时
        #   也降级为 (default, default)，避免把占位身份当真用户。
        #
        # 这一步只补 session_tenants，**不动** memories 表本身，零数据丢失风险。
        existing_sessions = {
            row[0] for row in c.execute("SELECT session_id FROM session_tenants").fetchall()
        }
        backfill_rows: list[tuple[str, str, str, str]] = []
        for (session_id,) in c.execute(
            "SELECT DISTINCT session_id FROM conversation_turns WHERE session_id IS NOT NULL "
            "AND session_id != ''"
        ).fetchall():
            if not session_id or session_id in existing_sessions:
                continue
            parts = session_id.split("__")
            if (
                len(parts) >= 3
                and parts[2]
                and parts[2] not in {"default", "anonymous", "system", "legacy", ""}
            ):
                user_id = parts[2]
            else:
                user_id = "default"
            backfill_rows.append((session_id, user_id, "default", now))

        if backfill_rows:
            c.executemany(
                """
                INSERT OR IGNORE INTO session_tenants
                    (session_id, user_id, workspace_id, last_updated_at)
                VALUES (?, ?, ?, ?)
                """,
                backfill_rows,
            )
            logger.info(
                "[MemoryStorage] v3→v4 backfill: registered %d sessions in session_tenants "
                "(IM-style parsed: %d, default fallback: %d)",
                len(backfill_rows),
                sum(1 for r in backfill_rows if r[1] != "default"),
                sum(1 for r in backfill_rows if r[1] == "default"),
            )

        # 恢复 FTS update 触发器
        try:
            c.execute(
                """CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
                VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
                INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
                VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
            END"""
            )
        except sqlite3.OperationalError:
            pass

        if commit:
            c.commit()

    def _migrate_v4_to_v5(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        """Add ``conversation_turns.metadata`` JSON column.

        v1.27.15 (plan v1.28 S2 P1-6).  Old schemas had no place to record
        per-turn metadata, so ``Session.append_marker`` could persist the
        bare role/content of a ``marker_type="aborted_partial"`` /
        ``"preempted"`` message but lost the marker_type itself — which
        meant the memory extraction lifecycle could not distinguish a
        real assistant turn from a "[task was interrupted]" placeholder
        and would happily index the placeholder as a long-term memory.

        Idempotent ALTER (only adds the column if not already present, in
        case the table was just created at v5 in this same session).
        """
        c = conn or self._conn
        # FIX-D (post-S2 audit): do NOT swallow ALTER TABLE failures.  If
        # we silently continue and then ``_set_schema_version`` bumps
        # the schema marker, the database lands in a "claims v5 but has
        # no metadata column" state, causing every subsequent
        # ``save_turn`` INSERT (11 placeholders against a 10-column
        # table) to fail with OperationalError.  Letting the exception
        # propagate makes the migration transaction rollback so the
        # next startup retries cleanly at v4.
        cur = c.execute("PRAGMA table_info(conversation_turns)")
        cols = {row[1] for row in cur.fetchall()}
        if "metadata" not in cols:
            c.execute("ALTER TABLE conversation_turns ADD COLUMN metadata TEXT")
            logger.info("[MemoryStorage] v4→v5: added conversation_turns.metadata column")
        if commit:
            c.commit()

    def _create_tables(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
        include_fts: bool = True,
    ) -> None:
        """Create all tables, indexes, FTS virtual tables and triggers.

        Execution is split into strict phases so that no index / trigger
        can ever reference a table that hasn't been created yet:

          Phase 1 – CREATE TABLE  (all regular tables)
          Phase 2 – CREATE INDEX  (all indexes, including cross-table)
          Phase 3 – FTS5 virtual tables + sync triggers (best-effort)
        """
        c = conn or self._conn

        # ==============================================================
        # Phase 1: CREATE TABLE — all regular tables first
        # ==============================================================

        c.execute("""
            CREATE TABLE IF NOT EXISTS _schema_meta (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)

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

        # v3: 记忆分层 — 新增 scope 列（兼容旧库）
        for col, default in [("scope", "'global'"), ("scope_owner", "''")]:
            try:
                c.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_owner)")

        # v4: 多 Agent 记忆隔离预留 — agent_id 标识记忆归属
        try:
            c.execute("ALTER TABLE memories ADD COLUMN agent_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_id)")

        # v5: 用户/工作区隔离。任何用户事实必须带 owner；legacy 迁移会把旧
        # global/空 owner 数据移入 legacy_quarantine，避免继续污染当前用户。
        for col, default in [("user_id", "'default'"), ("workspace_id", "'default'")]:
            try:
                c.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_owner "
            "ON memories(workspace_id, user_id, scope, scope_owner)"
        )

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
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_episode ON memories(source_episode_id)")
        episode_columns = {row[1] for row in c.execute("PRAGMA table_info(episodes)").fetchall()}
        for col in ("compaction_checkpoint_id", "workspace_snapshot_id"):
            if col not in episode_columns:
                c.execute(f"ALTER TABLE episodes ADD COLUMN {col} TEXT DEFAULT ''")

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
                metadata TEXT,
                UNIQUE(session_id, turn_index)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON conversation_turns(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_tool ON conversation_turns(has_tool_calls)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_extracted ON conversation_turns(extracted)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_episode ON conversation_turns(episode_id)")

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

        c.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER DEFAULT 1024,
                created_at TEXT NOT NULL
            )
        """)

        # v4: session_id → 租户映射，lifecycle 后台批处理用这张表反查
        # 一条 conversation_turns 的 session_id 属于哪个 (user_id, workspace_id)，
        # 避免裸用 ContextVar 默认值导致后台合成全部落到 default 共享桶。
        c.execute("""
            CREATE TABLE IF NOT EXISTS session_tenants (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'default',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                last_updated_at TEXT NOT NULL
            )
        """)

        # v4: 记忆 scope 迁移审计表，记录每条记忆历史上被哪次 migration
        # 从哪个 scope 移到哪个 scope，含理由和时间戳，便于排查 / 回滚。
        c.execute("""
            CREATE TABLE IF NOT EXISTS _memory_scope_audit (
                memory_id TEXT NOT NULL,
                old_scope TEXT NOT NULL,
                new_scope TEXT NOT NULL,
                old_user_id TEXT DEFAULT '',
                new_user_id TEXT DEFAULT '',
                reason TEXT NOT NULL,
                migrated_at TEXT NOT NULL,
                migration_version TEXT NOT NULL DEFAULT 'v3_to_v4'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS context_epochs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                digest TEXT NOT NULL,
                system_digest TEXT NOT NULL,
                tools_digest TEXT NOT NULL,
                model TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(session_id, digest)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS workspace_snapshots (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                root TEXT NOT NULL,
                capture_status TEXT NOT NULL DEFAULT 'unknown',
                capture_error TEXT DEFAULT '',
                vcs TEXT DEFAULT '',
                head TEXT DEFAULT '',
                status_digest TEXT DEFAULT '',
                changed_files TEXT DEFAULT '[]',
                patch_zlib BLOB,
                patch_truncated INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        workspace_snapshot_columns = {
            row[1] for row in c.execute("PRAGMA table_info(workspace_snapshots)").fetchall()
        }
        if "capture_status" not in workspace_snapshot_columns:
            c.execute(
                "ALTER TABLE workspace_snapshots "
                "ADD COLUMN capture_status TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "capture_error" not in workspace_snapshot_columns:
            c.execute("ALTER TABLE workspace_snapshots ADD COLUMN capture_error TEXT DEFAULT ''")
        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_output_blobs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                tool_name TEXT DEFAULT '',
                content_zlib BLOB NOT NULL,
                content_chars INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS compaction_checkpoints (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                source_message_count INTEGER NOT NULL,
                session_source_digest TEXT DEFAULT '',
                session_source_message_count INTEGER DEFAULT 0,
                summary TEXT DEFAULT '',
                recent_messages TEXT DEFAULT '[]',
                projected_messages TEXT DEFAULT '[]',
                tail_start_index INTEGER DEFAULT 0,
                tokens_before INTEGER DEFAULT 0,
                tokens_after INTEGER DEFAULT 0,
                epoch_digest TEXT DEFAULT '',
                workspace_snapshot_id TEXT DEFAULT '',
                contributions TEXT DEFAULT '[]',
                model TEXT DEFAULT '',
                agent_profile_id TEXT DEFAULT 'default',
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT '',
                error TEXT DEFAULT ''
            )
        """)

        # ==============================================================
        # Phase 2: CREATE INDEX — all tables already exist at this point
        # ==============================================================

        # memories
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_priority ON memories(priority)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_memories_episode ON memories(source_episode_id)")

        # episodes
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(started_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome)")

        # conversation_turns
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON conversation_turns(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_tool ON conversation_turns(has_tool_calls)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_extracted ON conversation_turns(extracted)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_turns_episode ON conversation_turns(episode_id)")

        # extraction_queue
        c.execute("CREATE INDEX IF NOT EXISTS idx_eq_status ON extraction_queue(status)")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_epochs_session "
            "ON context_epochs(session_id, id DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_snapshots_session "
            "ON workspace_snapshots(session_id, created_at DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_output_session "
            "ON tool_output_blobs(session_id, created_at DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_compaction_session_status "
            "ON compaction_checkpoints(session_id, status, completed_at DESC)"
        )
        try:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_eq_session_turn ON extraction_queue(session_id, turn_index)"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "[MemoryStorage] extraction_queue has duplicate (session_id, turn_index), deduplicating..."
            )
            c.execute("""
                DELETE FROM extraction_queue
                WHERE id NOT IN (
                    SELECT MAX(id) FROM extraction_queue
                    GROUP BY session_id, turn_index
                )
            """)
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_eq_session_turn ON extraction_queue(session_id, turn_index)"
            )
        except sqlite3.OperationalError:
            pass

        # attachments
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_session ON attachments(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_mime ON attachments(mime_type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_direction ON attachments(direction)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attach_created ON attachments(created_at)")

        # session_tenants + _memory_scope_audit (v4)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_scope_audit_memory ON _memory_scope_audit(memory_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_scope_audit_version ON _memory_scope_audit(migration_version)"
        )

        if include_fts:
            self._create_fts_objects(c)

        if commit:
            c.commit()

    def _create_fts_objects(self, conn: sqlite3.Connection | None = None) -> None:
        """Create FTS5 virtual tables and sync triggers on an already-valid schema."""
        c = conn or self._conn
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

    # ======================================================================
    # Semantic Memory CRUD
    # ======================================================================

    def save_memory(self, memory: dict) -> None:
        if not self._conn:
            return
        now = datetime.now().isoformat()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                    (id, content, type, priority, source, importance_score,
                     access_count, tags, created_at, updated_at, expires_at, metadata,
                     subject, predicate, confidence, decay_rate,
                     last_accessed_at, superseded_by, source_episode_id,
                     scope, scope_owner, agent_id, user_id, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.get("id", ""),
                        memory.get("content", ""),
                        memory.get("type", "FACT"),
                        memory.get("priority", "SHORT_TERM"),
                        memory.get("source", ""),
                        memory.get("importance_score", 0.5),
                        memory.get("access_count", 0),
                        json.dumps(normalize_tags(memory.get("tags")), ensure_ascii=False),
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
                        memory.get("scope", "global"),
                        memory.get("scope_owner", ""),
                        memory.get("agent_id", ""),
                        memory.get("user_id", "default"),
                        memory.get("workspace_id", "default"),
                    ),
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to save memory to SQLite: {e}")

    def save_memories_batch(self, memories: list[dict]) -> None:
        if not self._conn or not memories:
            return
        now = datetime.now().isoformat()
        with self._lock:
            try:
                self._conn.executemany(
                    """
                    INSERT OR REPLACE INTO memories
                    (id, content, type, priority, source, importance_score,
                     access_count, tags, created_at, updated_at, expires_at, metadata,
                     subject, predicate, confidence, decay_rate,
                     last_accessed_at, superseded_by, source_episode_id,
                     scope, scope_owner, agent_id, user_id, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            json.dumps(normalize_tags(m.get("tags")), ensure_ascii=False),
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
                            m.get("scope", "global"),
                            m.get("scope_owner", ""),
                            m.get("agent_id", ""),
                            m.get("user_id", "default"),
                            m.get("workspace_id", "default"),
                        )
                        for m in memories
                    ],
                )
                self._conn.commit()
                logger.debug(f"Batch saved {len(memories)} memories to SQLite")
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to batch save memories: {e}")

    def load_all(
        self,
        scope: str | None = None,
        scope_owner: str | None = None,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict]:
        if not self._conn:
            return []
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if scope is not None:
                conditions.append("(scope IS NULL OR scope = ?)")
                params.append(scope)
            if scope_owner is not None:
                conditions.append("(scope_owner IS NULL OR scope_owner = ?)")
                params.append(scope_owner)
            if active_only:
                conditions.extend(
                    [
                        "(expires_at IS NULL OR expires_at >= ?)",
                        "(superseded_by IS NULL OR superseded_by = '')",
                    ]
                )
                params.append(datetime.now().isoformat())
            if user_id is not None:
                conditions.append("COALESCE(user_id, '') = ?")
                params.append(user_id)
            if workspace_id is not None:
                conditions.append("COALESCE(workspace_id, 'default') = ?")
                params.append(workspace_id)
            where = " AND ".join(conditions) if conditions else "1=1"
            cursor = self._conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC",
                params,
            )
            return self._rows_to_dicts(cursor)
        except Exception as e:
            logger.error(f"Failed to load memories from SQLite: {e}")
            return []

    def get_memory(self, memory_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            rows = self._rows_to_dicts(cursor)
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    def delete_memory(self, memory_id: str) -> bool:
        if not self._conn:
            return False
        with self._lock:
            try:
                self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                self._conn.commit()
                return True
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to delete memory {memory_id}: {e}")
                return False

    def update_memory(self, memory_id: str, updates: dict) -> bool:
        """Update specific fields of a memory."""
        if not self._conn or not updates:
            return False
        allowed = {
            "content",
            "type",
            "priority",
            "source",
            "importance_score",
            "access_count",
            "tags",
            "subject",
            "predicate",
            "confidence",
            "decay_rate",
            "last_accessed_at",
            "superseded_by",
            "source_episode_id",
            "updated_at",
            "metadata",
            "scope",
            "scope_owner",
            "user_id",
            "workspace_id",
            "agent_id",
            "expires_at",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False

        if "tags" in filtered:
            filtered["tags"] = json.dumps(normalize_tags(filtered["tags"]), ensure_ascii=False)
        if "metadata" in filtered and isinstance(filtered["metadata"], dict):
            filtered["metadata"] = json.dumps(filtered["metadata"], ensure_ascii=False)

        filtered.setdefault("updated_at", datetime.now().isoformat())
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [memory_id]

        with self._lock:
            try:
                self._conn.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
                self._conn.commit()
                return True
            except Exception as e:
                if _is_db_locked(e):
                    raise
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
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
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
        if scope is not None:
            conditions.append("(scope IS NULL OR scope = ?)")
            params.append(scope)
        if scope_owner is not None:
            conditions.append("(scope_owner IS NULL OR scope_owner = ?)")
            params.append(scope_owner)
        if user_id is not None:
            conditions.append("COALESCE(user_id, '') = ?")
            params.append(user_id)
        if workspace_id is not None:
            conditions.append("COALESCE(workspace_id, 'default') = ?")
            params.append(workspace_id)
        if active_only:
            conditions.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(datetime.now().isoformat())
            conditions.append("(superseded_by IS NULL OR superseded_by = '')")

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

    def count(
        self,
        memory_type: str | None = None,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        active_only: bool = True,
    ) -> int:
        if not self._conn:
            return 0
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if memory_type:
                conditions.append("type = ?")
                params.append(memory_type)
            if scope is not None:
                conditions.append("(scope IS NULL OR scope = ?)")
                params.append(scope)
            if scope_owner is not None:
                conditions.append("(scope_owner IS NULL OR scope_owner = ?)")
                params.append(scope_owner)
            if user_id is not None:
                conditions.append("COALESCE(user_id, '') = ?")
                params.append(user_id)
            if workspace_id is not None:
                conditions.append("COALESCE(workspace_id, 'default') = ?")
                params.append(workspace_id)
            if active_only:
                conditions.append("(expires_at IS NULL OR expires_at >= ?)")
                params.append(datetime.now().isoformat())
                conditions.append("(superseded_by IS NULL OR superseded_by = '')")
            where = " AND ".join(conditions) if conditions else "1=1"
            cur = self._conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params)
            return cur.fetchone()[0]
        except Exception:
            return 0

    SORTABLE_COLUMNS = frozenset(
        {
            "importance_score",
            "created_at",
            "updated_at",
            "last_accessed_at",
            "access_count",
        }
    )

    def query_paged(
        self,
        *,
        memory_type: str | None = None,
        min_importance: float | None = None,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        sort_by: str = "importance_score",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> tuple[list[dict], int]:
        """Paginated query with SQL-level sorting. Returns (rows, total_count)."""
        if not self._conn:
            return [], 0

        if sort_by not in self.SORTABLE_COLUMNS:
            sort_by = "importance_score"
        if sort_order.lower() not in ("asc", "desc"):
            sort_order = "desc"

        conditions: list[str] = []
        params: list[Any] = []

        if memory_type:
            conditions.append("type = ?")
            params.append(memory_type)
        if min_importance is not None:
            conditions.append("importance_score >= ?")
            params.append(min_importance)
        if scope is not None:
            conditions.append("(scope IS NULL OR scope = ?)")
            params.append(scope)
        if scope_owner is not None:
            conditions.append("(scope_owner IS NULL OR scope_owner = ?)")
            params.append(scope_owner)
        if user_id is not None:
            conditions.append("COALESCE(user_id, '') = ?")
            params.append(user_id)
        if workspace_id is not None:
            conditions.append("COALESCE(workspace_id, 'default') = ?")
            params.append(workspace_id)
        if active_only:
            conditions.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(datetime.now().isoformat())
            conditions.append("(superseded_by IS NULL OR superseded_by = '')")

        where = " AND ".join(conditions) if conditions else "1=1"

        try:
            count_cur = self._conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params)
            total = count_cur.fetchone()[0]

            order = sort_order.upper()
            page_params = params + [limit, offset]
            cursor = self._conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
                page_params,
            )
            rows = self._rows_to_dicts(cursor)
            return rows, total
        except Exception as e:
            logger.error(f"Failed to query_paged memories: {e}")
            return [], 0

    # ======================================================================
    # FTS5 Search
    # ======================================================================

    def search_fts(
        self,
        query: str,
        limit: int = 10,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict]:
        """Full-text search using FTS5 with BM25 ranking, with LIKE fallback for CJK.

        Args:
            scope: If provided, restrict results to this scope (e.g. 'global').
            scope_owner: If provided, restrict results to this scope_owner.
        """
        if not self._conn or not query.strip():
            return []

        scope_clauses: list[str] = []
        scope_params: list[Any] = []
        if scope is not None:
            scope_clauses.append("(m.scope IS NULL OR m.scope = ?)")
            scope_params.append(scope)
        if scope_owner is not None:
            scope_clauses.append("(m.scope_owner IS NULL OR m.scope_owner = ?)")
            scope_params.append(scope_owner)
        if user_id is not None:
            scope_clauses.append("COALESCE(m.user_id, '') = ?")
            scope_params.append(user_id)
        if workspace_id is not None:
            scope_clauses.append("COALESCE(m.workspace_id, 'default') = ?")
            scope_params.append(workspace_id)
        if active_only:
            scope_clauses.append("(m.expires_at IS NULL OR m.expires_at >= ?)")
            scope_params.append(datetime.now().isoformat())
            scope_clauses.append("(m.superseded_by IS NULL OR m.superseded_by = '')")
        scope_where = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""

        try:
            safe_query = self._sanitize_fts_query(query)
            cursor = self._conn.execute(
                f"""
                SELECT m.*, bm25(memories_fts) AS rank
                FROM memories_fts fts
                JOIN memories m ON m.rowid = fts.rowid
                WHERE memories_fts MATCH ?{scope_where}
                ORDER BY rank
                LIMIT ?
                """,
                [safe_query] + scope_params + [limit],
            )
            results = self._rows_to_dicts(cursor)
            if results:
                return results
        except Exception as e:
            logger.debug(f"FTS5 search failed (query={query!r}): {e}")

        # Fallback: LIKE search for CJK text that FTS5 unicode61 can't tokenize
        try:
            keywords = query.strip().split()
            if not keywords:
                return []
            like_conditions = " OR ".join(["content LIKE ?"] * len(keywords))
            like_params: list[Any] = [f"%{kw}%" for kw in keywords]
            where = f"({like_conditions})"
            if scope is not None:
                where += " AND (scope IS NULL OR scope = ?)"
                like_params.append(scope)
            if scope_owner is not None:
                where += " AND (scope_owner IS NULL OR scope_owner = ?)"
                like_params.append(scope_owner)
            if user_id is not None:
                where += " AND COALESCE(user_id, '') = ?"
                like_params.append(user_id)
            if workspace_id is not None:
                where += " AND COALESCE(workspace_id, 'default') = ?"
                like_params.append(workspace_id)
            if active_only:
                where += " AND (expires_at IS NULL OR expires_at >= ?)"
                like_params.append(datetime.now().isoformat())
                where += " AND (superseded_by IS NULL OR superseded_by = '')"
            like_params.append(limit)
            cursor = self._conn.execute(
                f"SELECT * FROM memories WHERE {where} LIMIT ?",
                like_params,
            )
            return self._rows_to_dicts(cursor)
        except Exception as e:
            logger.debug(f"LIKE fallback search failed: {e}")
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
        with self._lock:
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
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO episodes
                    (id, session_id, summary, goal, outcome, started_at, ended_at,
                     action_nodes, entities, tools_used, linked_memory_ids, tags,
                     importance_score, access_count, source, compaction_checkpoint_id,
                     workspace_snapshot_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(normalize_tags(episode.get("tags")), ensure_ascii=False),
                        episode.get("importance_score", 0.5),
                        episode.get("access_count", 0),
                        episode.get("source", "session_end"),
                        episode.get("compaction_checkpoint_id", ""),
                        episode.get("workspace_snapshot_id", ""),
                    ),
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to save episode: {e}")

    def get_episode(self, episode_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
            rows = self._rows_to_dicts(
                cur,
                json_fields=["action_nodes", "entities", "tools_used", "linked_memory_ids", "tags"],
            )
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
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """搜索 episodes。

        Phase 2b.5 新增 ``user_id`` / ``workspace_id`` 过滤：通过 INNER JOIN
        ``session_tenants`` 表把 episode 收敛到给定租户。

        兼容性：
        - 不传 ``user_id`` 和 ``workspace_id`` 时回退到旧行为（全库扫描），
          老调用方完全不感知；
        - 显式传任一参数后，未在 ``session_tenants`` 登记的 session 对应的
          episode（v3 之前的老数据）会被自然过滤掉 —— 这是有意的安全默认。
          调用方如果想包含历史孤儿数据，可以在迁徙工具里单独走 raw SQL。
        """
        if not self._conn:
            return []
        conditions: list[str] = []
        params: list[Any] = []

        use_tenant_filter = user_id is not None or workspace_id is not None
        table_expr = "episodes"
        if use_tenant_filter:
            table_expr = (
                "episodes INNER JOIN session_tenants st ON episodes.session_id = st.session_id"
            )
            if user_id is not None:
                conditions.append("st.user_id = ?")
                params.append(user_id)
            if workspace_id is not None:
                conditions.append("st.workspace_id = ?")
                params.append(workspace_id)

        if session_id:
            conditions.append("episodes.session_id = ?" if use_tenant_filter else "session_id = ?")
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

        select_cols = "episodes.*" if use_tenant_filter else "*"

        try:
            cur = self._conn.execute(
                f"SELECT {select_cols} FROM {table_expr} WHERE {where} "
                "ORDER BY started_at DESC LIMIT ?",
                params,
            )
            return self._rows_to_dicts(
                cur,
                json_fields=["action_nodes", "entities", "tools_used", "linked_memory_ids", "tags"],
            )
        except Exception as e:
            logger.error(f"Failed to search episodes: {e}")
            return []

    def update_episode(self, episode_id: str, updates: dict) -> bool:
        """Update specific fields of an episode."""
        if not self._conn or not updates:
            return False
        allowed = {
            "summary",
            "goal",
            "outcome",
            "importance_score",
            "access_count",
            "linked_memory_ids",
            "tags",
            "entities",
            "tools_used",
            "compaction_checkpoint_id",
            "workspace_snapshot_id",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False

        json_fields = {"linked_memory_ids", "tags", "entities", "tools_used"}
        for k in json_fields:
            if k in filtered and isinstance(filtered[k], list):
                filtered[k] = json.dumps(filtered[k], ensure_ascii=False)

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [episode_id]

        with self._lock:
            try:
                self._conn.execute(f"UPDATE episodes SET {set_clause} WHERE id = ?", values)
                self._conn.commit()
                return True
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to update episode {episode_id}: {e}")
                return False

    def link_turns_to_episode(self, session_id: str, episode_id: str) -> int:
        """Set episode_id on all conversation_turns for a given session."""
        if not self._conn:
            return 0
        with self._lock:
            try:
                cur = self._conn.execute(
                    "UPDATE conversation_turns SET episode_id = ? WHERE session_id = ?",
                    (episode_id, session_id),
                )
                self._conn.commit()
                return cur.rowcount
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to link turns to episode: {e}")
                return 0

    # ======================================================================
    # Scratchpad CRUD
    # ======================================================================

    def get_scratchpad(self, user_id: str = "default") -> dict | None:
        if not self._conn:
            return None
        try:
            cur = self._conn.execute("SELECT * FROM scratchpad WHERE user_id = ?", (user_id,))
            rows = self._rows_to_dicts(
                cur, json_fields=["active_projects", "open_questions", "next_steps"]
            )
            return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Failed to get scratchpad: {e}")
            return None

    def save_scratchpad(self, scratchpad: dict) -> None:
        if not self._conn:
            return
        with self._lock:
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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to save scratchpad: {e}")

    # ======================================================================
    # Session Tenants (v4)
    # ======================================================================

    def upsert_session_tenant(
        self,
        session_id: str,
        user_id: str,
        workspace_id: str,
    ) -> None:
        """记录 session_id → (user_id, workspace_id) 映射，供 lifecycle 反查租户。

        每次 MemoryManager.start_session 都会被调用一次。重复写入只刷新时间戳，
        避免后台批处理时拿不到当前会话归属的 user / workspace 而误落 default。
        """
        if not self._conn or not session_id:
            return
        u = (user_id or "").strip() or "default"
        w = (workspace_id or "").strip() or "default"
        ts = datetime.now().isoformat()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO session_tenants (session_id, user_id, workspace_id, last_updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id = excluded.user_id,
                        workspace_id = excluded.workspace_id,
                        last_updated_at = excluded.last_updated_at
                    """,
                    (session_id, u, w, ts),
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.warning(f"[MemoryStorage] upsert_session_tenant failed: {e}")

    def get_session_tenant(self, session_id: str) -> tuple[str, str] | None:
        """根据 session_id 查 (user_id, workspace_id)。

        未找到返回 None；调用方应将 None 视为 “租户未知”，把记忆落到
        pending_consolidation 桶里，避免污染共享 default。
        """
        if not self._conn or not session_id:
            return None
        try:
            cur = self._conn.execute(
                "SELECT user_id, workspace_id FROM session_tenants WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return (row[0] or "default", row[1] or "default")
        except Exception as e:
            logger.debug(f"[MemoryStorage] get_session_tenant failed: {e}")
            return None

    def iter_owned_session_ids(
        self,
        *,
        user_id: str,
        workspace_id: str | None = None,
    ) -> list[str]:
        """返回 session_tenants 里所有属于 (user_id[, workspace_id]) 的 session_id。

        用于多用户 IM 部署下 JSONL / react_traces 文件级回退路径的 owner
        过滤 —— 没登记过的 session 自然被排除，即便文件还在磁盘上。
        """
        if not self._conn:
            return []
        try:
            if workspace_id is None:
                cur = self._conn.execute(
                    "SELECT session_id FROM session_tenants WHERE user_id = ?",
                    (user_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT session_id FROM session_tenants WHERE user_id = ? AND workspace_id = ?",
                    (user_id, workspace_id),
                )
            return [row[0] for row in cur.fetchall() if row[0]]
        except Exception as e:
            logger.debug(f"[MemoryStorage] iter_owned_session_ids failed: {e}")
            return []

    def list_known_tenants(self) -> list[tuple[str, str]]:
        """返回所有已知 (user_id, workspace_id) 组合，供 synthesize 等批处理分组。

        排除 ``legacy / system / anonymous / ''`` 这种 **明确表示"不知道是谁"** 的
        占位身份。``default`` **保留**：在桌面 / CLI 单用户场景，``default`` 就是
        合法用户身份，不能被当成共享桶过滤掉。
        """
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                """
                SELECT DISTINCT user_id, workspace_id
                FROM session_tenants
                WHERE user_id NOT IN ('legacy', 'system', 'anonymous', '')
                """
            )
            return [(r[0] or "default", r[1] or "default") for r in cur.fetchall()]
        except Exception as e:
            logger.debug(f"[MemoryStorage] list_known_tenants failed: {e}")
            return []

    def migrate_workspace_id(
        self,
        *,
        from_workspace_id: str,
        to_workspace_id: str,
        user_id: str,
        scope: str = "user",
    ) -> int:
        """Phase 2a：把同一 (scope, user_id) 下、workspace_id=from 的 memories
        全部改成 workspace_id=to。返回更新行数。

        典型场景：用户从默认 ``workspace_id="default"`` 切换到项目专属工作区，
        想把原来共享桶里的记忆"携过来"。
        - 仅修改 ``memories.workspace_id`` 字段，不动 content / scope / user_id；
        - 全程在事务内，单语句 UPDATE，失败回滚；
        - 写入 _memory_scope_audit 表记录每条变更，便于审计与可能的回滚。
        """
        if not self._conn:
            return 0
        if (
            not from_workspace_id
            or not to_workspace_id
            or from_workspace_id == to_workspace_id
            or not user_id
        ):
            return 0
        now = datetime.now().isoformat()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                # 先写审计
                self._conn.execute(
                    """
                    INSERT INTO _memory_scope_audit
                        (memory_id, old_scope, new_scope, old_user_id, new_user_id,
                         reason, migrated_at, migration_version)
                    SELECT id, scope, scope, user_id, user_id,
                           'workspace_migrate:' || ? || '->' || ?,
                           ?, 'workspace_migrate'
                    FROM memories
                    WHERE scope = ? AND user_id = ? AND workspace_id = ?
                    """,
                    (from_workspace_id, to_workspace_id, now, scope, user_id, from_workspace_id),
                )
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET workspace_id = ?, updated_at = ?
                    WHERE scope = ? AND user_id = ? AND workspace_id = ?
                    """,
                    (to_workspace_id, now, scope, user_id, from_workspace_id),
                )
                moved = cur.rowcount if cur.rowcount is not None else 0
                self._conn.execute("COMMIT")
                if moved:
                    logger.info(
                        "[MemoryStorage] workspace migrate: %d rows scope=%s user_id=%s "
                        "from %s → %s",
                        moved,
                        scope,
                        user_id,
                        from_workspace_id,
                        to_workspace_id,
                    )
                return moved
            except Exception as e:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.warning(f"[MemoryStorage] migrate_workspace_id failed: {e}")
                return 0

    def record_scope_audit(
        self,
        memory_id: str,
        *,
        old_scope: str,
        new_scope: str,
        reason: str,
        old_user_id: str = "",
        new_user_id: str = "",
        migration_version: str = "runtime",
    ) -> None:
        """记一条 scope 变更审计。runtime 路径下用 migration_version='runtime'。"""
        if not self._conn or not memory_id:
            return
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO _memory_scope_audit
                        (memory_id, old_scope, new_scope, old_user_id, new_user_id,
                         reason, migrated_at, migration_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        old_scope or "",
                        new_scope or "",
                        old_user_id or "",
                        new_user_id or "",
                        reason or "",
                        datetime.now().isoformat(),
                        migration_version or "runtime",
                    ),
                )
                self._conn.commit()
            except Exception as e:
                logger.debug(f"[MemoryStorage] record_scope_audit failed: {e}")

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
        metadata: dict | None = None,
    ) -> None:
        """Persist a single turn.

        v1.27.15 (P1-6): added ``metadata`` JSON column persistence.
        Lifecycle extraction reads ``metadata.marker_type`` to skip
        ``preempted`` / ``aborted_partial`` markers (they should NOT
        become long-term memories).
        """
        if not self._conn:
            return
        ts = timestamp or datetime.now().isoformat()
        has_tools = bool(tool_calls)
        # Only persist non-empty metadata; saves a few bytes per row
        # and keeps queries that filter ``metadata IS NULL`` meaningful.
        meta_json: str | None = None
        if metadata:
            try:
                meta_json = json.dumps(metadata, ensure_ascii=False, default=str)
            except Exception:
                meta_json = None
        # Auto-mark marker turns as "extracted" so the lifecycle background
        # loop never picks them up.  This is the second line of defense
        # alongside the lifecycle-side filter — even if a future code path
        # forgets to filter, the marker still won't pollute memory.
        marker_type = (metadata or {}).get("marker_type")
        is_marker = marker_type in ("preempted", "aborted_partial")
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO conversation_turns
                    (session_id, turn_index, role, content, tool_calls, tool_results,
                     has_tool_calls, timestamp, token_estimate, extracted, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        bool(is_marker),
                        meta_json,
                    ),
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to save turn: {e}")

    def get_unextracted_turns(self, limit: int = 100) -> list[dict]:
        if not self._conn:
            return []
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT * FROM conversation_turns WHERE extracted = FALSE "
                    "ORDER BY timestamp ASC LIMIT ?",
                    (limit,),
                )
                # v1.27.15: also parse ``metadata`` JSON so callers can
                # short-circuit on ``marker_type``.
                return self._rows_to_dicts(
                    cur,
                    json_fields=["tool_calls", "tool_results", "metadata"],
                )
            except Exception as e:
                logger.error(f"Failed to get unextracted turns: {e}")
                return []

    def mark_turns_extracted(self, session_id: str, turn_indices: list[int]) -> None:
        if not self._conn or not turn_indices:
            return
        placeholders = ",".join("?" * len(turn_indices))
        with self._lock:
            try:
                self._conn.execute(
                    f"UPDATE conversation_turns SET extracted = TRUE "
                    f"WHERE session_id = ? AND turn_index IN ({placeholders})",
                    [session_id] + turn_indices,
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
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
        except Exception as e:
            logger.warning(f"Failed to get max turn_index for {session_id}: {e}")
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
        except Exception as e:
            logger.warning(f"Failed to get recent turns for {session_id}: {e}")
            return []

    def get_global_recent_turns(self, limit: int = 20) -> list[dict]:
        """跨所有 session 按时间倒序获取最近 N 轮对话（用于 Memory Nudge）"""
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT role, content, timestamp "
                "FROM conversation_turns "
                "WHERE role IN ('user', 'assistant') AND content IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = self._rows_to_dicts(cur)
            rows.reverse()
            return rows
        except Exception as e:
            logger.warning(f"Failed to get global recent turns: {e}")
            return []

    def list_turns(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict], int]:
        """Paginated query for conversation_turns. Returns (rows, total).

        Optional date_from / date_to (ISO date strings, e.g. '2026-03-19')
        restrict results by the timestamp column.
        """
        if not self._conn:
            return [], 0
        with self._lock:
            try:
                where = "session_id = ?"
                params: list = [session_id]
                if date_from:
                    where += " AND timestamp >= ?"
                    params.append(date_from)
                if date_to:
                    where += " AND timestamp <= ?"
                    params.append(date_to + "T23:59:59.999999")
                total_row = self._conn.execute(
                    f"SELECT COUNT(*) FROM conversation_turns WHERE {where}",
                    params,
                ).fetchone()
                total = total_row[0] if total_row else 0
                cur = self._conn.execute(
                    "SELECT id, session_id, turn_index, role, content, "
                    "tool_calls, tool_results, timestamp, token_estimate "
                    f"FROM conversation_turns WHERE {where} "
                    "ORDER BY turn_index ASC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                )
                rows = self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
                return rows, total
            except Exception as e:
                logger.warning(f"Failed to list turns for {session_id}: {e}")
                return [], 0

    def delete_turns(self, turn_ids: list[int]) -> int:
        """Delete specific conversation_turns by their rowid."""
        if not self._conn or not turn_ids:
            return 0
        with self._lock:
            try:
                placeholders = ",".join("?" for _ in turn_ids)
                cur = self._conn.execute(
                    f"DELETE FROM conversation_turns WHERE id IN ({placeholders})",
                    turn_ids,
                )
                self._conn.commit()
                return cur.rowcount
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.warning(f"Failed to delete turns {turn_ids}: {e}")
                return 0

    def delete_turns_for_session(self, session_id: str) -> int:
        """删除指定 session 的所有 conversation_turns 记录（用于上下文重置）"""
        if not self._conn:
            return 0
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM conversation_turns WHERE session_id = ?",
                    (session_id,),
                )
                self._conn.commit()
                deleted = cur.rowcount
                if deleted:
                    logger.info(f"Deleted {deleted} conversation turns for session {session_id}")
                return deleted
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.warning(f"Failed to delete turns for {session_id}: {e}")
                return 0

    def search_turns(
        self,
        keyword: str,
        session_id: str | None = None,
        days_back: int = 7,
        limit: int = 20,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """按关键词搜索 conversation_turns（content + tool_calls + tool_results）。

        Phase 2b.5：新增 ``user_id`` / ``workspace_id`` 过滤，通过 JOIN
        ``session_tenants`` 限定结果到给定租户。和 ``search_episodes`` 一样，
        不传则保持旧的全库扫描行为（向后兼容）。
        """
        if not self._conn or not keyword:
            return []
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
        pattern = f"%{keyword}%"

        use_tenant_filter = user_id is not None or workspace_id is not None
        if use_tenant_filter:
            table_expr = (
                "conversation_turns AS ct "
                "INNER JOIN session_tenants AS st ON ct.session_id = st.session_id"
            )
            select_cols = (
                "ct.session_id, ct.turn_index, ct.role, ct.content, "
                "ct.tool_calls, ct.tool_results, ct.timestamp, ct.episode_id"
            )
        else:
            table_expr = "conversation_turns"
            select_cols = (
                "session_id, turn_index, role, content, "
                "tool_calls, tool_results, timestamp, episode_id"
            )

        conditions: list[str] = []
        params: list[Any] = []
        if use_tenant_filter and user_id is not None:
            conditions.append("st.user_id = ?")
            params.append(user_id)
        if use_tenant_filter and workspace_id is not None:
            conditions.append("st.workspace_id = ?")
            params.append(workspace_id)
        if session_id:
            conditions.append("ct.session_id = ?" if use_tenant_filter else "session_id = ?")
            params.append(session_id)
        conditions.append(("ct." if use_tenant_filter else "") + "timestamp >= ?")
        params.append(cutoff)
        # LIKE 三选一
        cols_prefix = "ct." if use_tenant_filter else ""
        conditions.append(
            f"({cols_prefix}content LIKE ? OR {cols_prefix}tool_calls LIKE ? "
            f"OR {cols_prefix}tool_results LIKE ?)"
        )
        params.extend([pattern, pattern, pattern])
        params.append(limit)

        where = " AND ".join(conditions)
        ordering = "ct.timestamp DESC" if use_tenant_filter else "timestamp DESC"
        try:
            cur = self._conn.execute(
                f"SELECT {select_cols} FROM {table_expr} WHERE {where} ORDER BY {ordering} LIMIT ?",
                params,
            )
            return self._rows_to_dicts(cur, json_fields=["tool_calls", "tool_results"])
        except Exception as e:
            logger.warning(f"Failed to search turns for '{keyword}': {e}")
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
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO extraction_queue
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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to enqueue extraction: {e}")

    def _recover_stuck_extractions(self, stuck_timeout_minutes: int = 30) -> int:
        """将卡在 'processing' 超过 stuck_timeout_minutes 的项重置为 'pending'"""
        if not self._conn:
            return 0
        try:
            cutoff = (datetime.now() - timedelta(minutes=stuck_timeout_minutes)).isoformat()
            cur = self._conn.execute(
                "UPDATE extraction_queue SET status = 'pending' "
                "WHERE status = 'processing' AND last_attempted_at < ?",
                (cutoff,),
            )
            self._conn.commit()
            recovered = cur.rowcount
            if recovered:
                logger.warning(
                    f"[ExtractionQueue] Recovered {recovered} stuck items (>{stuck_timeout_minutes}m)"
                )
            return recovered
        except Exception as e:
            if _is_db_locked(e):
                raise
            logger.error(f"Failed to recover stuck extractions: {e}")
            return 0

    def dequeue_extraction(self, batch_size: int = 10) -> list[dict]:
        if not self._conn:
            return []
        with self._lock:
            try:
                # 先恢复卡住的 processing 项
                self._recover_stuck_extractions()

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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to dequeue extraction: {e}")
                return []

    def complete_extraction(self, queue_id: int, success: bool = True) -> None:
        if not self._conn:
            return
        status = "completed" if success else "failed"
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE extraction_queue SET status = ? WHERE id = ?",
                    (status, queue_id),
                )
                self._conn.commit()
            except Exception as e:
                if _is_db_locked(e):
                    raise
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
        with self._lock:
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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to cache embedding: {e}")

    # ======================================================================
    # Attachments (文件/媒体记忆)
    # ======================================================================

    def save_attachment(self, data: dict) -> None:
        if not self._conn:
            return
        tags_val = json.dumps(normalize_tags(data.get("tags")), ensure_ascii=False)
        linked_val = data.get("linked_memory_ids", [])
        if isinstance(linked_val, list):
            linked_val = json.dumps(linked_val, ensure_ascii=False)

        with self._lock:
            try:
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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to save attachment {data.get('id')}: {e}")

    def get_attachment(self, attachment_id: str) -> dict | None:
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
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
        with self._lock:
            try:
                self._conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
                self._conn.commit()
                return True
            except Exception as e:
                if _is_db_locked(e):
                    raise
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
    # Durable context continuity
    # ======================================================================

    def save_context_epoch(self, session_id: str, epoch: dict) -> str:
        if not self._conn or not session_id or not epoch.get("digest"):
            return ""
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO context_epochs
                   (session_id, digest, system_digest, tools_digest, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    epoch["digest"],
                    epoch.get("system_digest", ""),
                    epoch.get("tools_digest", ""),
                    epoch.get("model", ""),
                    epoch.get("created_at") or datetime.now().isoformat(),
                ),
            )
            self._conn.commit()
        return str(epoch["digest"])

    def get_latest_context_epoch(self, session_id: str) -> dict | None:
        if not self._conn or not session_id:
            return None
        cursor = self._conn.execute(
            "SELECT * FROM context_epochs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        rows = self._rows_to_dicts(cursor)
        return rows[0] if rows else None

    def save_workspace_snapshot(self, snapshot: dict) -> str:
        if not self._conn or not snapshot.get("id"):
            return ""
        patch = str(snapshot.get("patch") or "")
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO workspace_snapshots
                   (id, session_id, root, capture_status, capture_error, vcs, head,
                    status_digest, changed_files, patch_zlib, patch_truncated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot["id"],
                    snapshot.get("session_id", ""),
                    snapshot.get("root", ""),
                    snapshot.get("capture_status", "unknown"),
                    str(snapshot.get("capture_error") or "")[:1000],
                    snapshot.get("vcs", ""),
                    snapshot.get("head", ""),
                    snapshot.get("status_digest", ""),
                    json.dumps(snapshot.get("changed_files", []), ensure_ascii=False),
                    zlib.compress(patch.encode("utf-8")) if patch else b"",
                    int(bool(snapshot.get("patch_truncated"))),
                    snapshot.get("created_at") or datetime.now().isoformat(),
                ),
            )
            self._conn.commit()
        return str(snapshot["id"])

    def get_workspace_snapshot(self, snapshot_id: str) -> dict | None:
        if not self._conn or not snapshot_id:
            return None
        cursor = self._conn.execute(
            "SELECT * FROM workspace_snapshots WHERE id = ?", (snapshot_id,)
        )
        rows = self._rows_to_dicts(cursor, json_fields=["changed_files"])
        if not rows:
            return None
        result = rows[0]
        compressed = result.pop("patch_zlib", b"") or b""
        result["patch"] = zlib.decompress(compressed).decode("utf-8") if compressed else ""
        result["patch_truncated"] = bool(result.get("patch_truncated"))
        return result

    def save_tool_output_blob(self, session_id: str, tool_name: str, content: str) -> str:
        if not self._conn or not content:
            return ""
        digest = hashlib.sha256(f"{session_id}\0{content}".encode()).hexdigest()
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO tool_output_blobs
                   (id, session_id, tool_name, content_zlib, content_chars, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    digest,
                    session_id,
                    tool_name,
                    zlib.compress(content.encode("utf-8")),
                    len(content),
                    datetime.now().isoformat(),
                ),
            )
            self._conn.commit()
        return digest

    def get_tool_output_blob(self, blob_id: str, session_id: str = "") -> str | None:
        if not self._conn or not blob_id:
            return None
        if session_id:
            row = self._conn.execute(
                "SELECT content_zlib FROM tool_output_blobs WHERE id = ? AND session_id = ?",
                (blob_id, session_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT content_zlib FROM tool_output_blobs WHERE id = ?", (blob_id,)
            ).fetchone()
        if not row:
            return None
        return zlib.decompress(row[0]).decode("utf-8")

    def save_compaction_checkpoint(self, checkpoint: dict) -> str:
        if not self._conn or not checkpoint.get("id") or not checkpoint.get("session_id"):
            return ""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO compaction_checkpoints
                   (id, session_id, status, source_digest, source_message_count,
                    session_source_digest, session_source_message_count,
                    summary, recent_messages, projected_messages, tail_start_index, tokens_before,
                    tokens_after, epoch_digest, workspace_snapshot_id, contributions,
                    model, agent_profile_id, created_at, completed_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint["id"],
                    checkpoint["session_id"],
                    checkpoint.get("status", "started"),
                    checkpoint.get("source_digest", ""),
                    int(checkpoint.get("source_message_count", 0)),
                    checkpoint.get("session_source_digest", ""),
                    int(checkpoint.get("session_source_message_count", 0)),
                    checkpoint.get("summary", ""),
                    json.dumps(checkpoint.get("recent_messages", []), ensure_ascii=False),
                    json.dumps(checkpoint.get("projected_messages", []), ensure_ascii=False),
                    int(checkpoint.get("tail_start_index", 0)),
                    int(checkpoint.get("tokens_before", 0)),
                    int(checkpoint.get("tokens_after", 0)),
                    checkpoint.get("epoch_digest", ""),
                    checkpoint.get("workspace_snapshot_id", ""),
                    json.dumps(checkpoint.get("contributions", []), ensure_ascii=False),
                    checkpoint.get("model", ""),
                    checkpoint.get("agent_profile_id", "default"),
                    checkpoint.get("created_at") or datetime.now().isoformat(),
                    checkpoint.get("completed_at", ""),
                    checkpoint.get("error", ""),
                ),
            )
            self._conn.commit()
        return str(checkpoint["id"])

    def get_latest_completed_compaction(self, session_id: str) -> dict | None:
        if not self._conn or not session_id:
            return None
        cursor = self._conn.execute(
            """SELECT * FROM compaction_checkpoints
               WHERE session_id = ? AND status = 'completed'
               ORDER BY completed_at DESC, created_at DESC LIMIT 1""",
            (session_id,),
        )
        rows = self._rows_to_dicts(
            cursor,
            json_fields=["recent_messages", "projected_messages", "contributions"],
        )
        return rows[0] if rows else None

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
            self.save_memories_batch(memories)  # already locked internally
            logger.info(f"Imported {len(memories)} memories from {json_path}")
            return len(memories)
        except Exception as e:
            logger.error(f"Failed to import memories from {json_path}: {e}")
            return 0

    def cleanup_expired(self) -> int:
        if not self._conn:
            return 0
        now = datetime.now().isoformat()
        with self._lock:
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
                if _is_db_locked(e):
                    raise
                logger.error(f"Failed to cleanup expired memories: {e}")
                return 0

    def get_expired_memory_ids(self) -> list[str]:
        if not self._conn:
            return []
        now = datetime.now().isoformat()
        try:
            cursor = self._conn.execute(
                "SELECT id FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            return [row["id"] for row in self._rows_to_dicts(cursor)]
        except Exception as e:
            if _is_db_locked(e):
                raise
            logger.error(f"Failed to list expired memories: {e}")
            return []

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
        key = str(self._db_path.resolve())
        with _instance_lock:
            if _instance_registry.get(key) is self:
                del _instance_registry[key]

    async def quiesce(self) -> None:
        """Close the underlying sqlite handle for quarantine to rename files.

        Async signature for consistency with the rest of the quiesce protocol;
        internally just delegates to :meth:`close`. Idempotent.
        """
        import asyncio as _asyncio

        await _asyncio.to_thread(self.close)

    def checkpoint_and_close(self, *, truncate: bool = True) -> None:
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                mode = "TRUNCATE" if truncate else "PASSIVE"
                conn.execute(f"PRAGMA wal_checkpoint({mode})")
            finally:
                conn.close()
                self._conn = None
        key = str(self._db_path.resolve())
        with _instance_lock:
            if _instance_registry.get(key) is self:
                del _instance_registry[key]

    def create_snapshot_incremental(
        self,
        *,
        max_size_bytes: int = 500 * 1024 * 1024,
        keep: int = 7,
    ) -> Path | None:
        if self._conn is None or not self._db_path.exists():
            return None
        if self._is_sync_folder_path() and os.environ.get("OPENAKITA_FORCE_SNAPSHOT") != "1":
            logger.warning(
                "[MemoryStorage] Snapshot skipped for sync folder path: %s", self._db_path
            )
            return None
        size = self._db_path.stat().st_size
        if size > max_size_bytes:
            logger.warning(
                "[MemoryStorage] Snapshot skipped because db is too large: %s bytes", size
            )
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot = self._db_path.with_name(f"{self._db_path.name}.snapshot.{timestamp}")
        tmp = snapshot.with_suffix(snapshot.suffix + ".tmp")
        with self._lock:
            try:
                dst = sqlite3.connect(str(tmp))
                try:
                    self._conn.backup(dst)
                    dst.commit()
                finally:
                    dst.close()
                tmp.replace(snapshot)
                check_conn = sqlite3.connect(str(snapshot))
                try:
                    row = check_conn.execute("PRAGMA quick_check").fetchone()
                    if str(row[0] if row else "").lower() != "ok":
                        snapshot.unlink(missing_ok=True)
                        raise MemoryStorageUnavailable(
                            "schema_corrupt", "snapshot quick_check failed"
                        )
                finally:
                    check_conn.close()
                self._prune_backups(pattern=f"{self._db_path.name}.snapshot.*", keep=keep)
                return snapshot
            except Exception as e:
                logger.warning("[MemoryStorage] Snapshot failed: %s", e)
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                return None

    def _is_sync_folder_path(self) -> bool:
        text = str(self._db_path).lower()
        sync_markers = ("onedrive", "dropbox", "google drive", "googledrive")
        return any(marker in text for marker in sync_markers) or text.startswith("\\\\")

    def _prune_backups(self, *, pattern: str, keep: int) -> None:
        try:
            candidates = sorted(
                self._db_path.parent.glob(pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in candidates[keep:]:
                path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("[MemoryStorage] Backup prune skipped: %s", e)

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
            if "tags" in d:
                d["tags"] = normalize_tags(d["tags"])
            results.append(d)
        return results
