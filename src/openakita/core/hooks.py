"""Re-export shim — hook system moved to ``agent.hooks``.

The canonical home of :class:`HookEvent`, :class:`HookHandler`,
:class:`HookResult`, :class:`HookExecutor`, and the global
``get_hook_executor`` accessor is now :mod:`openakita.agent.hooks`
per ADR-0003 and the Phase 2 sub-commit plan in
``docs/revamp/core_audit.md``.

This shim preserves the legacy import path until Phase 8
mechanical cleanup.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.hooks import (
    CallbackHook,
    HookEvent,
    HookExecutor,
    HookHandler,
    HookResult,
    ShellHook,
    get_hook_executor,
    set_hook_executor,
)

__all__ = [
    "CallbackHook",
    "HookEvent",
    "HookExecutor",
    "HookHandler",
    "HookResult",
    "ShellHook",
    "get_hook_executor",
    "set_hook_executor",
]
