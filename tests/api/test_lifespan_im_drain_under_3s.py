"""Sprint 17 / v34 P1-A integration: ``stop_im_channels`` lifespan stage
must complete under 3 s even when the gateway has wedged adapters.

Forensic background — see ``_v34_biz/_im_shutdown_chain_inventory.md``:

* Pre-fix, ``stop_im_channels()`` ran each sub-stage serially without
  per-stage wait_for: ``gateway.drain → desktop_pool.stop →
  orchestrator.shutdown → session_manager.stop``. A failure in any
  one (e.g. a desktop_pool socket close blocking) could eat the
  outer 35 s wait_for budget.

* Post-fix:
  - Each stage is wrapped in ``wait_for(lifespan_stage_timeout_s)``.
  - ``gateway.drain`` and ``desktop_pool.stop`` are issued in parallel
    via ``asyncio.gather`` (they are independent — desktop_pool is not
    referenced by any in-flight IM task).
  - ``orchestrator.shutdown`` and ``session_manager.stop`` remain
    serial (they depend on the gateway being drained first).

This test pins those guarantees end-to-end by injecting mock
gateway/pool/orchestrator/session_manager globals into ``main`` and
asserting the wallclock + per-stage parallelism.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

import openakita.main as main_mod


@pytest.fixture
def _restore_main_globals():
    """Save + restore the module-level globals stop_im_channels touches."""
    saved = (
        main_mod._message_gateway,
        main_mod._session_manager,
        main_mod._orchestrator,
        main_mod._desktop_pool,
    )
    yield
    (
        main_mod._message_gateway,
        main_mod._session_manager,
        main_mod._orchestrator,
        main_mod._desktop_pool,
    ) = saved


@pytest.mark.asyncio
async def test_stop_im_channels_bounded_under_3s(_restore_main_globals, monkeypatch):
    """All four stages combined must finish within 3 s for the v34 SLO."""
    # Tighten per-stage timeout so the test doesn't actually wait 8 s.
    monkeypatch.setattr(
        "openakita.config.settings.lifespan_stage_timeout_s", 1, raising=False
    )

    # ── Mock gateway: drain takes ~0.8 s (≈ wework_ws force-close bound) ──
    gateway = AsyncMock()

    async def _drain(*, timeout: float = 30.0):
        await asyncio.sleep(0.8)

    gateway.drain = _drain
    gateway.stop = AsyncMock()

    # ── Mock desktop_pool: ~0.5 s (runs in parallel with gateway.drain) ──
    desktop_pool = AsyncMock()

    async def _pool_stop():
        await asyncio.sleep(0.5)

    desktop_pool.stop = _pool_stop

    # ── Mock orchestrator: ~0.05 s (serial after gateway) ──
    orchestrator = AsyncMock()

    async def _orch_shutdown():
        await asyncio.sleep(0.05)

    orchestrator.shutdown = _orch_shutdown

    # ── Mock session_manager: ~0.05 s (serial after orchestrator) ──
    session_manager = AsyncMock()

    async def _sm_stop():
        await asyncio.sleep(0.05)

    session_manager.stop = _sm_stop

    main_mod._message_gateway = gateway
    main_mod._desktop_pool = desktop_pool
    main_mod._orchestrator = orchestrator
    main_mod._session_manager = session_manager

    started = time.monotonic()
    await main_mod.stop_im_channels(graceful=True, drain_timeout=5.0)
    elapsed = time.monotonic() - started

    # gateway.drain (0.8) ∥ desktop_pool.stop (0.5)  → ~0.8 s
    # + orchestrator.shutdown (0.05) + session_manager.stop (0.05)
    # ≈ 0.9 s ideal; allow generous CI jitter.
    assert elapsed < 2.0, (
        f"stop_im_channels exceeded 2 s budget: elapsed={elapsed:.2f}s"
    )
    # And of course this must beat the 3 s envelope used by the v34 smoke target.
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_stop_im_channels_gateway_and_desktop_pool_run_in_parallel(
    _restore_main_globals, monkeypatch
):
    """Wallclock proves gateway.drain and desktop_pool.stop overlap.

    If both sleep 0.6 s in parallel the total is ~0.6 s; if serial the
    total is ~1.2 s. We assert <1.0 s to cleanly distinguish.
    """
    monkeypatch.setattr(
        "openakita.config.settings.lifespan_stage_timeout_s", 5, raising=False
    )

    gateway = AsyncMock()

    async def _drain(*, timeout: float = 30.0):
        await asyncio.sleep(0.6)

    gateway.drain = _drain

    desktop_pool = AsyncMock()

    async def _pool_stop():
        await asyncio.sleep(0.6)

    desktop_pool.stop = _pool_stop

    main_mod._message_gateway = gateway
    main_mod._desktop_pool = desktop_pool
    main_mod._orchestrator = None
    main_mod._session_manager = None

    started = time.monotonic()
    await main_mod.stop_im_channels(graceful=True, drain_timeout=5.0)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"gateway.drain and desktop_pool.stop are running serially "
        f"(elapsed={elapsed:.2f}s; expected ~0.6s if parallel, ~1.2s if serial)"
    )


@pytest.mark.asyncio
async def test_stop_im_channels_stage_timeout_does_not_kill_others(
    _restore_main_globals, monkeypatch
):
    """A hung stage must NOT prevent later stages from running."""
    monkeypatch.setattr(
        "openakita.config.settings.lifespan_stage_timeout_s", 0.4, raising=False
    )

    # gateway.drain hangs forever — the wait_for must cut it off.
    gateway = AsyncMock()

    async def _drain(*, timeout: float = 30.0):
        await asyncio.sleep(30)

    gateway.drain = _drain

    # orchestrator + session_manager must still be invoked.
    orchestrator = AsyncMock()
    session_manager = AsyncMock()

    main_mod._message_gateway = gateway
    main_mod._desktop_pool = None
    main_mod._orchestrator = orchestrator
    main_mod._session_manager = session_manager

    started = time.monotonic()
    await main_mod.stop_im_channels(graceful=True, drain_timeout=5.0)
    elapsed = time.monotonic() - started

    # gateway.drain bounded at 0.4 s + serial stages ≈ 0.4 s; allow slack.
    assert elapsed < 1.5, f"hung gateway pinned later stages: elapsed={elapsed:.2f}s"

    # The wedged gateway.drain must not have prevented orchestrator/session.
    orchestrator.shutdown.assert_awaited_once()
    session_manager.stop.assert_awaited_once()
    # globals reset
    assert main_mod._orchestrator is None
