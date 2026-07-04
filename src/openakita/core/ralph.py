"""Re-export shim — Ralph loop moved to ``agent.ralph``.

Canonical home: :mod:`openakita.agent.ralph`. Shim preserved at
the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.ralph import (
    RalphLoop,
    StopHook,
    Task,
    TaskResult,
    TaskStatus,
)

__all__ = [
    "RalphLoop",
    "StopHook",
    "Task",
    "TaskResult",
    "TaskStatus",
]
