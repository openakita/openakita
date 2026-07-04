"""Re-export shim — domain allowlist moved to ``agent.domain_allowlist``.

Canonical home: :mod:`openakita.agent.domain_allowlist`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.domain_allowlist import (
    Decision,
    DomainAllowlist,
    get_domain_allowlist,
)

__all__ = ["Decision", "DomainAllowlist", "get_domain_allowlist"]
