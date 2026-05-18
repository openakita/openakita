"""V2 context management surface — canonical home for ``ContextManager``.

Per ADR-0001 / ADR-0003 and the Phase 2 sub-commit plan (commit 15),
the canonical import path for the agent's context compressor, token
estimator, and pressure snapshot moves to :mod:`openakita.agent.context`.

Current shape
-------------
This module re-exports :class:`ContextManager`, :class:`ContextPressure`,
and supporting constants from the legacy ``core.context_manager`` body.
The internal split into compression/grouping/budget-trace sub-modules
under ``runtime/context/`` is staged for Phase 8 when the legacy
``core/`` tree is removed in a single sweep — keeping the cutover
byte-faithful here avoids destabilising the prompt builder, session
turn handler, and the brain's compression hook.

Migration guidance
------------------
* New code: ``from openakita.agent.context import ContextManager``
* Old code remains valid through the cutover.
"""

from __future__ import annotations

from openakita.core.context_manager import (
    CHARS_PER_TOKEN,
    CHUNK_MAX_TOKENS,
    CONTEXT_BOUNDARY_MARKER,
    ContextManager,
    ContextPressure,
)
from openakita.core.context_utils import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    estimate_tokens,
    get_max_context_tokens,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "CHUNK_MAX_TOKENS",
    "CONTEXT_BOUNDARY_MARKER",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "ContextManager",
    "ContextPressure",
    "estimate_tokens",
    "get_max_context_tokens",
]
