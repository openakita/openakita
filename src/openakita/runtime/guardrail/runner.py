"""Guardrail runner.

The runner is intentionally small. Real guardrail logic lives in
individual :class:`Guardrail` implementations — either the builtins
shipped in :mod:`builtin` or plugin-supplied ones declared through the
``WORKBENCH`` manifest (see ADR-0009). The runner just sequences them
and aggregates the verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..supervisor import DelegationResult

__all__ = [
    "Guardrail",
    "GuardrailDecision",
    "GuardrailVerdict",
    "GuardrailRunner",
    "GuardrailContext",
]


class GuardrailVerdict(StrEnum):
    OK = "ok"
    RETRY = "retry"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True)
class GuardrailContext:
    """Side context that some guardrails want to consult."""

    speaker: str
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailDecision:
    """Aggregate result of running every guardrail attached to a node."""

    verdict: GuardrailVerdict
    failures: list[tuple[str, str]] = field(default_factory=list)
    """List of ``(guardrail_name, reason)`` tuples; empty on OK."""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            # Tuples -> lists so the payload survives json.dumps cleanly.
            "failures": [list(item) for item in self.failures],
        }


# ---------------------------------------------------------------------------
# Guardrail protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Guardrail(Protocol):
    """A single quality check."""

    name: str

    def check(
        self, result: DelegationResult, ctx: GuardrailContext
    ) -> tuple[GuardrailVerdict, str]:
        """Return ``(verdict, reason)``.

        ``reason`` is mandatory on RETRY / HARD_FAIL; it MAY be empty
        on OK. ``reason`` will be relayed verbatim to the LLM (RETRY
        case) so write it as something a model can act on.
        """
        ...


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class GuardrailRunner:
    """Sequences guardrails and aggregates the verdict.

    Order matters: HARD_FAIL trumps RETRY trumps OK. Even if an early
    guardrail says HARD_FAIL we still run the remaining guardrails so
    we can collect every failure for diagnosis (mirrors CrewAI's
    failure aggregation).
    """

    def __init__(self, guardrails: Sequence[Guardrail] | None = None) -> None:
        self._guardrails: list[Guardrail] = list(guardrails or ())

    def add(self, guardrail: Guardrail) -> None:
        self._guardrails.append(guardrail)

    def __len__(self) -> int:
        return len(self._guardrails)

    def evaluate(
        self,
        result: DelegationResult,
        ctx: GuardrailContext,
    ) -> GuardrailDecision:
        """Run every guardrail and aggregate."""
        if not self._guardrails:
            return GuardrailDecision(verdict=GuardrailVerdict.OK)

        verdict = GuardrailVerdict.OK
        failures: list[tuple[str, str]] = []
        for g in self._guardrails:
            v, reason = g.check(result, ctx)
            if v is GuardrailVerdict.HARD_FAIL:
                failures.append((g.name, reason or "hard fail"))
                verdict = GuardrailVerdict.HARD_FAIL
            elif v is GuardrailVerdict.RETRY:
                failures.append((g.name, reason or "retry"))
                if verdict is not GuardrailVerdict.HARD_FAIL:
                    verdict = GuardrailVerdict.RETRY
            else:
                continue
        return GuardrailDecision(verdict=verdict, failures=failures)
