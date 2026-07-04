"""Sprint 14 / v31 Phase A regression: ``MessageGateway.stop()`` must run
adapter shutdown concurrently with a per-adapter bounded timeout.

Forensic background — see ``_v31_biz/_phase_a_shutdown_chain.md`` and
``_v31_biz/_phase_a_adapter_stop_audit.md``:

* Pre-fix ``stop()`` looped ``await adapter.stop()`` serially. With 6
  feishu + 3 wework_ws + 2 qqbot adapters, a single wedged
  ``wework_ws._connection_task`` (no ``wait_for``) held the whole
  shutdown hostage for 13~20 s — reproduced 6 / 6 times in v23/v24/v26/v28/v29/v30.

These tests pin three guarantees of the new path:

1. Adapter ``stop()`` calls run concurrently (gather) — total wallclock
   should be roughly the slowest single adapter, not the sum.
2. A per-adapter ``asyncio.wait_for`` cap (default 8 s) kicks in when an
   adapter never returns; the call site continues + logs.
3. An adapter that raises does not break the rest of the shutdown.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openakita.channels.base import ChannelAdapter
from openakita.channels.gateway import MessageGateway
from openakita.channels.types import MediaFile


class _FakeAdapter(ChannelAdapter):
    """Minimal ``ChannelAdapter`` stub that exposes a tunable ``stop()``.

    Subclassing the real abstract base ensures the ``isinstance`` /
    ``channel_name`` plumbing matches what ``MessageGateway`` registers.
    """

    channel_name = "fake"

    def __init__(
        self,
        name: str,
        *,
        stop_sleep_s: float = 0.0,
        raise_on_stop: BaseException | None = None,
    ) -> None:
        super().__init__(channel_name=name)
        self._stop_sleep_s = stop_sleep_s
        self._raise_on_stop = raise_on_stop
        self.stopped = False
        self.stop_started_at: float | None = None
        self.stop_finished_at: float | None = None

    async def start(self) -> None:  # pragma: no cover - not exercised
        return None

    async def stop(self) -> None:
        self.stop_started_at = time.monotonic()
        try:
            if self._raise_on_stop is not None:
                raise self._raise_on_stop
            if self._stop_sleep_s > 0:
                await asyncio.sleep(self._stop_sleep_s)
            self.stopped = True
        finally:
            self.stop_finished_at = time.monotonic()

    async def send_message(self, message):  # pragma: no cover - not used here
        return ""

    async def download_media(self, media: MediaFile) -> Path:  # pragma: no cover
        raise NotImplementedError

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:  # pragma: no cover
        raise NotImplementedError


def _gateway_with_adapters(adapters: list[_FakeAdapter]) -> MessageGateway:
    gw = MessageGateway(session_manager=MagicMock())
    for ad in adapters:
        gw._adapters[ad.channel_name] = ad
    # ``stop()`` reads ``_running`` flag transitions; harmless to pre-set
    # to True so the cleanup paths (processing_task etc.) all short-circuit
    # the way they would in production.
    gw._running = True
    return gw


@pytest.mark.asyncio
async def test_gateway_stop_runs_adapters_concurrently(monkeypatch):
    """Three adapters each sleeping 1.0 s should finish in ~1.0 s, not ~3.0 s.

    Pre-fix the serial loop made the wallclock ``≈ sum``; the
    ``asyncio.gather`` rewrite makes it ``≈ max``.
    """
    adapters = [_FakeAdapter(f"fake:{i}", stop_sleep_s=1.0) for i in range(3)]
    gw = _gateway_with_adapters(adapters)

    started = time.monotonic()
    await gw.stop()
    elapsed = time.monotonic() - started

    assert all(a.stopped for a in adapters)
    # 3 × 1.0 s serial would be ~3 s. Concurrent should land close to 1 s;
    # we allow up to 2.0 s to absorb scheduler jitter on slow CI.
    assert elapsed < 2.0, (
        f"adapters stopped serially? elapsed={elapsed:.2f}s "
        "(expected <2.0s with concurrent gather)"
    )


@pytest.mark.asyncio
async def test_gateway_stop_timeout_per_adapter(monkeypatch):
    """A single wedged adapter must NOT block the gateway past the cap.

    With a 1.0 s cap and one adapter sleeping 30 s, ``stop()`` must return
    in ≤ ~1.5 s (cap + scheduling slack), the wedged adapter is logged +
    abandoned, and the well-behaved adapters still report ``stopped``.
    """
    # Shrink the timeout knob for the test so we don't sleep 8 s.
    monkeypatch.setattr(
        "openakita.config.settings.channels_gateway_stop_timeout_s", 1, raising=False
    )

    healthy = _FakeAdapter("fake:healthy", stop_sleep_s=0.05)
    wedged = _FakeAdapter("fake:wedged", stop_sleep_s=30.0)
    gw = _gateway_with_adapters([healthy, wedged])

    started = time.monotonic()
    await gw.stop()
    elapsed = time.monotonic() - started

    assert healthy.stopped is True
    # Wedged was cancelled mid-sleep — its stop body did not finish.
    assert wedged.stopped is False
    # ≤ 1.0 s cap + small slack. If this regresses to no-timeout we'd see
    # ~30 s and the test would visibly hang past the asyncio default.
    assert elapsed < 3.0, (
        f"stop() did not honor per-adapter timeout: elapsed={elapsed:.2f}s"
    )


@pytest.mark.asyncio
async def test_gateway_stop_adapter_raise_does_not_block_others():
    """If one adapter ``raise``s on stop, the others must still stop cleanly."""
    bad = _FakeAdapter("fake:bad", raise_on_stop=RuntimeError("boom"))
    good_a = _FakeAdapter("fake:good_a", stop_sleep_s=0.05)
    good_b = _FakeAdapter("fake:good_b", stop_sleep_s=0.05)
    gw = _gateway_with_adapters([bad, good_a, good_b])

    # Should not raise.
    await gw.stop()

    assert good_a.stopped is True
    assert good_b.stopped is True
    # bad's body raised before setting stopped=True.
    assert bad.stopped is False


@pytest.mark.asyncio
async def test_gateway_stop_no_adapters_is_noop():
    """Empty adapter dict must not crash (gather over [] is a no-op)."""
    gw = _gateway_with_adapters([])
    await gw.stop()
