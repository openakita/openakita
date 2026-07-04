"""Re-export shim — trusted-path policy moved to ``agent.trusted_paths``.

Canonical home: :mod:`openakita.agent.trusted_paths`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.trusted_paths import (
    SESSION_KEY,
    clear_session_trust,
    consume_session_trust,
    get_session_overrides,
    grant_session_trust,
    is_trusted_workspace_path,
)

__all__ = [
    "SESSION_KEY",
    "clear_session_trust",
    "consume_session_trust",
    "get_session_overrides",
    "grant_session_trust",
    "is_trusted_workspace_path",
]
