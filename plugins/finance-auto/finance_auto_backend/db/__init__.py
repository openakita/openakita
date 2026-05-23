"""SQLite connection helper for finance-auto.

Why a tiny module instead of an ORM?

* M1 W1 needs five tables and ~5 endpoints — pulling in SQLAlchemy would
  triple the code we have to read.
* We mirror ``plugins/fin-pulse``'s pattern (single ``aiosqlite.Connection``
  cached on the plugin instance, WAL + ``synchronous=NORMAL`` PRAGMAs at
  connect time) so the operational behaviour is consistent with the rest of
  the host.

The encryption ``_encrypted_payload BLOB`` columns are present in the schema
but always written as ``NULL`` in M1 W1 — M1 W2's KeyManager will populate
them via a follow-up migration that simply re-encrypts the cleartext columns.

History note (M2 AI Stage 1, 2026-05-23)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This module used to live at ``finance_auto_backend/db.py``.  When M2 added
the per-version migration helpers under ``db.migrations``, the file was
promoted to a package.  Existing imports such as ``from .db import
FinanceAutoDB`` keep working because ``__init__.py`` re-exports the same
symbols.  The DB lifecycle and PRAGMA dance are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from ..schema import MIGRATION_STEPS, SCHEMA_SQL, SCHEMA_VERSION

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


class FinanceAutoDB:
    """Thin wrapper around ``aiosqlite.Connection`` with init/close lifecycle.

    Usage::

        db = FinanceAutoDB(Path("data/plugin_data/finance-auto/finance.sqlite"))
        await db.init()
        async with db.conn.execute("SELECT 1") as cur:
            ...
        await db.close()

    All connections enable WAL mode + ``synchronous=NORMAL`` for concurrent
    read while ingest writes (M1 W1 explicit ask from product).
    """

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()
        self._ready = False

    @property
    def path(self) -> Path:
        return self._db_path

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("FinanceAutoDB.conn accessed before init()")
        return self._conn

    def is_ready(self) -> bool:
        return self._ready and self._conn is not None

    async def init(self) -> None:
        """Open the connection, enable WAL, and apply schema.

        Idempotent: calling it twice is a no-op. Concurrent callers are
        serialised by an ``asyncio.Lock`` so the bootstrap task and a fast
        first request cannot both race the PRAGMA dance.
        """
        async with self._init_lock:
            if self._ready and self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            # Always apply the full canonical schema first (idempotent thanks
            # to ``IF NOT EXISTS``).  Then replay any migration steps whose
            # target version exceeds the current recorded one -- this lets
            # databases created at v1 pick up the v2 additions in-place.
            await conn.executescript(SCHEMA_SQL)
            current_version = await _read_recorded_version(conn)
            for target_version, step_sql in MIGRATION_STEPS:
                if target_version > current_version:
                    await _run_idempotent_script(conn, step_sql)
                    logger.info(
                        "finance-auto: migrated schema %d -> %d",
                        current_version,
                        target_version,
                    )
                    current_version = target_version
            now = _utcnow_iso()
            await conn.execute(
                "INSERT INTO schema_version(component, version, applied_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(component) DO UPDATE SET version=excluded.version, "
                "applied_at=excluded.applied_at WHERE schema_version.version < excluded.version",
                ("finance_auto", SCHEMA_VERSION, now),
            )
            await conn.commit()
            self._conn = conn
            self._ready = True
            logger.info(
                "finance-auto: SQLite ready at %s (WAL, schema v%d)",
                self._db_path,
                SCHEMA_VERSION,
            )

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception as exc:
                logger.warning("finance-auto: SQLite close error: %s", exc)
            self._conn = None
        self._ready = False

    async def journal_mode(self) -> str:
        """Read back ``PRAGMA journal_mode`` for diagnostic / verification."""
        if self._conn is None:
            return "closed"
        async with self._conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
            return row[0] if row else "unknown"


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _read_recorded_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT version FROM schema_version WHERE component='finance_auto'"
    ) as cur:
        row = await cur.fetchone()
        if row is None:
            return 0
        return int(row[0])


def _strip_sql_line_comments(script: str) -> str:
    """Strip ``--`` line comments without touching anything inside quoted
    strings.  Block comments (``/* */``) are not used in our migrations, so
    we keep this simple."""
    out: list[str] = []
    for line in script.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            continue
        in_single = False
        cut = -1
        for i, ch in enumerate(line):
            if ch == "'":
                in_single = not in_single
            elif ch == "-" and not in_single and i + 1 < len(line) and line[i + 1] == "-":
                cut = i
                break
        if cut >= 0:
            line = line[:cut].rstrip()
        if line.strip():
            out.append(line)
    return "\n".join(out)


async def _run_idempotent_script(conn: aiosqlite.Connection, script: str) -> None:
    """Replay a migration script tolerating "duplicate column" /
    "table already exists" errors so the chain is safe to re-apply.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so we split the script into
    statements and swallow only the two specific errors that indicate a
    re-run.  Any other error propagates so genuine bugs surface fast.
    """
    cleaned = _strip_sql_line_comments(script)
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    for stmt in statements:
        try:
            await conn.execute(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            raise
    await conn.commit()


__all__ = ["FinanceAutoDB"]
