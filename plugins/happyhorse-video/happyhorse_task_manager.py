"""happyhorse-video task manager — pure ``aiosqlite`` CRUD.

Five tables, no foreign keys (we keep cross-table cleanup explicit so a
failed asset cleanup never leaves a task row in an undeletable state):

- ``tasks``    — one row per generation job (HappyHorse / Wan / s2v /
                 animate / videoretalk). Carries the workbench protocol
                 fields (``video_url`` / ``video_path`` /
                 ``last_frame_url`` / ``last_frame_path`` /
                 ``asset_ids_json``) so the OrgRuntime hook can register
                 produced media as task attachments.
- ``assets``   — Asset Bus shadow rows; an asset_id here is what
                 downstream workbenches consume via ``from_asset_ids``.
- ``voices``   — system voices are NOT persisted; this table only holds
                 user-cloned cosyvoice-v2 voices. The system catalog stays
                 a pure code constant — see
                 :data:`happyhorse_models.SYSTEM_VOICES`.
- ``figures``  — uploaded portrait images (with ``wan2.2-s2v-detect``
                 cache), selectable from CreateTab as a one-click figure.
- ``config``   — plugin settings (api_key / oss_* / per-mode default
                 model / preferences). Written by the ``PUT /settings``
                 route, read on plugin load and on every DashScope call.

Pixelle anti-patterns avoided
-----------------------------
- C1 in-memory task store → SQLite WAL on disk.
- C7 implicit env-var paths → caller hands us an absolute ``db_path``
  derived from ``api.get_data_dir()``; we never read ENV.
- C6 silent column corruption → ``update_task_safe`` rejects unknown /
  read-only columns with ``ValueError``.

The ``update_task_safe`` whitelist is the only path that mutates a task
row; ``id`` / ``created_at`` are non-writable.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    mode                TEXT NOT NULL,
    model_id            TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    prompt              TEXT NOT NULL DEFAULT '',
    params_json         TEXT NOT NULL DEFAULT '{}',
    dashscope_id        TEXT,
    dashscope_endpoint  TEXT,
    asset_paths_json    TEXT NOT NULL DEFAULT '{}',
    -- Workbench protocol fields. Always populated on succeeded tasks
    -- so OrgRuntime._record_plugin_asset_output picks the artifacts up.
    video_url           TEXT NOT NULL DEFAULT '',
    video_path          TEXT NOT NULL DEFAULT '',
    last_frame_url      TEXT NOT NULL DEFAULT '',
    last_frame_path     TEXT NOT NULL DEFAULT '',
    asset_ids_json      TEXT NOT NULL DEFAULT '[]',
    -- Cost & error metadata (mirrors avatar-studio).
    cost_breakdown_json TEXT,
    error_kind          TEXT,
    error_message       TEXT,
    error_hints_json    TEXT,
    audio_duration_sec  REAL,
    video_duration_sec  REAL,
    -- Idempotency for CreateTab double-submits (browser-supplied).
    client_request_id   TEXT NOT NULL DEFAULT '',
    -- Long-video chain grouping (set when a task is part of a
    -- storyboard chain; same group_id across all segments).
    chain_group_id      TEXT NOT NULL DEFAULT '',
    chain_index         INTEGER,
    chain_total         INTEGER,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL
);

CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    task_id         TEXT,
    type            TEXT NOT NULL,            -- video / image / audio
    file_path       TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT '', -- first_frame / last_frame / output_video / ...
    sort_order      INTEGER NOT NULL DEFAULT 0,
    original_name   TEXT,
    size_bytes      INTEGER,
    width           INTEGER,
    height          INTEGER,
    duration_sec    REAL,
    validated_at    REAL,
    validation_result_json TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS voices (
    id                  TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    sample_url          TEXT,
    is_system           INTEGER NOT NULL DEFAULT 0,
    source_audio_path   TEXT,
    dashscope_voice_id  TEXT,
    language            TEXT NOT NULL DEFAULT 'zh-CN',
    gender              TEXT NOT NULL DEFAULT 'unknown',
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS figures (
    id                  TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    image_path          TEXT NOT NULL,
    preview_url         TEXT NOT NULL,
    -- Signed OSS URL pushed by POST /figures when OSS is configured.
    -- This is the URL fed to DashScope for face-detect / s2v /
    -- animate-mix when the figure is picked from the library.
    oss_url             TEXT NOT NULL DEFAULT '',
    oss_key             TEXT NOT NULL DEFAULT '',
    detect_pass         INTEGER NOT NULL DEFAULT 0,
    detect_humanoid     INTEGER NOT NULL DEFAULT 0,
    detect_message      TEXT,
    detect_status       TEXT NOT NULL DEFAULT 'pending',
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_mode       ON tasks(mode);
CREATE INDEX IF NOT EXISTS idx_tasks_created    ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_chain      ON tasks(chain_group_id);
CREATE INDEX IF NOT EXISTS idx_tasks_request_id ON tasks(client_request_id);
CREATE INDEX IF NOT EXISTS idx_assets_task      ON assets(task_id);
CREATE INDEX IF NOT EXISTS idx_assets_type      ON assets(type);
CREATE INDEX IF NOT EXISTS idx_voices_system    ON voices(is_system);
CREATE INDEX IF NOT EXISTS idx_figures_created  ON figures(created_at DESC);
"""


# Default plugin settings. Read on plugin load to seed an empty DB so the
# UI's GET /settings has something to render. PUT /settings replaces only
# the keys the caller touched (incremental save).
DEFAULT_CONFIG: dict[str, str] = {
    # Aliyun DashScope (Bailian) credentials.
    "api_key": "",
    "base_url": "https://dashscope.aliyuncs.com",
    # OSS — required for any DashScope video task.
    "oss_endpoint": "",
    "oss_bucket": "",
    "oss_access_key_id": "",
    "oss_access_key_secret": "",
    "oss_path_prefix": "happyhorse-video",
    # Default preferences.
    "default_resolution": "720P",
    "default_aspect_ratio": "16:9",
    "default_duration": "5",
    "default_voice": "longxiaochun_v2",
    "tts_engine": "cosyvoice",
    "tts_voice_edge": "zh-CN-YunxiNeural",
    # Per-mode default model — Create form falls back to these if the
    # caller doesn't specify ``model``. Keys mirror the 12 modes
    # registered in :data:`happyhorse_models.MODES`.
    "default_model_t2v": "happyhorse-1.0-t2v",
    "default_model_i2v": "happyhorse-1.0-i2v",
    "default_model_i2v_end": "wan2.7-i2v",
    "default_model_video_extend": "wan2.7-i2v",
    "default_model_r2v": "happyhorse-1.0-r2v",
    "default_model_video_edit": "happyhorse-1.0-video-edit",
    "default_model_photo_speak": "wan2.2-s2v",
    "default_model_video_relip": "videoretalk",
    "default_model_video_reface": "wan2.2-animate-mix",
    "default_model_pose_drive": "wan2.2-animate-move",
    "default_model_avatar_compose": "wan2.7-image",
    "default_model_long_video": "happyhorse-1.0-i2v",
    # Cost gate.
    "cost_threshold_cny": "5.00",
    # HTTP behaviour.
    "timeout_sec": "60",
    "max_retries": "2",
    # Storage / cleanup.
    "custom_data_dir": "",
    "output_subdir_mode": "task",
    "output_naming_rule": "{filename}",
    "auto_archive": "false",
    "retention_days": "30",
    # Long-video / system-deps preferences.
    "poll_interval": "10",
    "auto_download": "true",
}


_TASK_WRITABLE: frozenset[str] = frozenset(
    {
        "status",
        "mode",
        "model_id",
        "prompt",
        "params_json",
        "dashscope_id",
        "dashscope_endpoint",
        "asset_paths_json",
        "video_url",
        "video_path",
        "last_frame_url",
        "last_frame_path",
        "asset_ids_json",
        "cost_breakdown_json",
        "error_kind",
        "error_message",
        "error_hints_json",
        "audio_duration_sec",
        "video_duration_sec",
        "client_request_id",
        "chain_group_id",
        "chain_index",
        "chain_total",
        "completed_at",
    }
)

_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled", "timeout"}
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite Row into a plain dict, decoding *_json columns.

    The decoded variant lives on the bare key (``params``) while the raw
    JSON string stays on the ``*_json`` key, so callers that round-trip
    rows into the DB can write back the raw form unchanged.
    """
    if row is None:
        return None
    out: dict[str, Any] = dict(row)
    for k in (
        "params_json",
        "asset_paths_json",
        "asset_ids_json",
        "cost_breakdown_json",
        "error_hints_json",
    ):
        if k in out and isinstance(out[k], str) and out[k]:
            try:
                out[k.removesuffix("_json")] = json.loads(out[k])
            except (ValueError, TypeError):
                pass
    # Always expose ``asset_ids`` as a list — workbench callers count on it.
    if not isinstance(out.get("asset_ids"), list):
        out["asset_ids"] = []
    return out


class HappyhorseTaskManager:
    """SQLite-backed CRUD for tasks / assets / voices / figures / config.

    Lifecycle:

        tm = HappyhorseTaskManager(db_path)
        async with tm:                # opens DB + creates schema + seeds config
            await tm.create_task(...)

    Or call ``await tm.init()`` / ``await tm.close()`` manually.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def __aenter__(self) -> HappyhorseTaskManager:
        await self.init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def init(self) -> None:
        if self._db is not None:
            return
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
            try:
                await self._db.close()
            finally:
                self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("HappyhorseTaskManager.init() must be called first")
        return self._db

    async def _init_default_config(self) -> None:
        for key, val in DEFAULT_CONFIG.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, val),
            )

    # ── Config ─────────────────────────────────────────────────────────

    async def get_config(self, key: str) -> str:
        async with self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return str(row[0])
        return DEFAULT_CONFIG.get(key, "")

    async def get_all_config(self) -> dict[str, str]:
        async with self._conn.execute("SELECT key, value FROM config") as cur:
            rows = await cur.fetchall()
        merged = dict(DEFAULT_CONFIG)
        for r in rows:
            merged[str(r[0])] = str(r[1])
        return merged

    async def set_config(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._conn.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        if not updates:
            return
        for k, v in updates.items():
            await self._conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (k, v),
            )
        await self._conn.commit()

    # ── Tasks ──────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        mode: str,
        model_id: str = "",
        prompt: str = "",
        params: dict[str, Any] | None = None,
        asset_paths: dict[str, str] | None = None,
        cost_breakdown: dict[str, Any] | None = None,
        client_request_id: str = "",
        chain_group_id: str = "",
        chain_index: int | None = None,
        chain_total: int | None = None,
    ) -> str:
        """Insert a new task row and return its id."""
        task_id = _new_id("hh")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO tasks (
                id, mode, model_id, status, prompt, params_json,
                asset_paths_json, cost_breakdown_json,
                client_request_id, chain_group_id, chain_index, chain_total,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                mode,
                model_id,
                prompt,
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(asset_paths or {}, ensure_ascii=False),
                json.dumps(cost_breakdown, ensure_ascii=False) if cost_breakdown else None,
                client_request_id,
                chain_group_id,
                chain_index,
                chain_total,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def get_task_by_client_request_id(
        self, client_request_id: str
    ) -> dict[str, Any] | None:
        """Return the latest task created by a given browser request id.

        Used as a post-reload safety net to absorb double-submits the
        in-process ``_pending_create_requests`` guard would have caught
        in the live case. Scans a recent window because SQLite JSON1
        availability varies by build (so we avoid ``json_extract``).
        """
        if not client_request_id:
            return None
        async with self._conn.execute(
            "SELECT * FROM tasks WHERE client_request_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (client_request_id,),
        ) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        mode: str | None = None,
        chain_group_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        binds: list[Any] = []
        if status:
            clauses.append("status = ?")
            binds.append(status)
        if mode:
            clauses.append("mode = ?")
            binds.append(mode)
        if chain_group_id:
            clauses.append("chain_group_id = ?")
            binds.append(chain_group_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # ``ROWID DESC`` is a tiebreaker for equal ``created_at`` (Windows
        # ``time.time()`` granularity is ~15ms and rapid inserts can collide).
        sql = (
            f"SELECT * FROM tasks {where} "
            "ORDER BY created_at DESC, ROWID DESC LIMIT ? OFFSET ?"
        )
        binds.extend([max(1, min(200, int(limit))), max(0, int(offset))])
        async with self._conn.execute(sql, tuple(binds)) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def count_tasks(self, *, status: str | None = None) -> int:
        if status:
            sql = "SELECT COUNT(*) FROM tasks WHERE status = ?"
            binds: tuple[Any, ...] = (status,)
        else:
            sql = "SELECT COUNT(*) FROM tasks"
            binds = ()
        async with self._conn.execute(sql, binds) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def update_task_safe(self, task_id: str, /, **updates: Any) -> bool:
        """Update writable columns only. Returns True iff a row was changed.

        - Unknown / read-only columns raise ``ValueError`` (loud failure
          beats silent corruption — Pixelle C6 — and prevents accidental
          mutation of ``id`` / ``created_at``).
        - ``status`` value is validated against ``_TASK_STATUSES``.
        - dict / list values are auto-encoded to JSON for ``*_json`` columns.
        - ``updated_at`` is bumped automatically.
        """
        if not updates:
            return False

        bad = set(updates) - _TASK_WRITABLE
        if bad:
            raise ValueError(
                f"non-writable column(s) for tasks: {sorted(bad)}; "
                f"writable={sorted(_TASK_WRITABLE)}",
            )
        if "status" in updates and updates["status"] not in _TASK_STATUSES:
            raise ValueError(
                f"invalid status {updates['status']!r}; "
                f"allowed={sorted(_TASK_STATUSES)}",
            )

        cols = list(updates)
        binds: list[Any] = []
        for c in cols:
            v = updates[c]
            if c.endswith("_json") and not isinstance(v, (str, type(None))):
                v = json.dumps(v, ensure_ascii=False)
            binds.append(v)
        binds.append(_now())
        binds.append(task_id)
        sql = (
            f"UPDATE tasks SET {', '.join(f'{c} = ?' for c in cols)}, "
            "updated_at = ? WHERE id = ?"
        )
        cursor = await self._conn.execute(sql, tuple(binds))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def mark_task_timeout(
        self,
        task_id: str,
        error_message: str = "等待 HappyHorse / Wan 任务完成超时",
    ) -> bool:
        return await self.update_task_safe(
            task_id,
            status="timeout",
            error_kind="timeout",
            error_message=error_message,
            completed_at=_now(),
        )

    async def delete_task(self, task_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def list_expired_task_ids(self, *, retention_days: int = 30) -> list[str]:
        cutoff = _now() - max(0, int(retention_days)) * 86400
        async with self._conn.execute(
            "SELECT id FROM tasks WHERE created_at < ? "
            "AND status IN ('succeeded','failed','cancelled','timeout')",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
        return [str(r[0]) for r in rows]

    async def cleanup_expired(self, *, retention_days: int = 30) -> int:
        cutoff = _now() - max(0, int(retention_days)) * 86400
        cur = await self._conn.execute(
            "DELETE FROM tasks WHERE created_at < ? "
            "AND status IN ('succeeded','failed','cancelled','timeout')",
            (cutoff,),
        )
        await self._conn.commit()
        return cur.rowcount

    async def find_pending_dashscope_ids(self) -> Iterable[tuple[str, str, str, str]]:
        """Return ``(task_id, dashscope_id, dashscope_endpoint, model_id)`` for in-flight tasks.

        Used by the on_load polling loop to resume tracking after restart.
        """
        async with self._conn.execute(
            """
            SELECT id, dashscope_id, dashscope_endpoint, model_id
            FROM tasks
            WHERE status IN ('pending','running') AND dashscope_id IS NOT NULL
            """
        ) as cur:
            rows = await cur.fetchall()
        return [
            (r["id"], r["dashscope_id"], r["dashscope_endpoint"] or "", r["model_id"] or "")
            for r in rows
        ]

    # ── Assets ─────────────────────────────────────────────────────────

    async def create_asset(self, **kwargs: Any) -> dict[str, Any]:
        asset_id = kwargs.get("id") or _new_id("ast")
        now = _now()
        record = {
            "id": asset_id,
            "task_id": kwargs.get("task_id"),
            "type": kwargs.get("type", "video"),
            "file_path": kwargs.get("file_path", ""),
            "role": kwargs.get("role", ""),
            "sort_order": int(kwargs.get("sort_order", 0)),
            "original_name": kwargs.get("original_name"),
            "size_bytes": kwargs.get("size_bytes"),
            "width": kwargs.get("width"),
            "height": kwargs.get("height"),
            "duration_sec": kwargs.get("duration_sec"),
            "validated_at": kwargs.get("validated_at"),
            "validation_result_json": (
                json.dumps(kwargs["validation_result"], ensure_ascii=False)
                if kwargs.get("validation_result") is not None
                else None
            ),
            "created_at": now,
        }
        cols = ", ".join(record.keys())
        placeholders = ", ".join(["?"] * len(record))
        await self._conn.execute(
            f"INSERT INTO assets ({cols}) VALUES ({placeholders})",
            tuple(record.values()),
        )
        await self._conn.commit()
        return record

    async def list_assets(
        self,
        *,
        asset_type: str | None = None,
        task_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        binds: list[Any] = []
        if asset_type:
            clauses.append("type = ?")
            binds.append(asset_type)
        if task_id:
            clauses.append("task_id = ?")
            binds.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self._conn.execute(
            f"SELECT COUNT(*) FROM assets {where}", tuple(binds)
        ) as cur:
            count_row = await cur.fetchone()
        total = int(count_row[0]) if count_row else 0
        async with self._conn.execute(
            f"SELECT * FROM assets {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*binds, max(1, min(200, int(limit))), max(0, int(offset))),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows], total

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        async with self._conn.execute(
            "SELECT * FROM assets WHERE id = ?", (asset_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_asset(self, asset_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Voices (cloned only — system voices live in code) ─────────────

    async def list_voices(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM voices WHERE is_system = 0 ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_voice(self, voice_id: str) -> dict[str, Any] | None:
        async with self._conn.execute(
            "SELECT * FROM voices WHERE id = ? LIMIT 1", (voice_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def create_custom_voice(
        self,
        *,
        label: str,
        source_audio_path: str,
        dashscope_voice_id: str,
        sample_url: str | None = None,
        language: str = "zh-CN",
        gender: str = "unknown",
    ) -> str:
        voice_id = _new_id("vc")
        await self._conn.execute(
            """
            INSERT INTO voices (
                id, label, sample_url, is_system, source_audio_path,
                dashscope_voice_id, language, gender, created_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                voice_id,
                label,
                sample_url,
                source_audio_path,
                dashscope_voice_id,
                language,
                gender,
                _now(),
            ),
        )
        await self._conn.commit()
        return voice_id

    async def delete_custom_voice(self, voice_id: str) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM voices WHERE id = ? AND is_system = 0", (voice_id,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def update_custom_voice_label(self, voice_id: str, label: str) -> bool:
        cur = await self._conn.execute(
            "UPDATE voices SET label = ? WHERE id = ? AND is_system = 0",
            (label, voice_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Figures ───────────────────────────────────────────────────────

    async def list_figures(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM figures ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def create_figure(
        self,
        *,
        label: str,
        image_path: str,
        preview_url: str,
        oss_url: str = "",
        oss_key: str = "",
        detect_pass: bool = False,
        detect_humanoid: bool = False,
        detect_message: str | None = None,
        detect_status: str = "pending",
    ) -> str:
        fig_id = _new_id("fig")
        await self._conn.execute(
            """
            INSERT INTO figures (
                id, label, image_path, preview_url, oss_url, oss_key,
                detect_pass, detect_humanoid, detect_message, detect_status,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fig_id,
                label,
                image_path,
                preview_url,
                oss_url,
                oss_key,
                1 if detect_pass else 0,
                1 if detect_humanoid else 0,
                detect_message,
                detect_status,
                _now(),
            ),
        )
        await self._conn.commit()
        return fig_id

    async def get_figure(self, fig_id: str) -> dict[str, Any] | None:
        async with self._conn.execute(
            "SELECT * FROM figures WHERE id = ?", (fig_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def update_figure_detect(
        self,
        fig_id: str,
        *,
        status: str,
        message: str | None = None,
        humanoid: bool | None = None,
    ) -> bool:
        if status not in {"pending", "pass", "fail", "skipped"}:
            raise ValueError(f"unknown detect_status {status!r}")
        msg = (message or "")[:500] or None
        sets = ["detect_status = ?", "detect_message = ?", "detect_pass = ?"]
        binds: list[Any] = [status, msg, 1 if status == "pass" else 0]
        if humanoid is not None:
            sets.append("detect_humanoid = ?")
            binds.append(1 if humanoid else 0)
        binds.append(fig_id)
        cur = await self._conn.execute(
            f"UPDATE figures SET {', '.join(sets)} WHERE id = ?", tuple(binds)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def list_pending_figures(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM figures WHERE detect_status = 'pending' ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_figure(self, fig_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM figures WHERE id = ?", (fig_id,))
        await self._conn.commit()
        return cur.rowcount > 0
