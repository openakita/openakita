"""Move-compatibility tests for commit 12 (ralph / trait_miner / user_profile).

Phase 2 commit 12 ports three more modules into the ``agent/``
package, finishing the MOVE block of the Phase 2 plan:

* ``core/ralph.py``        → ``agent/ralph.py``
* ``core/trait_miner.py``  → ``agent/trait_miner.py``
* ``core/user_profile.py`` → ``agent/user_profile.py``

Each legacy path is now a re-export shim. The tests below pin
classes / functions / constants to a single identity across both
import paths so existing callers (Agent constructor, persona
update flow, /api/user_profile endpoints, Ralph-loop entry point)
keep working unchanged.
"""

from __future__ import annotations


def test_ralph_match_across_paths() -> None:
    from openakita.agent.ralph import (
        RalphLoop as A_RL,
    )
    from openakita.agent.ralph import (
        StopHook as A_SH,
    )
    from openakita.agent.ralph import (
        Task as A_T,
    )
    from openakita.agent.ralph import (
        TaskResult as A_TR,
    )
    from openakita.agent.ralph import (
        TaskStatus as A_TS,
    )
    from openakita.core.ralph import (
        RalphLoop as C_RL,
    )
    from openakita.core.ralph import (
        StopHook as C_SH,
    )
    from openakita.core.ralph import (
        Task as C_T,
    )
    from openakita.core.ralph import (
        TaskResult as C_TR,
    )
    from openakita.core.ralph import (
        TaskStatus as C_TS,
    )

    assert A_RL is C_RL
    assert A_SH is C_SH
    assert A_T is C_T
    assert A_TR is C_TR
    assert A_TS is C_TS


def test_trait_miner_match_across_paths() -> None:
    from openakita.agent.trait_miner import (
        ANSWER_ANALYSIS_PROMPT as A_AAP,
    )
    from openakita.agent.trait_miner import (
        ANSWER_ANALYSIS_SYSTEM as A_AAS,
    )
    from openakita.agent.trait_miner import (
        TRAIT_MINING_PROMPT as A_TMP,
    )
    from openakita.agent.trait_miner import (
        TRAIT_MINING_SYSTEM as A_TMS,
    )
    from openakita.agent.trait_miner import (
        TraitMiner as A_TM,
    )
    from openakita.core.trait_miner import (
        ANSWER_ANALYSIS_PROMPT as C_AAP,
    )
    from openakita.core.trait_miner import (
        ANSWER_ANALYSIS_SYSTEM as C_AAS,
    )
    from openakita.core.trait_miner import (
        TRAIT_MINING_PROMPT as C_TMP,
    )
    from openakita.core.trait_miner import (
        TRAIT_MINING_SYSTEM as C_TMS,
    )
    from openakita.core.trait_miner import (
        TraitMiner as C_TM,
    )

    assert A_TM is C_TM
    assert A_AAP == C_AAP
    assert A_AAS == C_AAS
    assert A_TMP == C_TMP
    assert A_TMS == C_TMS


def test_user_profile_match_across_paths() -> None:
    from openakita.agent.user_profile import (
        USER_PROFILE_ITEMS as A_ITEMS,
    )
    from openakita.agent.user_profile import (
        USER_PROFILE_KEY_ALIASES as A_ALIASES,
    )
    from openakita.agent.user_profile import (
        UserProfileItem as A_UPI,
    )
    from openakita.agent.user_profile import (
        UserProfileManager as A_UPM,
    )
    from openakita.agent.user_profile import (
        UserProfileState as A_UPS,
    )
    from openakita.agent.user_profile import (
        get_profile_manager as a_get,
    )
    from openakita.agent.user_profile import (
        resolve_profile_key as a_resolve,
    )
    from openakita.core.user_profile import (
        USER_PROFILE_ITEMS as C_ITEMS,
    )
    from openakita.core.user_profile import (
        USER_PROFILE_KEY_ALIASES as C_ALIASES,
    )
    from openakita.core.user_profile import (
        UserProfileItem as C_UPI,
    )
    from openakita.core.user_profile import (
        UserProfileManager as C_UPM,
    )
    from openakita.core.user_profile import (
        UserProfileState as C_UPS,
    )
    from openakita.core.user_profile import (
        get_profile_manager as c_get,
    )
    from openakita.core.user_profile import (
        resolve_profile_key as c_resolve,
    )

    assert A_UPI is C_UPI
    assert A_UPM is C_UPM
    assert A_UPS is C_UPS
    assert a_get is c_get
    assert a_resolve is c_resolve
    assert A_ITEMS is C_ITEMS
    assert A_ALIASES is C_ALIASES


def test_agent_namespace_re_exports_commit12_symbols() -> None:
    from openakita import agent

    for sym in (
        "RalphLoop",
        "StopHook",
        "Task",
        "TaskResult",
        "TaskStatus",
        "TraitMiner",
        "ANSWER_ANALYSIS_PROMPT",
        "ANSWER_ANALYSIS_SYSTEM",
        "TRAIT_MINING_PROMPT",
        "TRAIT_MINING_SYSTEM",
        "UserProfileItem",
        "UserProfileManager",
        "UserProfileState",
        "USER_PROFILE_ITEMS",
        "USER_PROFILE_KEY_ALIASES",
        "get_profile_manager",
        "resolve_profile_key",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
