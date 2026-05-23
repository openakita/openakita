"""M2 AI Stage 1 — schema v8: AI consent / scenarios / call audit.

Three new tables backing the v0.2 Part 2 (AI 介入策略 + 数据隐私保护)
contract:

* ``ai_consent``      — per-user / per-scenario / per-sensitivity授权记录.
* ``ai_scenarios``    — registry table seeded with the M2 AI 6 default
  场景 (S1–S6).  Subsequent rows can be appended by future versions.
* ``llm_call_audit``  — every LLM call's audit log (one row per attempt
  including ``denied``/``error``/``timeout`` outcomes).

The v0.2 design (§8) prescribes the full DDL.  We deviate on a single
point — ``decision`` includes ``deny`` so the audit log can carry the
denied outcome without a separate enum; the v0.2 §8 DDL only listed
``allow_once``/``allow_permanent`` for grants.  This is documented in
the M2 AI completion report.

The v0.2 design also mentions an additional ``skip_desensitize`` and
``is_local_endpoint`` columns on ``ai_consent`` / ``llm_call_audit``;
v0.3 explicitly trimmed those down to the user-prompt-listed schema
because all that bookkeeping can be derived from the ``model_provider``
+ ``model_name`` + the related ``ai_consent`` row.

Indexes mirror v0.2 §7.1 / §8.1 to keep the AI-history page snappy:

* ``idx_llm_audit_user_time``      — page-by-time lookups.
* ``idx_llm_audit_scenario``       — group-by-scenario stats card.
* ``idx_ai_consent_lookup``        — partial index for the active grants.

The seed inserts the 6 AI scenarios listed in the M2 task spec.  We
use ``INSERT OR IGNORE`` so a re-run is a no-op (rows are keyed by
``scenario_id`` PRIMARY KEY).
"""

from __future__ import annotations

TARGET_VERSION = 8

# ---------------------------------------------------------------------------
# DDL — appended unconditionally to the canonical SCHEMA_SQL.  All statements
# are ``CREATE TABLE IF NOT EXISTS`` so re-runs are safe.
# ---------------------------------------------------------------------------

DDL_SQL = """
-- ===========================================================================
-- M2 AI Stage 1 (schema v8): AI consent + scenarios + call audit.
-- v0.2 Part 2 §3 / §4 / §7 / §8.  Plugin-local SQLite so the AGPL-licensed
-- finance-auto plugin owns its data without leaking PII into the host DB.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ai_consent (
    consent_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL DEFAULT 'local',
    scenario_id         TEXT NOT NULL,
    sensitivity_level   TEXT NOT NULL CHECK(sensitivity_level IN ('metadata','aggregated','raw')),
    decision            TEXT NOT NULL CHECK(decision IN ('deny','allow_once','allow_permanent')),
    granted_at          TEXT NOT NULL,
    revoked_at          TEXT,
    source_dialog_id    TEXT,
    skip_desensitize    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Partial index honours the v0.2 §8.1 lookup pattern: "active permanent
-- grants for this (user, scenario, sensitivity)".  ``revoked_at IS NULL``
-- short-circuits the read path.
CREATE INDEX IF NOT EXISTS idx_ai_consent_lookup
    ON ai_consent(user_id, scenario_id, sensitivity_level)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ai_consent_user
    ON ai_consent(user_id, granted_at DESC);

CREATE TABLE IF NOT EXISTS ai_scenarios (
    scenario_id           TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT,
    default_sensitivity   TEXT NOT NULL CHECK(default_sensitivity IN ('metadata','aggregated','raw')),
    default_enabled       INTEGER NOT NULL DEFAULT 1,
    prompt_template_path  TEXT,
    is_local_only         INTEGER NOT NULL DEFAULT 0,
    require_dialog        INTEGER NOT NULL DEFAULT 1,
    sensitivity_override  TEXT,
    enabled_override      INTEGER,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT
);

CREATE TABLE IF NOT EXISTS llm_call_audit (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                   TEXT NOT NULL,
    user_id                     TEXT NOT NULL DEFAULT 'local',
    org_id                      TEXT,
    scenario_id                 TEXT NOT NULL,
    sensitivity_level           TEXT NOT NULL CHECK(sensitivity_level IN ('metadata','aggregated','raw')),
    model_provider              TEXT,
    model_name                  TEXT,
    is_local_endpoint           INTEGER NOT NULL DEFAULT 0,
    payload_hash                TEXT NOT NULL,
    payload_size_bytes          INTEGER NOT NULL DEFAULT 0,
    prompt_tokens               INTEGER,
    completion_tokens           INTEGER,
    consent_id                  INTEGER,
    outcome                     TEXT NOT NULL CHECK(outcome IN ('success','denied','error','timeout')),
    error_message               TEXT,
    desensitized_payload_path   TEXT,
    duration_ms                 INTEGER,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (consent_id) REFERENCES ai_consent(consent_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_audit_user_time
    ON llm_call_audit(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_scenario
    ON llm_call_audit(scenario_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_llm_audit_org
    ON llm_call_audit(org_id, timestamp DESC);
"""

# ---------------------------------------------------------------------------
# Seed for the 6 default AI scenarios (S1–S6 per the M2 spec).
# Must be idempotent — `INSERT OR IGNORE` keyed by `scenario_id` PK.
# ---------------------------------------------------------------------------

# fmt: off
_SCENARIOS: tuple[tuple[str, str, str, str, int, str], ...] = (
    # (id, name, description, default_sensitivity, default_enabled, template_path)
    (
        "erp_source_detect",
        "ERP 来源识别",
        "给定余额表前若干行的表头特征，识别 ERP（用友 / 金蝶 / 管家婆 / 通用）。"
        "🟢 metadata 级——只发字段名 + 量级，不出现金额或客户名。",
        "metadata", 1,
        "templates/ai_prompts/erp_source_detect.md.j2",
    ),
    (
        "account_classify_suggest",
        "未识别科目归类建议",
        "对接 W3 Stage 1 ParseIssue：把无法归类的科目（仅科目编码 + 名称）批量发给 "
        "AI，返回类别 / 子类 / 置信度，写回 parse_issues.ai_suggestion。",
        "metadata", 1,
        "templates/ai_prompts/account_classify_suggest.md.j2",
    ),
    (
        "trial_balance_diagnose",
        "试算平衡失败诊断",
        "试算不平衡时，把借贷合计 + 差额量级（不发原始金额）交给 AI 解释可能成因 "
        "（科目结构 / 类型不匹配 / 子户漏抓等）。",
        "metadata", 1,
        "templates/ai_prompts/trial_balance_diagnose.md.j2",
    ),
    (
        "cross_period_anomaly",
        "跨期波动异常分析",
        "针对同比 > 50% 的报表项（已按量级 + 增长率% 脱敏）请 AI 解释风险等级 + "
        "向业务部门提问的清单。🟡 aggregated 级。",
        "aggregated", 1,
        "templates/ai_prompts/cross_period_anomaly.md.j2",
    ),
    (
        "cash_flow_aux_classify",
        "现金流量表辅助科目归类",
        "现金流量表项缺数据来源时，让 AI 在候选科目集合里推荐归属（如银行手续费 → "
        "财务费用-手续费）。仅发科目名候选清单，🟡 aggregated 级。",
        "aggregated", 1,
        "templates/ai_prompts/cash_flow_aux_classify.md.j2",
    ),
    (
        "audit_risk_warning",
        "审计风险预警",
        "对突变 > 100% / 流动比率异常 / 毛利率突变等高风险信号生成审计询问清单。"
        "🟡 aggregated 级（只发比率 + 风险等级，不发原始金额）。",
        "aggregated", 1,
        "templates/ai_prompts/audit_risk_warning.md.j2",
    ),
)
# fmt: on


def _scenario_seed_sql() -> str:
    """Render the INSERT-OR-IGNORE seed for the 6 default scenarios.

    Built as a Python f-string so the (small) tuple above stays the single
    source of truth — extending the seed later means appending one row to
    ``_SCENARIOS`` and bumping the schema version.
    """
    lines: list[str] = []
    for sid, name, desc, level, enabled, template_path in _SCENARIOS:
        # SQLite allows single-quote escaping with '' inside string literals.
        sid_q = sid.replace("'", "''")
        name_q = name.replace("'", "''")
        desc_q = desc.replace("'", "''")
        path_q = template_path.replace("'", "''")
        lines.append(
            "INSERT OR IGNORE INTO ai_scenarios("
            "scenario_id, name, description, default_sensitivity, "
            "default_enabled, prompt_template_path, created_at) VALUES "
            f"('{sid_q}', '{name_q}', '{desc_q}', '{level}', "
            f"{int(enabled)}, '{path_q}', datetime('now'));"
        )
    return "\n".join(lines) + "\n"


SEED_SQL = _scenario_seed_sql()


def default_scenarios() -> tuple[tuple[str, str, str, str, int, str], ...]:
    """Return the in-memory scenario tuple — convenience for tests."""
    return _SCENARIOS


__all__ = ["DDL_SQL", "SEED_SQL", "TARGET_VERSION", "default_scenarios"]
