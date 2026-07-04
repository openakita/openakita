"""Move-compatibility tests for commit 9 (skill_manager / capabilities /
security_actions).

Phase 2 commit 9 ports three more modules into the ``agent/``
package:

* ``core/skill_manager.py``     → ``agent/skill_manager.py``
* ``core/capabilities.py``      → ``agent/capabilities.py``
* ``core/security_actions.py``  → ``agent/security_actions.py``

Each legacy path is now a re-export shim. The tests below pin
classes / functions / constants to a single identity across both
import paths so existing callers (Agent constructor, install_skill
tool, plugin registry, scheduler task-source registry, controlled
action API) keep working unchanged.

The pre-existing behaviour suites that already touch these
modules (``tests/unit/test_capabilities.py``, plugin/skill suites)
keep importing through the legacy path; their continued green run
is the strongest backwards-compat anchor.
"""

from __future__ import annotations


def test_skill_manager_match_across_paths() -> None:
    from openakita.agent.skill_manager import (
        SKILL_GIT_CLONE_TIMEOUT_SECONDS as A_GTO,
    )
    from openakita.agent.skill_manager import (
        SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS as A_CC,
    )
    from openakita.agent.skill_manager import (
        SKILL_INSTALL_CIRCUIT_THRESHOLD as A_CT,
    )
    from openakita.agent.skill_manager import (
        SkillManager as A_SM,
    )
    from openakita.core.skill_manager import (
        SKILL_GIT_CLONE_TIMEOUT_SECONDS as C_GTO,
    )
    from openakita.core.skill_manager import (
        SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS as C_CC,
    )
    from openakita.core.skill_manager import (
        SKILL_INSTALL_CIRCUIT_THRESHOLD as C_CT,
    )
    from openakita.core.skill_manager import (
        SkillManager as C_SM,
    )

    assert A_SM is C_SM
    assert A_GTO == C_GTO
    assert A_CC == C_CC
    assert A_CT == C_CT


def test_capabilities_match_across_paths() -> None:
    from openakita.agent.capabilities import (
        CapabilityDescriptor as A_CD,
    )
    from openakita.agent.capabilities import (
        CapabilityKind as A_CK,
    )
    from openakita.agent.capabilities import (
        CapabilityOrigin as A_CO,
    )
    from openakita.agent.capabilities import (
        CapabilityVisibility as A_CV,
    )
    from openakita.agent.capabilities import (
        build_capability_id as a_bci,
    )
    from openakita.agent.capabilities import (
        build_namespace as a_bn,
    )
    from openakita.agent.capabilities import (
        normalize_slug as a_ns,
    )
    from openakita.core.capabilities import (
        CapabilityDescriptor as C_CD,
    )
    from openakita.core.capabilities import (
        CapabilityKind as C_CK,
    )
    from openakita.core.capabilities import (
        CapabilityOrigin as C_CO,
    )
    from openakita.core.capabilities import (
        CapabilityVisibility as C_CV,
    )
    from openakita.core.capabilities import (
        build_capability_id as c_bci,
    )
    from openakita.core.capabilities import (
        build_namespace as c_bn,
    )
    from openakita.core.capabilities import (
        normalize_slug as c_ns,
    )

    assert A_CD is C_CD
    assert A_CK is C_CK
    assert A_CO is C_CO
    assert A_CV is C_CV
    assert a_bci is c_bci
    assert a_bn is c_bn
    assert a_ns is c_ns


def test_security_actions_match_across_paths() -> None:
    from openakita.agent.security_actions import (
        add_security_allowlist_entry as a_add,
    )
    from openakita.agent.security_actions import (
        execute_controlled_action as a_exec,
    )
    from openakita.agent.security_actions import (
        list_security_allowlist as a_list_sec,
    )
    from openakita.agent.security_actions import (
        list_skill_external_allowlist as a_list_skill,
    )
    from openakita.agent.security_actions import (
        maybe_broadcast_death_switch_reset as a_broadcast,
    )
    from openakita.agent.security_actions import (
        maybe_refresh_skills as a_refresh,
    )
    from openakita.agent.security_actions import (
        remove_security_allowlist_entry as a_remove,
    )
    from openakita.agent.security_actions import (
        reset_death_switch as a_reset,
    )
    from openakita.agent.security_actions import (
        set_skill_external_allowlist as a_set_skill,
    )
    from openakita.core.security_actions import (
        add_security_allowlist_entry as c_add,
    )
    from openakita.core.security_actions import (
        execute_controlled_action as c_exec,
    )
    from openakita.core.security_actions import (
        list_security_allowlist as c_list_sec,
    )
    from openakita.core.security_actions import (
        list_skill_external_allowlist as c_list_skill,
    )
    from openakita.core.security_actions import (
        maybe_broadcast_death_switch_reset as c_broadcast,
    )
    from openakita.core.security_actions import (
        maybe_refresh_skills as c_refresh,
    )
    from openakita.core.security_actions import (
        remove_security_allowlist_entry as c_remove,
    )
    from openakita.core.security_actions import (
        reset_death_switch as c_reset,
    )
    from openakita.core.security_actions import (
        set_skill_external_allowlist as c_set_skill,
    )

    assert a_add is c_add
    assert a_exec is c_exec
    assert a_list_sec is c_list_sec
    assert a_list_skill is c_list_skill
    assert a_broadcast is c_broadcast
    assert a_refresh is c_refresh
    assert a_remove is c_remove
    assert a_reset is c_reset
    assert a_set_skill is c_set_skill


def test_agent_namespace_re_exports_commit9_symbols() -> None:
    from openakita import agent

    for sym in (
        "SkillManager",
        "SKILL_GIT_CLONE_TIMEOUT_SECONDS",
        "SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS",
        "SKILL_INSTALL_CIRCUIT_THRESHOLD",
        "CapabilityDescriptor",
        "CapabilityKind",
        "CapabilityOrigin",
        "CapabilityVisibility",
        "build_capability_id",
        "build_namespace",
        "normalize_slug",
        "add_security_allowlist_entry",
        "execute_controlled_action",
        "list_security_allowlist",
        "list_skill_external_allowlist",
        "maybe_broadcast_death_switch_reset",
        "maybe_refresh_skills",
        "remove_security_allowlist_entry",
        "reset_death_switch",
        "set_skill_external_allowlist",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
