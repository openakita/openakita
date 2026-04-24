# ruff: noqa: N999
"""SQLite-backed task / config / assets_bus manager for footage-gate.

Schema is the verbatim 3-table layout from the v1.0 plan §5.1. Modeled
after ``plugins/subtitle-craft/subtitle_task_manager.py`` with two
deliberate simplifications:

- No ``transcripts`` table — footage-gate's transcription is opt-in and
  ephemeral (we just store the resulting text in ``params_json``).
- ``assets_bus`` is **created but never written in v1.0**: the table
  exists so v2.0 cross-plugin handoff (subtitle-craft → footage-gate →
  media-post) can land without a schema migration. Phase 1 tests assert
  ``COUNT(*) == 0`` after every CRUD operation.

Hardening reused from subtitle-craft / seedance:

- ``update_task_safe`` accepts only whitelisted columns; passing an
  unknown column raises ``ValueError`` (defends against SQL-column
  injection via dynamic UPDATE column names).
- ``cleanup_expired`` purges tasks whose ``completed_at`` falls outside
  the configured retention window.
- All writes serialise via ``aiosqlite``'s implicit per-connection lock —
  we never spawn a second connection so concurrent callers wait their
  turn rather than racing the SQLite file.
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
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_path TEXT NOT NULL DEFAULT '',
    input_kind TEXT,
    is_hdr_source INTEGER NOT NULL DEFAULT 0,
    params_json TEXT NOT NULL DEFAULT '{}',
    output_path TEXT,
    report_path TEXT,
    thumbs_json TEXT,
    error_kind TEXT,
    error_message TEXT,
    error_hints_json TEXT,
    duration_input_sec REAL,
    duration_output_sec REAL,
    removed_seconds REAL,
    qc_attempts INTEGER NOT NULL DEFAULT 0,
    qc_issues_count INTEGER,
    -- v2.0 cross-plugin handoff (DEFAULT NULL in v1.0).
    origin_plugin_id TEXT,
    origin_task_id TEXT,
    asset_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- v2.0 reserved (created on init, NEVER written in v1.0).
CREATE TABLE IF NOT EXISTS assets_bus (
    asset_id TEXT PRIMARY KEY,
    asset_kind TEXT NOT NULL,
    asset_uri TEXT NOT NULL,
    source_plugin_id TEXT NOT NULL,
    source_task_id TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_mode       ON tasks(mode);
CREATE INDEX IF NOT EXISTS idx_tasks_created    ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_completed  ON tasks(completed_at);
"""


DEFAULT_CONFIG: dict[str, str] = {
    # Optional: only consumed by source_review when the user toggles the
    # "transcribe with Paraformer" checkbox.
    "transcribe_api_key": "",
    "default_mode": "source_review",
    # auto_color
    "auto_color_preset": "auto",
    "auto_color_hdr_tonemap": "true",
    # silence_cut (defaults mirror SILENCE_DEFAULTS in footage_gate_models)
    "silence_threshold_db": "-45",
    "silence_min_silence_len": "0.15",
    "silence_min_sound_len": "0.05",
    "silence_pad": "0.05",
    # cut_qc
    "cut_qc_auto_remux_default": "false",
    "cut_qc_max_attempts": "3",
    "cut_qc_subtitle_safe_zone_min_marginv": "90",
    # General
    "ffmpeg_timeout_sec": "600",
    "task_retention_days": "30",
    "output_dir": "",
    "output_naming_rule": "{date}_{mode}_{shortid}",
    "callback_url": "",
}


# Whitelist of caller-facing keys → physical column names. Any key not
# in this map is rejected by ``update_task_safe`` to prevent column-name
# injection via the dynamic UPDATE statement.
_UPDATABLE_COLUMNS: dict[str, str] = {
    "status": "status",
    "input_kind": "input_kind",
    "is_hdr_source": "is_hdr_source",
    "params": "params_json",
    "output_path": "output_path",
    "report_path": "report_path",
    "thumbs": "thumbs_json",
    "error_kind": "error_kind",
    "error_message": "error_message",
    "error_hints": "error_hints_json",
    "duration_input_sec": "duration_input_sec",
    "duration_output_sec": "duration_output_sec",
    "removed_seconds": "removed_seconds",
    "qc_attempts": "qc_attempts",
    "qc_issues_count": "qc_issues_count",
    "completed_at": "completed_at",
    "asset_id": "asset_id",
}

# Logical → JSON-encoded columns. Values are dumped with ``json.dumps`` on
# write and loaded with ``json.loads`` on read so callers see Python dicts.
_JSON_KEYS: frozenset[str] = frozenset({"params", "thumbs", "error_hints"})


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


class FootageGateTaskManager:
    """Async sqlite manager owning the three footage-gate tables.

    Lifecycle:
    - ``init()`` opens the connection, runs ``SCHEMA_SQL`` + seeds defaults.
    - ``close()`` cleanly closes the connection (called from on_unload).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
        await self._init_default_config()
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _init_default_config(self) -> None:
        assert self._db is not None
        for key, val in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, val),
            )

    # ── Config ───────────────────────────────────────────────────────

    async def get_config(self, key: str) -> str:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT value FROM config WHERE key = ?", (key,))
        if rows:
            return rows[0][0]
        return DEFAULT_CONFIG.get(key, "")

    async def get_all_config(self) -> dict[str, str]:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT key, value FROM config")
        return {r[0]: r[1] for r in rows}

    async def set_config(self, key: str, value: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        assert self._db is not None
        for k, v in updates.items():
            await self._db.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (k, v),
            )
        await self._db.commit()

    # ── Tasks ────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        mode: str,
        input_path: str,
        params: dict[str, Any] | None = None,
        input_kind: str | None = None,
        status: str = "pending",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        assert self._db is not None
        task_id = task_id or _short_id()
        now = time.time()
        row = {
            "id": task_id,
            "mode": mode,
            "status": status,
            "input_path": input_path,
            "input_kind": input_kind,
            "is_hdr_source": 0,
            "params_json": json.dumps(params or {}, ensure_ascii=False),
            "qc_attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        await self._db.execute(
            f"INSERT INTO tasks ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        await self._db.commit()
        return await self.get_task(task_id) or {}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        assert self._db is not None
        wheres: list[str] = []
        args: list[Any] = []
        if mode:
            wheres.append("mode = ?")
            args.append(mode)
        if status:
            wheres.append("status = ?")
            args.append(status)
        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

        count_rows = await self._db.execute_fetchall(f"SELECT COUNT(*) FROM tasks{where_sql}", args)
        total = int(count_rows[0][0]) if count_rows else 0

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)],
        )
        return [self._row_to_task(r) for r in rows], total

    async def update_task_safe(self, task_id: str, **updates: Any) -> bool:
        """Strict whitelist UPDATE — see ``_UPDATABLE_COLUMNS`` for the
        accepted keys. Raises ``ValueError`` on unknown column names so a
        typo surfaces in tests instead of being silently dropped.
        """
        assert self._db is not None
        if not updates:
            return False
        sets: list[str] = []
        args: list[Any] = []
        for k, v in updates.items():
            col = _UPDATABLE_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task_safe: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(_UPDATABLE_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in _JSON_KEYS:
                args.append(json.dumps(v, ensure_ascii=False))
            elif k == "is_hdr_source":
                args.append(1 if v else 0)
            else:
                args.append(v)
        sets.append("updated_at = ?")
        args.append(time.time())
        args.append(task_id)
        result = await self._db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args)
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def delete_task(self, task_id: str) -> bool:
        assert self._db is not None
        result = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def cleanup_expired(self, retention_days: int) -> int:
        """Purge tasks whose ``completed_at`` is older than ``retention_days``.

        Returns the number of rows deleted. ``retention_days <= 0`` is a no-op
        (callers can disable the sweep without removing the cron entry).
        """
        assert self._db is not None
        if retention_days <= 0:
            return 0
        cutoff = time.time() - (retention_days * 86400)
        result = await self._db.execute(
            "DELETE FROM tasks WHERE completed_at IS NOT NULL AND completed_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json") or "{}")
        thumbs_raw = d.pop("thumbs_json", None)
        d["thumbs"] = json.loads(thumbs_raw) if thumbs_raw else []
        hints_raw = d.pop("error_hints_json", None)
        d["error_hints"] = json.loads(hints_raw) if hints_raw else []
        d["is_hdr_source"] = bool(d.get("is_hdr_source") or 0)
        return d

    # ── assets_bus (v2.0 reserved — v1.0 only CREATE/READ are wired) ─

    async def list_assets_bus(self) -> list[dict[str, Any]]:
        """Return all rows in ``assets_bus``.

        v1.0: always ``[]`` because the pipeline never INSERTs. Exposed so
        the Phase 1 ``test_task_manager`` red-line can assert "the table
        is empty after every CRUD path" without monkey-patching internals.
        """
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT * FROM assets_bus ORDER BY created_at")
        return [dict(r) for r in rows]

    async def count_assets_bus(self) -> int:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT COUNT(*) FROM assets_bus")
        return int(rows[0][0]) if rows else 0


__all__ = [
    "DEFAULT_CONFIG",
    "SCHEMA_SQL",
    "FootageGateTaskManager",
]
