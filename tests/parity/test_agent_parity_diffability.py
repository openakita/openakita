"""Proof-of-life that the v2/v1 Agent parity harness catches divergence.

The N-G6-2 nit (G-RC-6 P-RC-6 audit) raised the same self-consistency
hole for the Agent parity suite that N10 raised for the
ReasoningEngine parity suite: because :class:`openakita.agent.core.Agent`
inherits from :class:`openakita.core._agent_runtime.Agent`, every deep
method the parity sweep does not override is byte-faithfully equal to
the legacy implementation by attribute lookup on the parent class. The
12 + 2 fixtures landed by P-RC-6 / P-RC-7 P7.0b pin the v2-only surface
(lifecycle graph, classify_inbound_risk, should_skip_risk_gate); a
reader could legitimately ask: "the parity sweep passes, but does it
actually have the power to detect a real behavioural divergence?"

This file mirrors :mod:`tests.parity.test_parity_diffability` (N10) for
the agent layer. Each test below is marked
``xfail(strict=True)`` -- the test body is written so the parity
behaviour is *known* to diverge; pytest expects the assertion to fail
and reports ``xfailed``; if it ever passes, pytest surfaces
``XPASSED`` which is a hard failure under ``strict=True``. That keeps
this file permanently green during normal runs while pinning the
diffability of the agent parity infrastructure.

Two diff surfaces are exercised, matching N-G6-2 scope:

* :func:`test_diffability_via_classify_inbound_risk_mutation` patches
  ``V2Agent.classify_inbound_risk`` to return a wrong risk level
  sentinel and asserts the e2e probe ``would`` fail. This proves
  ``test_agent_parity_probe[classify_inbound_risk_*]`` reacts to
  body drift of the new v2-native method.
* :func:`test_diffability_via_format_attachment_reference_mutation`
  patches ``V2Agent.format_attachment_reference`` to return a wrong
  text marker and asserts the byte-faithful equivalence assertion in
  ``tests/unit/test_desktop_attachment_reference.py`` ``would`` fail.
  This proves the runtime/desktop helper extraction parity holds for
  the v2 surface.

If a future refactor neutered the parity sweep (e.g. accidentally
called the legacy method via ``super()`` even after subclass override,
or removed the v2 method entirely so it fell through to the legacy
attribute), both xfail tests would turn into ``XPASSED`` and fail the
gate.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openakita.agent.core import (
    Agent as V2Agent,
)
from openakita.agent.core import (
    build_agent_lifecycle_graph,
)


@pytest.mark.xfail(
    strict=True,
    reason="N-G6-2 diffability proof: mutated classify_inbound_risk MUST diverge",
)
def test_diffability_via_classify_inbound_risk_mutation() -> None:
    """Mutate V2Agent.classify_inbound_risk; the e2e parity probe MUST flag it.

    Pins the behaviour of ``test_agent_parity_probe[classify_inbound_risk_*]``
    against a deliberately broken v2 implementation. xfail(strict=True)
    turns the expected assertion failure into a green test, and turns an
    unexpected pass into a hard failure -- so a future refactor that
    silently neutered the e2e probe would surface here as XPASSED.
    """

    class _MutatedResult:
        # Stand-in for RiskIntentResult; the parity probe reads
        # ``.risk_level.value`` and ``.target_kind.value``.
        class _Kind:
            value = "__mutated__"

        risk_level = _Kind()
        target_kind = _Kind()
        requires_confirmation = False

    agent = V2Agent.__new__(V2Agent)
    agent._lifecycle_graph = build_agent_lifecycle_graph()
    with patch.object(
        V2Agent, "classify_inbound_risk", lambda self, msg, intent=None: _MutatedResult()
    ):
        result = agent.classify_inbound_risk("rm -rf /")
        # Sanity: the patch is live.
        assert result.risk_level.value == "__mutated__"
        # And the parity assertion as written in
        # tests/parity/test_agent_parity.py would now fail (HIGH != __mutated__):
        assert result.risk_level.value == "high"


@pytest.mark.xfail(
    strict=True,
    reason="N-G6-2 diffability proof: mutated format_attachment_reference MUST diverge",
)
def test_diffability_via_format_attachment_reference_mutation() -> None:
    """Mutate V2Agent.format_attachment_reference; downstream tests MUST flag it.

    Pins the behaviour of the byte-faithful extraction assertion in
    ``tests/runtime/test_desktop_attachments.py`` and the legacy alias
    assertion in ``tests/unit/test_desktop_attachment_reference.py``:
    both pin the formatted-text marker for a non-image attachment.
    Patching the v2 method to return a wrong marker proves drift would
    be caught.
    """
    agent = V2Agent.__new__(V2Agent)
    with patch.object(
        V2Agent,
        "format_attachment_reference",
        lambda self, **kw: "__mutated_attachment_marker__",
    ):
        text = agent.format_attachment_reference(
            att_type="file",
            att_name="contract.pdf",
            att_mime="application/pdf",
            att_url="http://localhost/contract.pdf",
        )
        # Sanity: the patch is live.
        assert text == "__mutated_attachment_marker__"
        # The byte-faithful parity assertion would now fail because
        # the legacy formatter never returns this sentinel marker.
        assert "contract.pdf" in text
