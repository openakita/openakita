"""Sprint 17 / v34 P1-A regression: ``QQBotAdapter.stop()`` must honor
``force_close_after_s`` and not block on hung ``_task`` /
``_webhook_runner.cleanup()``.

Forensic background — see ``_v34_biz/_im_shutdown_chain_inventory.md``:

v33 measured qqbot stop() at ~0.5 s (already fast), but ``await self._task``
sits on top of ``websockets.connect(..., close_timeout=10)`` in WS mode.
A QQ Gateway hiccup that delays the close-frame ACK can stretch the
graceful path to 10 s — well over the ≤10 s shutdown SLO. The
force-close path is preventive: cap each cooperative await at the
adapter-wide ``channels_ws_force_close_after_s`` (default 2 s).

Tests pin:
1. A hung ``_webhook_runner.cleanup()`` does NOT block stop().
2. A hung ``_task`` does NOT block stop().
3. Graceful path completes promptly when nothing is wedged.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.channels.adapters.qq_official import QQBotAdapter


def _make_adapter(tmp_path: Path) -> QQBotAdapter:
    """Build a QQBotAdapter without touching websockets/aiohttp at __init__."""
    return QQBotAdapter(
        app_id="test_app",
        app_secret="test_secret",
        sandbox=True,
        mode="websocket",  # avoids the aiohttp-only webhook code path
        channel_name="qqbot:test",
        bot_id="test",
        media_dir=str(tmp_path / "media"),
    )


@pytest.mark.asyncio
async def test_stop_does_not_block_on_wedged_task(tmp_path):
    """A ``_task`` that ignores cancellation must not pin stop()."""
    adapter = _make_adapter(tmp_path)

    async def _never_returns():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(30)

    adapter._task = asyncio.create_task(_never_returns())

    started = time.monotonic()
    await adapter.stop(force_close_after_s=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, (
        f"stop() did not honor force_close_after_s: elapsed={elapsed:.2f}s"
    )

    # Cleanup
    if not adapter._task.done():
        adapter._task.cancel()
        try:
            await asyncio.wait_for(adapter._task, timeout=0.1)
        except (asyncio.CancelledError, TimeoutError):
            pass


@pytest.mark.asyncio
async def test_stop_does_not_block_on_hung_webhook_runner_cleanup(tmp_path):
    """A ``_webhook_runner.cleanup()`` that hangs must not pin stop()."""
    adapter = _make_adapter(tmp_path)

    async def _hung_cleanup():
        await asyncio.sleep(30)

    runner: Any = AsyncMock()
    runner.cleanup = _hung_cleanup
    adapter._webhook_runner = runner

    started = time.monotonic()
    await adapter.stop(force_close_after_s=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, (
        f"stop() did not honor force_close_after_s for webhook_runner: elapsed={elapsed:.2f}s"
    )
    assert adapter._webhook_runner is None


@pytest.mark.asyncio
async def test_stop_graceful_path_completes_fast(tmp_path):
    """Happy path: nothing wedged, stop() returns quickly without warnings."""
    adapter = _make_adapter(tmp_path)

    async def _quick_done():
        return None

    adapter._task = asyncio.create_task(_quick_done())
    await asyncio.sleep(0.01)  # let it complete

    started = time.monotonic()
    await adapter.stop(force_close_after_s=2.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, (
        f"happy-path stop() regressed: elapsed={elapsed:.2f}s"
    )


@pytest.mark.asyncio
async def test_stop_default_deadline_reads_settings(tmp_path, monkeypatch):
    """Omitting ``force_close_after_s`` must read from settings."""
    adapter = _make_adapter(tmp_path)

    async def _never_returns():
        await asyncio.sleep(30)

    adapter._task = asyncio.create_task(_never_returns())

    monkeypatch.setattr(
        "openakita.config.settings.channels_ws_force_close_after_s", 0.3, raising=False
    )

    started = time.monotonic()
    await adapter.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, (
        f"settings-driven deadline not honored: elapsed={elapsed:.2f}s"
    )

    if not adapter._task.done():
        adapter._task.cancel()
        try:
            await asyncio.wait_for(adapter._task, timeout=0.1)
        except (asyncio.CancelledError, TimeoutError):
            pass
