"""Thin shim re-exporting the v2 :mod:`openakita.agent.core` symbols.

Per continuation plan section 7 (P-RC-6) the legacy ~9200 LOC
``core.agent`` god-module collapsed to this shim. Public surface
is served by lazy delegation to :mod:`openakita.agent.core` -- which
in P-RC-6 P6.5 onwards composes a v2 ``Agent`` subclass that
inherits from the legacy class -- with a private fallback to
:mod:`openakita.core._agent_legacy` for the long tail of private
symbols (~50+ private helpers). Mirrors the
``brain`` / ``tool_executor`` / ``context_manager`` /
``reasoning_engine`` shims that landed in P-RC-4 / P-RC-5. Both
fallbacks drop in P-RC-7 when the legacy module is deleted.
"""

from __future__ import annotations

__all__ = ["Agent", "PromptStrategy", "get_primary_agent", "set_primary_agent"]


def __getattr__(name):
    if name in __all__:
        from openakita.core import _agent_legacy as _legacy
        return getattr(_legacy, name)
    from openakita.core import _agent_legacy as _legacy
    if hasattr(_legacy, name):
        return getattr(_legacy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
