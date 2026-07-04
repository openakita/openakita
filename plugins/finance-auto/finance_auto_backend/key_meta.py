"""Tiny DAO for the ``key_meta`` table.

Encapsulates "salt + iteration count + enabled flag" reads/writes so the
service layer doesn't have to remember the SQL.  Single-component for M1 W2;
the M2 per-org refactor will add ``component='org_<id>'`` rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from .key_manager import PBKDF2_ITERATIONS, SALT_LEN

logger = logging.getLogger(__name__)

GLOBAL_COMPONENT = "global"


@dataclass(frozen=True)
class KeyMeta:
    component: str
    salt: bytes
    kdf_iterations: int
    enabled: bool
    seed_source: str | None
    created_at: str
    updated_at: str


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def read_key_meta(
    conn: aiosqlite.Connection, component: str = GLOBAL_COMPONENT
) -> KeyMeta | None:
    async with conn.execute(
        "SELECT component, salt, kdf_iterations, enabled, seed_source, "
        "created_at, updated_at FROM key_meta WHERE component=?",
        (component,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return KeyMeta(
        component=row[0],
        salt=row[1] if isinstance(row[1], (bytes, bytearray)) else bytes(row[1]),
        kdf_iterations=int(row[2]),
        enabled=bool(row[3]),
        seed_source=row[4],
        created_at=row[5],
        updated_at=row[6],
    )


async def write_key_meta(
    conn: aiosqlite.Connection,
    *,
    salt: bytes,
    enabled: bool,
    seed_source: str | None,
    component: str = GLOBAL_COMPONENT,
    kdf_iterations: int = PBKDF2_ITERATIONS,
) -> KeyMeta:
    if len(salt) != SALT_LEN:
        raise ValueError(f"salt must be {SALT_LEN} bytes, got {len(salt)}")
    now = _utcnow_iso()
    await conn.execute(
        "INSERT INTO key_meta(component, salt, kdf_iterations, enabled, "
        "seed_source, created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(component) DO UPDATE SET salt=excluded.salt, "
        "kdf_iterations=excluded.kdf_iterations, enabled=excluded.enabled, "
        "seed_source=COALESCE(excluded.seed_source, key_meta.seed_source), "
        "updated_at=excluded.updated_at",
        (
            component,
            salt,
            kdf_iterations,
            int(enabled),
            seed_source,
            now,
            now,
        ),
    )
    await conn.commit()
    meta = await read_key_meta(conn, component)
    assert meta is not None
    return meta


async def disable_encryption(
    conn: aiosqlite.Connection, component: str = GLOBAL_COMPONENT
) -> None:
    """Flip the ``enabled`` flag off without deleting the salt.

    Used by tests and rollback flows; production code should re-encrypt with a
    new salt rather than disabling field encryption mid-flight.
    """
    await conn.execute(
        "UPDATE key_meta SET enabled=0, updated_at=? WHERE component=?",
        (_utcnow_iso(), component),
    )
    await conn.commit()


__all__ = [
    "GLOBAL_COMPONENT",
    "KeyMeta",
    "disable_encryption",
    "read_key_meta",
    "write_key_meta",
]
