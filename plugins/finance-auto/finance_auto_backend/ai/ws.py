"""WebSocket endpoint + connection manager for the AI consent dialog channel.

Single mount point: ``/api/plugins/finance-auto/ws``.  Clients connect,
register their interest, and receive every event the in-memory bus
emits — most importantly ``ai_consent_request`` so the React UI can
render the AI consent dialog.

This is a *plugin-local* channel (deliberately not multiplexed through
OpenAkita's host WebSocket).  Reasons:

* The plugin's React side has no chat/session ID; it talks directly to
  ``/api/plugins/finance-auto/...`` via the host PluginManager prefix.
* The host WS layer is session-scoped; we want a single broadcast pipe
  per local install, no auth dance.
* Plugin lifetime is bounded by the plugin install, so a per-plugin
  connection manager owns its task graph cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .event_bus import InMemoryEventBus, get_event_bus

logger = logging.getLogger(__name__)


class FinanceWSConnectionManager:
    """Track active WS connections and broadcast bus events to all of them.

    Implementation notes:

    * ``send_text`` is wrapped in ``asyncio.shield`` so a slow client
      can't block the event loop's broadcast task; the failure path
      drops the slow client instead.
    * The WS frames are JSON dumps of the event payload as-is, so the
      front-end gets the same shape it would have inside a REST list
      response — no special channel marshalling.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        async with self._lock:
            targets = list(self._connections)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await asyncio.shield(ws.send_text(text))
            except Exception as exc:  # noqa: BLE001
                logger.info("finance-auto ws: dropping client (%s)", exc)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


_default_manager: FinanceWSConnectionManager | None = None


def get_ws_manager() -> FinanceWSConnectionManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = FinanceWSConnectionManager()
    return _default_manager


def reset_ws_manager_for_tests() -> FinanceWSConnectionManager:
    global _default_manager
    _default_manager = FinanceWSConnectionManager()
    return _default_manager


# ---------------------------------------------------------------------------
# Bus → manager wiring
# ---------------------------------------------------------------------------


def attach_bus_broadcaster(
    bus: InMemoryEventBus | None = None,
    manager: FinanceWSConnectionManager | None = None,
) -> None:
    """Wire ``bus.set_ws_broadcaster`` to ``manager.broadcast``.

    Idempotent — calling twice replaces the prior broadcaster.  Tests
    swap fresh bus + manager via ``reset_*_for_tests``.
    """
    bus = bus or get_event_bus()
    manager = manager or get_ws_manager()
    bus.set_ws_broadcaster(manager.broadcast)


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_ws_endpoint(router: APIRouter) -> None:
    """Mount ``/ws`` under the plugin's `/api/plugins/finance-auto`
    prefix.  The router is the same one routes.build_router returns.

    The endpoint is read-only from the client's perspective — it
    receives events but does not forward client messages back to the
    bus.  The consent decision flows through the REST endpoint
    ``POST /ai/consent/respond`` instead so we get standard HTTP error
    handling for free.
    """

    manager = get_ws_manager()
    attach_bus_broadcaster()

    @router.websocket("/ws")
    async def finance_ws(websocket: WebSocket) -> None:
        await manager.connect(websocket)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "finance_ws_hello",
                        "subscriptions": [
                            "ai_consent_request",
                            "parse_issue_ai_filled",
                        ],
                    }
                )
            )
            while True:
                # We only consume to keep the socket alive; the React
                # side currently sends nothing — and we want a quiet
                # client to be a happy client.
                try:
                    await websocket.receive_text()
                except WebSocketDisconnect:
                    raise
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)


__all__ = [
    "FinanceWSConnectionManager",
    "attach_bus_broadcaster",
    "get_ws_manager",
    "register_ws_endpoint",
    "reset_ws_manager_for_tests",
]
