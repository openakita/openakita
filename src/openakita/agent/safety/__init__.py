"""Pre-LLM safety gates -- destructive-intent classifier + risk authorization.

The pre-ReAct risk gate is a pure classification and bookkeeping
responsibility rather than an Agent state-machine concern, so it lives under
``openakita.agent.safety.*``.

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
