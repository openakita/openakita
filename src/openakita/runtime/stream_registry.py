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

The dispatch path will relay its per-command bus into the org-
level bus in a follow-up commit (P-RC-3); this commit ships the
registry + endpoint so tests can drive it deterministically and
the frontend can be written against a stable contract.
"""

from __future__ import annotations

import threading

from openakita.runtime.stream import StreamBus

__all__ = [
    "get_or_create_org_stream_bus",
    "list_org_stream_buses",
    "reset_org_stream_buses",
]


_LOCK = threading.RLock()
_BUSES: dict[str, StreamBus] = {}


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
    """
    if not org_id:
        raise ValueError("org_id must be a non-empty string")
    with _LOCK:
        bus = _BUSES.get(org_id)
        if bus is None:
            bus = StreamBus(max_queue_size=max_queue_size)
            _BUSES[org_id] = bus
        return bus


def list_org_stream_buses() -> dict[str, StreamBus]:
    """Snapshot of the current registry. Used by debug endpoints / tests."""
    with _LOCK:
        return dict(_BUSES)


def reset_org_stream_buses() -> None:
    """Drop every registered bus (test teardown only)."""
    with _LOCK:
        _BUSES.clear()
