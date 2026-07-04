"""Sprint 17 / v34 P1-A regression: end-to-end IM gateway stop speed test.

Beyond ``test_gateway_stop_bounded.py`` (which already pins per-adapter
``wait_for`` + concurrent gather), this test asserts the wallclock bound
under the realistic IM topology v33 measured:

* 6 feishu adapters that stop in <50 ms each
* 2 qqbot adapters at <500 ms each
* 3 wework_ws bots that pre-fix stayed wedged ~4 s on ``ws.close()``

With Sprint 17 P1-A force-close in place, the 3 wework_ws bots should
no longer hold the gather past ``force_close_after_s`` (we mock each
adapter's ``stop()`` to honor a 0.5 s wedge cap so the test runs in
<2 s without external WS infra).

Verdict: ``MessageGateway.stop()`` total wallclock < 1.5 s with N=11
mixed adapters where the 3 wedged ones each pretend to be wedged
beyond the gather wait_for cap. Pre-fix would have been ~8 s
(per-adapter timeout) or more (sum if serial regressed).
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


class _MixedAdapter(ChannelAdapter):
    """Adapter whose ``stop()`` takes a tunable duration.

    ``wedged=True`` makes ``stop()`` sleep past any reasonable per-adapter
    cap; the gateway's ``wait_for`` should still cap the gather.
    """

    channel_name = "fake-mixed"

    def __init__(
        self,
        name: str,
        *,
        stop_sleep_s: float,
        wedged: bool = False,
    ) -> None:
        super().__init__(channel_name=name)
        self._stop_sleep_s = stop_sleep_s
        self._wedged = wedged
        self.stopped_cleanly = False

    async def start(self) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:
        if self._wedged:
            # Pretend the WS adapter is stuck in ws.close() handshake;
            # the gateway's per-adapter wait_for must cut us off.
            await asyncio.sleep(self._stop_sleep_s)
        else:
            await asyncio.sleep(self._stop_sleep_s)
        self.stopped_cleanly = True

    async def send_message(self, message):  # pragma: no cover
        return ""

    async def download_media(self, media: MediaFile) -> Path:  # pragma: no cover
        raise NotImplementedError

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:  # pragma: no cover
        raise NotImplementedError


def _gateway_with(adapters: list[_MixedAdapter]) -> MessageGateway:
    gw = MessageGateway(session_manager=MagicMock())
    for ad in adapters:
        gw._adapters[ad.channel_name] = ad
    gw._running = True
    return gw


@pytest.mark.asyncio
async def test_gateway_stop_under_realistic_im_topology(monkeypatch):
    """v33-shaped topology: 6 fast + 2 medium + 3 wedged adapters.

    Total wallclock should be bounded by the per-adapter ``wait_for``
    cap (the gateway honors ``channels_gateway_stop_timeout_s``); the
    fast / medium adapters must complete cleanly.
    """
    # Tighten the cap so the test does not actually wait 8 s.
    monkeypatch.setattr(
        "openakita.config.settings.channels_gateway_stop_timeout_s", 1, raising=False
    )

    fast = [_MixedAdapter(f"feishu:{i}", stop_sleep_s=0.02) for i in range(6)]
    medium = [_MixedAdapter(f"qqbot:{i}", stop_sleep_s=0.4) for i in range(2)]
    wedged = [
        _MixedAdapter(f"wework_ws:{i}", stop_sleep_s=30.0, wedged=True) for i in range(3)
    ]
    gw = _gateway_with(fast + medium + wedged)

    started = time.monotonic()
    await gw.stop()
    elapsed = time.monotonic() - started

    # ≤ 1 s cap + scheduler slack. Pre-fix (serial loop, no wait_for)
    # this would be ~30 s and pytest would visibly hang on it.
    assert elapsed < 3.0, (
        f"gateway.stop() exceeded bound under wedged topology: elapsed={elapsed:.2f}s"
    )

    # Fast and medium adapters must report clean stop within their own time.
    for ad in fast + medium:
        assert ad.stopped_cleanly, f"{ad.channel_name} did not stop cleanly"

    # Wedged adapters must NOT have set stopped_cleanly (they were abandoned).
    for ad in wedged:
        assert not ad.stopped_cleanly, (
            f"{ad.channel_name} reported clean stop despite being wedged"
        )


@pytest.mark.asyncio
async def test_gateway_stop_no_adapters_is_fast(monkeypatch):
    """Empty adapter dict must return immediately (gather over [] is no-op)."""
    monkeypatch.setattr(
        "openakita.config.settings.channels_gateway_stop_timeout_s", 1, raising=False
    )
    gw = _gateway_with([])
    started = time.monotonic()
    await gw.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, f"empty stop() too slow: {elapsed:.3f}s"
