"""SQLite-backed task / transcript / assets_bus / config manager for subtitle-craft.

Schema is the verbatim 4-table layout from ``docs/subtitle-craft-plan.md``
§8.3 (post-patch P-2 & P-6). Modeled after ``plugins/clip-sense/clip_task_manager.py``
with two structural additions for v2.0 forward-compatibility:

- ``tasks.origin_plugin_id`` / ``tasks.origin_task_id`` — DEFAULT NULL columns
  reserved for cross-plugin Handoff. **v1.0 always writes NULL**; v2.0 will
  populate them when accepting a Handoff payload from another plugin.
- ``assets_bus`` table — full schema reserved for v2.0 cross-plugin asset
  cache. **v1.0 creates the table on init but the pipeline never INSERT/UPDATEs
  any row.** Every Phase 1 / Phase 3 unit test asserts ``COUNT(*) == 0``.

These additions are explicitly called out in ``docs/post-production-plugins-roadmap.md``
§4 and protected by the Phase 0 ``test_no_handoff_*`` red-line guards (no
Python module / route / tool may reference Handoff in v1.0).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_CONFIG: dict[str, str] = {
    "dashscope_api_key": "",
    "dashscope_base_url": "https://dashscope.aliyuncs.com",
    "ffmpeg_path": "",
    "default_translation_model": "qwen-mt-flash",
    "default_subtitle_style": "default",
    "default_burn_path": "ass",  # "ass" (ffmpeg) or "html" (Playwright)
    "diarization_default": "false",
    "character_identify_default": "false",
    "moderation_filter_enabled": "true",
    "paraformer_timeout_sec": "900",
    "poll_interval_sec": "3",
}


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ---------------------------------------------------------------------------
# Public update whitelists.
#
# Mirrors ``docs/subtitle-craft-plan.md`` §8.3. Any attempt to update a column
# outside these sets raises ``ValueError`` — this is the C6-reverse-example
# guard ("silent param ignore") for SQL writes.
# ---------------------------------------------------------------------------

_UPDATABLE_COLUMNS: dict[str, frozenset[str]] = {
    "tasks": frozenset(
        {
            "status",
            "params_json",
            "transcript_id",
            "paraformer_task_id",
            "output_srt_path",
            "output_vtt_path",
            "output_video_path",
            "cost_json",
            "pipeline_step",
            "error_kind",
            "error_message",
            "error_hints_json",
            "updated_at",
        }
    ),
    "transcripts": frozenset(
        {
            "status",
            "duration_sec",
            "words_json",
            "full_text",
            "language",
            "speaker_count",
            "speaker_map_json",
            "channel_count",
            "raw_payload_json",
            "updated_at",
        }
    ),
    # v1.0: assets_bus is NEVER written by the pipeline; declared here so v2.0
    # plugins can call ``update_asset(...)`` without re-touching this module.
    "assets_bus": frozenset({"shared_with", "metadata_json"}),
}

# Logical name → real DB column name (some payload keys differ from columns,
# e.g. ``params`` ↔ ``params_json``). Values absent from this map are treated
# as already being canonical column names.
_TASK_COLUMN_ALIASES: dict[str, str] = {
    "params": "params_json",
    "cost": "cost_json",
    "error_hints": "error_hints_json",
}

_TASK_JSON_KEYS: frozenset[str] = frozenset({"params", "cost", "error_hints"})

_TRANSCRIPT_COLUMN_ALIASES: dict[str, str] = {
    "words": "words_json",
    "speaker_map": "speaker_map_json",
    "raw_payload": "raw_payload_json",
}

_TRANSCRIPT_JSON_KEYS: frozenset[str] = frozenset(
    {"words", "speaker_map", "raw_payload"},
)


class SubtitleTaskManager:
    """Async SQLite manager for subtitle-craft (4-table schema).

    Tables: ``tasks`` / ``transcripts`` / ``assets_bus`` (v2.0-reserved) /
    ``config``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._canceled: set[str] = set()

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
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
        await self._db.executescript(
            """
            -- tasks
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                mode TEXT NOT NULL,
                source_kind TEXT,
                source_path TEXT,
                source_duration_sec REAL,
                source_lang TEXT,
                target_lang TEXT,
                asset_id TEXT,
                transcript_id TEXT,
                params_json TEXT,
                paraformer_task_id TEXT,
                output_srt_path TEXT,
                output_vtt_path TEXT,
                output_video_path TEXT,
                cost_json TEXT,
                pipeline_step TEXT,
                error_kind TEXT,
                error_message TEXT,
                error_hints_json TEXT,
                -- v1.0 always NULL; v2.0 cross-plugin Handoff enables.
                origin_plugin_id TEXT DEFAULT NULL,
                origin_task_id TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_mode ON tasks(mode);
            CREATE INDEX IF NOT EXISTS idx_tasks_origin ON tasks(origin_plugin_id);

            -- transcripts (word-level cache; the v1.0 main asset table)
            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                source_hash TEXT UNIQUE,
                source_path TEXT,
                source_name TEXT,
                duration_sec REAL,
                words_json TEXT,
                full_text TEXT,
                language TEXT,
                speaker_count INTEGER,
                speaker_map_json TEXT,
                channel_count INTEGER,
                status TEXT DEFAULT 'pending',
                paraformer_task_id TEXT,
                raw_payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transcripts_hash ON transcripts(source_hash);

            -- assets_bus (cross-plugin asset registry — v2.0 only)
            -- v1.0: table exists for forward-compat; pipeline NEVER writes here.
            CREATE TABLE IF NOT EXISTS assets_bus (
                asset_id TEXT PRIMARY KEY,
                source_path TEXT,
                preview_url TEXT,
                asset_kind TEXT,
                duration_sec REAL,
                metadata_json TEXT,
                created_by_plugin TEXT,
                shared_with TEXT,
                created_at TEXT NOT NULL
            );

            -- config
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self._db.commit()

    async def _seed_config(self) -> None:
        assert self._db
        for k, v in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (k, v),
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Config CRUD
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

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        mode: str,
        source_kind: str = "",
        source_path: str = "",
        source_duration_sec: float | None = None,
        source_lang: str = "",
        target_lang: str = "",
        asset_id: str = "",
        params: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> dict[str, Any]:
        assert self._db
        task_id = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO tasks
               (id, status, mode, source_kind, source_path, source_duration_sec,
                source_lang, target_lang, asset_id, params_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                status,
                mode,
                source_kind,
                source_path,
                source_duration_sec,
                source_lang,
                target_lang,
                asset_id,
                json.dumps(params or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self._db.commit()
        task = await self.get_task(task_id)
        assert task is not None
        return task

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return self._task_row_to_dict(row) if row else None

    async def update_task(self, task_id: str, **updates: Any) -> None:
        """Update one task. Raises ``ValueError`` for non-whitelisted columns.

        Use ``update_task_safe`` from pipeline error handlers to silently
        ignore extra keys (also raises on unknown ones — but only in
        development; production stub may swallow).
        """
        assert self._db
        if not updates:
            return
        sets, vals = self._build_update(
            "tasks",
            updates,
            _TASK_COLUMN_ALIASES,
            _TASK_JSON_KEYS,
        )
        sets.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
        await self._db.execute(sql, vals)
        await self._db.commit()

    async def update_task_safe(self, task_id: str, **updates: Any) -> None:
        """Like ``update_task`` but never raises — pipeline error handlers
        use this so a malformed update payload doesn't mask the original error.
        """
        try:
            await self.update_task(task_id, **updates)
        except (ValueError, aiosqlite.Error):
            # Best-effort: do not propagate during error-handling paths.
            pass

    async def delete_task(self, task_id: str) -> bool:
        assert self._db
        cur = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        self._canceled.discard(task_id)
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

        cur = await self._db.execute(f"SELECT COUNT(*) as cnt FROM tasks {where}", params)
        row = await cur.fetchone()
        total = row["cnt"] if row else 0

        cur = await self._db.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = await cur.fetchall()
        return {
            "tasks": [self._task_row_to_dict(r) for r in rows],
            "total": total,
        }

    async def get_running_tasks(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute("SELECT * FROM tasks WHERE status IN ('pending', 'running')")
        rows = await cur.fetchall()
        return [self._task_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cooperative cancel (in-memory flag — pipeline polls this between steps)
    # ------------------------------------------------------------------

    def request_cancel(self, task_id: str) -> None:
        self._canceled.add(task_id)

    def is_canceled(self, task_id: str) -> bool:
        return task_id in self._canceled

    def clear_cancel(self, task_id: str) -> None:
        self._canceled.discard(task_id)

    # ------------------------------------------------------------------
    # Transcript CRUD
    # ------------------------------------------------------------------

    async def create_transcript(
        self,
        *,
        source_hash: str,
        source_path: str = "",
        source_name: str = "",
        duration_sec: float | None = None,
        language: str = "",
    ) -> dict[str, Any]:
        assert self._db
        tid = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO transcripts
               (id, source_hash, source_path, source_name, duration_sec,
                language, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (tid, source_hash, source_path, source_name, duration_sec, language, now, now),
        )
        await self._db.commit()
        tr = await self.get_transcript(tid)
        assert tr is not None
        return tr

    async def get_transcript(self, tid: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute("SELECT * FROM transcripts WHERE id = ?", (tid,))
        row = await cur.fetchone()
        return self._transcript_row_to_dict(row) if row else None

    async def get_transcript_by_hash(self, source_hash: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM transcripts WHERE source_hash = ?",
            (source_hash,),
        )
        row = await cur.fetchone()
        return self._transcript_row_to_dict(row) if row else None

    async def update_transcript(self, tid: str, **updates: Any) -> None:
        assert self._db
        if not updates:
            return
        sets, vals = self._build_update(
            "transcripts",
            updates,
            _TRANSCRIPT_COLUMN_ALIASES,
            _TRANSCRIPT_JSON_KEYS,
        )
        sets.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(tid)
        sql = f"UPDATE transcripts SET {', '.join(sets)} WHERE id = ?"
        await self._db.execute(sql, vals)
        await self._db.commit()

    async def list_transcripts(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        assert self._db
        cur = await self._db.execute("SELECT COUNT(*) as cnt FROM transcripts")
        row = await cur.fetchone()
        total = row["cnt"] if row else 0
        cur = await self._db.execute(
            "SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        )
        rows = await cur.fetchall()
        return {
            "transcripts": [self._transcript_row_to_dict(r) for r in rows],
            "total": total,
        }

    async def delete_transcript(self, tid: str) -> bool:
        assert self._db
        cur = await self._db.execute("DELETE FROM transcripts WHERE id = ?", (tid,))
        await self._db.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # assets_bus reads (v1.0 read-only; rows only ever come from v2.0 code)
    # ------------------------------------------------------------------

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute("SELECT * FROM assets_bus WHERE asset_id = ?", (asset_id,))
        row = await cur.fetchone()
        return self._asset_row_to_dict(row) if row else None

    async def assets_bus_count(self) -> int:
        """Test/diagnostic helper: v1.0 invariant is COUNT == 0 always."""
        assert self._db
        cur = await self._db.execute("SELECT COUNT(*) as cnt FROM assets_bus")
        row = await cur.fetchone()
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_update(
        table: str,
        updates: dict[str, Any],
        aliases: dict[str, str],
        json_keys: Iterable[str],
    ) -> tuple[list[str], list[Any]]:
        whitelist = _UPDATABLE_COLUMNS[table]
        json_keys_set = frozenset(json_keys)
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in updates.items():
            col = aliases.get(k, k)
            if col not in whitelist:
                raise ValueError(
                    f"update_{table}: column '{k}' (-> '{col}') is not whitelisted "
                    f"(allowed logical keys: "
                    f"{sorted(set(whitelist) | set(aliases.keys()))})"
                )
            sets.append(f"{col} = ?")
            if k in json_keys_set:
                vals.append(json.dumps(v, ensure_ascii=False) if v is not None else None)
            else:
                vals.append(v)
        return sets, vals

    @staticmethod
    def _task_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("params_json", "cost_json", "error_hints_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        return d

    @staticmethod
    def _transcript_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("words_json", "speaker_map_json", "raw_payload_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        # UI-friendly aliases. The transcripts table column is named
        # `language` (matches Paraformer's `language` field) but the UI's
        # TranscriptsList expects `source_lang` for parity with the tasks
        # table. Compute both rather than break either consumer.
        d["source_lang"] = d.get("language") or ""
        words = d.get("words")
        d["word_count"] = len(words) if isinstance(words, list) else 0
        d["engine"] = "paraformer-v2"
        return d

    @staticmethod
    def _asset_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        val = d.pop("metadata_json", None)
        try:
            d["metadata"] = json.loads(val) if val else None
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = None
        return d
