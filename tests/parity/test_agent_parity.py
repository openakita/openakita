"""Real parity tests for the v2 Agent vs the legacy class.

Per continuation plan section 0.2 / 7 and the G-RC-6 review
checklist, this suite pins three invariants across 12 fixtures
(``tests/parity/fixtures/agent/*.json``):

1.  :func:`inspect.getfile` of the v1 and v2 ``Agent`` classes
    points at **different** files (the new
    ``openakita.agent.core`` module and the renamed
    ``openakita.core._agent_legacy`` module respectively).
2.  The v2 :class:`Agent` is a subclass of the legacy class, so
    ``isinstance(legacy_obj, Agent)`` keeps working and all
    inherited methods continue to resolve.
3.  Each fixture describes a probe (``graph_entry_point``,
    ``graph_nodes``, ``graph_successors`` /
    ``graph_successors_set``, ``risk_decision_next_node``,
    ``supports_lifecycle_node``) and an expected output; the v2
    surface MUST produce exactly that output. Drifting the v2
    lifecycle graph or the risk-routing map without updating the
    fixture corpus fails the suite -- the same regression-corpus
    pattern P5.11 used for ReasoningEngine.

The fixtures double as a regression net: any future change to the
``build_agent_lifecycle_graph`` topology or
``_RISK_LABEL_TO_NODE`` map is caught by a fixture mismatch
instead of an opaque integration test failure.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from openakita.agent.core import (
    Agent as V2Agent,
)
from openakita.agent.core import (
    RiskGateDecision,
    build_agent_lifecycle_graph,
)
from openakita.core._agent_legacy import Agent as V1Agent

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "agent"


def _load_fixtures() -> list[dict]:
    files = sorted(FIXTURE_DIR.glob("*.json"))
    assert files, f"no fixtures under {FIXTURE_DIR!r}"
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def test_v1_v2_module_files_differ() -> None:
    """Real parity invariant: the two Agent classes live in different files."""
    v1_file = inspect.getfile(V1Agent)
    v2_file = inspect.getfile(V2Agent)
    assert v1_file != v2_file
    assert v1_file.endswith("_agent_legacy.py")
    assert v2_file.endswith("agent/core.py") or v2_file.endswith("agent\\core.py")


def test_v2_inherits_from_legacy() -> None:
    """Real parity invariant: v2 *is* a subclass of v1 for backward compat."""
    assert issubclass(V2Agent, V1Agent)
    assert V2Agent.__module__ == "openakita.agent.core"


def test_v2_adds_lifecycle_surface() -> None:
    """The v2 class exposes the new lifecycle + safety + attachment surface.

    The five v2-native method groups documented in the agent/core.py
    module docstring MUST exist on the v2 class but MUST NOT exist
    on the legacy class (otherwise inheritance gave them to v1 too
    and the parity is trivial).
    """
    v2_only = {
        "lifecycle_graph",
        "route_lifecycle",
        "describe_lifecycle",
        "supports_lifecycle_node",
        "classify_inbound_risk",
        "build_destructive_question",
        "should_skip_risk_gate",
        "format_attachment_reference",
        "inline_local_image_if_eligible",
    }
    for name in v2_only:
        assert hasattr(V2Agent, name), f"v2 Agent missing {name}"
        assert not hasattr(V1Agent, name), (
            f"v1 legacy Agent unexpectedly has v2-only attr {name}; "
            f"that would make the v2 surface trivially equal to v1"
        )


def test_fixture_count_is_at_least_ten() -> None:
    """Continuation plan section 7 requires >=10 agent parity fixtures."""
    fixtures = _load_fixtures()
    assert len(fixtures) >= 10, f"only {len(fixtures)} agent fixtures"


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["name"])
def test_agent_parity_probe(fixture: dict) -> None:
    """Execute the probe described in the fixture and assert the result."""
    probe = fixture["probe"]
    expected = fixture["expected"]

    if probe == "graph_entry_point":
        g = build_agent_lifecycle_graph()
        actual = g.entry_point
    elif probe == "graph_nodes":
        g = build_agent_lifecycle_graph()
        actual = sorted(g.nodes)
    elif probe == "graph_successors" or probe == "graph_successors_set":
        g = build_agent_lifecycle_graph()
        actual = sorted(g.successors(fixture["node"]))
    elif probe == "risk_decision_next_node":
        decision = RiskGateDecision(fixture["label"], None, None)
        actual = decision.next_node
    elif probe == "supports_lifecycle_node":
        # Use a bare-init Agent to dodge the heavy legacy __init__.
        agent = V2Agent.__new__(V2Agent)
        agent._lifecycle_graph = build_agent_lifecycle_graph()
        actual = agent.supports_lifecycle_node(fixture["node"])
    elif probe == "classify_inbound_risk_end_to_end":
        # N-G6-1 closure: exercise classify_inbound_risk + should_skip_risk_gate
        # end-to-end on a bare-init Agent (dodge heavy legacy __init__).
        # The fixture pins both the classifier output (risk_level + target_kind +
        # requires_confirmation) and the gate decision (label + reason); a
        # behavioural drift in either layer surfaces here.
        agent = V2Agent.__new__(V2Agent)
        agent._lifecycle_graph = build_agent_lifecycle_graph()
        message = fixture["message"]
        classification = agent.classify_inbound_risk(message)
        decision = agent.should_skip_risk_gate(None, message, classification)
        target_kind_value = (
            classification.target_kind.value
            if classification.target_kind is not None
            else None
        )
        actual = {
            "risk_level": classification.risk_level.value,
            "target_kind": target_kind_value,
            "requires_confirmation": classification.requires_confirmation,
            "skip_gate_label": decision.label,
            "skip_gate_reason": decision.reason,
        }
    else:
        raise AssertionError(f"unknown probe {probe!r}")

    assert actual == expected, (
        f"{fixture['name']}: probe={probe!r} expected={expected!r} actual={actual!r}"
    )


def test_describe_lifecycle_snapshot_shape() -> None:
    """describe_lifecycle returns a JSON-friendly snapshot covering all nodes."""
    agent = V2Agent.__new__(V2Agent)
    agent._lifecycle_graph = build_agent_lifecycle_graph()
    snap = agent.describe_lifecycle()
    assert snap["entry_point"] == "init"
    assert "classify_risk" in snap["nodes"]
    assert "run_loop" in snap["successors"]
    assert snap["successors"]["init"] == ["validate_input"]


def test_risk_gate_decision_value_semantics() -> None:
    """RiskGateDecision equality + repr behave as documented."""
    a = RiskGateDecision("skip", "trust_mode", None)
    b = RiskGateDecision("skip", "trust_mode", None)
    c = RiskGateDecision("run", None, None)
    assert a == b
    assert a != c
    assert "skip" in repr(a)
    assert a.next_node == "run_loop"
    assert c.next_node == "run_loop"


def test_v2_specific_routing_label_set() -> None:
    """The four routing labels pinned in the module are stable.

    A future refactor that adds / drops a label without updating the
    fixture corpus surfaces here.
    """
    from openakita.agent.core import _RISK_LABEL_TO_NODE

    assert set(_RISK_LABEL_TO_NODE) == {"skip", "confirm", "run", "abort"}
