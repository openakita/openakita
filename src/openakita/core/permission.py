"""Re-export shim — permission system moved to ``agent.permission``.

The canonical home of the permission rules and the unified
``check_permission`` entry point is now
:mod:`openakita.agent.permission`, per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``.

This shim preserves every existing import path
(``from openakita.core.permission import ...``) so the live test
suite, API routes, agent task queue, plugin manager, and
``policy_v2`` adapter glue can keep working without an audit-flag
sweep. Phase 8 mechanically removes the legacy ``core/`` tree and
this shim along with it.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.permission import (
    ASK_MODE_RULESET,
    COORDINATOR_MODE_RULESET,
    DEFAULT_RULESET,
    EDIT_TOOLS,
    PLAN_MODE_RULESET,
    READ_TOOLS,
    DeniedError,
    PermissionDecision,
    PermissionRule,
    Ruleset,
    check_mode_permission,
    check_path,
    check_permission,
    disabled,
    evaluate,
    from_config,
    merge,
)

__all__ = [
    "ASK_MODE_RULESET",
    "COORDINATOR_MODE_RULESET",
    "DEFAULT_RULESET",
    "EDIT_TOOLS",
    "PLAN_MODE_RULESET",
    "READ_TOOLS",
    "DeniedError",
    "PermissionDecision",
    "PermissionRule",
    "Ruleset",
    "check_mode_permission",
    "check_path",
    "check_permission",
    "disabled",
    "evaluate",
    "from_config",
    "merge",
]
