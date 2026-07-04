"""Move-compatibility tests for commit 8 (budget modules).

Phase 2 commit 8 ports four budget modules into the ``agent/``
package:

* ``core/token_budget.py``      → ``agent/token_budget.py``
* ``core/resource_budget.py``   → ``agent/resource_budget.py``
* ``core/loop_budget_guard.py`` → ``agent/loop_budget.py`` (renamed)
* ``core/tool_result_budget.py`` → ``agent/tool_result_budget.py``

Each legacy path is now a re-export shim. The tests below pin the
class / function / dataclass objects to a single identity across
both paths so ``isinstance`` checks and ``__module__`` introspection
keep matching after the move.

The full behavioural suites
(``tests/unit/test_loop_budget_guard.py``,
``tests/unit/test_resource_budget_progress.py``,
``tests/unit/test_supervisor_no_injection.py``,
``tests/unit/test_destructive_intent_gate.py``,
``tests/unit/test_context_budget_repair.py``) keep importing
via the legacy paths; their continued green run is the strongest
backwards-compat anchor.
"""

from __future__ import annotations


def test_token_budget_match_across_paths() -> None:
    from openakita.agent.token_budget import (
        TokenBudget as A_TB,
    )
    from openakita.agent.token_budget import (
        parse_token_budget as a_parse,
    )
    from openakita.core.token_budget import (
        TokenBudget as C_TB,
    )
    from openakita.core.token_budget import (
        parse_token_budget as c_parse,
    )

    assert A_TB is C_TB
    assert a_parse is c_parse


def test_resource_budget_match_across_paths() -> None:
    from openakita.agent.resource_budget import (
        BudgetAction as A_ACT,
    )
    from openakita.agent.resource_budget import (
        BudgetConfig as A_CFG,
    )
    from openakita.agent.resource_budget import (
        BudgetExceeded as A_EXC,
    )
    from openakita.agent.resource_budget import (
        BudgetStatus as A_STAT,
    )
    from openakita.agent.resource_budget import (
        ResourceBudget as A_RB,
    )
    from openakita.agent.resource_budget import (
        create_budget_from_settings as a_create,
    )
    from openakita.core.resource_budget import (
        BudgetAction as C_ACT,
    )
    from openakita.core.resource_budget import (
        BudgetConfig as C_CFG,
    )
    from openakita.core.resource_budget import (
        BudgetExceeded as C_EXC,
    )
    from openakita.core.resource_budget import (
        BudgetStatus as C_STAT,
    )
    from openakita.core.resource_budget import (
        ResourceBudget as C_RB,
    )
    from openakita.core.resource_budget import (
        create_budget_from_settings as c_create,
    )

    assert A_ACT is C_ACT
    assert A_CFG is C_CFG
    assert A_EXC is C_EXC
    assert A_STAT is C_STAT
    assert A_RB is C_RB
    assert a_create is c_create


def test_loop_budget_match_across_paths() -> None:
    """The legacy module is ``loop_budget_guard``; the new module is
    ``agent.loop_budget`` per the audit's rename. Both paths must
    point at the same class objects.
    """
    from openakita.agent.loop_budget import (
        READONLY_EXPLORATION_TOOLS as A_RE,
    )
    from openakita.agent.loop_budget import (
        LoopBudgetDecision as A_D,
    )
    from openakita.agent.loop_budget import (
        LoopBudgetGuard as A_G,
    )
    from openakita.core.loop_budget_guard import (
        READONLY_EXPLORATION_TOOLS as C_RE,
    )
    from openakita.core.loop_budget_guard import (
        LoopBudgetDecision as C_D,
    )
    from openakita.core.loop_budget_guard import (
        LoopBudgetGuard as C_G,
    )

    assert A_D is C_D
    assert A_G is C_G
    assert A_RE is C_RE


def test_tool_result_budget_match_across_paths() -> None:
    from openakita.agent.tool_result_budget import (
        DEFAULT_MAX_RESULT_CHARS as A_MAX,
    )
    from openakita.agent.tool_result_budget import (
        OVERFLOW_DIR as A_DIR,
    )
    from openakita.agent.tool_result_budget import (
        truncate_tool_result as a_fn,
    )
    from openakita.core.tool_result_budget import (
        DEFAULT_MAX_RESULT_CHARS as C_MAX,
    )
    from openakita.core.tool_result_budget import (
        OVERFLOW_DIR as C_DIR,
    )
    from openakita.core.tool_result_budget import (
        truncate_tool_result as c_fn,
    )

    assert A_MAX == C_MAX
    assert A_DIR == C_DIR
    assert a_fn is c_fn


def test_agent_namespace_re_exports_commit8_symbols() -> None:
    from openakita import agent

    for sym in (
        "TokenBudget",
        "parse_token_budget",
        "BudgetAction",
        "BudgetConfig",
        "BudgetExceeded",
        "BudgetStatus",
        "ResourceBudget",
        "create_budget_from_settings",
        "READONLY_EXPLORATION_TOOLS",
        "LoopBudgetDecision",
        "LoopBudgetGuard",
        "DEFAULT_MAX_RESULT_CHARS",
        "OVERFLOW_DIR",
        "truncate_tool_result",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
