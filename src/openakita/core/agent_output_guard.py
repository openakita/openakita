"""Re-export shim — sub-agent output guard moved to ``agent.output_guard``.

The canonical home is now :mod:`openakita.agent.output_guard`, per
ADR-0003 and the Phase 2 sub-commit plan in
``docs/revamp/core_audit.md``. This shim keeps the existing import
paths in ``agents/orchestrator.py`` and ``tests/smoke/`` working
until Phase 8 mechanically removes the legacy ``core/`` tree.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.output_guard import (
    CODE_EXEC_TOOLS,
    DISCLAIMER_TEXT,
    detect_numeric_output,
    detect_numeric_task,
    validate_no_fabricated_numbers,
)

__all__ = [
    "CODE_EXEC_TOOLS",
    "DISCLAIMER_TEXT",
    "detect_numeric_output",
    "detect_numeric_task",
    "validate_no_fabricated_numbers",
]
