"""Re-export shim — pending approvals store moved to ``agent.pending_approvals``.

The canonical home of :class:`PendingApproval` and
:class:`PendingApprovalsStore` is now
:mod:`openakita.agent.pending_approvals` per ADR-0003 and the
Phase 2 sub-commit plan in ``docs/revamp/core_audit.md``.

This shim preserves the legacy import path
``openakita.core.pending_approvals`` until Phase 8 mechanical
cleanup so the following callers keep working unchanged:

* :mod:`openakita.api.server`
* :mod:`openakita.api.routes.pending_approvals`
* :mod:`tests.unit.test_pending_approvals_store`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.pending_approvals import (
    ARCHIVE_AFTER_SECONDS,
    DEFAULT_TTL_SECONDS,
    EventHook,
    PendingApproval,
    PendingApprovalsStore,
    PendingApprovalStatus,
    get_pending_approvals_store,
    reset_pending_approvals_store,
)

__all__ = [
    "ARCHIVE_AFTER_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "EventHook",
    "PendingApproval",
    "PendingApprovalStatus",
    "PendingApprovalsStore",
    "get_pending_approvals_store",
    "reset_pending_approvals_store",
]
