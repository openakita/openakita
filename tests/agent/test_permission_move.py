"""Move-compatibility tests for ``openakita.agent.permission``.

Phase 2 of the revamp ports ``core/permission.py`` to
``agent/permission.py`` and leaves a re-export shim in place. We
must guarantee that legacy import paths and the new path resolve
to the *same* class / function / dataclass objects, otherwise:

* :class:`isinstance` checks on the legacy path stop matching new
  instances (``isinstance(d, core.permission.PermissionDecision)``);
* downstream tools that inspect ``__module__`` (audit log
  serialisation, error reporting) would diverge across import
  paths.

Behavioural correctness of the permission system itself stays
under the existing legacy suites
(``tests/unit/test_permission_refactor.py``,
``tests/unit/test_mode_tool_policy.py``,
``tests/orgs/test_org_coordinator_delegation.py`` and friends).
Those keep importing through the legacy path, so their continued
green run is the strongest backwards-compat anchor we have.
"""

from __future__ import annotations


def test_permission_decision_is_same_class_via_both_paths() -> None:
    from openakita.agent.permission import PermissionDecision as Agent
    from openakita.core.permission import PermissionDecision as Core

    assert Agent is Core


def test_permission_rule_is_same_class_via_both_paths() -> None:
    from openakita.agent.permission import PermissionRule as Agent
    from openakita.core.permission import PermissionRule as Core

    assert Agent is Core


def test_denied_error_is_same_class_via_both_paths() -> None:
    from openakita.agent.permission import DeniedError as Agent
    from openakita.core.permission import DeniedError as Core

    assert Agent is Core


def test_check_permission_is_same_function_via_both_paths() -> None:
    from openakita.agent.permission import check_permission as agent_fn
    from openakita.core.permission import check_permission as core_fn

    assert agent_fn is core_fn


def test_check_mode_permission_is_same_function_via_both_paths() -> None:
    from openakita.agent.permission import check_mode_permission as agent_fn
    from openakita.core.permission import check_mode_permission as core_fn

    assert agent_fn is core_fn


def test_plan_mode_ruleset_is_same_object_via_both_paths() -> None:
    """Pre-built rulesets are list singletons; both paths must point
    at the same list so a runtime patch shows up everywhere.
    """
    from openakita.agent.permission import PLAN_MODE_RULESET as AGENT_RS
    from openakita.core.permission import PLAN_MODE_RULESET as CORE_RS

    assert AGENT_RS is CORE_RS


def test_edit_tools_frozenset_is_same_via_both_paths() -> None:
    from openakita.agent.permission import EDIT_TOOLS as AGENT_EDIT
    from openakita.core.permission import EDIT_TOOLS as CORE_EDIT

    assert AGENT_EDIT is CORE_EDIT


def test_agent_namespace_re_exports_permission_symbols() -> None:
    from openakita import agent

    for sym in (
        "PermissionDecision",
        "PermissionRule",
        "Ruleset",
        "DeniedError",
        "check_permission",
        "check_mode_permission",
        "check_path",
        "PLAN_MODE_RULESET",
        "ASK_MODE_RULESET",
        "COORDINATOR_MODE_RULESET",
        "DEFAULT_RULESET",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
