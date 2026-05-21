"""Centralised, fault-tolerant SQLite open helpers.

Every long-lived SQLite database the backend touches (memory store, token
tracking, asset bus, feedback store, scheduler etc.) should be opened
through one of the helpers in this module so we get a uniform safety
net:

* ``PRAGMA busy_timeout`` set explicitly (so concurrent writers fail
  with a sane error instead of hanging the event loop).
* Optional ``PRAGMA journal_mode=WAL`` for readers-don't-block-writers.
* ``PRAGMA quick_check`` runs before any DDL/DML touches the file, so
  we detect corruption at open time instead of mid-query.
* Sidecar (``-wal`` / ``-journal``) "hot journal orphan" detection,
  which catches the specific pattern where a previous process crashed
  and left a near-empty main file alongside a fat WAL.
* Windows-friendly retry loop for the brief window where another
  process holds the file open exclusively.

Failures are surfaced as :class:`SQLiteUnavailable` with a structured
``reason`` aligned to ``memory.MemoryStorageUnavailable`` so callers
can decide whether to degrade, retry, or escalate. The async helpers
are offered in two flavours — a plain ``async def`` returning a raw
connection (caller owns ``close()``) and an ``@asynccontextmanager``
wrapper for ``async with`` callers (e.g. existing ``aiosqlite.connect``
usages migrating in place).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

__all__ = [
    "SQLiteUnavailable",
    "safe_open_sync",
    "safe_open_async",
    "safe_open_async_ctx",
    "quick_check_or_raise_sync",
    "quick_check_or_raise_async",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass
class SQLiteUnavailable(Exception):
    """Raised when a SQLite database cannot be opened safely.

    The ``reason`` field is intentionally a string enum (not a Python
    ``enum.Enum``) so it stays JSON-serialisable for ``/api/health``
    payloads and the degraded-registry. Aligned with
    :class:`openakita.memory.exceptions.MemoryStorageUnavailable`.

    Known reasons:

    * ``corrupted`` -- ``PRAGMA quick_check`` returned anything but ``ok``.
    * ``hot_journal_orphan`` -- main file looks empty but the sidecar
      ``-wal``/``-journal`` is sizeable; almost always indicates the
      previous process crashed mid-checkpoint and left an unreadable pair.
    * ``disk_full`` -- ``ENOSPC`` (errno 28) or SQLite "disk is full".
    * ``permission_denied`` -- ``EACCES`` (errno 13).
    * ``filesystem_error`` -- any other ``OSError``.
    * ``path_in_sync_folder`` -- path contains Dropbox/OneDrive/GoogleDrive
      markers or a UNC prefix; SQLite WAL is unsafe there.
    * ``schema_init_failed`` -- caller-side DDL (CREATE TABLE etc.) raised.
    * ``unknown_db_error`` -- catch-all.
    """

    reason: str
    path: Path | None = None
    details: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        suffix = f": {self.details}" if self.details else ""
        path_part = f" (path={self.path})" if self.path else ""
        return f"{self.reason}{path_part}{suffix}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SYNC_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive")


def _is_sync_folder_path(path: Path) -> bool:
    """Return ``True`` for paths that live inside a cloud sync folder.

    SQLite WAL is unsafe on Dropbox/OneDrive/GoogleDrive because the
    sync agent may rename or copy ``-wal``/``-shm`` files mid-write,
    corrupting the database. UNC paths (``\\\\server\\share\\...``) are
    treated the same way to be conservative.

    Override with ``OPENAKITA_ALLOW_SYNC_FOLDER_DB=1`` for power users
    who accept the risk.
    """
    text = str(path).lower()
    if os.environ.get("OPENAKITA_ALLOW_SYNC_FOLDER_DB") == "1":
        return False
    return any(marker in text for marker in _SYNC_FOLDER_MARKERS) or text.startswith("\\\\")


def _looks_like_corruption(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "malformed" in msg
        or "corrupt" in msg
        or "not a database" in msg
        or "file is not a database" in msg
        or "database disk image is malformed" in msg
    )


def _classify_oserror(exc: OSError) -> str:
    errno = getattr(exc, "errno", None)
    if errno == 13:  # EACCES
        return "permission_denied"
    if errno == 28:  # ENOSPC
        return "disk_full"
    return "filesystem_error"


def _hot_journal_orphan(path: Path) -> tuple[int, int] | None:
    """Detect the "empty DB + fat WAL/journal" failure mode.

    Returns ``(main_size, sidecar_size)`` when a hot journal orphan is
    detected, or ``None`` otherwise. We require main < 1KB and the
    sidecar to be at least 64KB to fire — small thresholds tuned to
    avoid false positives on freshly-created DBs.
    """
    try:
        main_size = path.stat().st_size if path.exists() else 0
    except OSError:
        return None
    if main_size >= 1024:
        return None

    for sidecar_suffix in ("-wal", "-journal"):
        side = Path(str(path) + sidecar_suffix)
        try:
            if side.exists() and side.stat().st_size >= 64 * 1024:
                return main_size, side.stat().st_size
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def quick_check_or_raise_sync(conn: sqlite3.Connection, path: Path | None = None) -> None:
    """Run ``PRAGMA quick_check`` on an open connection.

    Lifted from ``MemoryStorage.quick_check_or_raise`` so we only have
    one implementation. Existing ``MemoryStorage`` code keeps its own
    method as a thin wrapper for backwards compatibility.
    """
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as e:
        if _looks_like_corruption(e):
            raise SQLiteUnavailable("corrupted", path=path, details=str(e)) from e
        raise
    result = str(row[0] if row else "").strip().lower()
    if result != "ok":
        raise SQLiteUnavailable("corrupted", path=path, details=result or "quick_check failed")


def safe_open_sync(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    check_same_thread: bool = False,
    windows_retry: int = 3,
    windows_retry_sleep_s: float = 0.2,
) -> sqlite3.Connection:
    """Open a SQLite database safely (synchronous flavour).

    Returns an already-pragma'd ``sqlite3.Connection``. On any failure
    detected during PRAGMA / quick_check, the partial connection is
    closed and ``SQLiteUnavailable`` is raised. Callers are responsible
    for ``conn.close()`` on success.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if _is_sync_folder_path(p):
        raise SQLiteUnavailable("path_in_sync_folder", path=p, details=str(p))

    orphan = _hot_journal_orphan(p)
    if orphan is not None:
        main_size, side_size = orphan
        raise SQLiteUnavailable(
            "hot_journal_orphan",
            path=p,
            details=f"main={main_size}B sidecar={side_size}B",
            extra={"main_size": main_size, "sidecar_size": side_size},
        )

    last_exc: BaseException | None = None
    for _attempt in range(max(1, windows_retry)):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(p), check_same_thread=check_same_thread)
            conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
            if want_wal:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            if foreign_keys:
                conn.execute("PRAGMA foreign_keys=ON")
            if run_quick_check:
                quick_check_or_raise_sync(conn, path=p)
            return conn
        except SQLiteUnavailable:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            raise
        except sqlite3.DatabaseError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            if _looks_like_corruption(e):
                raise SQLiteUnavailable("corrupted", path=p, details=str(e)) from e
            msg = str(e).lower()
            if "disk i/o error" in msg or "disk is full" in msg:
                raise SQLiteUnavailable("disk_full", path=p, details=str(e)) from e
            if "locked" in msg or "busy" in msg:
                last_exc = e
                time.sleep(windows_retry_sleep_s)
                continue
            raise SQLiteUnavailable("unknown_db_error", path=p, details=str(e)) from e
        except OSError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            raise SQLiteUnavailable(_classify_oserror(e), path=p, details=str(e)) from e

    assert last_exc is not None
    raise SQLiteUnavailable(
        "unknown_db_error",
        path=p,
        details=f"exhausted windows_retry: {last_exc}",
    ) from last_exc


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def quick_check_or_raise_async(
    conn: aiosqlite.Connection, path: Path | None = None
) -> None:
    """Async counterpart to :func:`quick_check_or_raise_sync`."""
    try:
        cursor = await conn.execute("PRAGMA quick_check")
        row = await cursor.fetchone()
        await cursor.close()
    except sqlite3.DatabaseError as e:
        if _looks_like_corruption(e):
            raise SQLiteUnavailable("corrupted", path=path, details=str(e)) from e
        raise
    result = str(row[0] if row else "").strip().lower()
    if result != "ok":
        raise SQLiteUnavailable("corrupted", path=path, details=result or "quick_check failed")


async def safe_open_async(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    row_factory: Any = None,
    windows_retry: int = 3,
    windows_retry_sleep_s: float = 0.2,
) -> aiosqlite.Connection:
    """Open a SQLite database safely (async flavour).

    Returns an already-pragma'd ``aiosqlite.Connection`` whose ``close()``
    the caller must invoke. For ``async with`` consumers, see
    :func:`safe_open_async_ctx`.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if _is_sync_folder_path(p):
        raise SQLiteUnavailable("path_in_sync_folder", path=p, details=str(p))

    orphan = _hot_journal_orphan(p)
    if orphan is not None:
        main_size, side_size = orphan
        raise SQLiteUnavailable(
            "hot_journal_orphan",
            path=p,
            details=f"main={main_size}B sidecar={side_size}B",
            extra={"main_size": main_size, "sidecar_size": side_size},
        )

    last_exc: BaseException | None = None
    for _attempt in range(max(1, windows_retry)):
        conn: aiosqlite.Connection | None = None
        try:
            conn = await aiosqlite.connect(str(p))
            if row_factory is not None:
                conn.row_factory = row_factory
            await conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
            if want_wal:
                await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            if foreign_keys:
                await conn.execute("PRAGMA foreign_keys=ON")
            if run_quick_check:
                await quick_check_or_raise_async(conn, path=p)
            return conn
        except SQLiteUnavailable:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            raise
        except sqlite3.DatabaseError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            if _looks_like_corruption(e):
                raise SQLiteUnavailable("corrupted", path=p, details=str(e)) from e
            msg = str(e).lower()
            if "disk i/o error" in msg or "disk is full" in msg:
                raise SQLiteUnavailable("disk_full", path=p, details=str(e)) from e
            if "locked" in msg or "busy" in msg:
                last_exc = e
                await asyncio.sleep(windows_retry_sleep_s)
                continue
            raise SQLiteUnavailable("unknown_db_error", path=p, details=str(e)) from e
        except OSError as e:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
            raise SQLiteUnavailable(_classify_oserror(e), path=p, details=str(e)) from e

    assert last_exc is not None
    raise SQLiteUnavailable(
        "unknown_db_error",
        path=p,
        details=f"exhausted windows_retry: {last_exc}",
    ) from last_exc


@contextlib.asynccontextmanager
async def safe_open_async_ctx(
    path: str | Path,
    *,
    want_wal: bool = True,
    busy_ms: int = 30_000,
    run_quick_check: bool = True,
    foreign_keys: bool = True,
    row_factory: Any = None,
) -> AsyncIterator[aiosqlite.Connection]:
    """``async with`` wrapper around :func:`safe_open_async`.

    Use this when migrating call sites that already used
    ``async with aiosqlite.connect(path) as db:`` so the diff is
    minimal. The connection is closed automatically on ``__aexit__``.
    """
    conn = await safe_open_async(
        path,
        want_wal=want_wal,
        busy_ms=busy_ms,
        run_quick_check=run_quick_check,
        foreign_keys=foreign_keys,
        row_factory=row_factory,
    )
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
