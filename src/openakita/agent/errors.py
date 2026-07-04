"""Agent-package exceptions for the v2 stack.

Ported from ``openakita.core.errors`` per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``. The legacy module
becomes a thin re-export shim until Phase 8 mechanically removes it,
so every existing caller (``llm/client.py``, ``orgs/runtime.py``, the
test suite, etc.) keeps working without an import-graph edit.

Only one exception lives here: :class:`UserCancelledError`. The richer
:class:`openakita.tools.errors.ToolError` taxonomy is unrelated and
stays in :mod:`openakita.tools.errors`.
"""

from __future__ import annotations

__all__ = ["UserCancelledError"]


class UserCancelledError(Exception):
    """User actively stopped the running task.

    Raised when a user sends a stop instruction (e.g. "停止", "stop",
    "cancel"). The exception lets long-running LLM calls and tool
    executions unwind cleanly through their normal except paths.

    Attributes:
        reason: human-readable cancel reason, typically the user's
            verbatim instruction.
        source: stage where the cancel was observed
            (``"llm_call"`` or ``"tool_exec"``).
    """

    def __init__(self, reason: str = "", source: str = "") -> None:
        self.reason = reason
        self.source = source
        super().__init__(f"User cancelled ({source}): {reason}")
