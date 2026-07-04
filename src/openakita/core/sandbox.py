"""Re-export shim — bash sandbox moved to ``agent.sandbox``.

Canonical home: :mod:`openakita.agent.sandbox`. Shim preserved
at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.sandbox import (
    CommandSandbox,
    SandboxExecutor,
    SandboxPolicy,
    SandboxResult,
    SandboxVerdict,
    get_sandbox_executor,
)

__all__ = [
    "CommandSandbox",
    "SandboxExecutor",
    "SandboxPolicy",
    "SandboxResult",
    "SandboxVerdict",
    "get_sandbox_executor",
]
