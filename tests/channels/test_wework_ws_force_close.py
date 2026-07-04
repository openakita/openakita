"""Sprint 17 / v34 P1-A regression: ``WeWorkWsAdapter.stop()`` must honor
``force_close_after_s`` and abort the underlying ``transport`` if the
WebSocket ``close()`` handshake never returns.

Forensic background — see ``_v34_biz/_im_shutdown_chain_inventory.md``
and ``_v33_biz_e2e/_drain_decomposition.md``:

* v33 measured each ``wework_ws`` bot's ``stop()`` at ~4 s, driven by
  ``await self._connection_task`` and ``await self._ws.close()`` with
  no timeout. With 3 wework_ws bots gather'd inside
  ``MessageGateway.stop()``, the slowest pinned IM drain to ~4 s and
  total shutdown p50 to 11.14 s (0.5 s over the ≤10 s SLO edge).

* Post-fix ``stop()`` wraps every cooperative ``await`` (heartbeat task,
  connection task, ``ws.close()``, ``webhook.close()``) in a
  ``wait_for(force_close_after_s)`` cap and, on timeout, falls back to
  ``transport.close()`` so the asyncio loop really releases the socket.

These tests pin three guarantees of the new path without spinning up a
real WeWork WebSocket connection:

1. A wedged ``_connection_task`` does NOT block ``stop()`` past the cap.
2. A wedged ``ws.close()`` triggers a ``transport.close()`` fallback.
3. The graceful path still completes promptly when nothing is wedged.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openakita.channels.adapters.wework_ws import WeWorkWsAdapter


def _make_adapter(tmp_path: Path) -> WeWorkWsAdapter:
    """Build a WeWorkWsAdapter without touching ``websockets`` or HTTP."""
    return WeWorkWsAdapter(
        bot_id="test_bot",
        secret="test_secret",
        ws_url="wss://example.invalid/ws",
        media_dir=tmp_path / "media",
        channel_name="wework_ws:test",
        bot_id_alias="test",
        webhook_url="",  # disable webhook helper entirely
    )


class _StubWs:
    """Mimics enough of a websockets client to exercise ``force_close_ws``.

    ``close()`` sleeps ``close_sleep_s`` seconds — set absurdly high to
    simulate a hung close-frame handshake.
    """

    def __init__(self, *, close_sleep_s: float) -> None:
        self._close_sleep_s = close_sleep_s
        self.close_called = False
        # ``transport`` is what websockets exposes; we mock the asyncio
        # WriteTransport surface (``close`` + ``is_closing``).
        self.transport = MagicMock()
        self.transport.is_closing.return_value = False

    async def close(self) -> None:
        self.close_called = True
        await asyncio.sleep(self._close_sleep_s)


@pytest.mark.asyncio
async def test_stop_does_not_block_on_wedged_connection_task(tmp_path):
    """``_connection_task`` that ignores cancellation must not pin stop().

    Pre-fix this would hang until external kill. Post-fix the await is
    bounded by ``force_close_after_s`` and we log + abandon.
    """
    adapter = _make_adapter(tmp_path)

    async def _never_returns():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            # Simulate a buggy task that swallows CancelledError and keeps
            # sleeping (e.g. inside a broad except). This is the worst
            # case the force-close path must defeat.
            await asyncio.sleep(30)

    adapter._connection_task = asyncio.create_task(_never_returns())

    started = time.monotonic()
    await adapter.stop(force_close_after_s=0.4)
    elapsed = time.monotonic() - started

    # ≤ deadline + scheduling slack. Pre-fix this hung indefinitely.
    assert elapsed < 1.5, (
        f"stop() did not honor force_close_after_s: elapsed={elapsed:.2f}s "
        "(expected <1.5s with 0.4s deadline)"
    )

    # Cleanup: cancel the still-sleeping task to keep pytest from leaking.
    if not adapter._connection_task.done():
        adapter._connection_task.cancel()
        try:
            await asyncio.wait_for(adapter._connection_task, timeout=0.1)
        except (asyncio.CancelledError, TimeoutError):
            pass


@pytest.mark.asyncio
async def test_stop_aborts_transport_when_ws_close_hangs(tmp_path):
    """A ``ws.close()`` that never returns must trigger ``transport.close()``.

    This is the "asyncio loop is still holding the socket open" failure
    mode that drove the v33 IM drain tail.
    """
    adapter = _make_adapter(tmp_path)
    stub_ws = _StubWs(close_sleep_s=30.0)
    adapter._ws = stub_ws

    started = time.monotonic()
    await adapter.stop(force_close_after_s=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"stop() exceeded deadline: {elapsed:.2f}s"
    assert stub_ws.close_called, "force-close path must still attempt graceful close first"
    assert stub_ws.transport.close.called, (
        "transport.close() fallback was not invoked after ws.close() timeout"
    )
    assert adapter._ws is None, "stop() must clear self._ws after force-close"


@pytest.mark.asyncio
async def test_stop_graceful_path_completes_fast(tmp_path):
    """When nothing is wedged, the graceful path must complete within deadline.

    Pre-fix this was already true (no behavior change); we pin it so the
    force-close fallback doesn't regress the happy path.
    """
    adapter = _make_adapter(tmp_path)
    stub_ws = _StubWs(close_sleep_s=0.01)  # close almost immediately
    adapter._ws = stub_ws

    started = time.monotonic()
    await adapter.stop(force_close_after_s=2.0)
    elapsed = time.monotonic() - started

    # ≤ 0.5 s is generous; in practice this lands in single-digit ms.
    assert elapsed < 0.5, (
        f"happy-path stop() regressed: elapsed={elapsed:.2f}s (expected <0.5s)"
    )
    assert stub_ws.close_called
    # transport.close fallback must NOT fire when graceful close succeeds.
    assert not stub_ws.transport.close.called, (
        "transport.close() should not be invoked when ws.close() returned in time"
    )
    assert adapter._ws is None


@pytest.mark.asyncio
async def test_stop_default_deadline_reads_settings(tmp_path, monkeypatch):
    """When ``force_close_after_s`` is omitted, settings must drive the cap."""
    adapter = _make_adapter(tmp_path)
    stub_ws = _StubWs(close_sleep_s=30.0)
    adapter._ws = stub_ws

    monkeypatch.setattr(
        "openakita.config.settings.channels_ws_force_close_after_s", 0.3, raising=False
    )

    started = time.monotonic()
    await adapter.stop()  # no explicit deadline — must read from settings
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, (
        f"settings-driven deadline not honored: elapsed={elapsed:.2f}s "
        "(expected <1.5s with 0.3s settings cap)"
    )
    assert stub_ws.transport.close.called
