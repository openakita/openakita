"""Re-export shim — user profile manager moved to ``agent.user_profile``.

Canonical home: :mod:`openakita.agent.user_profile`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.user_profile import (
    USER_PROFILE_ITEMS,
    USER_PROFILE_KEY_ALIASES,
    UserProfileItem,
    UserProfileManager,
    UserProfileState,
    get_profile_manager,
    resolve_profile_key,
)

__all__ = [
    "USER_PROFILE_ITEMS",
    "USER_PROFILE_KEY_ALIASES",
    "UserProfileItem",
    "UserProfileManager",
    "UserProfileState",
    "get_profile_manager",
    "resolve_profile_key",
]
