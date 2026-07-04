"""Re-export shim — loop budget guard moved to ``agent.loop_budget``.

Canonical home: :mod:`openakita.agent.loop_budget` (renamed from
``loop_budget_guard`` to match the audit's MOVE target column).
Shim preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers:

* :mod:`openakita.core.reasoning_engine`
* :mod:`tests.unit.test_loop_budget_guard`
* :mod:`tests.unit.test_destructive_intent_gate`
* :mod:`tests.unit.test_context_budget_repair`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.loop_budget import (
    READONLY_EXPLORATION_TOOLS,
    LoopBudgetDecision,
    LoopBudgetGuard,
)

__all__ = [
    "READONLY_EXPLORATION_TOOLS",
    "LoopBudgetDecision",
    "LoopBudgetGuard",
]
