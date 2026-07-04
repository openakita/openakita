"""KeyRotationService — v0.3 Part Infra §2.5 key rotation.

The M1 W2 ``KeyManager`` uses a single ``(salt, iterations)`` pair held
in the ``key_meta`` table.  v11 introduces an explicit ``key_versions``
table so the lineage of derivation salts is observable and every
rotation run is auditable through ``key_rotation_runs``.

Three observable operations:

* :meth:`list_versions` — read the version history for a component;
* :meth:`preview_rotation` — count how many rows in the encrypted
  tables would be re-encrypted by a rotation;
* :meth:`rotate_key` — full rotation flow.

The rotation flow is transactional: it derives a *new* KeyManager with
a fresh 32-byte salt (and optionally a fresh seed), walks every row in
``organizations`` / ``trial_balance_imports`` / ``trial_balance_rows``
whose ``_encrypted_payload`` is non-NULL, decrypts each blob with the
*old* KeyManager and re-encrypts it with the *new* one.  On success it
flips the previous ``key_versions`` row to ``status='retired'`` and
points ``key_meta.global`` at the new salt; on error it rolls back the
SQLite transaction and restores the previous salt + version state so
the database remains consistent.

The service intentionally stays single-component for M3 — the v0.3
per-org KeyManager refactor is tracked separately in
``docs/follow-ups/skipped-items-roadmap.md``.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ..encryption import pack_payload, unpack_payload
from ..key_manager import (
    PBKDF2_ITERATIONS,
    SALT_LEN,
    KeyManager,
    acquire_seed,
)
from ..key_meta import GLOBAL_COMPONENT, read_key_meta, write_key_meta

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Tables whose ``_encrypted_payload`` BLOB column is rotated by this service.
# Order matters only for monotone progress counters; the rotation itself is
# wrapped in a single SQLite transaction so partial failure rolls back.
_ENCRYPTED_TABLES: tuple[str, ...] = (
    "organizations",
    "trial_balance_imports",
    "trial_balance_rows",
)

# Tables whose encrypted payload is embedded inside a JSON column instead of
# living in a dedicated ``_encrypted_payload`` BLOB.  ``parse_issues`` is the
# only such table today: the route layer packs amounts/PII into a hex blob
# under the ``__enc_blob__`` key of ``original_data`` (see
# ``parse_issue_routes._persist_detected_issues``).  Rotation must walk these
# rows separately because the regular ``_encrypted_payload`` scan misses
# them — that gap was the P1-D finding in the audit report.
_EMBEDDED_BLOB_TABLES: tuple[tuple[str, str, str], ...] = (
    # (table_name, primary_key_col, json_column_with_blob)
    ("parse_issues", "id", "original_data"),
)

# JSON key inside the embedded column that holds the hex-encoded encrypted
# blob.  Must match ``parse_issue_routes`` / ``_decode_original_data``.
_EMBEDDED_BLOB_KEY = "__enc_blob__"

_PROGRESS_FLUSH_EVERY = 200  # rows; controls how often we update rows_processed.


class KeyRotationError(RuntimeError):
    """Raised when a rotation cannot proceed (e.g. encryption not enabled)."""


class KeyRotationService:
    """Single-component key rotation orchestrator."""

    def __init__(self, service: Any):
        # ``service`` is a ``FinanceAutoService``; we only need ``.db`` and
        # ``.key_manager`` so we accept ``Any`` to avoid a hard import cycle.
        self.service = service
        self.db = service.db
        # Per-test hook: when set, the rotation flow uses this manager
        # instead of the live ``service.key_manager``.  Used by the
        # acceptance script to inject a deliberate-failure mock.
        self._encrypt_override: KeyManager | None = None

    # ------------------------------------------------------------------ read

    async def list_versions(self, component: str = GLOBAL_COMPONENT) -> list[dict]:
        """Return every ``key_versions`` row for ``component`` ordered by
        ``key_version`` ascending.  Sentinel ``__migration_marker__`` rows
        are filtered out for caller convenience."""
        conn = self.db.conn
        async with conn.execute(
            "SELECT id, component, key_version, kdf_iterations, status, "
            "rotated_from, rotated_at, rotated_by, rotation_reason, "
            "version, created_at, length(sample_canary_ct) AS canary_len "
            "FROM key_versions WHERE component=? "
            "ORDER BY key_version ASC",
            (component,),
        ) as cur:
            rows = await cur.fetchall()
        out: list[dict] = []
        for row in rows:
            d = {
                "id": row["id"],
                "component": row["component"],
                "key_version": row["key_version"],
                "kdf_iterations": row["kdf_iterations"],
                "status": row["status"],
                "rotated_from": row["rotated_from"],
                "rotated_at": row["rotated_at"],
                "rotated_by": row["rotated_by"],
                "rotation_reason": row["rotation_reason"],
                "version": row["version"],
                "created_at": row["created_at"],
                "canary_len": row["canary_len"],
            }
            out.append(d)
        return out

    async def list_runs(
        self, component: str = GLOBAL_COMPONENT, limit: int = 50
    ) -> list[dict]:
        """Return the most recent ``key_rotation_runs`` rows for component."""
        conn = self.db.conn
        async with conn.execute(
            "SELECT id, component, from_version, to_version, status, "
            "started_at, completed_at, rows_processed, total_rows, "
            "error_message, version FROM key_rotation_runs "
            "WHERE component=? ORDER BY started_at DESC LIMIT ?",
            (component, int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "component": r["component"],
                "from_version": r["from_version"],
                "to_version": r["to_version"],
                "status": r["status"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "rows_processed": r["rows_processed"],
                "total_rows": r["total_rows"],
                "error_message": r["error_message"],
                "version": r["version"],
            }
            for r in rows
        ]

    async def preview_rotation(self, component: str = GLOBAL_COMPONENT) -> dict:
        """Return ``{table: count}`` of rows that would be re-encrypted.

        Reads ``key_meta.<component>`` to surface the current ``key_version``
        and salt-source hint so callers can decide whether a rotation is
        worth running.
        """
        conn = self.db.conn
        meta = await read_key_meta(conn, component)
        counts: dict[str, int] = {}
        total = 0
        for table in _ENCRYPTED_TABLES:
            try:
                async with conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "
                    "WHERE _encrypted_payload IS NOT NULL"
                ) as cur:
                    row = await cur.fetchone()
                n = int(row["n"]) if row else 0
            except aiosqlite.OperationalError:
                n = 0
            counts[table] = n
            total += n
        # Embedded ``__enc_blob__`` rows live inside a JSON column; SQLite has
        # no easy index on substring content so we fall back to ``LIKE``.
        for table, _pk, json_col in _EMBEDDED_BLOB_TABLES:
            try:
                async with conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "
                    f"WHERE {json_col} LIKE '%\"__enc_blob__\"%'"
                ) as cur:
                    row = await cur.fetchone()
                n = int(row["n"]) if row else 0
            except aiosqlite.OperationalError:
                n = 0
            counts[f"{table}.{json_col}"] = n
            total += n
        current_version_row = await self._find_active_version(component)
        return {
            "component": component,
            "encryption_enabled": bool(meta and meta.enabled),
            "current_key_version": (
                current_version_row["key_version"]
                if current_version_row
                else (1 if meta and meta.enabled else 0)
            ),
            "current_seed_source": meta.seed_source if meta else None,
            "counts": counts,
            "total_rows": total,
        }

    # ----------------------------------------------------------------- write

    async def rotate_key(
        self,
        *,
        component: str = GLOBAL_COMPONENT,
        new_seed: bytes | None = None,
        reason: str = "",
        rotated_by: str = "local",
    ) -> dict:
        """Perform a full rotation; return a summary dict.

        Workflow:

        1. Read the active ``key_versions`` row (insert v1 lazily from
           ``key_meta`` if no rows exist yet).
        2. Generate a fresh 32-byte salt.  ``new_seed`` defaults to the
           existing seed loaded via :func:`acquire_seed` so the worker box
           can rotate without prompting the operator.
        3. Insert the new ``key_versions`` row with ``key_version =
           prev + 1`` and an AES-GCM canary ciphertext for verification.
        4. Insert a ``key_rotation_runs`` row with ``status='running'``.
        5. Walk every encrypted table, decrypt with the OLD KeyManager
           and re-encrypt with the NEW one.  Periodically flush
           ``rows_processed`` so the admin UI can render progress.
        6. On success: update ``key_meta`` to point at the new salt,
           retire the previous version, mark the run ``success``.
        7. On error: rollback the SQLite transaction and update the
           ``key_rotation_runs`` row to ``status='failed'`` with the
           error message; the previous salt + version stay active.
        """
        conn = self.db.conn
        meta = await read_key_meta(conn, component)
        if meta is None or not meta.enabled:
            raise KeyRotationError(
                f"encryption is not enabled for component '{component}'; "
                "call enable_encryption first."
            )

        old_seed: bytes | None = None
        seed_source = "existing"
        if new_seed is None:
            try:
                old_seed, seed_source = acquire_seed(create_if_missing=False)
            except Exception as exc:  # noqa: BLE001
                raise KeyRotationError(
                    "could not load existing seed; rotation aborted: "
                    f"{exc!r}"
                ) from exc
            seed_for_new = old_seed
        else:
            try:
                old_seed, _src = acquire_seed(create_if_missing=False)
            except Exception as exc:  # noqa: BLE001
                raise KeyRotationError(
                    "could not load existing seed for old key; rotation aborted: "
                    f"{exc!r}"
                ) from exc
            seed_for_new = new_seed
            seed_source = "provided"

        # Build the OLD KeyManager from the on-disk salt.
        old_km = KeyManager()
        old_km.unlock(old_seed, meta.salt)

        # Generate the new salt + new KeyManager.
        new_salt = secrets.token_bytes(SALT_LEN)
        new_km = KeyManager()
        new_km.unlock(seed_for_new, new_salt)

        # Compute the canary ciphertext under the new key — both for
        # ``key_versions.sample_canary_ct`` and for the "decrypt round-trip"
        # smoke test below.
        canary_pt = b"canary"
        canary_ct = new_km.encrypt("pii", canary_pt)
        try:
            assert new_km.decrypt("pii", canary_ct) == canary_pt
        except AssertionError as exc:  # pragma: no cover — defensive
            raise KeyRotationError(
                "new KeyManager failed self-canary round-trip"
            ) from exc

        # Ensure the v1 row exists (lazy materialisation from key_meta).
        prev_version_row = await self._ensure_version_row(
            component, meta.salt, meta.kdf_iterations, meta.seed_source
        )
        prev_version = int(prev_version_row["key_version"])
        new_version_num = prev_version + 1

        # Insert the new key_versions row.
        await conn.execute(
            "INSERT INTO key_versions("
            "component, key_version, kdf_salt, kdf_iterations, status, "
            "rotated_from, rotated_at, rotated_by, rotation_reason, "
            "sample_canary_ct, version, created_at) VALUES "
            "(?,?,?,?,'active',?,?,?,?,?,1,?)",
            (
                component,
                new_version_num,
                new_salt,
                PBKDF2_ITERATIONS,
                prev_version_row["id"],
                _utcnow_iso(),
                rotated_by,
                reason,
                canary_ct,
                _utcnow_iso(),
            ),
        )
        # Insert the run row (status='running').
        cur = await conn.execute(
            "INSERT INTO key_rotation_runs("
            "component, from_version, to_version, status, started_at, "
            "rows_processed, total_rows, version) "
            "VALUES (?,?,?,'running',?,0,0,1)",
            (component, prev_version, new_version_num, _utcnow_iso()),
        )
        run_id = cur.lastrowid
        await conn.commit()

        # Count total rows we expect to touch (best-effort).
        total_rows = 0
        for table in _ENCRYPTED_TABLES:
            try:
                async with conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "
                    "WHERE _encrypted_payload IS NOT NULL"
                ) as c:
                    r = await c.fetchone()
                total_rows += int(r["n"]) if r else 0
            except aiosqlite.OperationalError:
                continue
        for table, _pk, json_col in _EMBEDDED_BLOB_TABLES:
            try:
                async with conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "
                    f"WHERE {json_col} LIKE '%\"__enc_blob__\"%'"
                ) as c:
                    r = await c.fetchone()
                total_rows += int(r["n"]) if r else 0
            except aiosqlite.OperationalError:
                continue
        await conn.execute(
            "UPDATE key_rotation_runs SET total_rows=? WHERE id=?",
            (total_rows, run_id),
        )
        await conn.commit()

        # Re-encrypt walk wrapped in a transaction so a mid-flight error
        # rolls back every row write.
        rows_processed = 0
        try:
            await conn.execute("BEGIN")
            for table in _ENCRYPTED_TABLES:
                rows_processed = await self._reencrypt_table(
                    table, old_km, new_km, run_id, rows_processed
                )
            for table, pk_col, json_col in _EMBEDDED_BLOB_TABLES:
                rows_processed = await self._reencrypt_embedded_blob(
                    table, pk_col, json_col, old_km, new_km, run_id, rows_processed
                )
            # Flip the previous version to retired + commit.
            await conn.execute(
                "UPDATE key_versions SET status='retired', "
                "version=version+1 WHERE id=?",
                (prev_version_row["id"],),
            )
            # Point key_meta at the new salt.  We reuse write_key_meta which
            # does an upsert preserving seed_source unless overridden.
            await write_key_meta(
                conn,
                salt=new_salt,
                enabled=True,
                seed_source=seed_source,
                component=component,
                kdf_iterations=PBKDF2_ITERATIONS,
            )
            # Swap the live KeyManager.
            self.service.key_manager.lock()
            self.service.key_manager.unlock(seed_for_new, new_salt)
            await conn.execute(
                "UPDATE key_rotation_runs SET status='success', "
                "completed_at=?, rows_processed=?, version=version+1 "
                "WHERE id=?",
                (_utcnow_iso(), rows_processed, run_id),
            )
            await conn.commit()
            old_km.lock()
            return {
                "run_id": run_id,
                "component": component,
                "from_version": prev_version,
                "to_version": new_version_num,
                "rows_processed": rows_processed,
                "total_rows": total_rows,
                "status": "success",
                "reason": reason,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("finance-auto: key rotation failed: %s", exc)
            try:
                await conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            # Outside the rollback, mark the failure + retire the brand-new
            # version row (it's safe to keep around for audit but its salt
            # is no longer pointed at by key_meta).
            try:
                await conn.execute(
                    "UPDATE key_versions SET status='retired', "
                    "version=version+1 WHERE component=? AND key_version=?",
                    (component, new_version_num),
                )
                await conn.execute(
                    "UPDATE key_rotation_runs SET status='failed', "
                    "completed_at=?, error_message=?, rows_processed=?, "
                    "version=version+1 WHERE id=?",
                    (
                        _utcnow_iso(),
                        f"{type(exc).__name__}: {exc!s}"[:500],
                        rows_processed,
                        run_id,
                    ),
                )
                await conn.commit()
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.warning(
                    "finance-auto: rotation cleanup failed: %s", cleanup_exc
                )
            old_km.lock()
            new_km.lock()
            return {
                "run_id": run_id,
                "component": component,
                "from_version": prev_version,
                "to_version": new_version_num,
                "rows_processed": rows_processed,
                "total_rows": total_rows,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc!s}",
                "reason": reason,
            }

    # ----------------------------------------------------------- internals

    async def _find_active_version(
        self, component: str
    ) -> aiosqlite.Row | None:
        """Return the active ``key_versions`` row for ``component`` or None.

        Skips sentinel ``__migration_marker__`` entries that the v11 SEED
        inserts as a no-op chain marker.
        """
        conn = self.db.conn
        async with conn.execute(
            "SELECT * FROM key_versions WHERE component=? "
            "AND status='active' "
            "ORDER BY key_version DESC LIMIT 1",
            (component,),
        ) as cur:
            return await cur.fetchone()

    async def _ensure_version_row(
        self,
        component: str,
        salt: bytes,
        kdf_iterations: int,
        seed_source: str | None,
    ) -> aiosqlite.Row:
        """Return the active ``key_versions`` row; create a v1 row from the
        existing key_meta entry if no active row exists yet."""
        conn = self.db.conn
        row = await self._find_active_version(component)
        if row is not None:
            return row
        await conn.execute(
            "INSERT INTO key_versions("
            "component, key_version, kdf_salt, kdf_iterations, status, "
            "rotation_reason, version, created_at) VALUES "
            "(?,1,?,?,'active',?,1,?)",
            (
                component,
                bytes(salt),
                int(kdf_iterations),
                f"initial v1 row materialised from key_meta (seed_source="
                f"{seed_source or 'unknown'})",
                _utcnow_iso(),
            ),
        )
        await conn.commit()
        row = await self._find_active_version(component)
        assert row is not None, "v1 key_versions row missing after insert"
        return row

    async def _reencrypt_table(
        self,
        table: str,
        old_km: KeyManager,
        new_km: KeyManager,
        run_id: int,
        rows_processed: int,
    ) -> int:
        """Re-encrypt every ``_encrypted_payload`` blob in ``table``."""
        conn = self.db.conn
        try:
            async with conn.execute(
                f"SELECT id, _encrypted_payload FROM {table} "
                "WHERE _encrypted_payload IS NOT NULL"
            ) as cur:
                ids_blobs = await cur.fetchall()
        except aiosqlite.OperationalError as exc:
            logger.warning(
                "finance-auto: skipping rotation for table %s: %s", table, exc
            )
            return rows_processed

        last_flush = time.time()
        for row in ids_blobs:
            rid = row["id"]
            blob = row["_encrypted_payload"]
            if not blob:
                continue
            payload = unpack_payload(old_km, bytes(blob))
            new_blob = pack_payload(
                new_km,
                amounts=payload.get("amounts") or None,
                pii=payload.get("pii") or None,
                docrefs=payload.get("docrefs") or None,
            )
            await conn.execute(
                f"UPDATE {table} SET _encrypted_payload=? WHERE id=?",
                (new_blob, rid),
            )
            rows_processed += 1
            if (
                rows_processed % _PROGRESS_FLUSH_EVERY == 0
                or (time.time() - last_flush) > 1.0
            ):
                await conn.execute(
                    "UPDATE key_rotation_runs SET rows_processed=? WHERE id=?",
                    (rows_processed, run_id),
                )
                last_flush = time.time()
        return rows_processed


    async def _reencrypt_embedded_blob(
        self,
        table: str,
        pk_col: str,
        json_col: str,
        old_km: KeyManager,
        new_km: KeyManager,
        run_id: int,
        rows_processed: int,
    ) -> int:
        """Re-encrypt rows whose payload is embedded inside a JSON column.

        Used by ``parse_issues`` where ``original_data`` carries a hex-encoded
        ``__enc_blob__`` value containing the AES-GCM ciphertext of the PII
        and amount sub-fields.  Without this walk the rotation succeeds at
        the table level but leaves these embedded blobs encrypted under the
        OLD key, making subsequent reads fail (the audit's P1-D finding).

        Skipped tables / malformed JSON are logged at WARNING but do not
        abort the rotation transaction (the embedded blob is a "best effort"
        side-channel — the canonical ``_encrypted_payload`` columns are what
        the rest of the system relies on).
        """
        conn = self.db.conn
        try:
            async with conn.execute(
                f"SELECT {pk_col} AS pk, {json_col} AS payload FROM {table} "
                f"WHERE {json_col} LIKE '%\"{_EMBEDDED_BLOB_KEY}\"%'"
            ) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError as exc:
            logger.warning(
                "finance-auto: skipping rotation for embedded-blob table "
                "%s.%s: %s",
                table, json_col, exc,
            )
            return rows_processed

        last_flush = time.time()
        for row in rows:
            pk = row["pk"]
            raw = row["payload"]
            if not raw:
                continue
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "finance-auto: %s.%s pk=%r holds non-JSON payload; "
                    "skipping rotation for this row: %s",
                    table, json_col, pk, exc,
                )
                continue
            if not isinstance(doc, dict):
                continue
            blob_hex = doc.get(_EMBEDDED_BLOB_KEY)
            if not blob_hex:
                continue
            try:
                old_blob = bytes.fromhex(blob_hex)
            except ValueError as exc:
                logger.warning(
                    "finance-auto: %s.%s pk=%r holds non-hex __enc_blob__; "
                    "skipping rotation for this row: %s",
                    table, json_col, pk, exc,
                )
                continue
            try:
                payload = unpack_payload(old_km, old_blob)
            except Exception as exc:  # noqa: BLE001 — best-effort isolation
                logger.warning(
                    "finance-auto: %s.%s pk=%r __enc_blob__ failed to "
                    "decrypt with old key; leaving as-is: %s",
                    table, json_col, pk, exc,
                )
                continue
            new_blob = pack_payload(
                new_km,
                amounts=payload.get("amounts") or None,
                pii=payload.get("pii") or None,
                docrefs=payload.get("docrefs") or None,
            )
            doc[_EMBEDDED_BLOB_KEY] = new_blob.hex()
            new_payload = json.dumps(doc, ensure_ascii=False, default=str)
            await conn.execute(
                f"UPDATE {table} SET {json_col}=? WHERE {pk_col}=?",
                (new_payload, pk),
            )
            rows_processed += 1
            if (
                rows_processed % _PROGRESS_FLUSH_EVERY == 0
                or (time.time() - last_flush) > 1.0
            ):
                await conn.execute(
                    "UPDATE key_rotation_runs SET rows_processed=? WHERE id=?",
                    (rows_processed, run_id),
                )
                last_flush = time.time()
        return rows_processed


__all__ = [
    "KeyRotationError",
    "KeyRotationService",
]
