"""Schema v14 — ``org.delete`` permission seed for admin role.

EX-P2-10 (v1.0.0-rc1 follow-up): the new ``DELETE /orgs/{org_id}``
endpoint is gated by ``require_permission("org", "delete")``.  The v9 +
v12 seed chains never had an ``org.delete`` row, so without this
migration **every** caller — even the system-admin role — would get
``403 rbac_denied`` from the new endpoint.

Why a dedicated migration instead of adding the row to ``v12``?
``v12_extended_permissions`` is already shipped + committed; bumping it
in-place would break ``schema_version`` consistency for installations
that already ran v12.  A separate, additive v14 step is cheaper and
keeps the migration log auditable ("this 3-row seed showed up when
org delete landed").

Idempotent via ``INSERT OR IGNORE`` keyed by the existing
``ux_permissions_role_action`` index.

Policy:

* ``admin``   — destructive admin verb; sits next to ``admin.backup.*``
  and ``admin.key.rotate`` per the v0.3 Part Biz §1.1 RBAC model.
* ``partner`` — partners own the engagement and may retire stale
  account sets after the audit signs off.  Scope ``all`` because by
  the time deletion is requested the partner is past the per-org
  ``scope=assigned`` lifecycle.

``auditor`` and ``manager`` deliberately do **not** receive
``org.delete``: the action is irreversible (even with ``cascade=true``
the DB rows are gone), so we keep it behind the two seniormost roles.
"""

from __future__ import annotations

TARGET_VERSION = 14


_ORG_DELETE_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("admin",   "org", "delete", "all"),
    ("partner", "org", "delete", "all"),
)


def org_delete_permissions() -> tuple[tuple[str, str, str, str], ...]:
    """Public read-only accessor — handy for tests + the audit ledger."""
    return _ORG_DELETE_PERMISSIONS


def _permission_seed_sql() -> str:
    out: list[str] = []
    for role, resource, action, scope in _ORG_DELETE_PERMISSIONS:
        out.append(
            "INSERT OR IGNORE INTO permissions(role, resource, action, scope) "
            f"VALUES ('{role}', '{resource}', '{action}', '{scope}');"
        )
    return "\n".join(out) + "\n"


DDL_SQL = ""
SEED_SQL = _permission_seed_sql()


__all__ = [
    "DDL_SQL",
    "SEED_SQL",
    "TARGET_VERSION",
    "org_delete_permissions",
]
