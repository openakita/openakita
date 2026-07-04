"""Re-export shim — token budget moved to ``agent.token_budget``.

Canonical home: :mod:`openakita.agent.token_budget`. Shim
preserved until Phase 8 mechanical cleanup, per ADR-0003 and the
Phase 2 sub-commit plan in ``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.token_budget import TokenBudget, parse_token_budget

__all__ = ["TokenBudget", "parse_token_budget"]
