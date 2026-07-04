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
from fastapi import HTTPException

from ..key_meta import GLOBAL_COMPONENT, read_key_meta
from ..schema import SCHEMA_VERSION

logger = logging.getLogger(__name__)


# Constants chosen per Stage 3 specification (cf. ``finance_plugin
# design v0.3 part infra § 2.4`` plus the M3 Infra worker brief).
#
# Extended-audit EX-P1-3 / EX-P2-2 (2026-05): bumped default PBKDF2
# iteration count from 200_000 to 600_000 per OWASP 2023 minimum for
# PBKDF2-HMAC-SHA256.  The actual iteration count used by each archive
# is recorded in ``manifest.json["kdf_iterations"]`` so older backups
# created with 200k can still be decrypted by reading the manifest
# value back; the constant is only consulted when writing a fresh
# archive.  An override env var ``OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS``
# allows lower values in dev / CI without code changes.
BACKUP_DEFAULT_KDF_ITERATIONS = 600_000
BACKUP_KDF_ITERATIONS_ENV = "OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS"


def _resolve_kdf_iterations() -> int:
    """Return the iteration count to use when *creating* a new archive.

    Reads ``OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS`` first (truthy int
    only); falls back to :data:`BACKUP_DEFAULT_KDF_ITERATIONS`.  Values
    below 100_000 are rejected to prevent foot-guns; ``decrypt`` paths
    still honour whatever is stored in the archive header for backward
    compatibility with existing 200k backups.
    """
    raw = os.environ.get(BACKUP_KDF_ITERATIONS_ENV)
    if raw:
        try:
            value = int(raw.strip())
        except ValueError:
            logger.warning(
                "finance-auto: ignoring non-integer %s=%r",
                BACKUP_KDF_ITERATIONS_ENV,
                raw,
            )
        else:
            if value < 100_000:
                logger.warning(
                    "finance-auto: %s=%d is below the 100k floor; "
                    "using default %d instead",
                    BACKUP_KDF_ITERATIONS_ENV,
                    value,
                    BACKUP_DEFAULT_KDF_ITERATIONS,
                )
            else:
                return value
    return BACKUP_DEFAULT_KDF_ITERATIONS


# Back-compat alias for any caller still expecting the old constant
# name.  Tests + external scripts can keep importing it; the value now
# reflects the OWASP-aligned default but lookups always go through
# :func:`_resolve_kdf_iterations` so env overrides win.
BACKUP_KDF_ITERATIONS = BACKUP_DEFAULT_KDF_ITERATIONS

BACKUP_SALT_LEN = 32
BACKUP_NONCE_LEN = 12
BACKUP_AAD = b"openakita-finance-backup-v1"
BACKUP_MIN_SIZE_BYTES = 256


# Sandbox root for create / restore (EX-P1-1).  Default is under the
# user's home dir; can be overridden via env var so headless installs
# (Docker / CI) can stash backups in a volume-mounted directory.
BACKUP_ROOT_ENV = "OPENAKITA_FINANCE_AUTO_BACKUP_ROOT"


def _default_backup_root() -> Path:
    raw = os.environ.get(BACKUP_ROOT_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".openakita" / "finance_auto" / "backups"


def _is_within(path: Path, root: Path) -> bool:
    """Return True iff ``path`` (after resolve()) is rooted under ``root``.

    Uses :meth:`Path.is_relative_to` which is the canonical 3.9+
    helper.  ``resolve(strict=False)`` flattens ``..`` traversal and
    symlinks so a request like ``backups/../../etc/passwd`` is caught
    before any I/O happens.
    """
    try:
        resolved = path.resolve(strict=False)
        anchor = root.resolve(strict=False)
        return resolved == anchor or resolved.is_relative_to(anchor)
    except (OSError, ValueError):
        return False


def _ensure_within_sandbox(
    path: Path, allowed_root: Path, *, label: str
) -> Path:
    """Validate ``path`` lives under ``allowed_root`` (sandbox check).

    Raises ``HTTPException(403)`` when the resolved path escapes the
    sandbox.  The string ``label`` is purely for the error detail so
    callers can tell which input failed (e.g. ``dest_dir`` vs
    ``target_db_path``).
    """
    if not _is_within(path, allowed_root):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_outside_sandbox",
                "field": label,
                "given": str(path),
                "allowed_root": str(allowed_root.resolve(strict=False)),
            },
        )
    return path


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


def _encrypt_keys_blob(
    passphrase: str,
    plaintext: bytes,
    *,
    iterations: int | None = None,
) -> tuple[bytes, int]:
    """Return ``(salt || nonce || ciphertext, iterations_used)``.

    ``iterations`` defaults to whatever :func:`_resolve_kdf_iterations`
    decides — the caller must record the returned ``iterations_used``
    in the archive manifest so the decrypt path can replay the same
    KDF run later (backward compatibility with older 200k archives).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iterations_used = int(iterations) if iterations else _resolve_kdf_iterations()
    salt = secrets.token_bytes(BACKUP_SALT_LEN)
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    key = _derive_backup_key(passphrase, salt, iterations_used)
    ct = AESGCM(key).encrypt(nonce, plaintext, BACKUP_AAD)
    return salt + nonce + ct, iterations_used


def _decrypt_keys_blob(
    passphrase: str,
    blob: bytes,
    *,
    iterations: int,
) -> bytes:
    """Inverse of :func:`_encrypt_keys_blob`.  ``iterations`` MUST come
    from the archive manifest so we replay the exact KDF run that
    produced the ciphertext (older archives used 200k, newer use 600k).
    Raises on wrong passphrase / wrong iterations.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < BACKUP_SALT_LEN + BACKUP_NONCE_LEN + 16:
        raise ValueError("keys.bin too short for AES-GCM payload")
    salt = blob[:BACKUP_SALT_LEN]
    nonce = blob[BACKUP_SALT_LEN : BACKUP_SALT_LEN + BACKUP_NONCE_LEN]
    ct = blob[BACKUP_SALT_LEN + BACKUP_NONCE_LEN :]
    key = _derive_backup_key(passphrase, salt, int(iterations))
    return AESGCM(key).decrypt(nonce, ct, BACKUP_AAD)


class BackupRestoreError(RuntimeError):
    """Raised when a backup or restore operation cannot complete."""


class WrongPassphraseError(BackupRestoreError):
    """Raised by :meth:`restore_backup` when ``keys.bin`` decryption fails."""


class BackupRestoreService:
    """Backup + restore orchestrator backed by ``backup_history``."""

    def __init__(
        self,
        service: Any,
        *,
        default_dest: Path | None = None,
        allowed_root: Path | None = None,
    ):
        self.service = service
        self.db = service.db
        # Sandbox root for both create + restore (EX-P1-1).  When a
        # caller passes ``allowed_root`` we honour it (useful for
        # tests pointing at ``tmp_path``); otherwise we resolve from
        # env var / home-dir fallback.
        self.allowed_root: Path = (
            Path(allowed_root) if allowed_root else _default_backup_root()
        )
        self.allowed_root.mkdir(parents=True, exist_ok=True)
        # ``default_dest`` is kept for backward compatibility with
        # older test fixtures but always resolved to live INSIDE the
        # sandbox.  Path("data/finance_backups") legacy default is
        # ignored when it would escape the allowed root.
        if default_dest is not None and _is_within(
            Path(default_dest), self.allowed_root
        ):
            self.default_dest = Path(default_dest)
        else:
            self.default_dest = self.allowed_root

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

        EX-P2-7 (half-file cleanup): the tar archive is written to a
        sibling ``.partial`` path and only renamed to the final name
        once tarfile.close() returns successfully.  Any exception
        between open() and rename() removes the partial file so a
        disk-full / OS-error never leaves orphaned ``.tar.gz`` rubble.
        """
        if not passphrase:
            raise BackupRestoreError("passphrase is required for create_backup")

        dest = Path(dest_dir) if dest_dir else self.default_dest
        dest = _ensure_within_sandbox(dest, self.allowed_root, label="dest_dir")
        dest.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        suffix = f"org_{org_id}_" if org_id else "all_orgs_"
        backup_path = dest / f"finance_backup_{suffix}{ts}.tar.gz"
        # Write to a sibling .partial path so a half-flushed archive
        # never collides with a previously-completed backup of the
        # same timestamp.  We rename atomically once tarfile closed.
        partial_path = backup_path.with_suffix(backup_path.suffix + ".partial")

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
            # 4. Encrypt key_versions JSON into keys.bin (independent of DB).
            #    The iterations used for THIS archive are recorded in the
            #    manifest below so restore can replay the same KDF run
            #    (back-compat with older 200k backups).
            keys_json = json.dumps(key_versions_rows, ensure_ascii=False)
            keys_bin, kdf_iters = _encrypt_keys_blob(
                passphrase, keys_json.encode("utf-8")
            )

            manifest = {
                "schema_version": SCHEMA_VERSION,
                "key_version": current_key_version,
                "created_at": _utcnow_iso(),
                "org_id": org_id,
                "table_counts": counts,
                "source_db_hash": source_db_hash,
                "kdf_iterations": kdf_iters,
                "kdf_algo": "PBKDF2-HMAC-SHA256",
                "cipher": "AES-256-GCM",
                "aad": BACKUP_AAD.decode("ascii"),
                "encryption_enabled": bool(meta and meta.enabled),
                "key_meta_seed_source": (meta.seed_source if meta else None),
            }

            # 5. Build the tar.gz archive to .partial then atomic rename.
            try:
                with tarfile.open(partial_path, mode="w:gz") as tf:
                    _tar_add_file(tf, "database.sqlite", snap_path.read_bytes())
                    _tar_add_file(
                        tf,
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2).encode(
                            "utf-8"
                        ),
                    )
                    _tar_add_file(tf, "keys.bin", keys_bin)
                os.replace(partial_path, backup_path)
            except Exception:
                # EX-P2-7: best-effort cleanup of any orphaned .partial.
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except OSError as cleanup_exc:
                    logger.warning(
                        "finance-auto: failed to remove partial backup %s (%s)",
                        partial_path,
                        cleanup_exc,
                    )
                raise

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
        overwrite: bool = False,
    ) -> dict:
        """Verify + (optionally) materialise an archive.

        EX-P1-1: ``target_db_path`` must resolve inside
        :attr:`allowed_root` *OR* equal the live DB path (the only
        in-place restore destination we permit by default).  An
        existing file at the target raises 409 unless ``overwrite``
        is explicitly true.
        """
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
        # Pull the actual KDF iteration count from the manifest so
        # older archives written with 200k still decrypt cleanly.
        kdf_iters = int(
            manifest.get("kdf_iterations") or BACKUP_DEFAULT_KDF_ITERATIONS
        )
        try:
            keys_pt = _decrypt_keys_blob(
                passphrase, keys_bin, iterations=kdf_iters
            )
            key_versions_rows = json.loads(keys_pt.decode("utf-8"))
            verified = True
        except Exception as exc:  # noqa: BLE001 — covers InvalidTag
            return {
                "ok": False,
                "verified": False,
                "error": "wrong passphrase",
                "detail": str(exc),
                "kdf_iterations": kdf_iters,
            }

        if dry_run:
            return {
                "ok": True,
                "verified": verified,
                "dry_run": True,
                "manifest": manifest,
                "key_versions_count": len(key_versions_rows),
                "kdf_iterations": kdf_iters,
            }

        # 2. Resolve the materialised DB path with sandbox + overwrite
        #    enforcement.  An explicit ``target_db_path`` must either
        #    live under the sandbox OR equal the currently-open
        #    ``self.db.path`` (a true in-place restore).  ``overwrite``
        #    flips the existing-file → 409 behaviour off.
        if target_db_path is None:
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            target = self.db.path.parent / (
                self.db.path.stem + f".restored.{ts}" + self.db.path.suffix
            )
        else:
            target = Path(target_db_path)
            live_db = self.db.path.resolve(strict=False)
            if target.resolve(strict=False) != live_db:
                _ensure_within_sandbox(
                    target, self.allowed_root, label="target_db_path"
                )

        if target.exists() and not overwrite:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "target_already_exists",
                    "target_db_path": str(target),
                    "hint": (
                        "pass overwrite=true (query string ?overwrite=true) "
                        "to confirm clobbering an existing DB file"
                    ),
                },
            )

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
    "BACKUP_DEFAULT_KDF_ITERATIONS",
    "BACKUP_KDF_ITERATIONS",
    "BACKUP_KDF_ITERATIONS_ENV",
    "BACKUP_MIN_SIZE_BYTES",
    "BACKUP_ROOT_ENV",
    "BackupRestoreError",
    "BackupRestoreService",
    "WrongPassphraseError",
    "_default_backup_root",
    "_ensure_within_sandbox",
    "_is_within",
    "_resolve_kdf_iterations",
]
