"""Deliverable quality enforcement.

Implements ADR-0004's complement to stall detection: while the stall
detector decides whether the *process* is healthy, guardrails decide
whether each individual *deliverable* is acceptable. CrewAI's
``process_guardrail`` is the design reference (see
``D:/claw-research/repos/crewAI/lib/crewai/src/crewai/process.py``).

A guardrail is a small predicate over a delegation result. The runner
collects every guardrail attached to a node / mode (ADR-0009 lets the
plugin manifest declare them per mode), evaluates them in order, and
returns a verdict:

* OK              - all guardrails passed; supervisor accepts.
* RETRY           - failure is recoverable; supervisor feeds the reason
                    back to the speaker and re-evaluates the next turn
                    without bumping replan / stall counters.
* HARD_FAIL       - guardrail says "this output is structurally wrong
                    and cannot be made acceptable by retrying"; the
                    supervisor escalates to a stall (so the existing
                    replan path takes over).

Public exports:
- ``Guardrail`` protocol;
- ``GuardrailRunner``;
- ``GuardrailVerdict``, ``GuardrailDecision``;
- builtin guardrails: ``MinLengthGuardrail``, ``MaxLengthGuardrail``,
  ``RequiredFieldsGuardrail``, ``RegexGuardrail``.
"""

from __future__ import annotations

from .builtin import (
    MaxLengthGuardrail,
    MinLengthGuardrail,
    RegexGuardrail,
    RequiredFieldsGuardrail,
)
from .runner import (
    Guardrail,
    GuardrailContext,
    GuardrailDecision,
    GuardrailRunner,
    GuardrailVerdict,
)

__all__ = [
    "Guardrail",
    "GuardrailContext",
    "GuardrailDecision",
    "GuardrailRunner",
    "GuardrailVerdict",
    "MinLengthGuardrail",
    "MaxLengthGuardrail",
    "RequiredFieldsGuardrail",
    "RegexGuardrail",
]
