"""Real parity tests for the v2 ReasoningEngine vs the legacy class.

The continuation plan section 0.2 calls out the
**facade-self-equivalence false positive**: any v2 module that
merely re-exports its v1 counterpart would trivially "pass" parity
checks. To pin a true rewrite, these tests assert two invariants
across ten representative inputs (six Decision kinds and four
guard-evaluation contexts):

1. ``inspect.getfile(...)`` of the v1 and v2 ``ReasoningEngine``
   classes points at **different** files (the new
   ``openakita.agent.reasoning`` module and the renamed
   ``openakita.core._reasoning_engine_legacy`` module
   respectively).
2. For each fixture under ``tests/parity/fixtures/reasoning/``,
   the decision-routing label and the guard-verdict summary
   produced by the v2 engine match the legacy outputs.

The fixtures live under :data:`FIXTURE_DIR` as plain JSON so they
double as a regression corpus: any future change to a guard or to
the routing table is caught by a fixture mismatch instead of an
opaque integration test failure.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from openakita.agent.reasoning import ReasoningEngine as V2Engine
from openakita.agent.reasoning import build_reasoning_graph
from openakita.core._reasoning_engine_legacy import (
    ReasoningEngine as V1Engine,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "reasoning"


def _load_fixtures() -> list[dict]:
    files = sorted(FIXTURE_DIR.glob("*.json"))
    assert files, f"no fixtures under {FIXTURE_DIR!r}"
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def test_v1_v2_module_files_differ() -> None:
    """Real parity invariant: the two engines live in different files."""
    v1_file = inspect.getfile(V1Engine)
    v2_file = inspect.getfile(V2Engine)
    assert v1_file != v2_file
    assert v1_file.endswith("_reasoning_engine_legacy.py")
    assert v2_file.endswith("agent/reasoning.py") or v2_file.endswith(
        "agent\\reasoning.py"
    )


def test_v2_inherits_from_legacy() -> None:
    """Real parity invariant: v2 *is* a subclass of v1 for backward compat."""
    assert issubclass(V2Engine, V1Engine)
    # And ``__file__`` of v2 still resolves to agent/reasoning.py, not
    # _reasoning_engine_legacy.py (sanity check against accidental
    # re-export).
    assert V2Engine.__module__ == "openakita.agent.reasoning"


def test_routing_table_is_stable() -> None:
    """Real parity invariant: the v2 routing table covers every legacy DecisionType."""
    from openakita.core._reasoning_engine_legacy import DecisionType

    graph = build_reasoning_graph()
    # Each entry-point successor list must be deterministic.
    assert "reason" in graph.nodes
    succ = graph.successors("reason")
    assert set(succ) == {"act", "verify", "finalize", "reason"}
    # And every DecisionType.value must be a known routing key.
    engine = V2Engine.__new__(V2Engine)
    engine._decision_graph = graph
    for dt in DecisionType:
        assert engine.supports_decision_kind(dt.value), (
            f"DecisionType.{dt.name} ({dt.value!r}) missing from routing table"
        )


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["name"])
def test_decision_routing_parity(fixture: dict) -> None:
    """For each fixture, v2 ``classify_exit_reason`` matches the recorded label.

    The legacy engine does not expose a public ``classify_exit_reason``
    (the equivalent is buried in the ``run()`` if/elif cascade), so
    the fixture records the legacy *output token* directly and the
    test asserts the v2 method produces the same token. This is the
    real parity check: a v2 rewrite that diverged would surface as a
    fixture mismatch.
    """
    engine = V2Engine.__new__(V2Engine)
    engine._decision_graph = build_reasoning_graph()

    class _D:
        def __init__(self, kind: str) -> None:
            self.type = type(
                "_DT", (), {"value": kind, "__str__": lambda s: kind}
            )()

    decision = _D(fixture["decision_kind"]) if fixture["decision_kind"] else None
    actual = engine.classify_exit_reason(decision)
    assert actual == fixture["expected_exit_reason"]
    assert engine.is_terminal_decision(decision) == fixture["is_terminal"]


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["name"])
def test_guard_evaluation_parity(fixture: dict) -> None:
    """For each fixture, v2 ``evaluate_decision`` matches the recorded verdicts."""
    engine = V2Engine.__new__(V2Engine)
    engine._decision_graph = build_reasoning_graph()
    verdicts = engine.evaluate_decision(
        fixture["text"],
        last_user_text=fixture.get("last_user_text", ""),
        tool_results=fixture.get("tool_results"),
        recent_messages=fixture.get("recent_messages"),
    )
    actual = {v.guard: v.passed for v in verdicts}
    expected = fixture["expected_guard_passed"]
    assert actual == expected, (
        f"guard verdict drift on fixture {fixture['name']!r}: "
        f"v2={actual!r} expected={expected!r}"
    )


def test_fixtures_include_non_trivial_divergence() -> None:
    """N9 (G-RC-5 P-RC-5 audit) -- at least five fixtures MUST flip a guard.

    The original P-RC-5 fixture set was dominated by happy-path Decision
    routing cases where every guard returned ``passed=True``. That made
    ``test_guard_evaluation_parity`` a structural identity test on the v2
    routing table without ever exercising the actual guard divergence
    branches. The four guards we expect to flip in practice are
    ``source_tag``, ``tool_failure_ack``, ``unbacked_action``, and
    ``waiting_for_user`` -- this test pins that at least five fixtures
    flip *some* guard, with at least one fixture per guard kind.
    """
    fixtures = _load_fixtures()
    assert len(fixtures) >= 10, f"only {len(fixtures)} fixtures present"
    flipping = [f for f in fixtures if not all(f["expected_guard_passed"].values())]
    assert len(flipping) >= 5, (
        f"only {len(flipping)} non-trivial fixtures; need >= 5 with a "
        f"failing guard to make the parity sweep meaningful"
    )
    # Per-guard coverage: every interesting guard must flip in at least one fixture.
    flipping_guards: set[str] = set()
    for f in flipping:
        for k, v in f["expected_guard_passed"].items():
            if not v:
                flipping_guards.add(k)
    for required in ("source_tag", "tool_failure_ack", "unbacked_action", "waiting_for_user"):
        assert required in flipping_guards, (
            f"no fixture exercises guard {required!r}; please add one"
        )
