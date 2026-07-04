"""M3 Biz Stage 1 — schema v10: report notes + peer benchmarks.

Adds the data model behind v0.3 Part Biz §5 (报表附注自动生成) and the
v0.2 Part 2 §6.1 S5 (同业对比) scenario.

Five new tables:

* ``note_templates``          — registry of note templates (8 sections of
  the Chinese 企业会计准则 附注 structure).  Six are pure ``data_driven``
  (Sibling A's M3 scope) and two are ``hybrid`` (data table + narrative
  prompt placeholder; the narrative half is filled in by Sibling B's S11
  worker via ``finance.notes.draft_requested``).
* ``note_documents``          — one row per (org, period) generation;
  carries the workflow status (draft / in_review / finalized).
* ``report_notes``            — per-section notes attached to a document.
  ``kind ∈ {data, narrative, hybrid, narrative_pending_ai,
  narrative_pending_user}``.  ``ai_audit_id`` references
  ``llm_call_audit`` once Sibling B fills in narrative content.
* ``peer_benchmarks``         — quartile (p25, p50, p75) reference values
  per ``(industry_code, metric_code)``; seeded with 3 industries × 4
  metrics = 12 rows.
* ``peer_comparison_results`` — one row per peer-comparison run; stores
  the org's metrics + quartile assessment alongside an optional AI summary
  (the summary is left blank in M3, S5 will hook the field later).

Every table carries a ``version INTEGER NOT NULL DEFAULT 1`` column to
honour the v0.3 Part Infra C3 optimistic-lock contract — updates use the
``WHERE id=? AND version=?`` then ``version=version+1`` pattern.

The seed inserts the 6 data-driven templates and the 2 hybrid templates
listed in design Part Biz §5.1, plus 12 peer-benchmark rows mirroring
the YAML files under ``templates/peer_benchmarks/``.  All inserts use
``INSERT OR IGNORE`` keyed by ``UNIQUE(note_item_code,
accounting_standard, version)`` / ``UNIQUE(industry_code, metric_code,
period_label)`` so re-running the migration on an existing DB is a no-op.
"""

from __future__ import annotations

TARGET_VERSION = 10

# ---------------------------------------------------------------------------
# DDL — appended unconditionally to the canonical SCHEMA_SQL.  All statements
# are ``CREATE TABLE IF NOT EXISTS`` so re-runs are safe.
# ---------------------------------------------------------------------------

DDL_SQL = """
-- ===========================================================================
-- M3 Biz Stage 1 (schema v10): note templates + documents + report notes
-- + peer benchmarks + comparison results.  v0.3 Part Biz §5 + v0.2 §6.1 S5.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS note_templates (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    note_section         TEXT NOT NULL,
    note_item_code       TEXT NOT NULL,
    template_format      TEXT NOT NULL DEFAULT 'markdown' CHECK(template_format IN ('markdown','excel')),
    template_path        TEXT NOT NULL,
    data_source          TEXT NOT NULL CHECK(data_source IN ('data_driven','narrative','hybrid')),
    auto_fill_pct        INTEGER NOT NULL DEFAULT 0,
    requires_ai          INTEGER NOT NULL DEFAULT 0,
    ai_scenario_id       TEXT,
    accounting_standard  TEXT NOT NULL DEFAULT 'small_enterprise',
    version              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(note_item_code, accounting_standard, version)
);
CREATE INDEX IF NOT EXISTS idx_note_templates_section
    ON note_templates(note_section, accounting_standard);

CREATE TABLE IF NOT EXISTS note_documents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id               TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id            TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','in_review','finalized')),
    accounting_standard  TEXT NOT NULL DEFAULT 'small_enterprise',
    version              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_note_documents_org_period
    ON note_documents(org_id, period_id, status);

CREATE TABLE IF NOT EXISTS report_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES note_documents(id) ON DELETE CASCADE,
    template_id     INTEGER NOT NULL REFERENCES note_templates(id) ON DELETE RESTRICT,
    note_section    TEXT NOT NULL,
    note_item_code  TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'data' CHECK(kind IN ('data','narrative','hybrid','narrative_pending_ai','narrative_pending_user')),
    ai_audit_id     INTEGER REFERENCES llm_call_audit(id) ON DELETE SET NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_notes_doc
    ON report_notes(document_id, note_section);

CREATE TABLE IF NOT EXISTS peer_benchmarks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_code        TEXT NOT NULL,
    metric_code          TEXT NOT NULL,
    metric_name          TEXT NOT NULL,
    period_label         TEXT NOT NULL DEFAULT '2024',
    p25                  REAL NOT NULL DEFAULT 0,
    p50                  REAL NOT NULL DEFAULT 0,
    p75                  REAL NOT NULL DEFAULT 0,
    sample_size          INTEGER NOT NULL DEFAULT 0,
    source               TEXT NOT NULL DEFAULT 'seed',
    accounting_standard  TEXT NOT NULL DEFAULT 'small_enterprise',
    version              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(industry_code, metric_code, period_label)
);
CREATE INDEX IF NOT EXISTS idx_peer_benchmarks_industry
    ON peer_benchmarks(industry_code, metric_code);

CREATE TABLE IF NOT EXISTS peer_comparison_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id       TEXT NOT NULL,
    industry_code   TEXT NOT NULL,
    metrics_json    TEXT NOT NULL DEFAULT '[]',
    ai_summary      TEXT NOT NULL DEFAULT '',
    ai_audit_id     INTEGER REFERENCES llm_call_audit(id) ON DELETE SET NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_peer_results_org
    ON peer_comparison_results(org_id, period_id, created_at DESC);
"""

# ---------------------------------------------------------------------------
# Seed data.
#
# Two tables to seed:
#
# 1. ``note_templates``  — 6 data-driven + 2 hybrid rows (design Part Biz §5).
#    Sibling A owns the data-driven half end-to-end; the hybrid rows reserve
#    the row + template path so Sibling B's S11 worker can pick them up
#    without a schema bump.
# 2. ``peer_benchmarks`` — 3 industries × 4 metrics = 12 rows mirroring the
#    YAML files under ``templates/peer_benchmarks/``.  The YAML files are
#    the documentation; this SQL is the canonical store the
#    ``PeerComparisonService`` reads from.
# ---------------------------------------------------------------------------

# fmt: off
_NOTE_TEMPLATES: tuple[tuple[str, str, str, str, int, int, str | None, str], ...] = (
    # (note_section, note_item_code, template_path, data_source, auto_fill_pct,
    #  requires_ai, ai_scenario_id, accounting_standard)
    (
        "资产负债表附注", "NOTE_CASH_DETAIL",
        "templates/notes/cash_detail.md.j2",
        "data_driven", 95, 0, None, "small_enterprise",
    ),
    (
        "资产负债表附注", "NOTE_AR_AGING",
        "templates/notes/ar_aging.md.j2",
        "data_driven", 90, 0, None, "small_enterprise",
    ),
    (
        "资产负债表附注", "NOTE_INVENTORY",
        "templates/notes/inventory.md.j2",
        "data_driven", 90, 0, None, "small_enterprise",
    ),
    (
        "资产负债表附注", "NOTE_FIXED_ASSETS",
        "templates/notes/fixed_assets.md.j2",
        "data_driven", 90, 0, None, "small_enterprise",
    ),
    (
        "利润表附注", "NOTE_REVENUE_BY_CUSTOMER",
        "templates/notes/revenue_by_customer.md.j2",
        "data_driven", 85, 0, None, "small_enterprise",
    ),
    (
        "利润表附注", "NOTE_EXPENSES",
        "templates/notes/expenses.md.j2",
        "data_driven", 85, 0, None, "small_enterprise",
    ),
    (
        "资产负债表附注", "NOTE_ACCOUNTS_PAYABLE_CONCENTRATION",
        "templates/notes/accounts_payable_concentration.md.j2",
        "hybrid", 55, 1, "S11", "small_enterprise",
    ),
    (
        "关联方", "NOTE_RELATED_PARTY_TRANSACTIONS",
        "templates/notes/related_party_transactions.md.j2",
        "hybrid", 45, 1, "S11", "small_enterprise",
    ),
)

_PEER_BENCHMARKS: tuple[tuple[str, str, str, float, float, float, int], ...] = (
    # (industry_code, metric_code, metric_name, p25, p50, p75, sample_size)
    # 制造业 (manufacturing)
    ("manufacturing", "gross_margin",   "毛利率",     0.18,  0.27, 0.38, 180),
    ("manufacturing", "current_ratio",  "流动比率",   0.90,  1.40, 2.10, 180),
    ("manufacturing", "asset_turnover", "总资产周转率", 0.60,  1.00, 1.60, 180),
    ("manufacturing", "debt_ratio",     "资产负债率", 0.30,  0.48, 0.65, 180),
    # 餐饮 (restaurant)
    ("restaurant",    "gross_margin",   "毛利率",     0.55,  0.65, 0.72, 95),
    ("restaurant",    "current_ratio",  "流动比率",   0.70,  1.00, 1.40, 95),
    ("restaurant",    "asset_turnover", "总资产周转率", 1.20,  1.80, 2.60, 95),
    ("restaurant",    "debt_ratio",     "资产负债率", 0.40,  0.58, 0.72, 95),
    # 科技服务 (tech_service)
    ("tech_service",  "gross_margin",   "毛利率",     0.35,  0.50, 0.68, 140),
    ("tech_service",  "current_ratio",  "流动比率",   1.40,  2.00, 3.00, 140),
    ("tech_service",  "asset_turnover", "总资产周转率", 0.50,  0.90, 1.40, 140),
    ("tech_service",  "debt_ratio",     "资产负债率", 0.20,  0.32, 0.50, 140),
)
# fmt: on


def _note_template_seed_sql() -> str:
    """Render the INSERT-OR-IGNORE seed for the 8 default note templates."""
    lines: list[str] = []
    for section, code, path, src, fill_pct, req_ai, scenario, std in _NOTE_TEMPLATES:
        section_q = section.replace("'", "''")
        code_q = code.replace("'", "''")
        path_q = path.replace("'", "''")
        src_q = src.replace("'", "''")
        std_q = std.replace("'", "''")
        scenario_lit = "NULL" if scenario is None else f"'{scenario.replace(chr(39), chr(39) * 2)}'"
        lines.append(
            "INSERT OR IGNORE INTO note_templates("
            "note_section, note_item_code, template_format, template_path, "
            "data_source, auto_fill_pct, requires_ai, ai_scenario_id, "
            "accounting_standard, version, created_at) VALUES "
            f"('{section_q}', '{code_q}', 'markdown', '{path_q}', "
            f"'{src_q}', {int(fill_pct)}, {int(req_ai)}, {scenario_lit}, "
            f"'{std_q}', 1, datetime('now'));"
        )
    return "\n".join(lines) + "\n"


def _peer_benchmark_seed_sql() -> str:
    """Render the INSERT-OR-IGNORE seed for the 12 peer benchmark rows."""
    lines: list[str] = []
    for code, metric_code, metric_name, p25, p50, p75, n in _PEER_BENCHMARKS:
        code_q = code.replace("'", "''")
        metric_code_q = metric_code.replace("'", "''")
        metric_name_q = metric_name.replace("'", "''")
        lines.append(
            "INSERT OR IGNORE INTO peer_benchmarks("
            "industry_code, metric_code, metric_name, period_label, "
            "p25, p50, p75, sample_size, source, accounting_standard, "
            "version, created_at) VALUES "
            f"('{code_q}', '{metric_code_q}', '{metric_name_q}', '2024', "
            f"{p25}, {p50}, {p75}, {int(n)}, 'seed', 'small_enterprise', "
            "1, datetime('now'));"
        )
    return "\n".join(lines) + "\n"


SEED_SQL = _note_template_seed_sql() + _peer_benchmark_seed_sql()


def default_note_templates() -> tuple[tuple[str, str, str, str, int, int, str | None, str], ...]:
    """Return the in-memory note-template tuple — convenience for tests."""
    return _NOTE_TEMPLATES


def default_peer_benchmarks() -> tuple[tuple[str, str, str, float, float, float, int], ...]:
    """Return the in-memory peer-benchmark tuple — convenience for tests."""
    return _PEER_BENCHMARKS


__all__ = [
    "DDL_SQL",
    "SEED_SQL",
    "TARGET_VERSION",
    "default_note_templates",
    "default_peer_benchmarks",
]
