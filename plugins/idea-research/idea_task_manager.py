"""SQLite-backed task manager for idea-research.

Implements §8 (5 business tables + 2 system tables), the
``update_task_safe`` whitelist, persona seeding from §13.1.A and the
helper CRUDs used by the pipeline / collectors / MDRM adapter.

All public coroutines run the actual ``sqlite3`` work in a thread via
``asyncio.to_thread`` so the host event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:  # PERSONAS seeding is optional during early phases
    from idea_models import PERSONAS as _BUILTIN_PERSONAS
except Exception:  # pragma: no cover — defensive
    _BUILTIN_PERSONAS = []  # type: ignore[assignment]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error_kind TEXT,
    error_message TEXT,
    error_hint_zh TEXT,
    error_hint_en TEXT,
    progress_pct INTEGER DEFAULT 0,
    current_step TEXT,
    cost_cny REAL DEFAULT 0,
    handoff_target TEXT,
    origin_plugin_id TEXT,
    origin_task_id TEXT,
    mdrm_writes_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_mode_created
    ON tasks(mode, created_at DESC);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platforms TEXT NOT NULL,
    keywords TEXT NOT NULL,
    time_window TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    refresh_interval_min INTEGER DEFAULT 60,
    last_run_at INTEGER,
    last_task_id TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_items (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    author_url TEXT,
    cover_url TEXT,
    duration_seconds INTEGER,
    description TEXT,
    like_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    view_count INTEGER,
    publish_at INTEGER,
    fetched_at INTEGER NOT NULL,
    engine_used TEXT,
    collector_name TEXT,
    raw_payload_json TEXT,
    score REAL DEFAULT 0,
    keywords_matched TEXT,
    hook_type_guess TEXT,
    data_quality TEXT DEFAULT 'high',
    saved INTEGER DEFAULT 0,
    mdrm_hits TEXT,
    UNIQUE(platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_trend_items_score
    ON trend_items(score DESC, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_trend_items_saved
    ON trend_items(saved, fetched_at DESC);

CREATE TABLE IF NOT EXISTS personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    is_builtin INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hook_library (
    id TEXT PRIMARY KEY,
    hook_type TEXT NOT NULL,
    hook_text TEXT NOT NULL,
    persona TEXT,
    platform TEXT,
    score REAL,
    brand_keywords TEXT,
    source_task_id TEXT,
    written_to_vector INTEGER DEFAULT 0,
    written_to_memory INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hook_library_type
    ON hook_library(hook_type, score DESC);

CREATE TABLE IF NOT EXISTS cookies (
    platform TEXT PRIMARY KEY,
    encrypted BLOB NOT NULL,
    expires_at INTEGER,
    updated_at INTEGER NOT NULL,
    last_test_at INTEGER,
    last_test_ok INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


# Strict whitelist of columns ``update_task_safe`` may mutate; anything
# else gets silently dropped (after a debug-level log).
TASK_UPDATE_WHITELIST: frozenset[str] = frozenset(
    {
        "status",
        "progress_pct",
        "current_step",
        "output_json",
        "error_kind",
        "error_message",
        "error_hint_zh",
        "error_hint_en",
        "started_at",
        "finished_at",
        "cost_cny",
        "handoff_target",
        "mdrm_writes_json",
    }
)

VALID_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "done", "failed", "canceled"}
)

VALID_MODES: frozenset[str] = frozenset(
    {"radar_pull", "breakdown_url", "compare_accounts", "script_remix"}
)


def _now() -> int:
    return int(time.time())


def _row_to_dict(
    row: sqlite3.Row | None, *, json_cols: Iterable[str] = ()
) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {k: row[k] for k in row.keys()}  # noqa: SIM118
    for col in json_cols:
        raw = out.get(col)
        if raw is None or raw == "":
            continue
        try:
            out[col] = json.loads(raw)
        except (TypeError, ValueError):
            pass
    return out


class IdeaTaskManager:
    """SQLite façade for tasks / trend_items / personas / hooks / cookies."""

    JSON_TASK_COLS: tuple[str, ...] = (
        "input_json",
        "output_json",
        "mdrm_writes_json",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        # ``asyncio.Lock`` binds to the running loop on first use, which
        # breaks when fixtures spin up TestClient (anyio creates a fresh
        # loop per request). Keep one lock *per* loop instead.
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ---- low-level helpers -------------------------------------------------

    def _connect_sync(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect_sync()
        try:
            yield conn
        finally:
            conn.close()

    def _lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = id(loop)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _run(self, fn, *args, **kwargs):
        async with self._lock_for_loop():
            return await asyncio.to_thread(fn, *args, **kwargs)

    # ---- lifecycle ---------------------------------------------------------

    async def init(self) -> None:
        await self._run(self._init_sync)

    def _init_sync(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            self._seed_personas_sync(conn)

    def _seed_personas_sync(self, conn: sqlite3.Connection) -> None:
        if not _BUILTIN_PERSONAS:
            return
        existing = {
            row["id"] for row in conn.execute("SELECT id FROM personas WHERE is_builtin = 1")
        }
        now = _now()
        rows = [
            (
                p.id,
                p.name,
                p.description,
                p.system_prompt,
                1,
                now,
            )
            for p in _BUILTIN_PERSONAS
            if p.id not in existing
        ]
        if rows:
            conn.executemany(
                "INSERT INTO personas (id, name, description, system_prompt,"
                " is_builtin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    async def close(self) -> None:
        # No persistent connection is held; method exists so the plugin
        # lifecycle can call ``await tm.close()`` symmetrically with
        # ``init()``.
        return None

    # ---- tasks -------------------------------------------------------------

    async def insert_task(
        self,
        *,
        mode: str,
        input_payload: dict[str, Any],
        task_id: str | None = None,
        origin_plugin_id: str | None = None,
        origin_task_id: str | None = None,
    ) -> str:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown mode {mode!r}; valid: {sorted(VALID_MODES)}")
        tid = task_id or str(uuid.uuid4())
        now = _now()
        await self._run(
            self._insert_task_sync,
            tid,
            mode,
            json.dumps(input_payload, ensure_ascii=False),
            now,
            origin_plugin_id,
            origin_task_id,
        )
        return tid

    def _insert_task_sync(
        self,
        tid: str,
        mode: str,
        input_json_str: str,
        now: int,
        origin_plugin_id: str | None,
        origin_task_id: str | None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, mode, status, created_at, updated_at,"
                " input_json, origin_plugin_id, origin_task_id)"
                " VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
                (
                    tid,
                    mode,
                    now,
                    now,
                    input_json_str,
                    origin_plugin_id,
                    origin_task_id,
                ),
            )

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_task_sync, task_id)

    def _get_task_sync(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return _row_to_dict(row, json_cols=self.JSON_TASK_COLS)

    async def list_tasks(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await self._run(
            self._list_tasks_sync, mode, status, max(1, int(limit)), max(0, int(offset))
        )

    def _list_tasks_sync(
        self,
        mode: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM tasks {where}", params).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "tasks": [_row_to_dict(r, json_cols=self.JSON_TASK_COLS) for r in rows],
            "total": int(total),
        }

    async def update_task_safe(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in updates.items() if k in TASK_UPDATE_WHITELIST}
        ignored = sorted(set(updates) - set(clean))
        if "status" in clean and clean["status"] not in VALID_TASK_STATUSES:
            raise ValueError(
                f"Invalid status {clean['status']!r}; valid: {sorted(VALID_TASK_STATUSES)}"
            )
        if not clean:
            return {"updated": 0, "ignored": ignored}
        applied = await self._run(self._update_task_sync, task_id, clean)
        return {"updated": applied, "ignored": ignored}

    def _update_task_sync(self, task_id: str, clean: dict[str, Any]) -> int:
        sets = ", ".join(f"{k} = ?" for k in clean)
        params = [*clean.values(), _now(), task_id]
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
                params,
            )
            return int(cur.rowcount)

    async def delete_task(self, task_id: str) -> int:
        return await self._run(self._delete_task_sync, task_id)

    def _delete_task_sync(self, task_id: str) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return int(cur.rowcount)

    # ---- subscriptions -----------------------------------------------------

    async def upsert_subscription(self, sub: dict[str, Any]) -> str:
        return await self._run(self._upsert_subscription_sync, sub)

    def _upsert_subscription_sync(self, sub: dict[str, Any]) -> str:
        sid = str(sub.get("id") or uuid.uuid4())
        now = _now()
        platforms = json.dumps(sub.get("platforms", []), ensure_ascii=False)
        keywords = json.dumps(sub.get("keywords", []), ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO subscriptions (id, name, platforms, keywords,"
                " time_window, enabled, refresh_interval_min,"
                " last_run_at, last_task_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                "   name=excluded.name, platforms=excluded.platforms,"
                "   keywords=excluded.keywords,"
                "   time_window=excluded.time_window,"
                "   enabled=excluded.enabled,"
                "   refresh_interval_min=excluded.refresh_interval_min",
                (
                    sid,
                    sub.get("name", sid),
                    platforms,
                    keywords,
                    sub.get("time_window", "24h"),
                    1 if sub.get("enabled", True) else 0,
                    int(sub.get("refresh_interval_min", 60)),
                    sub.get("last_run_at"),
                    sub.get("last_task_id"),
                    now,
                ),
            )
        return sid

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        return await self._run(self._list_subscriptions_sync)

    def _list_subscriptions_sync(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM subscriptions ORDER BY created_at DESC").fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r) or {}
            for col in ("platforms", "keywords"):
                if isinstance(d.get(col), str):
                    try:
                        d[col] = json.loads(d[col])
                    except (TypeError, ValueError):
                        d[col] = []
            out.append(d)
        return out

    async def delete_subscription(self, sub_id: str) -> int:
        return await self._run(self._delete_subscription_sync, sub_id)

    def _delete_subscription_sync(self, sub_id: str) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            return int(cur.rowcount)

    # ---- trend_items -------------------------------------------------------

    async def upsert_trend_item(self, item: dict[str, Any]) -> None:
        await self._run(self._upsert_trend_item_sync, item)

    def _upsert_trend_item_sync(self, item: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trend_items (id, platform, external_id,"
                " external_url, title, author, author_url, cover_url,"
                " duration_seconds, description, like_count, comment_count,"
                " share_count, view_count, publish_at, fetched_at,"
                " engine_used, collector_name, raw_payload_json, score,"
                " keywords_matched, hook_type_guess, data_quality, saved,"
                " mdrm_hits)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(platform, external_id) DO UPDATE SET"
                "   title=excluded.title, score=excluded.score,"
                "   like_count=excluded.like_count,"
                "   comment_count=excluded.comment_count,"
                "   share_count=excluded.share_count,"
                "   view_count=excluded.view_count,"
                "   fetched_at=excluded.fetched_at,"
                "   raw_payload_json=excluded.raw_payload_json,"
                "   keywords_matched=excluded.keywords_matched,"
                "   mdrm_hits=excluded.mdrm_hits",
                (
                    item["id"],
                    item["platform"],
                    item["external_id"],
                    item["external_url"],
                    item.get("title"),
                    item.get("author"),
                    item.get("author_url"),
                    item.get("cover_url"),
                    item.get("duration_seconds"),
                    item.get("description"),
                    item.get("like_count"),
                    item.get("comment_count"),
                    item.get("share_count"),
                    item.get("view_count"),
                    int(item.get("publish_at") or 0),
                    int(item.get("fetched_at") or _now()),
                    item.get("engine_used", "a"),
                    item.get("collector_name"),
                    json.dumps(item.get("raw_payload", {}), ensure_ascii=False),
                    float(item.get("score") or 0.0),
                    json.dumps(item.get("keywords_matched", []), ensure_ascii=False),
                    item.get("hook_type_guess"),
                    item.get("data_quality", "high"),
                    1 if item.get("saved") else 0,
                    json.dumps(item.get("mdrm_hits", []), ensure_ascii=False),
                ),
            )

    async def list_trend_items(
        self,
        *,
        platforms: list[str] | None = None,
        limit: int = 20,
        sort: str = "score",
        only_saved: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._run(
            self._list_trend_items_sync,
            platforms,
            max(1, int(limit)),
            sort,
            only_saved,
        )

    def _list_trend_items_sync(
        self,
        platforms: list[str] | None,
        limit: int,
        sort: str,
        only_saved: bool,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if platforms:
            clauses.append("platform IN (" + ",".join(["?"] * len(platforms)) + ")")
            params.extend(platforms)
        if only_saved:
            clauses.append("saved = 1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "score DESC, fetched_at DESC" if sort == "score" else "fetched_at DESC"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM trend_items {where} ORDER BY {order} LIMIT ?",
                [*params, limit],
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r) or {}
            for col in ("keywords_matched", "mdrm_hits"):
                v = d.get(col)
                if isinstance(v, str):
                    try:
                        d[col] = json.loads(v)
                    except (TypeError, ValueError):
                        d[col] = []
            out.append(d)
        return out

    async def mark_item_saved(self, item_id: str, saved: bool = True) -> int:
        return await self._run(self._mark_item_saved_sync, item_id, saved)

    def _mark_item_saved_sync(self, item_id: str, saved: bool) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE trend_items SET saved = ? WHERE id = ?",
                (1 if saved else 0, item_id),
            )
            return int(cur.rowcount)

    # ---- personas ----------------------------------------------------------

    async def list_personas(self) -> list[dict[str, Any]]:
        return await self._run(self._list_personas_sync)

    def _list_personas_sync(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM personas ORDER BY is_builtin DESC, name ASC"
            ).fetchall()
        return [_row_to_dict(r) or {} for r in rows]

    # ---- hook_library (MDRM mirror) ---------------------------------------

    async def insert_hook_library(
        self,
        record: dict[str, Any],
        *,
        write_result: dict[str, str] | None = None,
    ) -> str:
        return await self._run(self._insert_hook_library_sync, record, write_result or {})

    def _insert_hook_library_sync(
        self,
        record: dict[str, Any],
        write_result: dict[str, str],
    ) -> str:
        rid = str(record.get("id") or uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hook_library (id, hook_type,"
                " hook_text, persona, platform, score, brand_keywords,"
                " source_task_id, written_to_vector, written_to_memory,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    record.get("hook_type", ""),
                    record.get("hook_text", ""),
                    record.get("persona"),
                    record.get("platform", "other"),
                    float(record.get("score") or 0.0),
                    json.dumps(record.get("brand_keywords", []), ensure_ascii=False),
                    record.get("source_task_id"),
                    1 if write_result.get("vector") == "ok" else 0,
                    1 if write_result.get("memory") == "ok" else 0,
                    _now(),
                ),
            )
        return rid

    async def get_hook_library_count(self) -> int:
        return await self._run(self._hook_count_sync)

    def _hook_count_sync(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM hook_library").fetchone()
        return int(row["c"]) if row else 0

    async def clear_hook_library(self) -> int:
        return await self._run(self._clear_hook_library_sync)

    def _clear_hook_library_sync(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM hook_library")
            return int(cur.rowcount)

    # ---- cookies (encrypted bytes) ----------------------------------------

    async def save_cookies(
        self,
        platform: str,
        encrypted: bytes,
        *,
        expires_at: int | None = None,
    ) -> None:
        await self._run(self._save_cookies_sync, platform, bytes(encrypted), expires_at)

    def _save_cookies_sync(
        self,
        platform: str,
        encrypted: bytes,
        expires_at: int | None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cookies (platform, encrypted, expires_at,"
                " updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(platform) DO UPDATE SET"
                "   encrypted=excluded.encrypted,"
                "   expires_at=excluded.expires_at,"
                "   updated_at=excluded.updated_at",
                (platform, encrypted, expires_at, _now()),
            )

    async def get_cookies(self, platform: str) -> dict[str, Any] | None:
        return await self._run(self._get_cookies_sync, platform)

    def _get_cookies_sync(self, platform: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM cookies WHERE platform = ?", (platform,)).fetchone()
            return _row_to_dict(row)

    async def list_cookies_status(self) -> list[dict[str, Any]]:
        return await self._run(self._list_cookies_status_sync)

    def _list_cookies_status_sync(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT platform, expires_at, updated_at, last_test_at,"
                " last_test_ok FROM cookies ORDER BY platform"
            ).fetchall()
        return [_row_to_dict(r) or {} for r in rows]

    async def update_cookies_test(self, platform: str, *, ok: bool) -> int:
        return await self._run(self._update_cookies_test_sync, platform, bool(ok))

    def _update_cookies_test_sync(self, platform: str, ok: bool) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE cookies SET last_test_at = ?, last_test_ok = ? WHERE platform = ?",
                (_now(), 1 if ok else 0, platform),
            )
            return int(cur.rowcount)

    # ---- settings ----------------------------------------------------------

    async def get_setting(self, key: str, default: Any = None) -> Any:
        return await self._run(self._get_setting_sync, key, default)

    def _get_setting_sync(self, key: str, default: Any) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except (TypeError, ValueError):
                return default

    async def set_setting(self, key: str, value: Any) -> None:
        await self._run(self._set_setting_sync, key, value)

    def _set_setting_sync(self, key: str, value: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                " value=excluded.value, updated_at=excluded.updated_at",
                (
                    key,
                    json.dumps(value, ensure_ascii=False),
                    _now(),
                ),
            )

    async def get_all_settings(self) -> dict[str, Any]:
        return await self._run(self._get_all_settings_sync)

    def _get_all_settings_sync(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                out[r["key"]] = r["value"]
        return out


__all__ = [
    "IdeaTaskManager",
    "SCHEMA_SQL",
    "TASK_UPDATE_WHITELIST",
    "VALID_MODES",
    "VALID_TASK_STATUSES",
]
