"""Re-export shim — confirmation state moved to ``agent.confirmation``.

The canonical home of :class:`ConfirmationDecision`,
:class:`PendingRiskConfirmation`, and
:class:`PendingRiskConfirmationStore` is now
:mod:`openakita.agent.confirmation` per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``.

This shim preserves the legacy import path until Phase 8
mechanical cleanup so the following callers keep working:

* :mod:`openakita.api.routes.chat`
* :mod:`tests.unit.test_destructive_intent_gate`
* :mod:`tests.unit.test_risk_authorized_replay`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.confirmation import (
    ConfirmationDecision,
    PendingRiskConfirmation,
    PendingRiskConfirmationStore,
    get_confirmation_store,
    normalize_confirmation_answer,
)

__all__ = [
    "ConfirmationDecision",
    "PendingRiskConfirmation",
    "PendingRiskConfirmationStore",
    "get_confirmation_store",
    "normalize_confirmation_answer",
]
