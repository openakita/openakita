"""Thin legacy-fallback shim for ``openakita.core.agent``.

Per continuation plan section 7 (P-RC-6) the legacy ~9200 LOC
``core.agent`` god-module collapsed to this shim. This shim is a
**lazy fallback to** :mod:`openakita.core._agent_legacy` only --
it does NOT delegate to the v2 implementation. The v2 ``Agent``
subclass and the explicit lifecycle ``StateGraph`` live at
:mod:`openakita.agent.core`; callers that want v2 semantics
(``lifecycle_graph``, ``classify_inbound_risk``,
``format_attachment_reference``, ...) MUST import explicitly from
:mod:`openakita.agent.core`::

    from openakita.agent.core import Agent  # v2 surface

Importing ``from openakita.core.agent import Agent`` resolves to
the legacy class via ``_agent_legacy``; this exists for backward
compatibility with the ~40 in-tree call sites that have not yet
been migrated to ``openakita.agent.core``. The migration to
``openakita.agent.*`` happens in P-RC-7; this shim disappears once
every caller is moved.

Public ``__all__`` mirrors the legacy surface; arbitrary private
helpers (``_format_desktop_attachment_reference``,
``_check_trust_mode_skip``, ...) are still reachable for byte-
faithful test imports via the ``__getattr__`` fallback.
"""

from __future__ import annotations

__all__ = ["Agent", "PromptStrategy", "get_primary_agent", "set_primary_agent"]


def __getattr__(name):
    # Single resolution path: every name (public or private) is served
    # from the preserved legacy module. The v2 ``Agent`` subclass at
    # ``openakita.agent.core`` is intentionally NOT exposed here so
    # callers must opt in explicitly (avoids accidental v2 semantics
    # for code that has not been audited for the new lifecycle graph).
    from openakita.core import _agent_legacy as _legacy
    if hasattr(_legacy, name):
        return getattr(_legacy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")