"""Pre-LLM safety gates -- destructive-intent classifier + risk authorization.

This package houses the v2 home for the pre-ReAct risk gate that was
historically embedded in the 9602 LOC ``core/agent.py`` god-class.
Per continuation plan section 7 (P-RC-6) the gate is a pure
classification / book-keeping responsibility (not an Agent state
machine concern), so it lives under ``openakita.agent.safety.*`` and
the legacy module re-exports the private aliases for backward
compatibility during the cutover.

See :mod:`openakita.agent.safety.destructive_intent`.
"""

from __future__ import annotations

from .destructive_intent import (
    DESTRUCTIVE_VERBS,
    TRUST_MODE_MUST_CONFIRM_TARGETS,
    build_destructive_intent_question,
    check_trust_mode_skip,
    check_trusted_path_skip,
    classify_risk_intent,
    consume_risk_authorization,
    summarize_destructive_action,
)

__all__ = [
    "DESTRUCTIVE_VERBS",
    "TRUST_MODE_MUST_CONFIRM_TARGETS",
    "build_destructive_intent_question",
    "check_trust_mode_skip",
    "check_trusted_path_skip",
    "classify_risk_intent",
    "consume_risk_authorization",
    "summarize_destructive_action",
]
