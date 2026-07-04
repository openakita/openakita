"""Schema v12 — extended permission seeds for the 9 write-operation modules.

EX-P1-2 (fix-round-3) — the v9 seed only covered ``report`` / ``comment``
/ ``workflow`` / ``consolidation`` / ``user`` / ``system`` resources.
9 finance-auto write paths (``admin``, ``reclass``, ``cashflow``,
``xperiod``, ``audit-tpl``, ``manual``, ``consol`` (extra actions),
``parse``, ``notes/peer``) were not represented in the seed at all,
so the CollaborationService's `check_permission` returned False for
every non-local user — i.e. the modules were "default-deny", but the
service layer wasn't actually calling check_permission either, which
left them effectively unguarded.

This DDL-less migration only re-asserts the new permission rows.
The existing v9 ``permissions`` table is already in place; the seed
is idempotent via the ``ux_permissions_role_action`` UNIQUE index.

Role policy (mirrors v0.3 Part Biz §1.1):

* ``admin`` owns the system-wide / dangerous-ops verbs:
  ``admin.backup.create`` / ``admin.backup.restore`` /
  ``admin.key.rotate``.
* ``auditor`` may do day-to-day data work scoped to ``assigned`` orgs:
  manual inputs, parse-issue triage, reclassification preview, notes
  drafting, cash-flow compute.
* ``manager`` retains everything ``auditor`` has, plus
  reclassification apply (the irreversible variant), peer comparison
  runs, and audit-template upload (uploaded templates are visible
  cross-engagement).
* ``partner`` retains everything ``manager`` has, plus
  audit-template *delete* and ``notes.edit`` for sign-off (the
  partner's signature is on the report).
"""

from __future__ import annotations

TARGET_VERSION = 12


# Each row: (role, resource, action, scope).  ``None`` scope = all
# (the check_permission code path treats None / '' / 'all' identically).
_EXTENDED_PERMISSIONS: tuple[tuple[str, str, str, str | None], ...] = (
    # ----- admin / system ops (super-admin only) -----
    ("admin", "admin_backup", "create", "all"),
    ("admin", "admin_backup", "restore", "all"),
    ("admin", "admin_key", "rotate", "all"),

    # ----- reclassification -----
    ("auditor", "reclassification", "preview", "assigned"),
    ("manager", "reclassification", "preview", "assigned"),
    ("partner", "reclassification", "preview", "all"),
    ("manager", "reclassification", "apply", "assigned"),
    ("partner", "reclassification", "apply", "all"),
    # Undo lives with apply: whoever can apply can roll it back.
    ("manager", "reclassification", "undo", "assigned"),
    ("partner", "reclassification", "undo", "all"),

    # ----- cash flow (indirect engine) -----
    ("auditor", "cash_flow", "compute", "assigned"),
    ("manager", "cash_flow", "compute", "assigned"),
    ("partner", "cash_flow", "compute", "all"),
    ("auditor", "cash_flow", "manual_input_update", "assigned"),
    ("manager", "cash_flow", "manual_input_update", "assigned"),
    ("partner", "cash_flow", "manual_input_update", "all"),

    # ----- cross-period check -----
    ("auditor", "cross_period", "run", "assigned"),
    ("manager", "cross_period", "run", "assigned"),
    ("partner", "cross_period", "run", "all"),

    # ----- audit template registry (org-independent) -----
    ("manager", "audit_template", "upload", "all"),
    ("partner", "audit_template", "upload", "all"),
    ("partner", "audit_template", "delete", "all"),
    ("admin",   "audit_template", "delete", "all"),

    # ----- manual_inputs (cash-flow + supplementary cells) -----
    ("auditor", "manual_inputs", "update", "assigned"),
    ("manager", "manual_inputs", "update", "assigned"),
    ("partner", "manual_inputs", "update", "all"),

    # ----- consolidation extra actions (v9 covered "run" only) -----
    ("manager", "consolidation", "create_group", "assigned"),
    ("partner", "consolidation", "create_group", "all"),
    ("manager", "consolidation", "add_member", "assigned"),
    ("partner", "consolidation", "add_member", "all"),

    # ----- parse issues (triage + L2 learning) -----
    ("auditor", "parse_issue", "decide", "assigned"),
    ("manager", "parse_issue", "decide", "assigned"),
    ("partner", "parse_issue", "decide", "all"),
    ("auditor", "parse_issue", "learn", "assigned"),
    ("manager", "parse_issue", "learn", "assigned"),
    ("partner", "parse_issue", "learn", "all"),

    # ----- notes / peer comparison -----
    ("auditor", "notes", "generate", "assigned"),
    ("manager", "notes", "generate", "assigned"),
    ("partner", "notes", "generate", "all"),
    ("manager", "notes", "edit", "assigned"),
    ("partner", "notes", "edit", "all"),
    ("manager", "peer_comparison", "run", "assigned"),
    ("partner", "peer_comparison", "run", "all"),
)


def extended_permissions() -> tuple[tuple[str, str, str, str | None], ...]:
    """Public read-only view — handy for tests + the audit ledger."""
    return _EXTENDED_PERMISSIONS


def _permission_seed_sql() -> str:
    out: list[str] = []
    for role, resource, action, scope in _EXTENDED_PERMISSIONS:
        scope_lit = "NULL" if scope is None else f"'{scope}'"
        out.append(
            "INSERT OR IGNORE INTO permissions(role, resource, action, scope) "
            f"VALUES ('{role}', '{resource}', '{action}', {scope_lit});"
        )
    return "\n".join(out) + "\n"


# DDL is intentionally empty — we lean on v9's existing ``permissions``
# table.  The migration only seeds new rows.
DDL_SQL = ""
SEED_SQL = _permission_seed_sql()


__all__ = [
    "DDL_SQL",
    "SEED_SQL",
    "TARGET_VERSION",
    "extended_permissions",
]
