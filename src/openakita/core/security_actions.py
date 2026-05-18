"""Re-export shim — security actions moved to ``agent.security_actions``.

Canonical home: :mod:`openakita.agent.security_actions`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.security_actions import (
    add_security_allowlist_entry,
    execute_controlled_action,
    list_security_allowlist,
    list_skill_external_allowlist,
    maybe_broadcast_death_switch_reset,
    maybe_refresh_skills,
    remove_security_allowlist_entry,
    reset_death_switch,
    set_skill_external_allowlist,
)

__all__ = [
    "add_security_allowlist_entry",
    "execute_controlled_action",
    "list_security_allowlist",
    "list_skill_external_allowlist",
    "maybe_broadcast_death_switch_reset",
    "maybe_refresh_skills",
    "remove_security_allowlist_entry",
    "reset_death_switch",
    "set_skill_external_allowlist",
]
