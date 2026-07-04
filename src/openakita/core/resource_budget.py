"""Re-export shim — resource budget moved to ``agent.resource_budget``.

Canonical home: :mod:`openakita.agent.resource_budget`. Shim
preserved until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers:

* :mod:`openakita.core.reasoning_engine`
* :mod:`tests.unit.test_resource_budget_progress`
* :mod:`tests.unit.test_supervisor_no_injection`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.resource_budget import (
    BudgetAction,
    BudgetConfig,
    BudgetExceeded,
    BudgetStatus,
    ResourceBudget,
    create_budget_from_settings,
)

__all__ = [
    "BudgetAction",
    "BudgetConfig",
    "BudgetExceeded",
    "BudgetStatus",
    "ResourceBudget",
    "create_budget_from_settings",
]
