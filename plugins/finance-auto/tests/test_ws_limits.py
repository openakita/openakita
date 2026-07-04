"""EX-P2-4 — WebSocket max_clients ceiling and heartbeat behaviour.

These are unit tests that exercise the connection manager / endpoint
directly via FastAPI's ``TestClient`` (which keeps the network bits
in-process).  The heartbeat windows are dialed way down via env vars
so the tests stay sub-second.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

import finance_auto_backend.ai.ws as ws_mod
from finance_auto_backend.ai.ws import (
    FinanceWSConnectionManager,
    WS_CLOSE_TRY_AGAIN_LATER,
    register_ws_endpoint,
    reset_ws_manager_for_tests,
)
from finance_auto_backend.routes import build_router_and_service


@pytest.fixture()
def app_with_tiny_ws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(ws_mod.MAX_WS_CLIENTS_ENV, "2")
    monkeypatch.setenv(ws_mod.HEARTBEAT_INTERVAL_ENV, "0.05")
    monkeypatch.setenv(ws_mod.HEARTBEAT_TIMEOUT_ENV, "0.20")

    reset_ws_manager_for_tests(max_clients=2)

    router, _svc, _db = build_router_and_service(tmp_path / "ws.sqlite")
    register_ws_endpoint(router)

    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/finance-auto")
    return app


def test_ws_max_clients_rejects_third_client(app_with_tiny_ws: FastAPI) -> None:
    client = TestClient(app_with_tiny_ws)
    with ExitStack() as stack:
        ws1 = stack.enter_context(
            client.websocket_connect("/api/plugins/finance-auto/ws")
        )
        ws2 = stack.enter_context(
            client.websocket_connect("/api/plugins/finance-auto/ws")
        )
        # Drain hellos so the two are fully admitted before we test #3.
        _ = ws1.receive_json()
        _ = ws2.receive_json()

        # Third connection must be rejected with code 1013.
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/plugins/finance-auto/ws"
            ) as ws3:
                # The server accepts → close(1013); the receive raises.
                ws3.receive_text()
        assert exc_info.value.code == WS_CLOSE_TRY_AGAIN_LATER


def test_ws_heartbeat_closes_silent_client(app_with_tiny_ws: FastAPI) -> None:
    client = TestClient(app_with_tiny_ws)
    with client.websocket_connect(
        "/api/plugins/finance-auto/ws"
    ) as ws:
        # We expect: hello -> ping -> (we stay silent) -> close 1011
        hello = ws.receive_json()
        assert hello["event"] == "finance_ws_hello"
        # First periodic ping (within heartbeat_interval=0.05s).
        first_ping = ws.receive_text()
        assert first_ping == "ping"
        # We deliberately do NOT pong; the server should close.
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == ws_mod.WS_CLOSE_INACTIVITY


def test_ws_pong_keeps_connection_alive(app_with_tiny_ws: FastAPI) -> None:
    client = TestClient(app_with_tiny_ws)
    with client.websocket_connect(
        "/api/plugins/finance-auto/ws"
    ) as ws:
        _hello = ws.receive_json()
        first_ping = ws.receive_text()
        assert first_ping == "ping"
        ws.send_text("pong")
        # We should get the server's pong-echo (or just stay alive
        # for the next heartbeat round-trip without an immediate
        # disconnect).
        next_frame = ws.receive_text()
        assert next_frame in {"pong", "ping"}


def test_connection_manager_add_remove_explicit() -> None:
    """Lower-level: the add/remove API must mirror ``connections``."""
    mgr = FinanceWSConnectionManager(max_clients=3)

    class _DummyWS:
        async def accept(self) -> None:
            return None

        async def close(self, code: int = 1000) -> None:
            return None

    async def _drive() -> None:
        w1 = _DummyWS()
        w2 = _DummyWS()
        await mgr.add(w1)  # type: ignore[arg-type]
        await mgr.add(w2)  # type: ignore[arg-type]
        assert mgr.connection_count == 2
        await mgr.remove(w1)  # type: ignore[arg-type]
        assert mgr.connection_count == 1
        await mgr.remove(w2)  # type: ignore[arg-type]
        assert mgr.connection_count == 0

    asyncio.run(_drive())
