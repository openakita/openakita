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

EX-P2-4: the connection manager additionally enforces a ``MAX_WS_CLIENTS``
ceiling and a heartbeat ping/pong probe so a leaking client (or a
malicious one) can't bleed the host process of FDs or keep dead
half-open sockets alive for hours.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .event_bus import InMemoryEventBus, get_event_bus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EX-P2-4 — connection limits & heartbeat tunables
# ---------------------------------------------------------------------------

#: Maximum concurrent WebSocket clients allowed on the plugin channel.
#: Overridable via ``OPENAKITA_FINANCE_AUTO_WS_MAX_CLIENTS``.  When the
#: ceiling is hit, additional ``connect`` calls close the new socket
#: with code 1013 (try again later) and return ``False``.
DEFAULT_MAX_WS_CLIENTS = 50
MAX_WS_CLIENTS_ENV = "OPENAKITA_FINANCE_AUTO_WS_MAX_CLIENTS"

#: Interval (in seconds) between server-initiated heartbeat pings.
DEFAULT_HEARTBEAT_INTERVAL_SEC = 30.0
HEARTBEAT_INTERVAL_ENV = "OPENAKITA_FINANCE_AUTO_WS_HEARTBEAT_INTERVAL"

#: How long the server waits for ANY frame from the peer before declaring
#: the connection dead.  Pings count; pong replies count; user messages
#: count.  60s of silence → close 1011 (server-side error/inactive).
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 60.0
HEARTBEAT_TIMEOUT_ENV = "OPENAKITA_FINANCE_AUTO_WS_HEARTBEAT_TIMEOUT"

#: Close code used when MAX_WS_CLIENTS is exceeded.  RFC 6455 1013 means
#: "Try Again Later" — the right semantic for a server overload.
WS_CLOSE_TRY_AGAIN_LATER = 1013

#: Close code used on heartbeat / inactivity timeout.
WS_CLOSE_INACTIVITY = 1011


def _resolve_max_clients() -> int:
    raw = os.environ.get(MAX_WS_CLIENTS_ENV)
    if raw is None:
        return DEFAULT_MAX_WS_CLIENTS
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "finance-auto ws: invalid %s=%r, falling back to %d",
            MAX_WS_CLIENTS_ENV, raw, DEFAULT_MAX_WS_CLIENTS,
        )
        return DEFAULT_MAX_WS_CLIENTS
    if n < 1:
        return DEFAULT_MAX_WS_CLIENTS
    return n


def _resolve_heartbeat_interval() -> float:
    raw = os.environ.get(HEARTBEAT_INTERVAL_ENV)
    if raw is None:
        return DEFAULT_HEARTBEAT_INTERVAL_SEC
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_HEARTBEAT_INTERVAL_SEC


def _resolve_heartbeat_timeout() -> float:
    raw = os.environ.get(HEARTBEAT_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_HEARTBEAT_TIMEOUT_SEC
    try:
        return max(2.0, float(raw))
    except ValueError:
        return DEFAULT_HEARTBEAT_TIMEOUT_SEC


class FinanceWSConnectionManager:
    """Track active WS connections and broadcast bus events to all of them.

    Implementation notes:

    * ``send_text`` is wrapped in ``asyncio.shield`` so a slow client
      can't block the event loop's broadcast task; the failure path
      drops the slow client instead.
    * The WS frames are JSON dumps of the event payload as-is, so the
      front-end gets the same shape it would have inside a REST list
      response — no special channel marshalling.
    * EX-P2-4: a per-connection lock + ``add()`` / ``remove()`` API
      makes the exception path explicit, and ``connect()`` enforces
      ``MAX_WS_CLIENTS``.
    """

    def __init__(self, *, max_clients: int | None = None) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._max_clients = (
            max_clients if max_clients is not None else _resolve_max_clients()
        )

    @property
    def max_clients(self) -> int:
        return self._max_clients

    async def connect(self, ws: WebSocket) -> bool:
        """Accept the socket and register it.  Returns False (and
        closes the socket with 1013) if the configured ceiling is
        already reached.  Callers should bail when False is returned."""
        async with self._lock:
            if len(self._connections) >= self._max_clients:
                # Important: accept first then close, otherwise some
                # clients (and the Starlette TestClient) treat the
                # pre-accept close as a generic connection failure
                # without seeing the 1013 code.
                try:
                    await ws.accept()
                    await ws.close(code=WS_CLOSE_TRY_AGAIN_LATER)
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "finance-auto ws: max clients=%d reached, rejecting",
                    self._max_clients,
                )
                return False
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        return True

    async def add(self, ws: WebSocket) -> None:
        """Lower-level helper exposed for tests / custom acceptors."""
        async with self._lock:
            self._connections.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        await self.remove(ws)

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


def reset_ws_manager_for_tests(
    *, max_clients: int | None = None
) -> FinanceWSConnectionManager:
    global _default_manager
    _default_manager = FinanceWSConnectionManager(max_clients=max_clients)
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
        admitted = await manager.connect(websocket)
        if not admitted:
            return
        heartbeat_interval = _resolve_heartbeat_interval()
        heartbeat_timeout = _resolve_heartbeat_timeout()
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "finance_ws_hello",
                        "subscriptions": [
                            "ai_consent_request",
                            "parse_issue_ai_filled",
                        ],
                        "heartbeat_interval_sec": heartbeat_interval,
                        "heartbeat_timeout_sec": heartbeat_timeout,
                    }
                )
            )
            # EX-P2-4: heartbeat loop.  We multiplex a periodic ping
            # with ``receive_text`` via ``asyncio.wait_for`` —
            # ``heartbeat_timeout`` seconds of total silence (no
            # client frame at all, not even a pong-shaped text frame)
            # closes the socket with 1011.
            while True:
                try:
                    msg = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=heartbeat_interval,
                    )
                    # We accept any inbound frame as a liveness signal.
                    if msg and msg.lower() in {"ping", "pong"}:
                        try:
                            await websocket.send_text("pong")
                        except Exception:  # noqa: BLE001
                            break
                except asyncio.TimeoutError:
                    # No frame within the ping window — send a ping.
                    # The next iteration extends the window by another
                    # ``heartbeat_interval`` seconds; if a full
                    # ``heartbeat_timeout`` worth of silence
                    # accumulates we close the socket.
                    try:
                        await asyncio.wait_for(
                            websocket.send_text("ping"),
                            timeout=heartbeat_timeout - heartbeat_interval,
                        )
                    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                        logger.info(
                            "finance-auto ws: client silent for >%.0fs, "
                            "closing 1011", heartbeat_timeout,
                        )
                        try:
                            await websocket.close(code=WS_CLOSE_INACTIVITY)
                        except Exception:  # noqa: BLE001
                            pass
                        break
                    # Wait for the pong (or any frame); a second silent
                    # window means the peer is dead.
                    try:
                        await asyncio.wait_for(
                            websocket.receive_text(),
                            timeout=heartbeat_timeout - heartbeat_interval,
                        )
                    except asyncio.TimeoutError:
                        logger.info(
                            "finance-auto ws: client silent post-ping, "
                            "closing 1011"
                        )
                        try:
                            await websocket.close(code=WS_CLOSE_INACTIVITY)
                        except Exception:  # noqa: BLE001
                            pass
                        break
                except WebSocketDisconnect:
                    raise
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            # EX-P2-4: even on the exception path we always
            # unregister so a future broadcast doesn't try to push
            # into a dead socket.
            await manager.remove(websocket)


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SEC",
    "DEFAULT_HEARTBEAT_TIMEOUT_SEC",
    "DEFAULT_MAX_WS_CLIENTS",
    "FinanceWSConnectionManager",
    "HEARTBEAT_INTERVAL_ENV",
    "HEARTBEAT_TIMEOUT_ENV",
    "MAX_WS_CLIENTS_ENV",
    "WS_CLOSE_INACTIVITY",
    "WS_CLOSE_TRY_AGAIN_LATER",
    "attach_bus_broadcaster",
    "get_ws_manager",
    "register_ws_endpoint",
    "reset_ws_manager_for_tests",
]
