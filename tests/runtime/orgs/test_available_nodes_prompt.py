"""Sprint-5 unexpected-finding #1: closed node list in producer prompt.

Audit v5 §4.2 + §5.3: the v16 producer LLM invented a ``director``
node that did not exist in the spec. The dispatch parser tolerated it
(unknown_target -> skip) but the invention still cost one LLM round
and polluted the reply. Listing the real node ids in the system
prompt at depth 0 measurably reduces invention.

Pins:

* The block is emitted at depth 0.
* The block is NOT emitted for sub-agents (depth >= 1) so we do not
  encourage them to cosplay coordinator.
* The block renders both ``node_id`` and the role / label so the LLM
  picks the right target.
* An empty ``available_nodes`` tuple skips the block entirely
  (legacy / single-node org).
"""

from __future__ import annotations

from openakita.orgs._default_agent_builder import _persona_system_prompt
from openakita.orgs._runtime_agent_pipeline import AgentSpec


def _spec(**overrides: object) -> AgentSpec:
    defaults: dict[str, object] = {
        "org_id": "o1",
        "node_id": "producer",
        "role": "producer",
        "external_tools": (),
        "enable_file_tools": False,
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


def test_depth_zero_emits_available_nodes_block() -> None:
    """case id: p05.ennum.depth0_lists_nodes"""

    spec = _spec(
        available_nodes=(
            ("screenwriter", "screenwriter"),
            ("art-director", "art-director"),
            ("wb-hh-image", "image workbench"),
        )
    )
    prompt = _persona_system_prompt(spec, depth=0)
    assert "Available child nodes" in prompt
    assert "screenwriter" in prompt
    assert "art-director" in prompt
    assert "wb-hh-image" in prompt
    assert "Do NOT invent new node ids" in prompt


def test_subagent_depth_skips_block() -> None:
    """case id: p05.ennum.depth1_no_list

    Sub-agents must not see the dispatch menu -- the orchestrator owns
    the multi-node coordination at depth 0.
    """

    spec = _spec(
        node_id="screenwriter",
        role="screenwriter",
        available_nodes=(("art-director", "art-director"),),
    )
    prompt = _persona_system_prompt(spec, depth=1)
    assert "Available child nodes" not in prompt


def test_empty_available_nodes_skips_block() -> None:
    """case id: p05.ennum.no_nodes_no_block

    Legacy / single-node orgs leave ``available_nodes`` at its default
    empty tuple. The prompt must look exactly like the Sprint-4
    producer prompt for those (no orphan section header).
    """

    prompt = _persona_system_prompt(_spec(), depth=0)
    assert "Available child nodes" not in prompt


def test_nodes_without_labels_still_listed_by_id() -> None:
    """case id: p05.ennum.label_optional"""

    spec = _spec(available_nodes=(("x1", ""), ("x2", "")))
    prompt = _persona_system_prompt(spec, depth=0)
    assert "- x1" in prompt
    assert "- x2" in prompt
