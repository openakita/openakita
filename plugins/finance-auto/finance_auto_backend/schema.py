"""SQL DDL for the finance-auto plugin (M1 W1 minimum set).

Five tables backing the M1 W1 vertical slice:

* ``organizations`` — accounting books (账套). One row per legal entity / fund.
* ``accounting_periods`` — yearly / monthly closing periods owned by an org.
* ``accounts`` — chart-of-accounts entries (parent + child + name + balance side).
* ``trial_balance_imports`` — header row for every uploaded balance file.
* ``trial_balance_rows`` — line items parsed from a single import.

Design references:

* v0.1 main design doc §4 (data model overview).
* v0.2 Part 1 §1.3 — ``aux_mode`` enum on ``organizations``
  (``full`` / ``light`` / ``none`` — controls how aux dimensions are stored).
* v0.3 Part Infra §2 — every "sensitive" table reserves a ``_encrypted_payload``
  BLOB column so M1 W2 can flip the encryption switch without a schema migration.

WAL mode is enabled at connection open time (see ``db.connect``); it is not a
DDL statement so it does not appear here.
"""

from __future__ import annotations

SCHEMA_VERSION = 2
"""Bumped to 2 in M1 W2 Stage 4: adds ``reports`` + ``report_cells`` tables."""

# ---------------------------------------------------------------------------
# DDL — single statement string executed via ``connection.executescript``.
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL,
    applied_at TEXT  NOT NULL
);

-- M1 W2 Stage 1: per-database encryption metadata.
-- One row per logical key scope.  In M1 W2 we only use ``component='global'``
-- (single shared key for the file).  v0.3 Part Infra §5.1 requires per-org
-- keys; that schema split is M2.
CREATE TABLE IF NOT EXISTS key_meta (
    component       TEXT PRIMARY KEY,
    salt            BLOB NOT NULL,
    kdf_iterations  INTEGER NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 0,
    seed_source     TEXT,                    -- keyring | env | generated
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    code            TEXT NOT NULL UNIQUE,
    industry        TEXT NOT NULL DEFAULT 'general',
    standard        TEXT NOT NULL DEFAULT 'cas',          -- cas | small | other
    aux_mode        TEXT NOT NULL DEFAULT 'full',          -- full | light | none
    erp_source      TEXT,                                  -- 用友 / 金蝶 / 通用 / null
    fiscal_start    TEXT,                                  -- YYYY-MM-DD
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    _encrypted_payload BLOB                                -- M1 W2 reserved
);
CREATE INDEX IF NOT EXISTS idx_org_code ON organizations(code);

CREATE TABLE IF NOT EXISTS accounting_periods (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id   TEXT NOT NULL,                             -- 2025-FY, 2025-01, etc.
    period_kind TEXT NOT NULL DEFAULT 'year',              -- year | month | quarter
    start_date  TEXT,                                      -- YYYY-MM-DD
    end_date    TEXT,
    is_closed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (org_id, period_id)
);
CREATE INDEX IF NOT EXISTS idx_period_org ON accounting_periods(org_id);

CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_code   TEXT NOT NULL,                           -- normalized 4-digit parent
    child_code    TEXT,                                    -- optional detail code
    full_code     TEXT NOT NULL,                           -- parent + '.' + child or parent
    name          TEXT NOT NULL,
    balance_side  TEXT NOT NULL DEFAULT 'debit',           -- debit | credit
    category      TEXT,                                    -- asset | liability | equity | income | expense
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    UNIQUE (org_id, full_code)
);
CREATE INDEX IF NOT EXISTS idx_accounts_org_parent ON accounts(org_id, parent_code);

CREATE TABLE IF NOT EXISTS trial_balance_imports (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id     TEXT NOT NULL,                           -- 2025-FY
    source_file   TEXT NOT NULL,                           -- original filename
    file_size     INTEGER NOT NULL DEFAULT 0,
    file_sha256   TEXT,
    parser_used   TEXT,                                    -- xlrd | pywin32 | openpyxl
    row_count     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',         -- pending | ok | failed
    error_message TEXT,
    uploaded_at   TEXT NOT NULL,
    parsed_at     TEXT,
    _encrypted_payload BLOB                                -- M1 W2 reserved
);
CREATE INDEX IF NOT EXISTS idx_import_org_period
    ON trial_balance_imports(org_id, period_id);

CREATE TABLE IF NOT EXISTS trial_balance_rows (
    id              TEXT PRIMARY KEY,
    import_id       TEXT NOT NULL REFERENCES trial_balance_imports(id) ON DELETE CASCADE,
    org_id          TEXT NOT NULL,
    period_id       TEXT NOT NULL,
    row_index       INTEGER NOT NULL,                      -- order in source file
    raw_code        TEXT,                                  -- unnormalized
    parent_code     TEXT NOT NULL,
    child_code      TEXT,
    full_code       TEXT NOT NULL,
    account_name    TEXT,
    aux_text        TEXT,                                  -- 辅助核算项原文
    opening_debit   REAL NOT NULL DEFAULT 0,
    opening_credit  REAL NOT NULL DEFAULT 0,
    period_debit    REAL NOT NULL DEFAULT 0,
    period_credit   REAL NOT NULL DEFAULT 0,
    closing_debit   REAL NOT NULL DEFAULT 0,
    closing_credit  REAL NOT NULL DEFAULT 0,
    _encrypted_payload BLOB                                -- M1 W2 reserved
);
CREATE INDEX IF NOT EXISTS idx_rows_import     ON trial_balance_rows(import_id, row_index);
CREATE INDEX IF NOT EXISTS idx_rows_code       ON trial_balance_rows(org_id, full_code);

-- ===========================================================================
-- M1 W2 Stage 4: report instances + per-cell traceability.
-- A ReportInstance materialises one (org, period, template) generation; the
-- ReportCells underneath give cell-level provenance back to the trial-balance
-- rows.  This is the data model the desktop UI's audit-trail panel renders.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS reports (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id       TEXT NOT NULL,
    sheet_kind      TEXT NOT NULL,                          -- balance_sheet | income_statement
    accounting_standard TEXT NOT NULL,                      -- small_enterprise | general_enterprise
    template_id     TEXT NOT NULL,
    template_version INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'ok',             -- ok | failed
    cell_count      INTEGER NOT NULL DEFAULT 0,
    warnings_json   TEXT,                                   -- JSON list of strings
    source_import_id TEXT REFERENCES trial_balance_imports(id) ON DELETE SET NULL,
    backend_used    TEXT,                                   -- xltpl | openpyxl | inline
    output_path     TEXT,                                   -- last exported xlsx path (if any)
    generated_at    TEXT NOT NULL,
    _encrypted_payload BLOB
);
CREATE INDEX IF NOT EXISTS idx_reports_org_period
    ON reports(org_id, period_id, sheet_kind);

CREATE TABLE IF NOT EXISTS report_cells (
    id              TEXT PRIMARY KEY,
    report_id       TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    reference_code  TEXT NOT NULL,                          -- e.g. BS_1001
    target_line_no  INTEGER NOT NULL DEFAULT 0,
    target_label    TEXT NOT NULL,
    indent_level    INTEGER NOT NULL DEFAULT 0,
    data_source     TEXT NOT NULL,                          -- section | account | formula | cross_year
    code            TEXT,                                   -- raw account code expression
    value           REAL NOT NULL DEFAULT 0,
    sign            INTEGER NOT NULL DEFAULT 1,
    is_total        INTEGER NOT NULL DEFAULT 0,
    is_tbd          INTEGER NOT NULL DEFAULT 0,
    formula         TEXT,                                   -- raw YAML formula (unevaluated)
    notes           TEXT,
    source_rows     TEXT,                                   -- JSON: list of trial_balance_row.id
    _encrypted_payload BLOB
);
CREATE INDEX IF NOT EXISTS idx_cells_report  ON report_cells(report_id, target_line_no);
CREATE INDEX IF NOT EXISTS idx_cells_refcode ON report_cells(report_id, reference_code);
"""

# ---------------------------------------------------------------------------
# Incremental migration steps.  ``run_migrations(conn, current_version)`` will
# replay every step whose key > current_version, in order.  Each step is a
# tuple of (target_version, sql) so the bookkeeping stays declarative.  The
# v0 -> v1 transition is implicit (initial creation), so the smallest key
# here is 2.
# ---------------------------------------------------------------------------

MIGRATION_STEPS: tuple[tuple[int, str], ...] = (
    (
        2,
        # M1 W2 Stage 4: adds reports + report_cells.  Idempotent because
        # the script uses CREATE TABLE IF NOT EXISTS.
        SCHEMA_SQL,
    ),
)
"""Each entry: (target_version, idempotent_DDL).  Replayed by db.run_migrations()."""
