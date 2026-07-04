"""Move-compatibility tests for ``openakita.agent.persona``.

Phase 2 of the revamp ports ``core/persona.py`` to ``agent/persona.py``
and replaces the original with a re-export shim. We must guarantee
that legacy import paths and the new path resolve to the *same*
class / function objects, otherwise:

* ``isinstance(x, PersonaManager)`` checks under the old path would
  silently fail when ``x`` was created via the new path,
* serialised memories whose ``__module__`` is
  ``openakita.core.persona`` would still need to import without
  registering a brand-new class, and
* tools/handlers under ``openakita.tools`` that import from the
  legacy path would diverge from agent-side internals.

Behavioural correctness of ``PersonaManager`` itself stays under
``tests/unit/test_persona.py`` — that whole file imports through
the legacy path, so its continued green run is the strongest
backwards-compat signal we have.
"""

from __future__ import annotations


def test_persona_manager_is_same_object_via_both_paths() -> None:
    from openakita.agent.persona import PersonaManager as AgentPersonaManager
    from openakita.core.persona import PersonaManager as CorePersonaManager

    assert AgentPersonaManager is CorePersonaManager


def test_persona_trait_is_same_object_via_both_paths() -> None:
    from openakita.agent.persona import PersonaTrait as AgentPersonaTrait
    from openakita.core.persona import PersonaTrait as CorePersonaTrait

    assert AgentPersonaTrait is CorePersonaTrait


def test_merged_persona_is_same_object_via_both_paths() -> None:
    from openakita.agent.persona import MergedPersona as A
    from openakita.core.persona import MergedPersona as C

    assert A is C


def test_persona_dimensions_is_same_dict_via_both_paths() -> None:
    """The dimensions table is shared mutable state — keeping it as one
    dict object means a runtime patch (e.g. test fixture, plugin) is
    visible regardless of which path the caller imported from.
    """
    from openakita.agent.persona import PERSONA_DIMENSIONS as A
    from openakita.core.persona import PERSONA_DIMENSIONS as C

    assert A is C


def test_persist_trait_to_memory_is_same_function_via_both_paths() -> None:
    from openakita.agent.persona import persist_trait_to_memory as agent_fn
    from openakita.core.persona import persist_trait_to_memory as core_fn

    assert agent_fn is core_fn


def test_agent_namespace_re_exports_persona_symbols() -> None:
    """``openakita.agent`` (no trailing module) should expose persona
    symbols on its top level, mirroring the existing exports for
    Identity, output_guard, and working_facts.
    """
    from openakita import agent

    assert hasattr(agent, "PersonaManager")
    assert hasattr(agent, "PersonaTrait")
    assert hasattr(agent, "MergedPersona")
    assert hasattr(agent, "PERSONA_DIMENSIONS")
    assert hasattr(agent, "persist_trait_to_memory")

    assert "PersonaManager" in agent.__all__
    assert "PERSONA_DIMENSIONS" in agent.__all__
