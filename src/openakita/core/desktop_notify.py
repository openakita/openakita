"""Re-export shim — desktop notify moved to ``agent.desktop_notify``.

Canonical home: :mod:`openakita.agent.desktop_notify`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers:

* :mod:`openakita.scheduler.executor`
* :mod:`openakita.api.routes.tasks`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.desktop_notify import (
    notify_task_completed,
    notify_task_completed_async,
    send_desktop_notification,
    send_desktop_notification_async,
)

__all__ = [
    "notify_task_completed",
    "notify_task_completed_async",
    "send_desktop_notification",
    "send_desktop_notification_async",
]
