"""
WebSocket hub for real-time event push.

Replaces Tauri `listen()` events for web/mobile clients.

Endpoints:
  /ws/events?token=<access_token>  — general event stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import WebAccessConfig

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: list[tuple[WebSocket, bool]] = []  # (ws, is_local)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, *, is_local: bool = False) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append((ws, is_local))
        logger.debug("WebSocket client connected (local=%s, total: %d)", is_local, len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections = [(c, loc) for c, loc in self._connections if c is not ws]
        logger.debug("WebSocket client disconnected (total: %d)", len(self._connections))

    async def broadcast(self, event: str, data: Any = None) -> None:
        """Send an event to all connected clients concurrently."""
        if not self._connections:
            return
        message = json.dumps({"event": event, "data": data, "ts": time.time()})

        async with self._lock:
            connections = list(self._connections)

        async def _safe_send(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=5.0)
            except Exception:
                return ws
            return None

        results = await asyncio.gather(
            *[_safe_send(ws) for ws, _loc in connections],
            return_exceptions=True,
        )

        dead = [ws for ws in results if isinstance(ws, WebSocket)]
        if dead:
            dead_set = set(id(ws) for ws in dead)
            async with self._lock:
                self._connections = [
                    (c, loc) for c, loc in self._connections if id(c) not in dead_set
                ]

    async def disconnect_remote_clients(self) -> int:
        """Close all non-local WebSocket connections (e.g. after password change)."""
        to_close: list[WebSocket] = []
        async with self._lock:
            to_close = [ws for ws, is_local in self._connections if not is_local]
            self._connections = [(ws, loc) for ws, loc in self._connections if loc]
        for ws in to_close:
            try:
                await ws.send_text(json.dumps({"event": "session_invalidated", "ts": time.time()}))
                await ws.close(code=4001, reason="Password changed")
            except Exception:
                pass
        if to_close:
            logger.info("Disconnected %d remote WebSocket client(s) after password change", len(to_close))
        return len(to_close)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Global manager instance
manager = ConnectionManager()


def _is_local_ws(ws: WebSocket) -> bool:
    """Check if WebSocket originates from localhost (handles IPv4-mapped IPv6)."""
    if not ws.client:
        return False
    host = ws.client.host
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    if host.startswith("::ffff:") and host[7:] == "127.0.0.1":
        return True
    return False


def _authenticate_ws(ws: WebSocket, config: WebAccessConfig) -> bool:
    """Authenticate WebSocket connection via query param or local access."""
    # Local connections are exempt — same logic as HTTP middleware:
    # direct local connections (no X-Forwarded-For) bypass auth even with
    # trust_proxy; proxy-forwarded ones must provide a valid token.
    if _is_local_ws(ws):
        import os
        trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
        if not trust_proxy or not ws.headers.get("x-forwarded-for"):
            return True

    # Check token from query params
    token = ws.query_params.get("token", "")
    if token and config.validate_access_token(token):
        return True

    return False


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    config: WebAccessConfig = ws.app.state.web_access_config

    if not _authenticate_ws(ws, config):
        await ws.close(code=4001, reason="Authentication required")
        return

    is_local = _is_local_ws(ws)
    await manager.connect(ws, is_local=is_local)
    try:
        # Send initial connection confirmation
        await ws.send_text(json.dumps({
            "event": "connected",
            "data": {"message": "WebSocket connected"},
            "ts": time.time(),
        }))

        # Keep connection alive; listen for client messages (ping/pong, etc.)
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                # Handle ping
                if msg == "ping":
                    await ws.send_text(json.dumps({"event": "pong", "ts": time.time()}))
            except asyncio.TimeoutError:
                # Send server-side ping to keep connection alive
                try:
                    await ws.send_text(json.dumps({"event": "ping", "ts": time.time()}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket error: %s", e)
    finally:
        await manager.disconnect(ws)


async def broadcast_event(event: str, data: Any = None) -> None:
    """Convenience function to broadcast events from anywhere in the codebase.

    Cross-loop safe: when called from the engine loop (e.g. OrgRuntime),
    the actual WebSocket send is scheduled in the API loop where the
    connections live.
    """
    from openakita.core.engine_bridge import fire_in_api, get_api_loop

    if get_api_loop() is not None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not get_api_loop():
            fire_in_api(manager.broadcast(event, data))
            return

    await manager.broadcast(event, data)
