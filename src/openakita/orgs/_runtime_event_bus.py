"""``_runtime_event_bus.py`` -- v2 OrgRuntime event-bus sibling (P9.6b).

Extracts the in-memory + WS-bridged :class:`EventBusProtocol`
implementation out of ``runtime.py`` so the default in-memory
backend in ``runtime.py`` stays a 30-line minimal stub while
this module ships the production-ready surface that
:class:`OrgRuntime` (P9.6alpha-d) and the upcoming
``_runtime_dispatch.py`` (P9.6beta) compose with.

Surface (all satisfying :class:`EventBusProtocol` from
``runtime.py``):

* :class:`InMemoryEventBus` -- pub / sub + best-effort
  :meth:`broadcast_ws` that posts to the
  ``api.routes.websocket.broadcast_event`` bridge when the
  v1 module is importable, no-op otherwise (graceful
  degradation matches v1 ``_broadcast_ws`` semantics).
* :class:`WebSocketEventBus` -- thin wrapper that ALWAYS
  routes through the WS bridge (used by production wiring;
  tests pin :class:`InMemoryEventBus`).
* :func:`get_default_event_bus` -- factory returning
  :class:`InMemoryEventBus` by default; opt into
  :class:`WebSocketEventBus` via ``ORGS_V2_EVENT_BUS=ws``.

The Protocol contract is owned by ``runtime.py`` (P9.6a0).
This module imports the Protocol and ships the two real
backends; ``runtime.py``''s own ``_InMemoryEventBus`` stays
as the zero-dependency default for unit tests + parity
fixtures.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from .runtime import EventBusProtocol

_LOGGER = logging.getLogger(__name__)

_HandlerT = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class InMemoryEventBus:
    """Pub / sub :class:`EventBusProtocol` with WS bridge.

    Parity-faithful to v1 ``OrgRuntime._broadcast_ws``
    semantics: WS broadcast is best-effort; failure to import
    or call ``api.routes.websocket.broadcast_event`` is logged
    at DEBUG and swallowed (production wiring observed v1
    behaviour: WS failures must never break the event emit
    pipeline).
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[_HandlerT]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        """Fan ``event`` out to every subscriber.

        Async handlers are awaited; sync handlers are called
        in-line; raised exceptions are logged + swallowed so
        one bad subscriber cannot block the others.
        """

        handlers = list(self._subs.get(event, ()))
        for handler in handlers:
            try:
                res = handler(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 (parity with v1 swallow)
                _LOGGER.exception(
                    "event_bus handler %r raised on %s",
                    getattr(handler, "__name__", handler),
                    event,
                )

    async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
        """Best-effort broadcast through the v1 WS bridge.

        Imports ``openakita.api.routes.websocket`` lazily to
        avoid a hard dependency at module load (the WS layer
        is only available when the API server boots).
        """

        try:
            from openakita.api.routes.websocket import (  # type: ignore[import-not-found]
                broadcast_event,
            )
        except ImportError:
            _LOGGER.debug("ws bridge unavailable; broadcast_ws no-op (event=%s)", event)
            return
        try:
            await broadcast_event(event, data)
        except Exception:  # noqa: BLE001 (v1 _broadcast_ws parity)
            _LOGGER.exception("ws broadcast failed (event=%s)", event)

    def subscribe(self, event: str, handler: _HandlerT) -> None:
        """Add ``handler`` to ``event``''s subscriber list."""

        self._subs[event].append(handler)

    def unsubscribe(self, event: str, handler: _HandlerT) -> None:
        """Remove ``handler`` from ``event``''s subscriber list."""

        try:
            self._subs[event].remove(handler)
        except (KeyError, ValueError):
            return


class WebSocketEventBus(InMemoryEventBus):
    """Variant that ALWAYS routes through the WS bridge.

    Production wiring opt-in: set ``ORGS_V2_EVENT_BUS=ws``.
    Behaves identically to :class:`InMemoryEventBus` for
    :meth:`subscribe` / :meth:`unsubscribe`; :meth:`emit`
    additionally calls :meth:`broadcast_ws` so external
    observers (UI / IM gateway) see the event too.
    """

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        await super().emit(event, payload)
        await self.broadcast_ws(event, payload)


def get_default_event_bus() -> EventBusProtocol:
    """Return the default :class:`EventBusProtocol` for this process.

    Selection (matches the P9.1 / P9.2 / P9.3 / P9.5 factory
    idiom):

    * ``ORGS_V2_EVENT_BUS=ws`` -> :class:`WebSocketEventBus`.
    * Anything else (or unset) -> :class:`InMemoryEventBus`.
    """

    mode = os.environ.get("ORGS_V2_EVENT_BUS", "memory").strip().lower()
    if mode == "ws":
        return WebSocketEventBus()
    return InMemoryEventBus()


__all__ = [
    "InMemoryEventBus",
    "WebSocketEventBus",
    "get_default_event_bus",
]
