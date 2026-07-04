"""Re-export shim — SSE replay registry moved to ``agent.sse_replay``.

Canonical home: :mod:`openakita.agent.sse_replay`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers:

* :mod:`openakita.api.routes.chat` (Last-Event-ID resume path)
* :mod:`tests.unit.test_sse_replay_*`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.sse_replay import (
    DEFAULT_MAXLEN,
    DEFAULT_TTL_SECONDS,
    MAX_SESSIONS,
    SSEEvent,
    SSESession,
    SSESessionRegistry,
    format_sse_frame,
    get_registry,
    parse_last_event_id,
    reset_registry_for_testing,
)

__all__ = [
    "DEFAULT_MAXLEN",
    "DEFAULT_TTL_SECONDS",
    "MAX_SESSIONS",
    "SSEEvent",
    "SSESession",
    "SSESessionRegistry",
    "format_sse_frame",
    "get_registry",
    "parse_last_event_id",
    "reset_registry_for_testing",
]
