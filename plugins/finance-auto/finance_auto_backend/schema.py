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

from .db.migrations import v8_ai_tables as _v8

SCHEMA_VERSION = 8
"""History:
* v1 -- M1 W1 baseline (5 tables).
* v2 -- M1 W2 Stage 4: adds ``reports`` + ``report_cells``.
* v3 -- M1 W2 Stage 5: adds ``vat_declarations``.
* v4 -- M1 W2 Stage 6: adds ``audit_templates``.
* v5 -- M1 W3 Stage 1+2: adds ``parse_issues`` + ``learning_samples``
        (unknown-data triage; v0.2 Part 1 §2) and widens ``report_cells``
        with simplify metadata (v0.2 Part 1 §3).
* v6 -- M1 W3 Stage 3: adds ``cross_period_check_results``
        (跨期校验; v0.3 Part Biz §4).  Carries a ``version`` column to
        satisfy the v0.3 Part Infra C3 optimistic-lock contract.
* v7 -- M1 W3 Stage 4: adds ``manual_inputs`` for the 7 cash-flow
        supplementary fields (v0.2 Part 1 §7.2 / design doc §7.2).
* v8 -- M2 AI Stage 1: adds ``ai_consent`` + ``ai_scenarios`` +
        ``llm_call_audit`` (v0.2 Part 2 §3 / §4 / §7 / §8).  Seeded
        with the 6 AI scenarios S1–S6.
"""

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
    -- W3 Stage 2 simplification metadata.  ``source_rows`` always carries
    -- the *full* detail set; the columns below describe the visible slice.
    simplified            INTEGER NOT NULL DEFAULT 0,
    simplified_top_n      INTEGER NOT NULL DEFAULT 0,
    simplify_config_json  TEXT,                              -- JSON SimplifyConfig
    merged_row_ids_json   TEXT,                              -- JSON list[str]
    footnote              TEXT,
    version               INTEGER NOT NULL DEFAULT 1,
    _encrypted_payload BLOB
);
CREATE INDEX IF NOT EXISTS idx_cells_report  ON report_cells(report_id, target_line_no);
CREATE INDEX IF NOT EXISTS idx_cells_refcode ON report_cells(report_id, reference_code);

-- ===========================================================================
-- M1 W2 Stage 5: VAT declarations (Golden-Tax-IV `增值税及附加税费申报表`).
-- One row per uploaded return, plus the parsed numeric fields.  raw_fields
-- is a JSON catch-all so future provincial fields can be persisted without
-- a schema bump.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS vat_declarations (
    id                 TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    declaration_period TEXT NOT NULL,            -- 2025-01 / 2025-Q1 etc.
    province           TEXT,                     -- BJ / GD / SH / ...
    dialect            TEXT NOT NULL,
    confidence         REAL NOT NULL DEFAULT 0,
    output_vat         REAL NOT NULL DEFAULT 0,
    input_vat          REAL NOT NULL DEFAULT 0,
    prev_credit        REAL NOT NULL DEFAULT 0,
    tax_payable        REAL NOT NULL DEFAULT 0,
    surtax_total       REAL NOT NULL DEFAULT 0,
    raw_fields_json    TEXT NOT NULL DEFAULT '{}',
    warnings_json      TEXT NOT NULL DEFAULT '[]',
    source_file        TEXT,
    file_sha256        TEXT,
    uploaded_at        TEXT NOT NULL,
    _encrypted_payload BLOB
);
CREATE INDEX IF NOT EXISTS idx_vat_org_period
    ON vat_declarations(org_id, declaration_period);

-- ===========================================================================
-- M1 W2 Stage 6: audit-template registry.
-- One row per uploaded ``审计底稿`` .xlsx.  ``placeholder_report_json`` is
-- the JSON form of services.audit_template.PlaceholderReport.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS audit_templates (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    description              TEXT,
    file_path                TEXT NOT NULL,
    file_sha256              TEXT,
    file_size                INTEGER NOT NULL DEFAULT 0,
    placeholder_count        INTEGER NOT NULL DEFAULT 0,
    unknown_placeholder_count INTEGER NOT NULL DEFAULT 0,
    placeholder_report_json  TEXT NOT NULL DEFAULT '{}',
    uploaded_at              TEXT NOT NULL,
    _encrypted_payload       BLOB
);
CREATE INDEX IF NOT EXISTS idx_audit_tpl_uploaded ON audit_templates(uploaded_at);

-- ===========================================================================
-- M1 W3 Stage 1: parse issues + learning samples (v0.2 Part 1 §2).
-- ``parse_issues`` captures every "needs human triage" finding the L1 detector
-- emits.  ``learning_samples`` records the user's decision keyed by a stable
-- pattern signature so the next import of the same shape can be auto-applied.
-- ``ai_*`` columns are reserved for the M2 Part-2 worker (this stage writes
-- them as NULL but exposes them through the API so the React front-end can
-- begin rendering the AI suggestion slot today).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS parse_issues (
    id                       TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id                TEXT NOT NULL,
    import_id                TEXT NOT NULL REFERENCES trial_balance_imports(id) ON DELETE CASCADE,
    row_index                INTEGER NOT NULL,
    sheet_name               TEXT NOT NULL DEFAULT '',
    column_name              TEXT NOT NULL DEFAULT '',
    issue_type               TEXT NOT NULL,
    severity                 TEXT NOT NULL,
    pattern_signature        TEXT NOT NULL DEFAULT '',
    original_data            TEXT NOT NULL DEFAULT '{}',         -- JSON (may contain encrypted bits via key_manager)
    ai_suggestion            TEXT,                                -- JSON; Part-2 fills
    ai_confidence            REAL,
    ai_consent_id            INTEGER,                             -- v0.2 终稿契约
    user_decision            TEXT,
    user_decision_payload    TEXT NOT NULL DEFAULT '{}',
    user_decided_at          TEXT,
    user_decided_by          TEXT NOT NULL DEFAULT '',
    applied_to_learning      INTEGER NOT NULL DEFAULT 0,
    learning_sample_id       TEXT,
    auto_applied             INTEGER NOT NULL DEFAULT 0,
    auto_applied_source      TEXT,                                -- learning_sample id, if applied
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parse_issues_org_status
    ON parse_issues(org_id, user_decision);
CREATE INDEX IF NOT EXISTS idx_parse_issues_import
    ON parse_issues(import_id, row_index);
CREATE INDEX IF NOT EXISTS idx_parse_issues_sig
    ON parse_issues(org_id, issue_type, pattern_signature);

CREATE TABLE IF NOT EXISTS learning_samples (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT,                                  -- NULL = global sample
    pattern_type        TEXT NOT NULL,
    pattern_signature   TEXT NOT NULL,
    action              TEXT NOT NULL DEFAULT '{}',            -- JSON
    confidence          REAL NOT NULL DEFAULT 1.0,
    hit_count           INTEGER NOT NULL DEFAULT 0,
    last_used_at        TEXT,
    auto_apply          INTEGER NOT NULL DEFAULT 0,
    source_decision_id  TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_signature
    ON learning_samples(IFNULL(org_id,''), pattern_type, pattern_signature);

-- ===========================================================================
-- M1 W3 Stage 3: cross-period validator results (v0.3 Part Biz §4).
-- One row per (prior_import_id, current_import_id) pair representing a
-- single check run.  ``differences_json`` holds the per-account diff array
-- (rich payload; opaque to SQL).  ``version`` is the optimistic-lock token
-- per v0.3 Part Infra C3.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS cross_period_check_results (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    prior_period_id     TEXT NOT NULL,
    current_period_id   TEXT NOT NULL,
    prior_import_id     TEXT NOT NULL REFERENCES trial_balance_imports(id) ON DELETE CASCADE,
    current_import_id   TEXT NOT NULL REFERENCES trial_balance_imports(id) ON DELETE CASCADE,
    tolerance           REAL NOT NULL DEFAULT 1.0,
    warn_threshold      REAL NOT NULL DEFAULT 100.0,
    total_accounts      INTEGER NOT NULL DEFAULT 0,
    exact_count         INTEGER NOT NULL DEFAULT 0,
    tolerance_count     INTEGER NOT NULL DEFAULT 0,
    warning_count       INTEGER NOT NULL DEFAULT 0,
    error_count         INTEGER NOT NULL DEFAULT 0,
    parse_issue_ids_json TEXT NOT NULL DEFAULT '[]',
    differences_json    TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'ok',
    notes               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xperiod_org ON cross_period_check_results(org_id, created_at);
CREATE INDEX IF NOT EXISTS idx_xperiod_imports
    ON cross_period_check_results(prior_import_id, current_import_id);

-- ===========================================================================
-- M1 W3 Stage 4: manual_inputs (cash-flow supplementary fields).
-- One row per (org, period, field_key).  ``source`` flags the provenance
-- ('manual' | 'vat_declaration' | 'learning_sample' | ...).  ``value`` is a
-- string column so the same row can hold either CNY amounts (typed via
-- ``value_type='cny'``) or free-form text (``value_type='text'``).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS manual_inputs (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id    TEXT NOT NULL,
    field_key    TEXT NOT NULL,
    field_label  TEXT NOT NULL DEFAULT '',
    value        TEXT NOT NULL DEFAULT '',
    value_type   TEXT NOT NULL DEFAULT 'cny',
    source       TEXT NOT NULL DEFAULT 'manual',
    notes        TEXT,
    decided_by   TEXT NOT NULL DEFAULT 'local',
    decided_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_manual_inputs_key
    ON manual_inputs(org_id, period_id, field_key);
""" + _v8.DDL_SQL

# ---------------------------------------------------------------------------
# Incremental migration steps.  ``run_migrations(conn, current_version)`` will
# replay every step whose key > current_version, in order.  Each step is a
# tuple of (target_version, sql) so the bookkeeping stays declarative.  The
# v0 -> v1 transition is implicit (initial creation), so the smallest key
# here is 2.
# ---------------------------------------------------------------------------

_V5_ALTER_REPORT_CELLS = """
-- W3 Stage 2: ALTER TABLE additions for report_cells.  Wrapped in a separate
-- statement so older v2 databases (which already had report_cells without
-- these columns) gain them on upgrade.  ``run_migrations`` catches the
-- "duplicate column" error for re-runs.
ALTER TABLE report_cells ADD COLUMN simplified           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE report_cells ADD COLUMN simplified_top_n     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE report_cells ADD COLUMN simplify_config_json TEXT;
ALTER TABLE report_cells ADD COLUMN merged_row_ids_json  TEXT;
ALTER TABLE report_cells ADD COLUMN footnote             TEXT;
ALTER TABLE report_cells ADD COLUMN version              INTEGER NOT NULL DEFAULT 1;
"""

MIGRATION_STEPS: tuple[tuple[int, str], ...] = (
    # All CREATE TABLE in SCHEMA_SQL already use IF NOT EXISTS and are applied
    # unconditionally by db.init() before this chain runs, so old databases
    # automatically pick up any newly added tables (e.g. parse_issues /
    # learning_samples in v5).  We therefore only register *delta* statements
    # here -- typically ALTERs that SQLite cannot express with IF NOT EXISTS.
    (2, ""),  # W2 Stage 4: reports + report_cells.
    (3, ""),  # W2 Stage 5: vat_declarations.
    (4, ""),  # W2 Stage 6: audit_templates.
    (5, _V5_ALTER_REPORT_CELLS),  # W3 Stage 2: widens report_cells with
                                  # simplify metadata columns + version.
    (6, ""),                      # W3 Stage 3: cross_period_check_results
                                  # is a brand-new CREATE TABLE IF NOT EXISTS
                                  # already in SCHEMA_SQL -- no ALTER needed.
    (7, ""),                      # W3 Stage 4: manual_inputs is a brand-new
                                  # CREATE TABLE IF NOT EXISTS in SCHEMA_SQL.
    (8, _v8.SEED_SQL),            # M2 AI Stage 1: ai_consent / ai_scenarios /
                                  # llm_call_audit DDL is in SCHEMA_SQL; the
                                  # seed step inserts the 6 default scenarios
                                  # (idempotent INSERT OR IGNORE).
)
"""Each entry: (target_version, idempotent_DDL).  All steps replay the full
canonical SCHEMA_SQL because every CREATE TABLE in it is IF NOT EXISTS, so
running an old DB through the chain just adds the new tables in-place."""
