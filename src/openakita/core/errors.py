"""Re-export shim - :class:UserCancelledError lives in `agent.errors`.

The canonical home of :class:UserCancelledError is
:mod:openakita.agent.errors, per ADR-0003 and the Phase 2 sub-commit
plan in `docs/revamp/core_audit.md`. This shim keeps every existing
import path working -- including the lazy attribute exposure in
`openakita/core/__init__.py` -- until Phase 8 mechanically removes
the legacy `core/` package.

The original eager `from openakita.agent.errors import
UserCancelledError` was rewritten to PEP 562 lazy access at P-RC-11
P11.2 to break the `core.errors -> agent.__init__ ->
agent.brain -> core._brain_legacy -> llm.client -> core.errors` cycle
(plus the parallel `... -> agent.core -> core._agent_legacy ->
core.errors` re-entry).  `core.errors` now loads without dragging
in the agent package; `UserCancelledError` is resolved on first
attribute access.

Do not add new code here.
"""

from __future__ import annotations

__all__ = ["UserCancelledError"]


def __getattr__(name: str):  # PEP 562 lazy access - break core/agent/llm cycle (P-RC-11 P11.2)
    if name == "UserCancelledError":
        from openakita.agent.errors import UserCancelledError as _U

        globals()["UserCancelledError"] = _U
        return _U
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
