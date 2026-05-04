"""manga-studio task manager — pure ``aiosqlite`` CRUD across four tables.

Schema is intentionally split across four tables (no foreign keys; we keep
cross-table cleanup explicit) to model the four core entities of a manga
drama studio:

- ``characters`` — reusable character cards (name + appearance + reference
  images + default voice). The single most-shared entity across episodes
  and series; the reason this plugin exists.
- ``series``     — a multi-episode series binding a default visual style,
  aspect ratio, backend preference, and a default character lineup.
- ``episodes``   — one episode under a series (or standalone). Holds the
  generated script + storyboard JSON and the final video.
- ``tasks``      — one row per asynchronous generation job (an episode
  build, a panel re-roll, a backend smoke test). Status transitions are
  validated against ``_TASK_STATUSES``.

Pixelle anti-patterns avoided
-----------------------------
- C1 in-memory task store → SQLite WAL on disk.
- C7 implicit env-var paths → ``db_path`` is handed in by the plugin
  layer from ``api.get_data_dir()``; we never touch ENV.

The ``update_<table>_safe`` whitelist is the only path that mutates any
row; ``id`` / ``created_at`` are non-writable across all tables. This
mirrors the design of ``plugins/avatar-studio/avatar_task_manager.py``.
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
CREATE TABLE IF NOT EXISTS characters (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    role_type           TEXT NOT NULL DEFAULT 'main',
    gender              TEXT NOT NULL DEFAULT 'unknown',
    age_range           TEXT NOT NULL DEFAULT '',
    appearance_json     TEXT NOT NULL DEFAULT '{}',
    personality         TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    ref_images_json     TEXT NOT NULL DEFAULT '[]',
    default_voice_id    TEXT NOT NULL DEFAULT '',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    visual_style        TEXT NOT NULL DEFAULT 'shonen',
    ratio               TEXT NOT NULL DEFAULT '9:16',
    backend_pref        TEXT NOT NULL DEFAULT 'direct',
    default_characters_json TEXT NOT NULL DEFAULT '[]',
    cover_url           TEXT NOT NULL DEFAULT '',
    total_episodes      INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id                  TEXT PRIMARY KEY,
    series_id           TEXT,
    episode_no          INTEGER NOT NULL DEFAULT 1,
    title               TEXT NOT NULL DEFAULT '',
    story               TEXT NOT NULL DEFAULT '',
    script_json         TEXT NOT NULL DEFAULT '{}',
    storyboard_json     TEXT NOT NULL DEFAULT '[]',
    bound_characters_json TEXT NOT NULL DEFAULT '[]',
    final_video_path    TEXT,
    final_video_url     TEXT,
    cover_image_path    TEXT,
    duration_sec        REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    episode_id          TEXT,
    mode                TEXT NOT NULL,
    backend             TEXT NOT NULL DEFAULT 'direct',
    status              TEXT NOT NULL DEFAULT 'pending',
    current_step        TEXT NOT NULL DEFAULT 'setup',
    progress            INTEGER NOT NULL DEFAULT 0,
    params_json         TEXT NOT NULL DEFAULT '{}',
    cost_breakdown_json TEXT,
    error_kind          TEXT,
    error_message       TEXT,
    error_hints_json    TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_characters_role     ON characters(role_type);
CREATE INDEX IF NOT EXISTS idx_characters_created  ON characters(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_series_created      ON series(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_series     ON episodes(series_id, episode_no);
CREATE INDEX IF NOT EXISTS idx_episodes_created    ON episodes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status        ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_episode       ON tasks(episode_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created       ON tasks(created_at DESC);
"""


# ─── Whitelists ────────────────────────────────────────────────────────────
#
# Strict allow-lists — the only columns that ``update_<table>_safe`` will
# ever write. Anything outside the set raises ``ValueError`` with a clear
# message (Pixelle C6: loud failure > silent corruption). ``id`` and
# ``created_at`` are intentionally absent so they can never be mutated
# after row creation.

_TASK_WRITABLE: frozenset[str] = frozenset(
    {
        "status",
        "current_step",
        "progress",
        "cost_breakdown_json",
        "error_kind",
        "error_message",
        "error_hints_json",
        "completed_at",
    }
)

_EPISODE_WRITABLE: frozenset[str] = frozenset(
    {
        "title",
        "story",
        "script_json",
        "storyboard_json",
        "bound_characters_json",
        "final_video_path",
        "final_video_url",
        "cover_image_path",
        "duration_sec",
        "episode_no",
    }
)

_CHARACTER_WRITABLE: frozenset[str] = frozenset(
    {
        "name",
        "role_type",
        "gender",
        "age_range",
        "appearance_json",
        "personality",
        "description",
        "ref_images_json",
        "default_voice_id",
    }
)

_SERIES_WRITABLE: frozenset[str] = frozenset(
    {
        "title",
        "summary",
        "visual_style",
        "ratio",
        "backend_pref",
        "default_characters_json",
        "cover_url",
        "total_episodes",
    }
)


_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled"}
)

_CHARACTER_ROLES: frozenset[str] = frozenset({"main", "support", "narrator", "villain"})

_BACKENDS: frozenset[str] = frozenset({"direct", "runninghub", "comfyui_local"})


# ─── Helpers ──────────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_JSON_SUFFIXES: tuple[str, ...] = (
    "params_json",
    "cost_breakdown_json",
    "error_hints_json",
    "appearance_json",
    "ref_images_json",
    "default_characters_json",
    "script_json",
    "storyboard_json",
    "bound_characters_json",
)


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    """Inflate JSON columns next to their string source.

    For each ``foo_json`` column we leave the raw string in place and add a
    decoded ``foo`` key. Decoding errors are swallowed (we keep the raw
    string accessible so debug tools / migrations can still read it).
    """
    if row is None:
        return None
    out: dict[str, Any] = dict(row)
    for k in list(out):
        if k in _JSON_SUFFIXES and isinstance(out[k], str) and out[k]:
            try:
                out[k.removesuffix("_json")] = json.loads(out[k])
            except (ValueError, TypeError):
                pass
    return out


def _encode_json_columns(updates: dict[str, Any]) -> dict[str, Any]:
    """Auto-JSON-encode dict / list values whose key ends in ``_json``."""
    out: dict[str, Any] = {}
    for k, v in updates.items():
        if k.endswith("_json") and not isinstance(v, (str, type(None))):
            v = json.dumps(v, ensure_ascii=False)
        out[k] = v
    return out


# ─── Manager ──────────────────────────────────────────────────────────────


class MangaTaskManager:
    """SQLite-backed CRUD for characters / series / episodes / tasks.

    Lifecycle:

        tm = MangaTaskManager(db_path)
        async with tm:                # opens DB + creates schema
            cid = await tm.create_character(name="Aoi")

    Or call ``await tm.init()`` / ``await tm.close()`` manually.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def __aenter__(self) -> MangaTaskManager:
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
            raise RuntimeError("MangaTaskManager.init() must be called first")
        return self._db

    # ── Characters ────────────────────────────────────────────────────

    async def create_character(
        self,
        *,
        name: str,
        role_type: str = "main",
        gender: str = "unknown",
        age_range: str = "",
        appearance: dict[str, Any] | None = None,
        personality: str = "",
        description: str = "",
        ref_images: list[dict[str, Any]] | None = None,
        default_voice_id: str = "",
    ) -> str:
        if role_type not in _CHARACTER_ROLES:
            raise ValueError(
                f"invalid role_type {role_type!r}; allowed={sorted(_CHARACTER_ROLES)}",
            )
        char_id = _new_id("char")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO characters (
                id, name, role_type, gender, age_range,
                appearance_json, personality, description,
                ref_images_json, default_voice_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                char_id,
                name,
                role_type,
                gender,
                age_range,
                json.dumps(appearance or {}, ensure_ascii=False),
                personality,
                description,
                json.dumps(ref_images or [], ensure_ascii=False),
                default_voice_id,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return char_id

    async def get_character(self, char_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_characters(self, *, role_type: str | None = None) -> list[dict[str, Any]]:
        if role_type:
            sql = (
                "SELECT * FROM characters WHERE role_type = ? ORDER BY created_at DESC, ROWID DESC"
            )
            binds: tuple[Any, ...] = (role_type,)
        else:
            sql = "SELECT * FROM characters ORDER BY created_at DESC, ROWID DESC"
            binds = ()
        async with self._conn.execute(sql, binds) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_character_safe(self, char_id: str, /, **updates: Any) -> bool:
        """Update writable character columns. Same contract as ``update_task_safe``."""
        if not updates:
            return False
        bad = set(updates) - _CHARACTER_WRITABLE
        if bad:
            raise ValueError(
                f"non-writable column(s) for characters: {sorted(bad)}; "
                f"writable={sorted(_CHARACTER_WRITABLE)}",
            )
        if "role_type" in updates and updates["role_type"] not in _CHARACTER_ROLES:
            raise ValueError(
                f"invalid role_type {updates['role_type']!r}; allowed={sorted(_CHARACTER_ROLES)}",
            )
        return await self._do_update("characters", char_id, updates)

    async def delete_character(self, char_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM characters WHERE id = ?", (char_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Series ─────────────────────────────────────────────────────────

    async def create_series(
        self,
        *,
        title: str,
        summary: str = "",
        visual_style: str = "shonen",
        ratio: str = "9:16",
        backend_pref: str = "direct",
        default_characters: list[str] | None = None,
        cover_url: str = "",
    ) -> str:
        if backend_pref not in _BACKENDS:
            raise ValueError(
                f"invalid backend_pref {backend_pref!r}; allowed={sorted(_BACKENDS)}",
            )
        ser_id = _new_id("ser")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO series (
                id, title, summary, visual_style, ratio, backend_pref,
                default_characters_json, cover_url, total_episodes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                ser_id,
                title,
                summary,
                visual_style,
                ratio,
                backend_pref,
                json.dumps(default_characters or [], ensure_ascii=False),
                cover_url,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return ser_id

    async def get_series(self, ser_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM series WHERE id = ?", (ser_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_series(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self._conn.execute(
            """
            SELECT * FROM series
            ORDER BY created_at DESC, ROWID DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, min(500, limit)), max(0, offset)),
        ) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_series_safe(self, ser_id: str, /, **updates: Any) -> bool:
        if not updates:
            return False
        bad = set(updates) - _SERIES_WRITABLE
        if bad:
            raise ValueError(
                f"non-writable column(s) for series: {sorted(bad)}; "
                f"writable={sorted(_SERIES_WRITABLE)}",
            )
        if "backend_pref" in updates and updates["backend_pref"] not in _BACKENDS:
            raise ValueError(
                f"invalid backend_pref {updates['backend_pref']!r}; allowed={sorted(_BACKENDS)}",
            )
        return await self._do_update("series", ser_id, updates)

    async def delete_series(self, ser_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM series WHERE id = ?", (ser_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Episodes ──────────────────────────────────────────────────────

    async def create_episode(
        self,
        *,
        series_id: str | None = None,
        episode_no: int = 1,
        title: str = "",
        story: str = "",
        bound_characters: list[str] | None = None,
    ) -> str:
        ep_id = _new_id("ep")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO episodes (
                id, series_id, episode_no, title, story,
                bound_characters_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ep_id,
                series_id,
                episode_no,
                title,
                story,
                json.dumps(bound_characters or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        await self._conn.commit()
        return ep_id

    async def get_episode(self, ep_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM episodes WHERE id = ?", (ep_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_episodes(
        self,
        *,
        series_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if series_id:
            sql = (
                "SELECT * FROM episodes WHERE series_id = ? "
                "ORDER BY episode_no ASC, created_at DESC LIMIT ? OFFSET ?"
            )
            binds: tuple[Any, ...] = (
                series_id,
                max(1, min(500, limit)),
                max(0, offset),
            )
        else:
            sql = "SELECT * FROM episodes ORDER BY created_at DESC, ROWID DESC LIMIT ? OFFSET ?"
            binds = (max(1, min(500, limit)), max(0, offset))
        async with self._conn.execute(sql, binds) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_episode_safe(self, ep_id: str, /, **updates: Any) -> bool:
        if not updates:
            return False
        bad = set(updates) - _EPISODE_WRITABLE
        if bad:
            raise ValueError(
                f"non-writable column(s) for episodes: {sorted(bad)}; "
                f"writable={sorted(_EPISODE_WRITABLE)}",
            )
        return await self._do_update("episodes", ep_id, updates)

    async def delete_episode(self, ep_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM episodes WHERE id = ?", (ep_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Tasks ─────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        mode: str,
        backend: str = "direct",
        episode_id: str | None = None,
        params: dict[str, Any] | None = None,
        cost_breakdown: dict[str, Any] | None = None,
    ) -> str:
        if backend not in _BACKENDS:
            raise ValueError(
                f"invalid backend {backend!r}; allowed={sorted(_BACKENDS)}",
            )
        task_id = _new_id("task")
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO tasks (
                id, episode_id, mode, backend, status, current_step,
                progress, params_json, cost_breakdown_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 'setup', 0, ?, ?, ?, ?)
            """,
            (
                task_id,
                episode_id,
                mode,
                backend,
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(cost_breakdown, ensure_ascii=False) if cost_breakdown else None,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            return _row_to_dict(await cur.fetchone())

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        episode_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        binds: list[Any] = []
        if status:
            clauses.append("status = ?")
            binds.append(status)
        if episode_id:
            clauses.append("episode_id = ?")
            binds.append(episode_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks {where} ORDER BY created_at DESC, ROWID DESC LIMIT ? OFFSET ?"
        binds.extend([max(1, min(200, limit)), max(0, offset)])
        async with self._conn.execute(sql, tuple(binds)) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_task_safe(self, task_id: str, /, **updates: Any) -> bool:
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
                f"invalid status {updates['status']!r}; allowed={sorted(_TASK_STATUSES)}",
            )
        if "progress" in updates:
            try:
                p = int(updates["progress"])
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"progress must be an int 0-100, got {updates['progress']!r}"
                ) from e
            if not 0 <= p <= 100:
                raise ValueError(f"progress out of range: {p}")
            updates["progress"] = p
        return await self._do_update("tasks", task_id, updates)

    async def delete_task(self, task_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def find_pending_tasks(self) -> Iterable[dict[str, Any]]:
        """Return ``pending`` / ``running`` tasks for on_load resume."""
        async with self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('pending', 'running')
            ORDER BY created_at ASC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def cleanup_expired_tasks(self, *, retention_days: int = 30) -> int:
        """Delete tasks older than the retention window. Returns rows removed.

        Only terminal-state rows (succeeded / failed / cancelled) are
        removed. Pending / running tasks are never touched here — that's
        what cancellation is for.
        """
        cutoff = _now() - max(0, retention_days) * 86400
        cur = await self._conn.execute(
            """
            DELETE FROM tasks
            WHERE created_at < ?
              AND status IN ('succeeded','failed','cancelled')
            """,
            (cutoff,),
        )
        await self._conn.commit()
        return cur.rowcount

    # ── Bulk helpers ──────────────────────────────────────────────────

    async def count(self, table: str = "tasks", *, status: str | None = None) -> int:
        if table not in {"characters", "series", "episodes", "tasks"}:
            raise ValueError(f"unknown table {table!r}")
        if status and table != "tasks":
            raise ValueError("status filter only applies to tasks")
        if status:
            sql = "SELECT COUNT(*) FROM tasks WHERE status = ?"
            binds: tuple[Any, ...] = (status,)
        else:
            sql = f"SELECT COUNT(*) FROM {table}"
            binds = ()
        async with self._conn.execute(sql, binds) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ── Internal: shared "safe update" SQL builder ────────────────────

    async def _do_update(self, table: str, row_id: str, updates: dict[str, Any]) -> bool:
        """Build & execute the ``UPDATE`` for any table, with auto JSON
        encoding and an automatic ``updated_at`` bump.

        Tasks have no ``updated_at`` bump-on-write (we keep them mostly
        immutable post-insert). characters / series / episodes do.
        """
        encoded = _encode_json_columns(updates)
        cols = list(encoded)
        binds: list[Any] = [encoded[c] for c in cols]
        bump_updated = table != "tasks"
        if bump_updated:
            cols_sql = ", ".join(f"{c} = ?" for c in cols)
            cols_sql += ", updated_at = ?"
            binds.append(_now())
        else:
            cols_sql = ", ".join(f"{c} = ?" for c in cols)
            # tasks table also tracks updated_at; bump it too. We bump for
            # all four tables — the only reason this branch exists is to
            # make the column ordering explicit and tests easy to read.
            cols_sql += ", updated_at = ?"
            binds.append(_now())
        binds.append(row_id)
        sql = f"UPDATE {table} SET {cols_sql} WHERE id = ?"
        cursor = await self._conn.execute(sql, tuple(binds))
        await self._conn.commit()
        return cursor.rowcount > 0
