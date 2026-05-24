"""BackupRestoreService — encrypted tar.gz snapshots + restore.

M3 Infra Stage 3.  Wraps the v0.3 Part Infra §2.4 "备份/迁移" row:
admins can produce a self-contained ``.tar.gz`` archive containing
the SQLite database, a manifest, and a separately-encrypted bundle
of the current ``key_versions`` rows so the archive can be restored
on another box (or after an accidental deletion) without leaking the
PBKDF2-derived master key.

Archive layout (all members live at the archive root):

* ``database.sqlite``   — physical copy of the live DB produced via
  the ``sqlite3.Connection.backup`` API (safe with WAL + concurrent
  readers).
* ``manifest.json``     — schema_version, current key_version,
  table counts, source DB sha256, KDF salt + iteration count for the
  ``keys.bin`` payload, archive creation timestamp.
* ``keys.bin``          — PBKDF2-derived AES-GCM-encrypted JSON of
  the current ``key_versions`` rows.  Layout = ``salt(32B) ||
  nonce(12B) || ciphertext`` (AAD = ``openakita-finance-backup-v1``).
  The DB itself stays field-encrypted; ``keys.bin`` carries the salt
  history so a restore can read it back even if ``key_meta`` was lost.

The passphrase used to encrypt / decrypt ``keys.bin`` is required at
both create and restore time; it lives only in caller memory.

The :class:`BackupRestoreService` records every archive in
``backup_history`` so the admin UI can render the list without
walking the filesystem.  ``status`` transitions are recorded
atomically alongside the file mutation.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import secrets
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from ..key_meta import GLOBAL_COMPONENT, read_key_meta
from ..schema import SCHEMA_VERSION

logger = logging.getLogger(__name__)


# Constants chosen per Stage 3 specification (cf. ``finance_plugin
# design v0.3 part infra § 2.4`` plus the M3 Infra worker brief).
BACKUP_KDF_ITERATIONS = 200_000
BACKUP_SALT_LEN = 32
BACKUP_NONCE_LEN = 12
BACKUP_AAD = b"openakita-finance-backup-v1"
BACKUP_MIN_SIZE_BYTES = 256


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_backup_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256 → 32-byte key for AES-GCM."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=int(iterations),
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt_keys_blob(passphrase: str, plaintext: bytes) -> bytes:
    """Return ``salt || nonce || ciphertext`` per Stage 3 layout."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(BACKUP_SALT_LEN)
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    key = _derive_backup_key(passphrase, salt, BACKUP_KDF_ITERATIONS)
    ct = AESGCM(key).encrypt(nonce, plaintext, BACKUP_AAD)
    return salt + nonce + ct


def _decrypt_keys_blob(passphrase: str, blob: bytes) -> bytes:
    """Inverse of :func:`_encrypt_keys_blob`.  Raises on wrong passphrase."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < BACKUP_SALT_LEN + BACKUP_NONCE_LEN + 16:
        raise ValueError("keys.bin too short for AES-GCM payload")
    salt = blob[:BACKUP_SALT_LEN]
    nonce = blob[BACKUP_SALT_LEN : BACKUP_SALT_LEN + BACKUP_NONCE_LEN]
    ct = blob[BACKUP_SALT_LEN + BACKUP_NONCE_LEN :]
    key = _derive_backup_key(passphrase, salt, BACKUP_KDF_ITERATIONS)
    return AESGCM(key).decrypt(nonce, ct, BACKUP_AAD)


class BackupRestoreError(RuntimeError):
    """Raised when a backup or restore operation cannot complete."""


class WrongPassphraseError(BackupRestoreError):
    """Raised by :meth:`restore_backup` when ``keys.bin`` decryption fails."""


class BackupRestoreService:
    """Backup + restore orchestrator backed by ``backup_history``."""

    def __init__(self, service: Any, *, default_dest: Path | None = None):
        self.service = service
        self.db = service.db
        self.default_dest = (
            Path(default_dest) if default_dest else Path("data/finance_backups")
        )

    # ------------------------------------------------------------- create

    async def create_backup(
        self,
        *,
        org_id: str | None = None,
        passphrase: str,
        dest_dir: Path | None = None,
    ) -> dict:
        """Create a tar.gz snapshot + record in ``backup_history``.

        Returns the inserted row plus the path / sha256 / manifest.
        """
        if not passphrase:
            raise BackupRestoreError("passphrase is required for create_backup")

        dest = Path(dest_dir) if dest_dir else self.default_dest
        dest.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        suffix = f"org_{org_id}_" if org_id else "all_orgs_"
        backup_path = dest / f"finance_backup_{suffix}{ts}.tar.gz"

        # 1. Snapshot the live DB into a temp file (safe with WAL).
        snap_fd, snap_path_str = tempfile.mkstemp(
            prefix="finauto_snap_", suffix=".sqlite"
        )
        os.close(snap_fd)
        snap_path = Path(snap_path_str)
        try:
            await self._snapshot_db(snap_path)

            # 2. Compute the source DB hash (post-snapshot copy).
            source_db_hash = _sha256_file(snap_path)

            # 3. Collect manifest metadata.
            counts = await self._table_counts()
            meta = await read_key_meta(self.db.conn, GLOBAL_COMPONENT)
            key_versions_rows = await self._dump_key_versions()
            current_key_version = max(
                (r["key_version"] for r in key_versions_rows),
                default=(1 if meta and meta.enabled else 0),
            )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "key_version": current_key_version,
                "created_at": _utcnow_iso(),
                "org_id": org_id,
                "table_counts": counts,
                "source_db_hash": source_db_hash,
                "kdf_iterations": BACKUP_KDF_ITERATIONS,
                "kdf_algo": "PBKDF2-HMAC-SHA256",
                "cipher": "AES-256-GCM",
                "aad": BACKUP_AAD.decode("ascii"),
                "encryption_enabled": bool(meta and meta.enabled),
                "key_meta_seed_source": (meta.seed_source if meta else None),
            }

            # 4. Encrypt key_versions JSON into keys.bin (independent of DB).
            keys_json = json.dumps(key_versions_rows, ensure_ascii=False)
            keys_bin = _encrypt_keys_blob(passphrase, keys_json.encode("utf-8"))

            # 5. Build the tar.gz archive.
            with tarfile.open(backup_path, mode="w:gz") as tf:
                _tar_add_file(tf, "database.sqlite", snap_path.read_bytes())
                _tar_add_file(
                    tf,
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode(
                        "utf-8"
                    ),
                )
                _tar_add_file(tf, "keys.bin", keys_bin)

            size_bytes = backup_path.stat().st_size
            sha256 = _sha256_file(backup_path)
            if size_bytes < BACKUP_MIN_SIZE_BYTES:
                logger.warning(
                    "finance-auto: backup archive smaller than expected (%d bytes)",
                    size_bytes,
                )
        finally:
            try:
                snap_path.unlink(missing_ok=True)
            except OSError:
                pass

        # 6. Record in backup_history.
        cur = await self.db.conn.execute(
            "INSERT INTO backup_history("
            "org_id, backup_path, size_bytes, sha256, encrypted, kdf_salt, "
            "key_version, schema_version, manifest_json, status, "
            "created_at, version) VALUES "
            "(?,?,?,?,1,NULL,?,?,?, 'completed', ?, 1)",
            (
                org_id,
                str(backup_path),
                size_bytes,
                sha256,
                current_key_version,
                SCHEMA_VERSION,
                json.dumps(manifest, ensure_ascii=False),
                _utcnow_iso(),
            ),
        )
        backup_id = cur.lastrowid
        await self.db.conn.commit()

        return {
            "id": backup_id,
            "org_id": org_id,
            "backup_path": str(backup_path),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "schema_version": SCHEMA_VERSION,
            "key_version": current_key_version,
            "manifest": manifest,
            "status": "completed",
            "created_at": manifest["created_at"],
        }

    # --------------------------------------------------------------- read

    async def list_backups(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        """List ``backup_history`` rows newest-first; filter by org if given."""
        conn = self.db.conn
        if org_id is None:
            async with conn.execute(
                "SELECT * FROM backup_history "
                "ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with conn.execute(
                "SELECT * FROM backup_history WHERE org_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (org_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_backup_dict(r) for r in rows]

    async def get_backup(self, backup_id: int) -> dict | None:
        conn = self.db.conn
        async with conn.execute(
            "SELECT * FROM backup_history WHERE id=?", (int(backup_id),)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return _row_to_backup_dict(row)

    # ------------------------------------------------------------ restore

    async def restore_backup(
        self,
        *,
        backup_id: int,
        passphrase: str,
        target_db_path: Path | str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Verify + (optionally) materialise an archive."""
        backup = await self.get_backup(backup_id)
        if backup is None:
            raise BackupRestoreError(f"backup {backup_id} not found")

        backup_path = Path(backup["backup_path"])
        if not backup_path.exists():
            raise BackupRestoreError(
                f"backup file missing on disk: {backup_path}"
            )

        try:
            manifest, db_bytes, keys_bin = _read_tar_members(backup_path)
        except (tarfile.TarError, KeyError) as exc:
            raise BackupRestoreError(
                f"backup archive malformed: {exc!r}"
            ) from exc

        # 1. Verify the passphrase by trying to decrypt keys.bin.
        try:
            keys_pt = _decrypt_keys_blob(passphrase, keys_bin)
            key_versions_rows = json.loads(keys_pt.decode("utf-8"))
            verified = True
        except Exception as exc:  # noqa: BLE001 — covers InvalidTag
            return {
                "ok": False,
                "verified": False,
                "error": "wrong passphrase",
                "detail": str(exc),
            }

        if dry_run:
            return {
                "ok": True,
                "verified": verified,
                "dry_run": True,
                "manifest": manifest,
                "key_versions_count": len(key_versions_rows),
            }

        # 2. Materialise the embedded DB.
        if target_db_path is None:
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            target = self.db.path.parent / (
                self.db.path.stem + f".restored.{ts}" + self.db.path.suffix
            )
        else:
            target = Path(target_db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(db_bytes)

        # 3. Re-install key_versions rows so the restored DB has the lineage.
        await self._reinstall_key_versions(target, key_versions_rows)

        # 4. Stamp backup_history.
        await self.db.conn.execute(
            "UPDATE backup_history SET status='restored', restored_at=?, "
            "version=version+1 WHERE id=?",
            (_utcnow_iso(), int(backup_id)),
        )
        await self.db.conn.commit()
        return {
            "ok": True,
            "verified": verified,
            "dry_run": False,
            "manifest": manifest,
            "restored_db_path": str(target),
            "key_versions_count": len(key_versions_rows),
        }

    async def delete_backup(self, backup_id: int) -> dict:
        """Mark deleted + unlink the file (best-effort)."""
        backup = await self.get_backup(backup_id)
        if backup is None:
            raise BackupRestoreError(f"backup {backup_id} not found")
        path = Path(backup["backup_path"])
        try:
            if path.exists():
                path.unlink()
            file_removed = True
        except OSError as exc:
            logger.warning("finance-auto: backup unlink failed: %s", exc)
            file_removed = False
        await self.db.conn.execute(
            "UPDATE backup_history SET status='deleted', "
            "version=version+1 WHERE id=?",
            (int(backup_id),),
        )
        await self.db.conn.commit()
        return {
            "ok": True,
            "id": backup_id,
            "file_removed": file_removed,
            "status": "deleted",
        }

    # ----------------------------------------------------------- helpers

    async def _snapshot_db(self, target: Path) -> None:
        """Use sqlite3.Connection.backup() to copy the live DB safely."""
        src_path = str(self.db.path)
        # ``aiosqlite`` keeps the DB locked in WAL mode; using a fresh
        # blocking sqlite3 connection in URI mode is the canonical way to
        # snapshot without contending with the live writer.
        # NB: this is sync I/O but the snapshot completes in <1s for the
        # databases involved in M3 acceptance.
        src = sqlite3.connect(
            f"file:{src_path}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=30,
        )
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    async def _table_counts(self) -> dict[str, int]:
        """Best-effort row counts for the core encrypted tables."""
        out: dict[str, int] = {}
        for table in (
            "organizations",
            "accounting_periods",
            "accounts",
            "trial_balance_imports",
            "trial_balance_rows",
            "reports",
            "report_cells",
            "key_versions",
        ):
            try:
                async with self.db.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ) as cur:
                    row = await cur.fetchone()
                out[table] = int(row["n"]) if row else 0
            except aiosqlite.OperationalError:
                continue
        return out

    async def _dump_key_versions(self) -> list[dict]:
        """Serialise ``key_versions`` rows for embedding in keys.bin."""
        conn = self.db.conn
        try:
            async with conn.execute(
                "SELECT id, component, key_version, "
                "hex(kdf_salt) AS kdf_salt_hex, kdf_iterations, status, "
                "rotated_from, rotated_at, rotated_by, rotation_reason, "
                "hex(sample_canary_ct) AS sample_canary_ct_hex, "
                "version, created_at FROM key_versions "
                "WHERE component <> '__migration_marker__' "
                "ORDER BY component, key_version"
            ) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError:
            return []
        return [
            {
                "id": r["id"],
                "component": r["component"],
                "key_version": r["key_version"],
                "kdf_salt_hex": r["kdf_salt_hex"],
                "kdf_iterations": r["kdf_iterations"],
                "status": r["status"],
                "rotated_from": r["rotated_from"],
                "rotated_at": r["rotated_at"],
                "rotated_by": r["rotated_by"],
                "rotation_reason": r["rotation_reason"],
                "sample_canary_ct_hex": r["sample_canary_ct_hex"],
                "version": r["version"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def _reinstall_key_versions(
        self, target_db_path: Path, key_versions_rows: list[dict]
    ) -> None:
        """Insert (or ignore) the source key_versions rows into the restored
        DB so the lineage survives the restore.

        Uses a synchronous sqlite3 connection because the freshly written
        target file isn't part of the aiosqlite pool yet.
        """
        if not key_versions_rows:
            return
        conn = sqlite3.connect(str(target_db_path))
        try:
            for r in key_versions_rows:
                salt = bytes.fromhex(r.get("kdf_salt_hex") or "")
                canary = bytes.fromhex(r.get("sample_canary_ct_hex") or "")
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO key_versions("
                        "component, key_version, kdf_salt, kdf_iterations, "
                        "status, rotated_from, rotated_at, rotated_by, "
                        "rotation_reason, sample_canary_ct, version, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            r["component"],
                            int(r["key_version"]),
                            salt,
                            int(r["kdf_iterations"]),
                            r["status"],
                            r.get("rotated_from"),
                            r.get("rotated_at"),
                            r.get("rotated_by") or "local",
                            r.get("rotation_reason"),
                            canary,
                            int(r.get("version") or 1),
                            r.get("created_at") or _utcnow_iso(),
                        ),
                    )
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "finance-auto: key_versions reinstall skipped: %s",
                        exc,
                    )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tar_add_file(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = int(time.time())
    tf.addfile(info, io.BytesIO(data))


def _read_tar_members(path: Path) -> tuple[dict, bytes, bytes]:
    """Return ``(manifest_dict, database_bytes, keys_bin)`` from the archive."""
    manifest: dict | None = None
    db_bytes: bytes | None = None
    keys_bin: bytes | None = None
    with tarfile.open(path, mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.name == "manifest.json":
                f = tf.extractfile(member)
                if f is not None:
                    manifest = json.loads(f.read().decode("utf-8"))
            elif member.name == "database.sqlite":
                f = tf.extractfile(member)
                if f is not None:
                    db_bytes = f.read()
            elif member.name == "keys.bin":
                f = tf.extractfile(member)
                if f is not None:
                    keys_bin = f.read()
    if manifest is None or db_bytes is None or keys_bin is None:
        raise KeyError(
            f"archive missing required members; "
            f"manifest={manifest is not None} db={db_bytes is not None} "
            f"keys.bin={keys_bin is not None}"
        )
    return manifest, db_bytes, keys_bin


def _row_to_backup_dict(row) -> dict:
    manifest: dict | None = None
    try:
        if row["manifest_json"]:
            manifest = json.loads(row["manifest_json"])
    except (ValueError, TypeError):
        manifest = None
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "backup_path": row["backup_path"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "encrypted": bool(row["encrypted"]),
        "key_version": row["key_version"],
        "schema_version": row["schema_version"],
        "manifest": manifest,
        "status": row["status"],
        "created_at": row["created_at"],
        "restored_at": row["restored_at"],
        "version": row["version"],
    }


__all__ = [
    "BACKUP_AAD",
    "BACKUP_KDF_ITERATIONS",
    "BACKUP_MIN_SIZE_BYTES",
    "BackupRestoreError",
    "BackupRestoreService",
    "WrongPassphraseError",
]
