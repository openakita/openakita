"""M2 Biz Stage 1 — schema v9 (part 3/3): reclassification rules + run log.

Adds v0.3 Part Biz §3.6 / v0.1 §5.2 重分类规则引擎 的数据模型:

* ``reclassification_rules``      — 触发条件 + 目标 action（YAML / JSON 描述).
* ``reclassification_runs``       — 每次预览 / 应用的执行记录.
* ``reclassification_run_items``  — 单次 run 下逐行 (account_code → 调整) 明细.

YAML 模板放在 ``templates/reports/reclassification_*.yaml``；本表只缓存
「上次同步进数据库」的规则副本，便于跨账套 quick lookup + UI 编辑.
"""

from __future__ import annotations

TARGET_VERSION = 9

DDL_SQL = """
-- ===========================================================================
-- M2 Biz Stage 1 (schema v9 · part 3/3): reclassification rules engine.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS reclassification_rules (
    rule_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id         TEXT REFERENCES organizations(id) ON DELETE CASCADE,  -- NULL = 全局规则
    name           TEXT NOT NULL,
    description    TEXT,
    when_condition TEXT NOT NULL DEFAULT '{}',   -- JSON-encoded predicate
    action         TEXT NOT NULL DEFAULT '{}',   -- JSON-encoded action
    active         INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 100, -- 数字小先执行；同序号按 rule_id ASC
    source_yaml    TEXT,                          -- 规则来源（templates/.../*.yaml 相对路径）
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL DEFAULT 'local',
    updated_at     TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_reclass_rules_org
    ON reclassification_rules(IFNULL(org_id, ''), active, priority);

CREATE TABLE IF NOT EXISTS reclassification_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id     TEXT NOT NULL,
    import_id     TEXT REFERENCES trial_balance_imports(id) ON DELETE SET NULL,
    mode          TEXT NOT NULL CHECK(mode IN ('preview','apply')),
    rules_count   INTEGER NOT NULL DEFAULT 0,
    items_count   INTEGER NOT NULL DEFAULT 0,
    total_amount  TEXT NOT NULL DEFAULT '0',    -- 该 run 涉及金额合计（Decimal as str）
    parse_issue_ids_json TEXT NOT NULL DEFAULT '[]',
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    triggered_by  TEXT NOT NULL DEFAULT 'local',
    status        TEXT NOT NULL DEFAULT 'ok' CHECK(status IN ('ok','failed','partial')),
    notes         TEXT,
    version       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_reclass_runs_org_period
    ON reclassification_runs(org_id, period_id, started_at DESC);

CREATE TABLE IF NOT EXISTS reclassification_run_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES reclassification_runs(run_id) ON DELETE CASCADE,
    rule_id         INTEGER REFERENCES reclassification_rules(rule_id) ON DELETE SET NULL,
    rule_name       TEXT NOT NULL DEFAULT '',
    source_account  TEXT NOT NULL,
    target_account  TEXT NOT NULL,
    amount          TEXT NOT NULL DEFAULT '0',     -- Decimal as str
    direction       TEXT NOT NULL DEFAULT 'credit', -- credit | debit
    reason          TEXT,
    matched_row_id  TEXT,                           -- trial_balance_rows.id (best-effort)
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reclass_items_run
    ON reclassification_run_items(run_id, rule_id);
"""

SEED_SQL = ""

__all__ = ["DDL_SQL", "SEED_SQL", "TARGET_VERSION"]
