"""Move-compatibility tests for commit 7.

Phase 2 commit 7 ports four legacy modules into the ``agent/``
package:

* ``core/pending_approvals.py`` → ``agent/pending_approvals.py``
* ``core/confirmation_state.py`` → ``agent/confirmation.py``
* ``core/ui_confirm_bus.py`` → ``agent/ui_confirm_bus.py``
* ``core/hooks.py`` → ``agent/hooks.py``

Each legacy path is now a re-export shim. The tests below pin the
class / function objects to a single identity across both paths so
``isinstance`` checks and ``__module__`` introspection in API
routes (``api/server.py``, ``api/routes/{pending_approvals,
config,sessions,chat}.py``) and the C13/C17/C18 / pending-approvals /
confirmation behavioural suites continue to match across the move.

The full behavioural suites
(``tests/unit/test_pending_approvals_store.py``,
``tests/unit/test_c18_confirm_batch.py``,
``tests/unit/test_c17_sse_replay.py``,
``tests/unit/test_policy_v2_c8b3_apply_resolution.py``,
``tests/unit/test_destructive_intent_gate.py``,
``tests/unit/test_risk_authorized_replay.py``) all import via the
legacy path; their continued green run is the strongest
backwards-compat anchor we have.
"""

from __future__ import annotations


def test_pending_approval_classes_match_across_paths() -> None:
    from openakita.agent.pending_approvals import (
        PendingApproval as A,
    )
    from openakita.agent.pending_approvals import (
        PendingApprovalsStore as AStore,
    )
    from openakita.agent.pending_approvals import (
        get_pending_approvals_store as a_get,
    )
    from openakita.agent.pending_approvals import (
        reset_pending_approvals_store as a_reset,
    )
    from openakita.core.pending_approvals import (
        PendingApproval as C,
    )
    from openakita.core.pending_approvals import (
        PendingApprovalsStore as CStore,
    )
    from openakita.core.pending_approvals import (
        get_pending_approvals_store as c_get,
    )
    from openakita.core.pending_approvals import (
        reset_pending_approvals_store as c_reset,
    )

    assert A is C
    assert AStore is CStore
    assert a_get is c_get
    assert a_reset is c_reset


def test_confirmation_decision_and_store_match_across_paths() -> None:
    from openakita.agent.confirmation import (
        ConfirmationDecision as A,
    )
    from openakita.agent.confirmation import (
        PendingRiskConfirmation as APending,
    )
    from openakita.agent.confirmation import (
        PendingRiskConfirmationStore as AStore,
    )
    from openakita.agent.confirmation import (
        get_confirmation_store as a_get,
    )
    from openakita.agent.confirmation import (
        normalize_confirmation_answer as a_norm,
    )
    from openakita.core.confirmation_state import (
        ConfirmationDecision as C,
    )
    from openakita.core.confirmation_state import (
        PendingRiskConfirmation as CPending,
    )
    from openakita.core.confirmation_state import (
        PendingRiskConfirmationStore as CStore,
    )
    from openakita.core.confirmation_state import (
        get_confirmation_store as c_get,
    )
    from openakita.core.confirmation_state import (
        normalize_confirmation_answer as c_norm,
    )

    assert A is C
    assert APending is CPending
    assert AStore is CStore
    assert a_get is c_get
    assert a_norm is c_norm


def test_ui_confirm_bus_match_across_paths() -> None:
    from openakita.agent.ui_confirm_bus import (
        UIConfirmBus as A,
    )
    from openakita.agent.ui_confirm_bus import (
        get_ui_confirm_bus as a_get,
    )
    from openakita.agent.ui_confirm_bus import (
        reset_ui_confirm_bus as a_reset,
    )
    from openakita.core.ui_confirm_bus import (
        UIConfirmBus as C,
    )
    from openakita.core.ui_confirm_bus import (
        get_ui_confirm_bus as c_get,
    )
    from openakita.core.ui_confirm_bus import (
        reset_ui_confirm_bus as c_reset,
    )

    assert A is C
    assert a_get is c_get
    assert a_reset is c_reset


def test_hooks_classes_match_across_paths() -> None:
    from openakita.agent.hooks import (
        CallbackHook as A_CB,
    )
    from openakita.agent.hooks import (
        HookEvent as A_EV,
    )
    from openakita.agent.hooks import (
        HookExecutor as A_EX,
    )
    from openakita.agent.hooks import (
        HookHandler as A_H,
    )
    from openakita.agent.hooks import (
        HookResult as A_RES,
    )
    from openakita.agent.hooks import (
        ShellHook as A_SH,
    )
    from openakita.agent.hooks import (
        get_hook_executor as a_get,
    )
    from openakita.agent.hooks import (
        set_hook_executor as a_set,
    )
    from openakita.core.hooks import (
        CallbackHook as C_CB,
    )
    from openakita.core.hooks import (
        HookEvent as C_EV,
    )
    from openakita.core.hooks import (
        HookExecutor as C_EX,
    )
    from openakita.core.hooks import (
        HookHandler as C_H,
    )
    from openakita.core.hooks import (
        HookResult as C_RES,
    )
    from openakita.core.hooks import (
        ShellHook as C_SH,
    )
    from openakita.core.hooks import (
        get_hook_executor as c_get,
    )
    from openakita.core.hooks import (
        set_hook_executor as c_set,
    )

    assert A_CB is C_CB
    assert A_EV is C_EV
    assert A_EX is C_EX
    assert A_H is C_H
    assert A_RES is C_RES
    assert A_SH is C_SH
    assert a_get is c_get
    assert a_set is c_set


def test_agent_namespace_re_exports_commit7_symbols() -> None:
    from openakita import agent

    for sym in (
        "PendingApproval",
        "PendingApprovalsStore",
        "get_pending_approvals_store",
        "reset_pending_approvals_store",
        "ConfirmationDecision",
        "PendingRiskConfirmation",
        "PendingRiskConfirmationStore",
        "get_confirmation_store",
        "normalize_confirmation_answer",
        "UIConfirmBus",
        "get_ui_confirm_bus",
        "reset_ui_confirm_bus",
        "HookEvent",
        "HookExecutor",
        "HookHandler",
        "HookResult",
        "CallbackHook",
        "ShellHook",
        "get_hook_executor",
        "set_hook_executor",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
