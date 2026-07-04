"""Re-export shim — tool-result budget moved to ``agent.tool_result_budget``.

Canonical home: :mod:`openakita.agent.tool_result_budget`. Shim
preserved until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.tool_result_budget import (
    DEFAULT_MAX_RESULT_CHARS,
    OVERFLOW_DIR,
    truncate_tool_result,
)

__all__ = [
    "DEFAULT_MAX_RESULT_CHARS",
    "OVERFLOW_DIR",
    "truncate_tool_result",
]
