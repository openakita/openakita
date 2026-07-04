"""M3 Infra Stage 1 — schema v11: key versioning + rotation + backup history.

Adds the data model behind v0.3 Part Infra §2.5 (密钥轮换策略) and the
"备份/迁移" row of §2.4 ("加密备份/恢复") which were previously deferred
to M3.  The M1 W2 implementation kept a single ``key_meta`` row per
component; v11 introduces an explicit ``key_versions`` history table so
rotation runs are auditable, plus a ``backup_history`` ledger so admins
can see which encrypted archives the plugin has produced and restored.

Three new tables — all carry ``version INTEGER NOT NULL DEFAULT 1`` to
honour the v0.3 Part Infra C3 optimistic-lock contract:

* ``key_versions``       — append-only history of derivation salts per
  ``(component, key_version)``.  ``status`` cycles ``active`` →
  ``retired`` (after a successful rotation) or → ``compromised`` (after
  a manual security review).  ``rotated_from`` chains rows back through
  the lineage and ``sample_canary_ct`` stores an opaque AES-GCM
  ciphertext of the literal ``b"canary"`` so the rotation service can
  later prove "the key I unlocked still matches the row we wrote".
* ``key_rotation_runs``  — one row per ``rotate_key`` invocation.  The
  service updates ``rows_processed`` as it walks the encrypted tables
  so a long-running rotation is observable from the admin UI.
* ``backup_history``     — one row per encrypted archive created via
  ``BackupRestoreService.create_backup``.  ``manifest_json`` mirrors the
  ``manifest.json`` inside the tar.gz so the admin UI can render the
  archive metadata without unpacking the file.  ``status`` cycles
  ``pending`` → ``completed`` (write succeeded) → ``restored`` /
  ``deleted`` as the operator interacts with the entry.

The seed is intentionally a no-op INSERT marker (idempotent ``INSERT
OR IGNORE`` against a sentinel key_version=0 row) so the migration
chain still records a non-empty SQL string for replay tracking.  The
real ``key_versions[0]`` row is materialised lazily by
``KeyRotationService.rotate_key`` the first time a rotation is asked
for (it captures the salt that lives in ``key_meta`` today).

All CREATE TABLE statements use ``IF NOT EXISTS`` so re-runs are safe.
"""

from __future__ import annotations

TARGET_VERSION = 11


# ---------------------------------------------------------------------------
# DDL — appended unconditionally to the canonical SCHEMA_SQL.  All statements
# are ``CREATE TABLE IF NOT EXISTS`` so re-runs are safe.
# ---------------------------------------------------------------------------

DDL_SQL = """
-- ===========================================================================
-- M3 Infra Stage 1 (schema v11): key versioning + rotation runs + encrypted
-- backup ledger.  v0.3 Part Infra §2.5 + §2.4 (备份/迁移 row).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS key_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    component           TEXT NOT NULL,
    key_version         INTEGER NOT NULL,
    kdf_salt            BLOB NOT NULL,
    kdf_iterations      INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','retired','compromised')),
    rotated_from        INTEGER,
    rotated_at          TEXT,
    rotated_by          TEXT NOT NULL DEFAULT 'local',
    rotation_reason     TEXT,
    sample_canary_ct    BLOB,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(component, key_version)
);
CREATE INDEX IF NOT EXISTS idx_key_versions_component_status
    ON key_versions(component, status);

CREATE TABLE IF NOT EXISTS key_rotation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component       TEXT NOT NULL,
    from_version    INTEGER,
    to_version      INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','success','failed','rolled_back')),
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    rows_processed  INTEGER NOT NULL DEFAULT 0,
    total_rows      INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_key_rotation_runs_started
    ON key_rotation_runs(component, started_at DESC);

CREATE TABLE IF NOT EXISTS backup_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT,
    backup_path     TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT,
    encrypted       INTEGER NOT NULL DEFAULT 1,
    kdf_salt        BLOB,
    key_version     INTEGER,
    schema_version  INTEGER,
    manifest_json   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','completed','failed','restored','deleted')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    restored_at     TEXT,
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_backup_history_org_created
    ON backup_history(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backup_history_status
    ON backup_history(status, created_at DESC);
"""


# ---------------------------------------------------------------------------
# Seed.  We do NOT pre-insert the v1 key_versions row here because the actual
# salt + iteration count live in ``key_meta`` and are populated by the
# encryption-enable flow (M1 W2).  The KeyRotationService lazily materialises
# the v1 row on first rotate / preview call by copying from key_meta.
#
# The SEED_SQL is still a non-empty string so the MIGRATION_STEPS chain sees
# a step worth re-replaying on upgrade.  We use a harmless INSERT OR IGNORE
# against a sentinel key_versions row whose ``key_version=0`` records the
# fact that the v11 migration ran on this DB.
# ---------------------------------------------------------------------------

SEED_SQL = (
    "INSERT OR IGNORE INTO key_versions("
    "component, key_version, kdf_salt, kdf_iterations, status, "
    "rotation_reason, version, created_at) VALUES "
    "('__migration_marker__', 0, X'', 0, 'retired', "
    "'v11 schema migration marker', 1, datetime('now'));\n"
)


__all__ = [
    "DDL_SQL",
    "SEED_SQL",
    "TARGET_VERSION",
]
