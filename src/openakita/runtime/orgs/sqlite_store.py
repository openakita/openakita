"""SQLite-backed :class:`OrgV2` store for the v2 API facade.

P-RC-3 commit P3.4. Mirrors the public surface of
:class:`openakita.runtime.orgs.store.JsonOrgStore` so the two
backends are interchangeable through the
:func:`openakita.runtime.orgs.get_default_store` factory and the
shared contract suite under ``tests/runtime/orgs/``.

Why SQLite: the JSON store rewrites the entire file on every
mutation, which produces a last-writer-wins race when two
processes mutate concurrently. SQLite with ``BEGIN IMMEDIATE`` +
WAL serialises competing transactions at the database file level.

Schema: a single ``orgs`` table keyed by ``id`` storing the full
:meth:`OrgV2.to_jsonable` payload in a ``payload`` TEXT column.

Concurrency: ``check_same_thread=False`` + ``threading.RLock`` on
the connection, ``isolation_level=None`` (autocommit) with
explicit ``BEGIN IMMEDIATE`` for writes. WAL mode +
``synchronous=NORMAL`` + 5 s ``busy_timeout`` for low-latency
writes without sacrificing durability.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from openakita.runtime.models import OrgV2, new_org_id

from .store import OrgNotFound

__all__ = ["SqliteOrgStore"]

logger = logging.getLogger(__name__)

_SELECT_COLS = (
    "id, name, description, payload, created_at, updated_at, version"
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    payload     TEXT NOT NULL,
    created_at  TEXT,
    updated_at  TEXT,
    version     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_orgs_updated_at ON orgs (updated_at DESC);
"""


class SqliteOrgStore:
    """Thread-safe SQLite-backed :class:`OrgV2` store."""

    def __init__(self, *, path: Path | str | None = None) -> None:
        if path is None:
            from openakita.config import settings

            base = getattr(settings, "data_dir", None) or "data"
            path = Path(base) / "orgs_v2.sqlite"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # 5 s busy_timeout: WAL writer contention retries internally
        # rather than raising "database is locked".
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA_SQL)
        self._closed = False

    def close(self) -> None:
        """Close the underlying SQLite connection. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error as exc:  # pragma: no cover
                logger.debug("SqliteOrgStore close error swallowed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _write_txn(self):
        """Hold the RLock + drive ``BEGIN IMMEDIATE`` / ``COMMIT``."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            else:
                self._conn.execute("COMMIT")

    def _row_to_org(self, row: tuple) -> OrgV2 | None:
        """Decode a row's ``payload`` JSON. ``None`` on malformed payload."""
        org_id, _name, _desc, payload, *_rest = row
        try:
            return OrgV2.from_jsonable(json.loads(payload))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "[orgs_v2 sqlite] dropping malformed org id=%s (%s)", org_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Public API (mirrors JsonOrgStore)
    # ------------------------------------------------------------------

    def list(self) -> list[OrgV2]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_SELECT_COLS} FROM orgs ORDER BY created_at DESC"
            ).fetchall()
        out = [o for r in rows if (o := self._row_to_org(r)) is not None]
        out.sort(key=lambda o: o.created_at, reverse=True)
        return out

    def get(self, org_id: str) -> OrgV2:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_SELECT_COLS} FROM orgs WHERE id=?", (org_id,)
            ).fetchone()
        if row is None:
            raise OrgNotFound(org_id)
        org = self._row_to_org(row)
        if org is None:
            # Corrupt row: surface as "missing" rather than a half-decoded payload.
            raise OrgNotFound(org_id)
        return org

    def create(self, org: OrgV2) -> OrgV2:
        if not org.id:
            org.id = new_org_id()
        now = datetime.now(UTC)
        org.created_at = org.created_at or now
        org.updated_at = now
        payload = json.dumps(org.to_jsonable(), ensure_ascii=False)
        with self._write_txn() as conn:
            if conn.execute("SELECT 1 FROM orgs WHERE id=?", (org.id,)).fetchone():
                raise ValueError(f"OrgV2 with id={org.id!r} already exists")
            conn.execute(
                "INSERT INTO orgs (id, name, description, payload,"
                " created_at, updated_at, version) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (
                    org.id,
                    org.name,
                    org.description,
                    payload,
                    org.created_at.isoformat(),
                    org.updated_at.isoformat(),
                ),
            )
        return org

    def patch(
        self,
        org_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> OrgV2:
        with self._write_txn() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM orgs WHERE id=?", (org_id,)
            ).fetchone()
            if row is None:
                raise OrgNotFound(org_id)
            org = self._row_to_org(row)
            if org is None:
                raise OrgNotFound(org_id)
            if name is not None:
                org.name = name
            if description is not None:
                org.description = description
            org.updated_at = datetime.now(UTC)
            payload = json.dumps(org.to_jsonable(), ensure_ascii=False)
            conn.execute(
                "UPDATE orgs SET name=?, description=?, payload=?,"
                " updated_at=?, version=version+1 WHERE id=?",
                (
                    org.name,
                    org.description,
                    payload,
                    org.updated_at.isoformat(),
                    org_id,
                ),
            )
        return org

    def delete(self, org_id: str) -> None:
        with self._write_txn() as conn:
            cur = conn.execute("DELETE FROM orgs WHERE id=?", (org_id,))
            if (cur.rowcount or 0) == 0:
                raise OrgNotFound(org_id)
