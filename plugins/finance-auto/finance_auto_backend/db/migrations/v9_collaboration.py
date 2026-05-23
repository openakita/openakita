"""M2 Biz Stage 1 — schema v9 (part 1/3): multi-auditor RBAC + review workflow.

Adds the foundation of v0.3 Part Biz §1 协作模型:

* ``users``             — global user registry (auditor / manager / partner / admin).
* ``permissions``       — role × resource × action × scope.
* ``assignments``       — user → (org, period, role_in_project) 三元绑定.
* ``review_workflows``  — 报表/底稿复核状态机（draft → pending_review →
  reviewed → pending_signoff → signed_off，含 returned 回流）.
* ``comments``          — 挂在 cell / report / workflow 上的线程化评论.

每张「可编辑」表都带 ``version INTEGER NOT NULL DEFAULT 1`` 列，满足
v0.3 Part Infra C3 乐观锁契约（更新时 ``UPDATE ... WHERE id=? AND version=?``
然后 ``version=version+1``，0 行受影响 → 409 Conflict）.

`current_user` 默认仍为 ``"local"``（v0.2 单本机用户语义），完整多用户改造在
v0.3 时升级 session 提取逻辑。本模块只准备数据模型，不绑定 session 中间件。
"""

from __future__ import annotations

TARGET_VERSION = 9

DDL_SQL = """
-- ===========================================================================
-- M2 Biz Stage 1 (schema v9 · part 1/3): multi-auditor RBAC + review workflow.
-- v0.3 Part Biz §1.  Plugin-local SQLite (same instance as the finance data
-- so foreign keys to organizations / reports / report_cells stay sound).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL CHECK(role IN ('auditor','manager','partner','admin')),
    email        TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_users_role  ON users(role, active);

CREATE TABLE IF NOT EXISTS permissions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role      TEXT NOT NULL,
    resource  TEXT NOT NULL,   -- 'org' | 'report' | 'cell' | 'comment' | ...
    action    TEXT NOT NULL,   -- 'read' | 'write' | 'review' | 'sign_off' | ...
    scope     TEXT             -- 'own' | 'assigned' | 'all' | NULL
);
-- SQLite forbids expressions in inline UNIQUE constraints; use an explicit
-- partial-index pattern with IFNULL so NULL ``scope`` still de-dupes.
CREATE UNIQUE INDEX IF NOT EXISTS ux_permissions_role_action
    ON permissions(role, resource, action, IFNULL(scope, ''));

CREATE TABLE IF NOT EXISTS assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id       TEXT,                                  -- NULL = 整账套
    role_in_project TEXT NOT NULL CHECK(role_in_project IN ('lead_auditor','reviewer','partner_signoff')),
    assigned_at     TEXT NOT NULL,
    assigned_by     TEXT NOT NULL DEFAULT 'local',
    revoked_at      TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
-- Same UNIQUE-with-IFNULL pattern as permissions: NULL period_id collapses to
-- empty string in the index so "whole-org" assignments still de-dupe per user/role.
CREATE UNIQUE INDEX IF NOT EXISTS ux_assignments_user_scope
    ON assignments(user_id, org_id, IFNULL(period_id, ''), role_in_project);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON assignments(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_assignments_org  ON assignments(org_id, period_id);

CREATE TABLE IF NOT EXISTS review_workflows (
    workflow_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    period_id       TEXT NOT NULL,
    report_id       TEXT,                       -- nullable: workflow 也可独立于 report
    target_kind     TEXT NOT NULL DEFAULT 'report_instance' CHECK(target_kind IN ('report_instance','audit_evidence','notes')),
    status          TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','pending_review','reviewed','pending_signoff','signed_off','returned')),
    auditor_id      TEXT,
    reviewer_id     TEXT,
    partner_id      TEXT,
    submitted_at    TEXT,
    reviewed_at     TEXT,
    signed_off_at   TEXT,
    returned_at     TEXT,
    return_reason   TEXT,
    history_json    TEXT NOT NULL DEFAULT '[]',  -- [{state, by, at, note}]
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_review_workflows_report
    ON review_workflows(org_id, report_id);
CREATE INDEX IF NOT EXISTS idx_review_workflows_status
    ON review_workflows(org_id, status, period_id);

CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id  INTEGER REFERENCES review_workflows(workflow_id) ON DELETE SET NULL,
    cell_id      TEXT,
    report_id    TEXT,
    org_id       TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id    INTEGER REFERENCES comments(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'general' CHECK(kind IN ('general','review_question','answer','audit_finding')),
    author_id    TEXT NOT NULL DEFAULT 'local',
    body         TEXT NOT NULL,
    mentions     TEXT NOT NULL DEFAULT '[]',   -- JSON list[str user_id]
    resolved     INTEGER NOT NULL DEFAULT 0,
    resolved_by  TEXT,
    resolved_at  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_comments_target_cell
    ON comments(org_id, cell_id, resolved);
CREATE INDEX IF NOT EXISTS idx_comments_target_report
    ON comments(org_id, report_id, resolved);
CREATE INDEX IF NOT EXISTS idx_comments_workflow
    ON comments(workflow_id);
"""

# 默认权限矩阵（v0.3 Part Biz §1.1）。INSERT OR IGNORE 配合 UNIQUE 约束，
# 重跑幂等。auditor 仅可写「指派范围」，manager 走「项目范围」即与 auditor
# 同等但加 review 权，partner 全局可写 + 签字，admin 仅元数据 + 用户指派。
_PERMISSIONS: tuple[tuple[str, str, str, str | None], ...] = (
    # role, resource, action, scope
    ("auditor", "report", "read", "assigned"),
    ("auditor", "report", "write", "assigned"),
    ("auditor", "comment", "write", "assigned"),
    ("auditor", "workflow", "submit", "assigned"),
    ("manager", "report", "read", "assigned"),
    ("manager", "report", "write", "assigned"),
    ("manager", "comment", "write", "assigned"),
    ("manager", "workflow", "review", "assigned"),
    ("manager", "workflow", "approve", "assigned"),
    ("manager", "workflow", "request_changes", "assigned"),
    ("manager", "consolidation", "run", "all"),
    ("partner", "report", "read", "all"),
    ("partner", "report", "write", "all"),
    ("partner", "comment", "write", "all"),
    ("partner", "workflow", "review", "all"),
    ("partner", "workflow", "sign_off", "all"),
    ("partner", "consolidation", "run", "all"),
    ("partner", "user", "assign", "all"),
    ("admin", "user", "create", "all"),
    ("admin", "user", "assign", "all"),
    ("admin", "system", "config", "all"),
)


def _permission_seed_sql() -> str:
    out: list[str] = []
    for role, resource, action, scope in _PERMISSIONS:
        scope_lit = "NULL" if scope is None else f"'{scope}'"
        out.append(
            "INSERT OR IGNORE INTO permissions(role, resource, action, scope) "
            f"VALUES ('{role}', '{resource}', '{action}', {scope_lit});"
        )
    return "\n".join(out) + "\n"


SEED_SQL = _permission_seed_sql()


def default_permissions() -> tuple[tuple[str, str, str, str | None], ...]:
    """Return the in-memory permission tuple — convenience for tests."""
    return _PERMISSIONS


__all__ = ["DDL_SQL", "SEED_SQL", "TARGET_VERSION", "default_permissions"]
