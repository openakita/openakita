"""M2 Biz Stage 1 — schema v9 (part 2/3): consolidation groups + eliminations.

Adds v0.3 Part Biz §2 合并报表 数据模型:

* ``consolidation_groups``  — 一条记录 = 一个母公司 + 一组子公司打包成的「集团」.
* ``consolidation_members`` — 母/子公司加入集团，含 ownership_pct + 合并方法.
* ``elimination_entries``   — 单条抵消分录（内部往来 / 内部销售 / 内部投资 / ...).
* ``consolidated_reports``  — 合并三表（资产负债/利润/现金流量）的实例.

金额一律用 TEXT 存 Decimal（v0.1 主设计 + Part Infra 一致性要求），
渲染时按需 round 到 2 位小数。``version`` 列满足 Part Infra C3 乐观锁契约。
"""

from __future__ import annotations

TARGET_VERSION = 9

DDL_SQL = """
-- ===========================================================================
-- M2 Biz Stage 1 (schema v9 · part 2/3): consolidation + eliminations.
-- v0.3 Part Biz §2.  Decimals stored as TEXT (canonical str(Decimal)).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS consolidation_groups (
    group_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    parent_org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    description   TEXT,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT 'local',
    updated_at    TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(parent_org_id, name)
);
CREATE INDEX IF NOT EXISTS idx_consol_groups_parent
    ON consolidation_groups(parent_org_id);

CREATE TABLE IF NOT EXISTS consolidation_members (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id           INTEGER NOT NULL REFERENCES consolidation_groups(group_id) ON DELETE CASCADE,
    subsidiary_org_id  TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    ownership_pct      REAL NOT NULL DEFAULT 100.0,
    join_method        TEXT NOT NULL DEFAULT 'full' CHECK(join_method IN ('full','equity','proportional')),
    is_parent          INTEGER NOT NULL DEFAULT 0,
    added_at           TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1,
    UNIQUE(group_id, subsidiary_org_id)
);
CREATE INDEX IF NOT EXISTS idx_consol_members_group
    ON consolidation_members(group_id);

CREATE TABLE IF NOT EXISTS elimination_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER NOT NULL REFERENCES consolidation_groups(group_id) ON DELETE CASCADE,
    period_id           TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'inter_ar_ap' CHECK(kind IN ('inter_ar_ap','inter_sales','inter_investment','inter_profit','minority_interest','other')),
    rule_key            TEXT NOT NULL DEFAULT '',
    debit_target        TEXT NOT NULL,       -- e.g. 'BS_2202' 报表 reference_code
    credit_target       TEXT NOT NULL,
    amount              TEXT NOT NULL DEFAULT '0',   -- Decimal as str
    rationale           TEXT,
    is_auto             INTEGER NOT NULL DEFAULT 0,
    review_required     INTEGER NOT NULL DEFAULT 1,
    auto_match_confidence REAL,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL DEFAULT 'local',
    version             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_elim_entries_group_period
    ON elimination_entries(group_id, period_id);
CREATE INDEX IF NOT EXISTS idx_elim_entries_kind
    ON elimination_entries(group_id, kind);

CREATE TABLE IF NOT EXISTS consolidated_reports (
    consolidated_report_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id                INTEGER NOT NULL REFERENCES consolidation_groups(group_id) ON DELETE CASCADE,
    period_id               TEXT NOT NULL,
    kind                    TEXT NOT NULL CHECK(kind IN ('balance_sheet','income_statement','cash_flow')),
    accounting_standard     TEXT NOT NULL DEFAULT 'small_enterprise',
    status                  TEXT NOT NULL DEFAULT 'ok' CHECK(status IN ('ok','failed','partial')),
    cells_json              TEXT NOT NULL DEFAULT '[]',          -- list[dict cell]
    minority_interest       TEXT NOT NULL DEFAULT '0',           -- Decimal as str
    consolidation_meta      TEXT NOT NULL DEFAULT '{}',          -- JSON meta
    member_orgs_snapshot    TEXT NOT NULL DEFAULT '[]',
    elimination_ids_json    TEXT NOT NULL DEFAULT '[]',
    warnings_json           TEXT NOT NULL DEFAULT '[]',
    generated_at            TEXT NOT NULL,
    generated_by            TEXT NOT NULL DEFAULT 'local',
    version                 INTEGER NOT NULL DEFAULT 1,
    UNIQUE(group_id, period_id, kind, generated_at)
);
CREATE INDEX IF NOT EXISTS idx_consol_reports_group_period
    ON consolidated_reports(group_id, period_id, kind);
"""

SEED_SQL = ""

__all__ = ["DDL_SQL", "SEED_SQL", "TARGET_VERSION"]
