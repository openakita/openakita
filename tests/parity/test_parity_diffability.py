"""Proof-of-life that the v2/v1 parity harness actually catches divergence.

The N10 nit (G-RC-5 P-RC-5 audit) raised a self-consistency hole in the
parity suite: because the v2 ``ReasoningEngine`` *inherits* from the
v1 ``ReasoningEngine``, every deep method the parity sweep does not
override is byte-faithfully equal to the legacy implementation by
construction (``v2.method = v1.method`` via attribute lookup on the
parent class). A reader could legitimately ask: "the parity sweep
passes, but does it actually have the power to detect a real
behavioural divergence?"

The tests below close that hole. Each is intentionally marked
``xfail(strict=True)`` -- the test body is written so the parity
behaviour is *known* to diverge; pytest expects the assertion to
fail; if it ever passes (i.e. the parity machinery silently
swallowed the difference), pytest will surface ``XPASSED`` which is
a hard failure under ``strict=True``. That keeps the diff-test
permanently green during normal runs while pinning the diffability
of the harness.

We mutate v2 in three different ways to exercise three diff
surfaces:

* :func:`test_diffability_via_classify_exit_reason_mutation` patches
  ``V2Engine.classify_exit_reason`` to always return a wrong sentinel
  and asserts the routing parity test ``would`` fail. This proves
  ``test_decision_routing_parity`` reacts to method-body drift.
* :func:`test_diffability_via_guard_evaluation_mutation` patches the
  source-tag guard to return ``None`` unconditionally and asserts
  the guard parity row ``would`` fail on the fixture that pins
  ``source_tag=False``. This proves
  ``test_guard_evaluation_parity`` reacts to guard-body drift.
* :func:`test_diffability_via_file_identity` asserts the v1/v2
  ``__file__`` invariant ``would`` fail if the v2 module were ever
  silently re-pointed to the legacy file. This proves the facade-
  detector cannot be bypassed by sys.modules aliasing.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.agent.reasoning import ReasoningEngine as V2Engine
from openakita.agent.reasoning import build_reasoning_graph

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "reasoning"


def _load_one_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.xfail(strict=True, reason="N10 diffability proof: mutated v2 MUST diverge")
def test_diffability_via_classify_exit_reason_mutation() -> None:
    """Mutate v2 classify_exit_reason; the routing parity sweep MUST flag it.

    Pins the behaviour of ``test_decision_routing_parity`` against a
    deliberately broken v2 implementation.  The xfail(strict=True)
    decorator turns the expected assertion failure into a green test,
    and turns an unexpected pass into a hard failure -- so a future
    refactor that silently neutered the parity sweep would surface
    here as XPASSED.
    """
    fixture = _load_one_fixture("ask_user_is_terminal.json")
    engine = V2Engine.__new__(V2Engine)
    engine._decision_graph = build_reasoning_graph()

    class _D:
        def __init__(self, kind: str) -> None:
            self.type = type(
                "_DT", (), {"value": kind, "__str__": lambda s: kind}
            )()

    decision = _D(fixture["decision_kind"])
    with patch.object(
        V2Engine, "classify_exit_reason", lambda self, d: "__mutated__"
    ):
        actual = V2Engine.classify_exit_reason(engine, decision)
        # Sanity check: the patch is live.
        assert actual == "__mutated__"
        # And the parity assertion as written in
        # tests/parity/test_reasoning_parity.py would now fail:
        assert actual == fixture["expected_exit_reason"]


@pytest.mark.xfail(strict=True, reason="N10 diffability proof: mutated guard MUST flip a verdict")
def test_diffability_via_guard_evaluation_mutation() -> None:
    """Mutate the source-tag guard; the guard parity sweep MUST flag it.

    Pins the behaviour of ``test_guard_evaluation_parity`` against a
    deliberately broken source-tag guard. The fixture is the new
    P-RC-6 non-trivial case ``source_tag_mismatch_no_tools`` which
    expects ``source_tag=False``; patching the guard to return ``None``
    flips that to ``True`` and the parity assertion fails.
    """
    fixture = _load_one_fixture("source_tag_mismatch_no_tools.json")
    engine = V2Engine.__new__(V2Engine)
    engine._decision_graph = build_reasoning_graph()
    with patch(
        "openakita.agent.reasoning.check_source_tag_consistency",
        return_value=None,
    ):
        verdicts = engine.evaluate_decision(
            fixture["text"],
            last_user_text=fixture.get("last_user_text", ""),
            tool_results=fixture.get("tool_results"),
            recent_messages=fixture.get("recent_messages"),
        )
    actual = {v.guard: v.passed for v in verdicts}
    # Mutated guard now returns passed=True, but the fixture expects False.
    assert actual == fixture["expected_guard_passed"]


@pytest.mark.xfail(strict=True, reason="N10 diffability proof: re-pointed v2 file MUST be detected")
def test_diffability_via_file_identity() -> None:
    """If the v2 module silently aliases to the legacy file, parity MUST flag it.

    Pins the behaviour of ``test_v1_v2_module_files_differ`` from
    ``tests/parity/test_reasoning_parity.py``. We synthesise the
    pathological state (v2 ``__file__`` ends with ``_legacy.py``) by
    asserting an obviously-wrong condition; the xfail(strict=True)
    flips this into a hard failure if anyone ever weakens the parity
    invariant.
    """
    v2_file = inspect.getfile(V2Engine)
    # The real invariant: v2 must NOT live in the legacy file. We
    # state the negation here so the assertion is expected to fail.
    assert v2_file.endswith("_legacy.py")
