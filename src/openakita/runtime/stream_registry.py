"""Per-org :class:`StreamBus` registry for the v2 SSE surface.

The dispatch path in :func:`runtime.channel_routing.
dispatch_inbound_message_to_v2` builds a fresh
:class:`~openakita.runtime.stream.StreamBus` per inbound IM
message; the SSE endpoint
(``GET /api/v2/orgs/{id}/stream``, P-RC-2 commit P2.3) instead
needs a *long-lived* bus per org so a connected ``EventSource``
keeps seeing events across sequential commands. This module owns
that registry: a process-wide ``dict[str, StreamBus]`` keyed by
``org_id`` plus a tiny get-or-create / reset surface.

P-RC-3 adds an idle-bus cleanup policy (T4 nit closeout):
:func:`cleanup_idle` recycles every bus that has had zero
subscribers for at least ``idle_seconds`` (default 60 s). The
``api/server.py`` lifespan starts a background asyncio task that
calls it every 30 s; tests can call ``cleanup_idle`` directly
with a synthetic ``now=`` to drive deterministic recycling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from openakita.runtime.stream import StreamBus

__all__ = [
    "cleanup_idle",
    "cleanup_idle_buses_periodically",
    "get_or_create_org_stream_bus",
    "list_org_stream_buses",
    "reset_org_stream_buses",
]


logger = logging.getLogger(__name__)

# Default polling cadence + idle threshold for the background task.
# Both are conservative so a transient SSE reconnect window does not
# recycle a bus that the client is about to re-attach to.
DEFAULT_CLEANUP_INTERVAL_S = 30.0
DEFAULT_IDLE_SECONDS = 60.0


_LOCK = threading.RLock()
_BUSES: dict[str, StreamBus] = {}
# org_id -> monotonic timestamp of the most recent moment the bus had
# zero subscribers. None when the bus currently has at least one
# subscriber attached.
_LAST_EMPTY_TS: dict[str, float | None] = {}


def _stamp_subscriber_loss_locked(org_id: str) -> None:
    """Mark ``org_id`` as freshly idle. Caller must hold ``_LOCK``."""
    _LAST_EMPTY_TS[org_id] = time.monotonic()


def _clear_subscriber_loss_locked(org_id: str) -> None:
    """Drop any pending idle stamp -- called when a subscriber attaches."""
    _LAST_EMPTY_TS[org_id] = None


def get_or_create_org_stream_bus(
    org_id: str,
    *,
    max_queue_size: int = 256,
) -> StreamBus:
    """Return the long-lived :class:`StreamBus` bound to ``org_id``.

    First caller for a given ``org_id`` creates the bus; subsequent
    callers receive the same instance. Thread-safe via a re-entrant
    lock so a producer that itself reaches into the registry from a
    callback cannot deadlock.

    A fresh attach clears any pending idle stamp on the org so the
    next cleanup pass treats the bus as live.
    """
    if not org_id:
        raise ValueError("org_id must be a non-empty string")
    with _LOCK:
        bus = _BUSES.get(org_id)
        if bus is None:
            bus = StreamBus(max_queue_size=max_queue_size)
            _BUSES[org_id] = bus
            _LAST_EMPTY_TS[org_id] = time.monotonic()
        # When a caller fetches the bus we cannot tell whether they
        # intend to subscribe or merely emit. Clearing the stamp
        # on every fetch means the cleanup loop is conservative: a
        # bus that is being actively used by either side stays
        # alive even between subscriber attachments.
        if bus.subscriber_count > 0:
            _clear_subscriber_loss_locked(org_id)
        return bus


def mark_subscriber_lost(org_id: str) -> None:
    """Stamp ``org_id`` as idle WHEN its bus has zero subscribers.

    Call this after a ``StreamBus.detach_subscription`` (or after an
    SSE generator's ``finally`` block) so the next
    :func:`cleanup_idle` pass can evict the bus once the idle
    deadline elapses.
    """
    with _LOCK:
        bus = _BUSES.get(org_id)
        if bus is None:
            return
        if bus.subscriber_count == 0:
            _stamp_subscriber_loss_locked(org_id)


def mark_subscriber_attached(org_id: str) -> None:
    """Clear any pending idle stamp on ``org_id``.

    Call this after a successful ``register_subscription`` so a bus
    that briefly hit zero subscribers between two SSE reconnects
    does not get recycled.
    """
    with _LOCK:
        if org_id in _BUSES:
            _clear_subscriber_loss_locked(org_id)


def list_org_stream_buses() -> dict[str, StreamBus]:
    """Snapshot of the current registry. Used by debug endpoints / tests."""
    with _LOCK:
        return dict(_BUSES)


def reset_org_stream_buses() -> None:
    """Drop every registered bus (test teardown only)."""
    with _LOCK:
        _BUSES.clear()
        _LAST_EMPTY_TS.clear()


def cleanup_idle(
    *,
    now: float | None = None,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
) -> int:
    """Recycle every bus idle for ``>= idle_seconds`` seconds.

    A bus is idle when its subscriber count is zero AND a prior call
    to :func:`mark_subscriber_lost` (or the initial registration)
    stamped its loss timestamp. Buses with at least one subscriber
    are always retained, regardless of the timestamp -- the stamp
    is informational, not authoritative.

    Returns the number of buses recycled. The actual ``bus.close()``
    is scheduled via ``asyncio.ensure_future`` from inside the
    registry lock; callers in an async context can ``await`` the
    next event-loop tick to drain the resulting close coroutines if
    they need synchronous evidence the close completed.
    """
    when = time.monotonic() if now is None else now
    recycled = 0
    with _LOCK:
        for org_id in list(_BUSES.keys()):
            bus = _BUSES[org_id]
            if bus.subscriber_count > 0:
                _clear_subscriber_loss_locked(org_id)
                continue
            stamp = _LAST_EMPTY_TS.get(org_id)
            if stamp is None:
                # Bus has zero subscribers but no stamp yet -- treat as
                # newly idle and start the clock now.
                _stamp_subscriber_loss_locked(org_id)
                continue
            if when - stamp < idle_seconds:
                continue
            del _BUSES[org_id]
            _LAST_EMPTY_TS.pop(org_id, None)
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(bus.close())
            except RuntimeError:
                # No running loop in the calling thread; close synchronously
                # is impossible (close is async), but the registry no longer
                # references the bus, so the next access creates a fresh one.
                logger.debug(
                    "cleanup_idle: bus for org=%s dropped without close (no loop)",
                    org_id,
                )
            recycled += 1
    if recycled:
        logger.info("StreamRegistry.cleanup_idle recycled %d bus(es)", recycled)
    return recycled


async def cleanup_idle_buses_periodically(
    *,
    interval: float = DEFAULT_CLEANUP_INTERVAL_S,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
) -> None:
    """Background coroutine that calls :func:`cleanup_idle` forever.

    Cancellation is cooperative: ``CancelledError`` exits the loop
    without propagating, so the lifespan handler can shut us down
    via ``task.cancel()`` and ``await task`` without exception
    handling boilerplate.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                cleanup_idle(idle_seconds=idle_seconds)
            except Exception:  # noqa: BLE001 -- never crash the loop
                logger.exception("cleanup_idle_buses_periodically: unexpected error")
    except asyncio.CancelledError:
        return
