"""Builtin guardrails.

A small, useful starter set. Templates and plugin manifests can pin
any of these by referencing their ``type`` (matching their class name
in lowercase, hyphenated):

* ``min_length``       -> :class:`MinLengthGuardrail`
* ``max_length``       -> :class:`MaxLengthGuardrail`
* ``required_fields``  -> :class:`RequiredFieldsGuardrail`
* ``regex``            -> :class:`RegexGuardrail`

Plugins ship custom guardrails through their optional
``register_guardrails(host)`` hook (ADR-0009). Custom guardrails
implement the :class:`Guardrail` protocol and may be referenced by
the same string-tag mechanism.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..supervisor import DelegationResult
from .runner import Guardrail, GuardrailContext, GuardrailVerdict

__all__ = [
    "MinLengthGuardrail",
    "MaxLengthGuardrail",
    "RequiredFieldsGuardrail",
    "RegexGuardrail",
]


# ---------------------------------------------------------------------------
# Length checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinLengthGuardrail(Guardrail):
    """Reject deliverables shorter than ``n`` characters in ``message``."""

    n: int
    name: str = "min_length"

    def check(
        self, result: DelegationResult, ctx: GuardrailContext
    ) -> tuple[GuardrailVerdict, str]:
        text = result.message or ""
        if len(text) < self.n:
            return (
                GuardrailVerdict.RETRY,
                f"deliverable is {len(text)} chars, minimum {self.n}",
            )
        return GuardrailVerdict.OK, ""


@dataclass(frozen=True)
class MaxLengthGuardrail(Guardrail):
    """Reject deliverables longer than ``n`` characters in ``message``."""

    n: int
    name: str = "max_length"

    def check(
        self, result: DelegationResult, ctx: GuardrailContext
    ) -> tuple[GuardrailVerdict, str]:
        text = result.message or ""
        if len(text) > self.n:
            return (
                GuardrailVerdict.RETRY,
                f"deliverable is {len(text)} chars, maximum {self.n}",
            )
        return GuardrailVerdict.OK, ""


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequiredFieldsGuardrail(Guardrail):
    """Treat ``result.message`` as JSON and require these top-level fields.

    HARD_FAIL when the message is not even a JSON object (the LLM
    will not be able to fix that by retrying with the same role and
    the same instructions; the supervisor's replan path is the right
    response).
    """

    fields: tuple[str, ...]
    name: str = "required_fields"

    def check(
        self, result: DelegationResult, ctx: GuardrailContext
    ) -> tuple[GuardrailVerdict, str]:
        text = result.message or ""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return (
                GuardrailVerdict.HARD_FAIL,
                f"deliverable is not valid JSON: {exc.msg}",
            )
        if not isinstance(payload, dict):
            return (
                GuardrailVerdict.HARD_FAIL,
                "deliverable JSON top-level must be an object",
            )
        missing = [f for f in self.fields if f not in payload]
        if missing:
            return (
                GuardrailVerdict.RETRY,
                f"deliverable is missing required fields: {missing}",
            )
        return GuardrailVerdict.OK, ""


@dataclass(frozen=True)
class RegexGuardrail(Guardrail):
    """Require ``result.message`` to match a regex.

    Useful for "must mention shot ids", "must contain the asset
    handle", etc. ``flags`` accepts the standard re flag values; pass
    ``re.MULTILINE | re.DOTALL`` if needed.
    """

    pattern: str
    flags: int = 0
    name: str = "regex"

    def check(
        self, result: DelegationResult, ctx: GuardrailContext
    ) -> tuple[GuardrailVerdict, str]:
        text = result.message or ""
        if re.search(self.pattern, text, self.flags) is None:
            return (
                GuardrailVerdict.RETRY,
                f"deliverable does not match required pattern {self.pattern!r}",
            )
        return GuardrailVerdict.OK, ""
