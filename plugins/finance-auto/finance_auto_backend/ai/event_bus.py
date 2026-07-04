"""In-memory event bus + WebSocket fan-out for the finance-auto AI module.

Used as the spine of v0.2 Part 2 §9.3:

* ``finance.parse.issue.created``    — published by Part 1 (W3 Stage 1).
* ``finance.ai.consent.requested``   — emitted before each consent check;
                                       the WS layer turns it into a
                                       ``ai_consent_request`` to the React
                                       side.
* ``finance.ai.consent.granted``     — fired after the user accepts.
* ``finance.ai.consent.denied``      — fired after the user rejects.
* ``finance.parse.issue.ai_filled``  — fired after S2 backfills.

We *don't* try to share OpenAkita's host-level
``InMemoryEventBus`` because the host's bus is wired to the
session-scoped chat WebSocket — that would leak finance events into
ordinary chat sessions, and conversely, the host's WS authentication
doesn't cover the (single-tenant) plugin UI.  Keeping a plugin-local
bus + WS endpoint is the cleanest path and matches the v0.2 design's
"plugin-local SQLite" boundary.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventName = str
Payload = dict[str, Any]
Listener = Callable[[Payload], Awaitable[None]] | Callable[[Payload], None]


class InMemoryEventBus:
    """Tiny pub/sub.  Listeners can be sync or async; we run async ones
    via ``asyncio.create_task`` so the publisher is never blocked.

    The bus is **process-local**.  Multi-process scenarios are out of
    scope for v0.2 (single-tenant desktop install).
    """

    def __init__(self) -> None:
        self._listeners: dict[EventName, list[Listener]] = defaultdict(list)
        self._ws_broadcaster: Callable[[Payload], Awaitable[None]] | None = None

    def subscribe(self, event: EventName, listener: Listener) -> None:
        self._listeners[event].append(listener)

    def unsubscribe(self, event: EventName, listener: Listener) -> None:
        if listener in self._listeners.get(event, ()):
            self._listeners[event].remove(listener)

    def set_ws_broadcaster(
        self, broadcaster: Callable[[Payload], Awaitable[None]] | None
    ) -> None:
        """Plug a WebSocket broadcaster in.  ``None`` removes it.

        The broadcaster is called for *every* emitted event whose payload
        carries ``"event": "<name>"`` so the same channel multiplexes
        consent requests, AI fill notifications, etc.
        """
        self._ws_broadcaster = broadcaster

    async def emit(self, event: EventName, payload: Payload | None = None) -> None:
        payload = dict(payload or {})
        payload.setdefault("event", event)
        listeners = list(self._listeners.get(event, ()))
        for listener in listeners:
            try:
                result = listener(payload)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as exc:  # noqa: BLE001 — never break the publisher
                logger.warning("finance-auto: bus listener for %s failed: %s",
                               event, exc)
        if self._ws_broadcaster is not None:
            try:
                await self._ws_broadcaster(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("finance-auto: ws broadcaster failed: %s", exc)


# Module-level singleton — the FinanceAutoService instantiates one and
# threads it through the DI surface; tests can monkey-patch ``_bus`` to
# inject a fresh bus per test.
_bus: InMemoryEventBus | None = None


def get_event_bus() -> InMemoryEventBus:
    global _bus
    if _bus is None:
        _bus = InMemoryEventBus()
    return _bus


def reset_event_bus_for_tests() -> InMemoryEventBus:
    """Replace the singleton with a fresh bus.  Test-only helper."""
    global _bus
    _bus = InMemoryEventBus()
    return _bus


__all__ = [
    "InMemoryEventBus",
    "get_event_bus",
    "reset_event_bus_for_tests",
]
