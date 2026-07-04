"""Re-export shim — audit logger moved to ``agent.audit``.

The canonical home of :class:`AuditLogger` and the global
``get_audit_logger`` accessor is now :mod:`openakita.agent.audit`
per ADR-0003 and the Phase 2 sub-commit plan in
``docs/revamp/core_audit.md``. The legacy module path
``openakita.core.audit_logger`` remains as a re-export shim so the
following call sites keep working without an audit sweep:

* :mod:`openakita.api.server`
* :mod:`openakita.api.routes.health`
* :mod:`openakita.api.routes.config`
* :mod:`openakita.agents.task_queue`
* :mod:`openakita.core.policy_v2.global_engine`
* :mod:`openakita.core.policy_v2.hot_reload`

Phase 8 mechanically removes the legacy ``core/`` tree and this
shim along with it.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.audit import (
    DEFAULT_AUDIT_PATH,
    AuditLogger,
    get_audit_logger,
    reset_audit_logger,
)

__all__ = [
    "DEFAULT_AUDIT_PATH",
    "AuditLogger",
    "get_audit_logger",
    "reset_audit_logger",
]
