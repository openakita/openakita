"""omni-post task manager — pure ``aiosqlite`` CRUD for 7 tables.

Design rules (inherited from avatar-studio):

- Single-source SQLite entry for every table mutation. Routes / pipeline /
  scheduler all flow through :class:`OmniPostTaskManager`. This gives us
  one place to enforce idempotency (UNIQUE constraints) and one place to
  swap in a real migration system later.
- WAL journaling + ``synchronous=NORMAL`` — same pragmas as avatar-studio.
- ``update_task_safe`` is a strict whitelist, preventing column-name
  injection AND forbidding mutation of the identity columns (``id`` /
  ``created_at``).
- ``idempotency`` — every task write is keyed on
  ``UNIQUE(platform, account_id, client_trace_id)``. This is the direct
  anti-pattern fix for MultiPost-Extension issue #206 (duplicate
  triggers): the LLM / UI can retry without risking a second publish.

Tables (7):

  tasks                     one row per (platform, account, asset, payload)
  assets                    one row per uploaded media file, MD5-dedup
  asset_publish_history     audit log: which asset went to which platform
  accounts                  Fernet-encrypted cookie pool + quota / health
  platforms                 static metadata (display name, capabilities)
  schedules                 cron / scheduled_at + jitter
  selectors_health          self-heal probe results, throttled alerts
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite

logger = logging.getLogger("openakita.plugins.omni-post")


SCHEMA_SQL = """
-- 1. Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id                 TEXT PRIMARY KEY,
    client_trace_id    TEXT,
    asset_id           TEXT,
    platform           TEXT NOT NULL,
    account_id         TEXT NOT NULL,
    payload_json       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    error_kind         TEXT,
    error_hint_i18n    TEXT,
    scheduled_at       TEXT,
    started_at         TEXT,
    finished_at        TEXT,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    result_url         TEXT,
    screenshot_path    TEXT,
    engine             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    UNIQUE(platform, account_id, client_trace_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_tasks_platform  ON tasks(platform);
CREATE INDEX IF NOT EXISTS idx_tasks_created   ON tasks(created_at DESC);

-- 2. Assets
CREATE TABLE IF NOT EXISTS assets (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    filename         TEXT NOT NULL,
    filesize         INTEGER NOT NULL,
    md5              TEXT NOT NULL UNIQUE,
    duration_ms      INTEGER,
    width            INTEGER,
    height           INTEGER,
    codec            TEXT,
    bitrate          INTEGER,
    thumb_path       TEXT,
    storage_path     TEXT NOT NULL,
    upload_status    TEXT NOT NULL DEFAULT 'uploading',
    upload_progress  REAL NOT NULL DEFAULT 0.0,
    chunks_done      INTEGER NOT NULL DEFAULT 0,
    tags_json        TEXT NOT NULL DEFAULT '[]',
    source_plugin    TEXT,
    source_asset_id  TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_kind   ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(upload_status);

-- 3. Asset x publish history
CREATE TABLE IF NOT EXISTS asset_publish_history (
    id               TEXT PRIMARY KEY,
    asset_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    platform         TEXT NOT NULL,
    account_id       TEXT NOT NULL,
    status           TEXT NOT NULL,
    published_url    TEXT,
    published_at     TEXT,
    screenshot_path  TEXT
);

CREATE INDEX IF NOT EXISTS idx_aph_asset   ON asset_publish_history(asset_id);
CREATE INDEX IF NOT EXISTS idx_aph_account ON asset_publish_history(account_id);
CREATE INDEX IF NOT EXISTS idx_aph_task    ON asset_publish_history(task_id);

-- 4. Accounts
CREATE TABLE IF NOT EXISTS accounts (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    nickname            TEXT NOT NULL DEFAULT '',
    avatar_url          TEXT,
    cookie_cipher       BLOB NOT NULL,
    tags_json           TEXT NOT NULL DEFAULT '[]',
    daily_limit         INTEGER NOT NULL DEFAULT 5,
    weekly_limit        INTEGER NOT NULL DEFAULT 30,
    monthly_limit       INTEGER NOT NULL DEFAULT 100,
    last_health_check   TEXT,
    health_status       TEXT NOT NULL DEFAULT 'unknown',
    last_published_at   TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(platform, nickname)
);

CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);

-- 5. Platforms metadata
CREATE TABLE IF NOT EXISTS platforms (
    id                 TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    supported_kinds    TEXT NOT NULL,
    selector_version   TEXT NOT NULL DEFAULT '1.0.0',
    engine_preferred   TEXT NOT NULL DEFAULT 'pw',
    notes              TEXT
);

-- 6. Schedules
CREATE TABLE IF NOT EXISTS schedules (
    id               TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    cron_expr        TEXT,
    scheduled_at     TEXT,
    jitter_seconds   INTEGER NOT NULL DEFAULT 900,
    status           TEXT NOT NULL DEFAULT 'scheduled',
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_status ON schedules(status);

-- 7. Selector health probe results
CREATE TABLE IF NOT EXISTS selectors_health (
    platform          TEXT PRIMARY KEY,
    last_probed_at    TEXT,
    hit_rate          REAL NOT NULL DEFAULT 0.0,
    total_probes      INTEGER NOT NULL DEFAULT 0,
    failed_probes     INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    last_alerted_at   TEXT
);
"""


_TASK_WRITABLE: frozenset[str] = frozenset(
    {
        "status",
        "error_kind",
        "error_hint_i18n",
        "started_at",
        "finished_at",
        "retry_count",
        "result_url",
        "screenshot_path",
        "scheduled_at",
        "engine",
    }
)

_ASSET_WRITABLE: frozenset[str] = frozenset(
    {
        "upload_status",
        "upload_progress",
        "chunks_done",
        "duration_ms",
        "width",
        "height",
        "codec",
        "bitrate",
        "thumb_path",
        "storage_path",
        "tags_json",
        "source_plugin",
        "source_asset_id",
    }
)

_ACCOUNT_WRITABLE: frozenset[str] = frozenset(
    {
        "nickname",
        "avatar_url",
        "cookie_cipher",
        "tags_json",
        "daily_limit",
        "weekly_limit",
        "monthly_limit",
        "last_health_check",
        "health_status",
        "last_published_at",
    }
)


_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled"}
)


def _utc_iso() -> str:
    """Return the current wall clock in ISO-8601 UTC (ms-free, no offset suffix)."""

    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = dict(row)
    for k in ("payload_json", "error_hint_i18n", "tags_json", "supported_kinds"):
        if k in out and isinstance(out[k], str) and out[k]:
            try:
                decoded_key = k.removesuffix("_json")
                out[decoded_key] = json.loads(out[k])
            except (ValueError, TypeError):
                pass
    return out


class OmniPostTaskManager:
    """SQLite-backed CRUD for all 7 omni-post tables.

    Lifecycle:

        tm = OmniPostTaskManager(db_path)
        async with tm:
            await tm.create_task(...)

    Or call ``await tm.init()`` / ``await tm.close()`` manually.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def __aenter__(self) -> OmniPostTaskManager:
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
        if self._db is None:
            return
        try:
            await self._db.close()
        finally:
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "OmniPostTaskManager is not initialised; call `await tm.init()`",
            )
        return self._db

    # ── Tasks ────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        platform: str,
        account_id: str,
        asset_id: str | None,
        payload: dict[str, Any],
        engine: str = "pw",
        client_trace_id: str | None = None,
        scheduled_at: str | None = None,
    ) -> str:
        """Insert a new task row. Returns the new task id.

        Idempotent on ``(platform, account_id, client_trace_id)``: when
        the same trace id is replayed, we return the *existing* task id
        (so the LLM can safely retry without doubling up a publish).
        This is the fix for MultiPost-Extension issue #206.
        """

        conn = self._conn()
        now = _utc_iso()

        if client_trace_id:
            async with conn.execute(
                "SELECT id FROM tasks WHERE platform=? AND account_id=? AND client_trace_id=?",
                (platform, account_id, client_trace_id),
            ) as cur:
                existing = await cur.fetchone()
                if existing is not None:
                    return existing["id"]

        task_id = _new_id("tk")
        await conn.execute(
            """
            INSERT INTO tasks (
                id, client_trace_id, asset_id, platform, account_id,
                payload_json, status, scheduled_at, engine, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                task_id,
                client_trace_id,
                asset_id,
                platform,
                account_id,
                json.dumps(payload, ensure_ascii=False),
                scheduled_at,
                engine,
                now,
            ),
        )
        await conn.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        async with conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        platform: str | None = None,
        account_id: str | None = None,
        asset_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        conn = self._conn()
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            if status not in _TASK_STATUSES:
                raise ValueError(f"unknown status: {status!r}")
            clauses.append("status=?")
            args.append(status)
        if platform:
            clauses.append("platform=?")
            args.append(platform)
        if account_id:
            clauses.append("account_id=?")
            args.append(account_id)
        if asset_id:
            clauses.append("asset_id=?")
            args.append(asset_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        async with conn.execute(sql, tuple(args)) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_task_safe(self, task_id: str, updates: dict[str, Any]) -> None:
        """Whitelist-guarded UPDATE for tasks.

        Only columns in :data:`_TASK_WRITABLE` may be mutated. ``status``
        is additionally checked against :data:`_TASK_STATUSES`. Any other
        key raises ``ValueError`` so a typo can never silently drop a
        field or open a SQL-injection vector.
        """

        if not updates:
            return
        bad = set(updates) - _TASK_WRITABLE
        if bad:
            raise ValueError(f"non-writable task columns: {sorted(bad)!r}")
        if "status" in updates and updates["status"] not in _TASK_STATUSES:
            raise ValueError(f"invalid status: {updates['status']!r}")

        cols = list(updates.keys())
        assignments = ", ".join(f"{c}=?" for c in cols)
        values: list[Any] = []
        for c in cols:
            v = updates[c]
            if c == "error_hint_i18n" and not isinstance(v, (str, bytes)):
                v = json.dumps(v, ensure_ascii=False)
            values.append(v)
        values.append(task_id)

        conn = self._conn()
        await conn.execute(
            f"UPDATE tasks SET {assignments} WHERE id=?",
            tuple(values),
        )
        await conn.commit()

    async def delete_task(self, task_id: str) -> bool:
        conn = self._conn()
        cur = await conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await conn.commit()
        return cur.rowcount > 0

    # ── Assets ───────────────────────────────────────────────────────

    async def create_asset(
        self,
        *,
        kind: str,
        filename: str,
        filesize: int,
        md5: str,
        storage_path: str,
        duration_ms: int | None = None,
        width: int | None = None,
        height: int | None = None,
        codec: str | None = None,
        bitrate: int | None = None,
        thumb_path: str | None = None,
        source_plugin: str | None = None,
        source_asset_id: str | None = None,
        tags: Iterable[str] | None = None,
    ) -> str:
        """Insert an asset. Returns the new asset id or the EXISTING id
        if the md5 already lives in the table (seamless dedup / "秒传").
        """

        conn = self._conn()
        async with conn.execute("SELECT id FROM assets WHERE md5=?", (md5,)) as cur:
            existing = await cur.fetchone()
            if existing is not None:
                return existing["id"]

        asset_id = _new_id("ast")
        await conn.execute(
            """
            INSERT INTO assets (
                id, kind, filename, filesize, md5, storage_path,
                duration_ms, width, height, codec, bitrate, thumb_path,
                upload_status, upload_progress, chunks_done,
                tags_json, source_plugin, source_asset_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 1.0, 0, ?, ?, ?, ?)
            """,
            (
                asset_id,
                kind,
                filename,
                int(filesize),
                md5,
                storage_path,
                duration_ms,
                width,
                height,
                codec,
                bitrate,
                thumb_path,
                json.dumps(list(tags or []), ensure_ascii=False),
                source_plugin,
                source_asset_id,
                _utc_iso(),
            ),
        )
        await conn.commit()
        return asset_id

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        async with conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)

    async def find_asset_by_md5(self, md5: str) -> dict[str, Any] | None:
        conn = self._conn()
        async with conn.execute("SELECT * FROM assets WHERE md5=?", (md5,)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)

    async def list_assets(
        self,
        *,
        kind: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        conn = self._conn()
        if kind:
            sql = "SELECT * FROM assets WHERE kind=? ORDER BY created_at DESC LIMIT ?"
            args = (kind, int(limit))
        else:
            sql = "SELECT * FROM assets ORDER BY created_at DESC LIMIT ?"
            args = (int(limit),)
        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_asset_safe(self, asset_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        bad = set(updates) - _ASSET_WRITABLE
        if bad:
            raise ValueError(f"non-writable asset columns: {sorted(bad)!r}")
        cols = list(updates.keys())
        assignments = ", ".join(f"{c}=?" for c in cols)
        values = [updates[c] for c in cols]
        values.append(asset_id)
        conn = self._conn()
        await conn.execute(f"UPDATE assets SET {assignments} WHERE id=?", tuple(values))
        await conn.commit()

    async def delete_asset(self, asset_id: str) -> bool:
        conn = self._conn()
        cur = await conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        await conn.commit()
        return cur.rowcount > 0

    # ── Asset x publish history ─────────────────────────────────────

    async def record_publish_history(
        self,
        *,
        asset_id: str,
        task_id: str,
        platform: str,
        account_id: str,
        status: str,
        published_url: str | None = None,
        screenshot_path: str | None = None,
    ) -> str:
        conn = self._conn()
        hid = _new_id("aph")
        await conn.execute(
            """
            INSERT INTO asset_publish_history (
                id, asset_id, task_id, platform, account_id,
                status, published_url, published_at, screenshot_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid,
                asset_id,
                task_id,
                platform,
                account_id,
                status,
                published_url,
                _utc_iso(),
                screenshot_path,
            ),
        )
        await conn.commit()
        return hid

    async def list_publish_history(
        self,
        *,
        asset_id: str | None = None,
        account_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        conn = self._conn()
        clauses: list[str] = []
        args: list[Any] = []
        if asset_id:
            clauses.append("asset_id=?")
            args.append(asset_id)
        if account_id:
            clauses.append("account_id=?")
            args.append(account_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM asset_publish_history {where} ORDER BY published_at DESC LIMIT ?"
        args.append(int(limit))
        async with conn.execute(sql, tuple(args)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Accounts ─────────────────────────────────────────────────────

    async def create_account(
        self,
        *,
        platform: str,
        nickname: str,
        cookie_cipher: bytes,
        avatar_url: str | None = None,
        tags: Iterable[str] | None = None,
        daily_limit: int = 5,
        weekly_limit: int = 30,
        monthly_limit: int = 100,
    ) -> str:
        conn = self._conn()
        acc_id = _new_id("acc")
        await conn.execute(
            """
            INSERT INTO accounts (
                id, platform, nickname, avatar_url, cookie_cipher,
                tags_json, daily_limit, weekly_limit, monthly_limit,
                health_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?)
            """,
            (
                acc_id,
                platform,
                nickname,
                avatar_url,
                cookie_cipher,
                json.dumps(list(tags or []), ensure_ascii=False),
                int(daily_limit),
                int(weekly_limit),
                int(monthly_limit),
                _utc_iso(),
            ),
        )
        await conn.commit()
        return acc_id

    async def get_account(self, account_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        async with conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row)

    async def list_accounts(self, *, platform: str | None = None) -> list[dict[str, Any]]:
        conn = self._conn()
        if platform:
            sql = "SELECT * FROM accounts WHERE platform=? ORDER BY created_at DESC"
            args: tuple[Any, ...] = (platform,)
        else:
            sql = "SELECT * FROM accounts ORDER BY created_at DESC"
            args = ()
        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    async def update_account_safe(self, account_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        bad = set(updates) - _ACCOUNT_WRITABLE
        if bad:
            raise ValueError(f"non-writable account columns: {sorted(bad)!r}")
        cols = list(updates.keys())
        assignments = ", ".join(f"{c}=?" for c in cols)
        values = [updates[c] for c in cols]
        values.append(account_id)
        conn = self._conn()
        await conn.execute(
            f"UPDATE accounts SET {assignments} WHERE id=?",
            tuple(values),
        )
        await conn.commit()

    async def delete_account(self, account_id: str) -> bool:
        conn = self._conn()
        cur = await conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await conn.commit()
        return cur.rowcount > 0

    # ── Platforms ────────────────────────────────────────────────────

    async def upsert_platform(
        self,
        *,
        platform_id: str,
        display_name: str,
        supported_kinds: list[str],
        selector_version: str = "1.0.0",
        engine_preferred: str = "pw",
        notes: str | None = None,
    ) -> None:
        conn = self._conn()
        await conn.execute(
            """
            INSERT INTO platforms (
                id, display_name, supported_kinds, selector_version,
                engine_preferred, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name=excluded.display_name,
                supported_kinds=excluded.supported_kinds,
                selector_version=excluded.selector_version,
                engine_preferred=excluded.engine_preferred,
                notes=excluded.notes
            """,
            (
                platform_id,
                display_name,
                json.dumps(supported_kinds, ensure_ascii=False),
                selector_version,
                engine_preferred,
                notes,
            ),
        )
        await conn.commit()

    async def list_platforms(self) -> list[dict[str, Any]]:
        conn = self._conn()
        async with conn.execute("SELECT * FROM platforms ORDER BY id") as cur:
            rows = await cur.fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]

    # ── Schedules ────────────────────────────────────────────────────

    async def create_schedule(
        self,
        *,
        task_id: str,
        scheduled_at: str | None = None,
        cron_expr: str | None = None,
        jitter_seconds: int = 900,
    ) -> str:
        conn = self._conn()
        sid = _new_id("sch")
        await conn.execute(
            """
            INSERT INTO schedules (
                id, task_id, cron_expr, scheduled_at, jitter_seconds,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
            """,
            (
                sid,
                task_id,
                cron_expr,
                scheduled_at,
                int(jitter_seconds),
                _utc_iso(),
            ),
        )
        await conn.commit()
        return sid

    async def mark_schedule(self, schedule_id: str, status: str) -> None:
        if status not in {"scheduled", "triggered", "cancelled"}:
            raise ValueError(f"invalid schedule status: {status!r}")
        conn = self._conn()
        await conn.execute(
            "UPDATE schedules SET status=? WHERE id=?",
            (status, schedule_id),
        )
        await conn.commit()

    async def list_pending_schedules(self) -> list[dict[str, Any]]:
        conn = self._conn()
        async with conn.execute(
            "SELECT * FROM schedules WHERE status='scheduled' ORDER BY scheduled_at",
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Selectors health ─────────────────────────────────────────────

    async def upsert_selector_health(
        self,
        *,
        platform: str,
        hit_rate: float,
        total_probes: int,
        failed_probes: int,
        last_error: str | None = None,
    ) -> None:
        conn = self._conn()
        now = _utc_iso()
        await conn.execute(
            """
            INSERT INTO selectors_health (
                platform, last_probed_at, hit_rate,
                total_probes, failed_probes, last_error
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform) DO UPDATE SET
                last_probed_at=excluded.last_probed_at,
                hit_rate=excluded.hit_rate,
                total_probes=excluded.total_probes,
                failed_probes=excluded.failed_probes,
                last_error=excluded.last_error
            """,
            (platform, now, float(hit_rate), int(total_probes), int(failed_probes), last_error),
        )
        await conn.commit()

    async def mark_selector_alerted(self, platform: str) -> None:
        conn = self._conn()
        await conn.execute(
            "UPDATE selectors_health SET last_alerted_at=? WHERE platform=?",
            (_utc_iso(), platform),
        )
        await conn.commit()

    async def list_selector_health(self) -> list[dict[str, Any]]:
        conn = self._conn()
        async with conn.execute(
            "SELECT * FROM selectors_health ORDER BY platform",
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Aggregates ───────────────────────────────────────────────────

    async def count_account_published_since(self, account_id: str, since_iso: str) -> int:
        """How many `succeeded` publishes this account accrued since ``since_iso``.

        Used by the quota guard to enforce daily / weekly / monthly caps.
        """

        conn = self._conn()
        async with conn.execute(
            """
            SELECT COUNT(*) AS n FROM asset_publish_history
            WHERE account_id=? AND status='succeeded' AND published_at >= ?
            """,
            (account_id, since_iso),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def stats(self) -> dict[str, int]:
        """Cheap dashboard aggregates for the Settings tab."""

        conn = self._conn()
        out: dict[str, int] = {}
        for key, sql in (
            ("tasks_total", "SELECT COUNT(*) AS n FROM tasks"),
            ("assets_total", "SELECT COUNT(*) AS n FROM assets"),
            ("accounts_total", "SELECT COUNT(*) AS n FROM accounts"),
            (
                "tasks_pending",
                "SELECT COUNT(*) AS n FROM tasks WHERE status='pending'",
            ),
            (
                "tasks_running",
                "SELECT COUNT(*) AS n FROM tasks WHERE status='running'",
            ),
            (
                "tasks_succeeded",
                "SELECT COUNT(*) AS n FROM tasks WHERE status='succeeded'",
            ),
            (
                "tasks_failed",
                "SELECT COUNT(*) AS n FROM tasks WHERE status='failed'",
            ),
        ):
            async with conn.execute(sql) as cur:
                row = await cur.fetchone()
                out[key] = int(row["n"]) if row else 0
        return out


def _coerce_iso(ts: float | str | None) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        from datetime import datetime

        return datetime.fromtimestamp(float(ts), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)


# Re-exported for tests.
__all__ = [
    "OmniPostTaskManager",
    "SCHEMA_SQL",
    "_new_id",
    "_utc_iso",
]


# Helpful alias so test suites don't import from a private name.
utc_iso = _utc_iso

# Expose a stable, non-private creator helper for tests.
new_id = _new_id

# Time helpers
_time_ = time  # keep the name ``time`` referenced so linters don't strip the import.
_ = _coerce_iso  # keep the internal helper available for future use.
