"""SQLite-backed task / per-mode result / assets_bus / config manager for media-post.

Schema is the verbatim 6-table layout from ``docs/media-post-plan.md``
§8 (Phase 1 DoD). Modeled after ``plugins/subtitle-craft/subtitle_task_manager.py``
with the per-mode result tables added (``cover_results`` / ``recompose_outputs`` /
``seo_results`` / ``chapter_cards_results``).

Forward-compatibility carve-outs:

- ``tasks.origin_plugin_id`` / ``tasks.origin_task_id`` — DEFAULT NULL columns
  reserved for cross-plugin Handoff. **v1.0 always writes NULL**; v2.0 will
  populate them when accepting a Handoff payload from another plugin.
- ``assets_bus`` table — full schema reserved for v2.0 cross-plugin asset
  cache. **v1.0 creates the table on init but the pipeline never INSERTs
  any row.** The Phase 1 ``test_assets_bus_count_zero`` red-line guard
  asserts ``COUNT(*) == 0`` after every test.
- ``_UPDATABLE_COLUMNS["assets_bus"]`` is intentionally empty in v1.0 —
  any v2.0 plugin attempting to UPDATE rows must first re-add columns
  here. This makes write attempts crash loudly instead of silently
  modifying schema reserved tables.

These additions are explicitly called out in
``docs/post-production-plugins-roadmap.md`` §3.3 and protected by the
Phase 0 ``test_no_handoff_*`` red-line guards (no Python module / route
/ tool may reference Handoff in v1.0).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Default config (Settings tab seed values per §9.4).
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, str] = {
    "dashscope_api_key": "",
    "dashscope_base_url": "https://dashscope.aliyuncs.com",
    "ffmpeg_path": "",
    "vlm_model": "qwen-vl-max",
    "qwen_plus_model": "qwen-plus",
    # VLM tuning (frozen defaults per red-line §13 #6/#7/#8).
    "vlm_batch_size": "8",
    "vlm_concurrency": "4",
    "recompose_fps": "2.0",
    "scene_threshold": "0.4",
    "ema_alpha": "0.15",
    # Cost guardrails (UI ApprovalRequired modal trigger).
    "cost_warn_threshold_cny": "10.0",
    "cost_danger_threshold_cny": "30.0",
    "multi_aspect_max_minutes": "30",
    # Chapter cards (A path = Playwright; B path = ffmpeg drawtext fallback).
    "chapter_template_default": "modern",
    "playwright_enabled": "true",
    # Polling / SSE.
    "poll_interval_sec": "3",
}


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ---------------------------------------------------------------------------
# Public update whitelists.
#
# Mirrors ``docs/media-post-plan.md`` §8. Any attempt to update a column
# outside these sets raises ``ValueError`` — the C6-reverse-example guard
# ("silent param ignore") for SQL writes.
# ---------------------------------------------------------------------------

_UPDATABLE_COLUMNS: dict[str, frozenset[str]] = {
    "tasks": frozenset(
        {
            "status",
            "progress",
            "params_json",
            "cost_estimated",
            "cost_actual",
            "cost_kind",
            "video_path",
            "video_meta_json",
            "pipeline_step",
            "result_summary_json",
            "error_kind",
            "error_message",
            "error_hints_json",
            "completed_at",
            "updated_at",
        }
    ),
    "cover_results": frozenset({"thumbnail_path", "extra_meta_json"}),
    "recompose_outputs": frozenset(
        {"output_path", "trajectory_json", "duration_sec", "extra_meta_json"}
    ),
    "seo_results": frozenset({"payload_json", "extra_meta_json"}),
    "chapter_cards_results": frozenset({"png_path", "extra_meta_json"}),
    # v1.0: assets_bus is NEVER written by the pipeline; declared empty so
    # v2.0 plugins must re-open this whitelist explicitly before writing.
    "assets_bus": frozenset(),
}

_TASK_COLUMN_ALIASES: dict[str, str] = {
    "params": "params_json",
    "video_meta": "video_meta_json",
    "result_summary": "result_summary_json",
    "error_hints": "error_hints_json",
}

_TASK_JSON_KEYS: frozenset[str] = frozenset(
    {"params", "video_meta", "result_summary", "error_hints"}
)


class MediaPostTaskManager:
    """Async SQLite manager for media-post (6-table schema).

    Tables (all created lazily on ``init``):

    1. ``tasks`` — main task row (one per UI submission).
    2. ``cover_results`` — N rows per ``cover_pick`` task (one per candidate).
    3. ``recompose_outputs`` — N rows per ``multi_aspect`` task (one per aspect).
    4. ``seo_results`` — N rows per ``seo_pack`` task (one per platform).
    5. ``chapter_cards_results`` — N rows per ``chapter_cards`` task.
    6. ``assets_bus`` — v2.0-reserved cross-plugin asset registry.
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
            -- tasks (main row, one per UI submission)
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                mode TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                video_path TEXT,
                video_meta_json TEXT,
                params_json TEXT,
                cost_estimated REAL NOT NULL DEFAULT 0,
                cost_actual REAL NOT NULL DEFAULT 0,
                cost_kind TEXT NOT NULL DEFAULT 'ok',
                pipeline_step TEXT,
                result_summary_json TEXT,
                error_kind TEXT,
                error_message TEXT,
                error_hints_json TEXT,
                -- v1.0 always NULL; v2.0 cross-plugin Handoff enables.
                origin_plugin_id TEXT DEFAULT NULL,
                origin_task_id TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_mode ON tasks(mode);
            CREATE INDEX IF NOT EXISTS idx_tasks_origin ON tasks(origin_plugin_id);

            -- cover_results (one row per candidate cover; cover_pick mode)
            CREATE TABLE IF NOT EXISTS cover_results (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                cover_path TEXT NOT NULL,
                thumbnail_path TEXT,
                overall_score REAL,
                lighting REAL,
                composition REAL,
                subject_clarity REAL,
                visual_appeal REAL,
                text_safe_zone REAL,
                main_subject_bbox_json TEXT,
                best_for TEXT,
                reason TEXT,
                extra_meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_cover_task ON cover_results(task_id);

            -- recompose_outputs (one row per target aspect; multi_aspect mode)
            CREATE TABLE IF NOT EXISTS recompose_outputs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                aspect TEXT NOT NULL,
                output_path TEXT NOT NULL,
                output_w INTEGER NOT NULL,
                output_h INTEGER NOT NULL,
                duration_sec REAL,
                trajectory_json TEXT,
                ema_alpha_used REAL,
                fps_used REAL,
                scene_cut_count INTEGER NOT NULL DEFAULT 0,
                fallback_letterbox_used INTEGER NOT NULL DEFAULT 0,
                extra_meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_recompose_task ON recompose_outputs(task_id);

            -- seo_results (one row per platform; seo_pack mode)
            CREATE TABLE IF NOT EXISTS seo_results (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                extra_meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_seo_task ON seo_results(task_id);

            -- chapter_cards_results (one row per chapter; chapter_cards mode)
            CREATE TABLE IF NOT EXISTS chapter_cards_results (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                template_id TEXT NOT NULL,
                png_path TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                render_path TEXT NOT NULL DEFAULT 'playwright',
                extra_meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chapter_task ON chapter_cards_results(task_id);

            -- assets_bus (cross-plugin asset registry — v2.0 only)
            -- v1.0: table exists for forward-compat; pipeline NEVER writes here.
            CREATE TABLE IF NOT EXISTS assets_bus (
                asset_id TEXT PRIMARY KEY,
                origin_plugin_id TEXT NOT NULL,
                origin_task_id TEXT NOT NULL,
                asset_kind TEXT NOT NULL,
                asset_uri TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assets_origin
                ON assets_bus(origin_plugin_id, origin_task_id);
            CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets_bus(asset_kind);

            -- config (key/value)
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
        video_path: str = "",
        params: dict[str, Any] | None = None,
        cost_estimated: float = 0.0,
        cost_kind: str = "ok",
        status: str = "pending",
    ) -> dict[str, Any]:
        assert self._db
        task_id = _short_id()
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO tasks
               (id, status, mode, progress, video_path, params_json,
                cost_estimated, cost_actual, cost_kind, created_at, updated_at)
               VALUES (?, ?, ?, 0, ?, ?, ?, 0, ?, ?, ?)""",
            (
                task_id,
                status,
                mode,
                video_path,
                json.dumps(params or {}, ensure_ascii=False),
                cost_estimated,
                cost_kind,
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

        Use ``update_task_safe`` from pipeline error handlers when an extra
        key in the payload should not mask the original error.
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
        cur = await self._db.execute(
            "SELECT * FROM tasks WHERE status IN ('pending', 'running')"
        )
        rows = await cur.fetchall()
        return [self._task_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cooperative cancel (in-memory flag — pipeline polls between steps)
    # ------------------------------------------------------------------

    def request_cancel(self, task_id: str) -> None:
        self._canceled.add(task_id)

    def is_canceled(self, task_id: str) -> bool:
        return task_id in self._canceled

    def clear_cancel(self, task_id: str) -> None:
        self._canceled.discard(task_id)

    # ------------------------------------------------------------------
    # Per-mode result table writers (one method per mode for explicit shape).
    # All writers serialize JSON columns inline so callers pass plain dicts.
    # ------------------------------------------------------------------

    async def insert_cover_result(
        self,
        *,
        task_id: str,
        rank: int,
        cover_path: str,
        thumbnail_path: str = "",
        overall_score: float | None = None,
        lighting: float | None = None,
        composition: float | None = None,
        subject_clarity: float | None = None,
        visual_appeal: float | None = None,
        text_safe_zone: float | None = None,
        main_subject_bbox: dict[str, Any] | None = None,
        best_for: str = "",
        reason: str = "",
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        assert self._db
        rid = _short_id()
        await self._db.execute(
            """INSERT INTO cover_results
               (id, task_id, rank, cover_path, thumbnail_path, overall_score,
                lighting, composition, subject_clarity, visual_appeal,
                text_safe_zone, main_subject_bbox_json, best_for, reason,
                extra_meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                task_id,
                rank,
                cover_path,
                thumbnail_path,
                overall_score,
                lighting,
                composition,
                subject_clarity,
                visual_appeal,
                text_safe_zone,
                json.dumps(main_subject_bbox) if main_subject_bbox else None,
                best_for,
                reason,
                json.dumps(extra_meta) if extra_meta else None,
                _now_iso(),
            ),
        )
        await self._db.commit()
        return rid

    async def list_cover_results(self, task_id: str) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM cover_results WHERE task_id = ? ORDER BY rank ASC",
            (task_id,),
        )
        rows = await cur.fetchall()
        return [self._cover_row_to_dict(r) for r in rows]

    async def insert_recompose_output(
        self,
        *,
        task_id: str,
        aspect: str,
        output_path: str,
        output_w: int,
        output_h: int,
        duration_sec: float | None = None,
        trajectory: list[Any] | None = None,
        ema_alpha_used: float | None = None,
        fps_used: float | None = None,
        scene_cut_count: int = 0,
        fallback_letterbox_used: bool = False,
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        assert self._db
        rid = _short_id()
        await self._db.execute(
            """INSERT INTO recompose_outputs
               (id, task_id, aspect, output_path, output_w, output_h,
                duration_sec, trajectory_json, ema_alpha_used, fps_used,
                scene_cut_count, fallback_letterbox_used, extra_meta_json,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                task_id,
                aspect,
                output_path,
                output_w,
                output_h,
                duration_sec,
                json.dumps(trajectory) if trajectory is not None else None,
                ema_alpha_used,
                fps_used,
                scene_cut_count,
                1 if fallback_letterbox_used else 0,
                json.dumps(extra_meta) if extra_meta else None,
                _now_iso(),
            ),
        )
        await self._db.commit()
        return rid

    async def list_recompose_outputs(self, task_id: str) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM recompose_outputs WHERE task_id = ? ORDER BY aspect ASC",
            (task_id,),
        )
        rows = await cur.fetchall()
        return [self._recompose_row_to_dict(r) for r in rows]

    async def insert_seo_result(
        self,
        *,
        task_id: str,
        platform: str,
        payload: dict[str, Any],
        tokens_used: int = 0,
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        assert self._db
        rid = _short_id()
        await self._db.execute(
            """INSERT INTO seo_results
               (id, task_id, platform, payload_json, tokens_used,
                extra_meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                task_id,
                platform,
                json.dumps(payload, ensure_ascii=False),
                tokens_used,
                json.dumps(extra_meta) if extra_meta else None,
                _now_iso(),
            ),
        )
        await self._db.commit()
        return rid

    async def list_seo_results(self, task_id: str) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM seo_results WHERE task_id = ? ORDER BY platform ASC",
            (task_id,),
        )
        rows = await cur.fetchall()
        return [self._seo_row_to_dict(r) for r in rows]

    async def insert_chapter_card_result(
        self,
        *,
        task_id: str,
        chapter_index: int,
        title: str,
        subtitle: str = "",
        template_id: str = "modern",
        png_path: str = "",
        width: int = 1280,
        height: int = 720,
        render_path: str = "playwright",
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        assert self._db
        rid = _short_id()
        await self._db.execute(
            """INSERT INTO chapter_cards_results
               (id, task_id, chapter_index, title, subtitle, template_id,
                png_path, width, height, render_path, extra_meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                task_id,
                chapter_index,
                title,
                subtitle,
                template_id,
                png_path,
                width,
                height,
                render_path,
                json.dumps(extra_meta) if extra_meta else None,
                _now_iso(),
            ),
        )
        await self._db.commit()
        return rid

    async def list_chapter_card_results(self, task_id: str) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM chapter_cards_results WHERE task_id = ? "
            "ORDER BY chapter_index ASC",
            (task_id,),
        )
        rows = await cur.fetchall()
        return [self._chapter_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # assets_bus reads (v1.0 read-only; rows only ever come from v2.0 code)
    # ------------------------------------------------------------------

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM assets_bus WHERE asset_id = ?", (asset_id,)
        )
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
        for jf in ("params_json", "video_meta_json", "result_summary_json", "error_hints_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        return d

    @staticmethod
    def _cover_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("main_subject_bbox_json", "extra_meta_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        return d

    @staticmethod
    def _recompose_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("trajectory_json", "extra_meta_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        d["fallback_letterbox_used"] = bool(d.get("fallback_letterbox_used"))
        return d

    @staticmethod
    def _seo_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        for jf in ("payload_json", "extra_meta_json"):
            val = d.pop(jf, None)
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(val) if val else None
            except (json.JSONDecodeError, TypeError):
                d[key] = None
        return d

    @staticmethod
    def _chapter_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        val = d.pop("extra_meta_json", None)
        try:
            d["extra_meta"] = json.loads(val) if val else None
        except (json.JSONDecodeError, TypeError):
            d["extra_meta"] = None
        return d

    @staticmethod
    def _asset_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        val = d.pop("meta_json", None)
        try:
            d["meta"] = json.loads(val) if val else None
        except (json.JSONDecodeError, TypeError):
            d["meta"] = None
        return d


__all__ = [
    "DEFAULT_CONFIG",
    "MediaPostTaskManager",
]
