"""Unified NewsNow aggregator fetcher.

Iterates all ``kind=="newsnow"`` sources in :data:`SOURCE_DEFS`, calls
:func:`fetch_from_newsnow` for each enabled channel, and returns the
merged item list.  Individual channel failures are captured per-source
so one broken upstream never blocks the rest.

Channels are fanned out concurrently with a small semaphore so a fresh
"全部拉取" against 15+ default-enabled channels finishes well within
the 30s host-bridge timeout — earlier the for-loop ran them serially
and the cumulative wall time tipped the UI into a spurious timeout on
first run. The cap is configurable via ``newsnow.channel_concurrency``
(default 4) so self-hosted deployments can crank it up safely while the
public volunteer-run upstream keeps a polite ceiling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from finpulse_fetchers._http import jittered_sleep
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_fetchers.newsnow_base import (
    NewsNowTransportError,
    fetch_from_newsnow,
    newsnow_mode,
)
from finpulse_models import SOURCE_DEFS

logger = logging.getLogger(__name__)

_MAX_TOTAL_ITEMS = 2000
_DEFAULT_CHANNEL_CONCURRENCY = 4
_MAX_CHANNEL_CONCURRENCY = 12


class NewsNowFetcher(BaseFetcher):
    """Fetch all enabled NewsNow channels concurrently in a single pass."""

    source_id = "newsnow"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        mode = newsnow_mode(self._config)
        if mode not in {"public", "self_host"}:
            return []

        channels = _resolve_channels(self._config)
        if not channels:
            return []

        concurrency = _resolve_channel_concurrency(self._config, len(channels))
        sem = asyncio.Semaphore(concurrency)

        async def _one(
            sid: str, newsnow_id: str
        ) -> tuple[str, str, list[NormalizedItem], str | None]:
            async with sem:
                # Stagger the first byte of each request a hair so fanning
                # out 15 channels at once doesn't look like a thundering
                # herd to the volunteer-run upstream. The jitter is well
                # below the per-channel timeout so it doesn't add wall
                # time worth measuring.
                await jittered_sleep(40, 80)
                try:
                    items = await fetch_from_newsnow(
                        platform_id=newsnow_id,
                        source_id=sid,
                        config=self._config,
                        timeout_sec=self._timeout_sec,
                    )
                    logger.info(
                        "newsnow channel %s (%s): %d items",
                        sid, newsnow_id, len(items),
                    )
                    return sid, newsnow_id, items, None
                except NewsNowTransportError as exc:
                    logger.warning(
                        "newsnow channel %s (%s) failed: [%s] %s",
                        sid, newsnow_id, exc.kind, exc,
                    )
                    return sid, newsnow_id, [], exc.kind
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "newsnow channel %s (%s) unexpected error: %s",
                        sid, newsnow_id, exc,
                    )
                    return sid, newsnow_id, [], str(exc)[:120]

        results = await asyncio.gather(
            *[_one(sid, nid) for sid, nid in channels],
            return_exceptions=False,
        )

        # Preserve the original channel order in `_channel_reports` so
        # the UI's per-source drawer renders in the same priority the
        # SOURCE_DEFS dict declared.
        out: list[NormalizedItem] = []
        self._channel_reports: list[dict[str, Any]] = []
        for sid, _newsnow_id, items, error in results:
            if not error and items:
                # Apply the global cap globally rather than per-channel
                # so a single noisy hot-list doesn't starve the rest.
                if len(out) < _MAX_TOTAL_ITEMS:
                    remaining = _MAX_TOTAL_ITEMS - len(out)
                    out.extend(items[:remaining])
            self._channel_reports.append(
                {"source_id": sid, "count": len(items), "error": error}
            )

        return out


def _resolve_channel_concurrency(config: dict[str, str], n_channels: int) -> int:
    """Pick a sensible concurrency cap, honouring the operator override
    in ``newsnow.channel_concurrency`` and clamping to the safe range
    so a typo cannot accidentally hammer the volunteer upstream.
    """
    raw = (config.get("newsnow.channel_concurrency") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = _DEFAULT_CHANNEL_CONCURRENCY
    else:
        value = _DEFAULT_CHANNEL_CONCURRENCY
    if value <= 0:
        value = _DEFAULT_CHANNEL_CONCURRENCY
    value = min(value, _MAX_CHANNEL_CONCURRENCY)
    # Never spin up more workers than channels — otherwise the semaphore
    # is just dead weight.
    return max(1, min(value, n_channels))


def _resolve_channels(config: dict[str, str]) -> list[tuple[str, str]]:
    """Return ``[(source_id, newsnow_id), ...]`` for enabled newsnow sources."""
    only_raw = (config.get("_newsnow.only_sources") or "").strip()
    only_sources = {s.strip() for s in only_raw.split(",") if s.strip()}
    channels: list[tuple[str, str]] = []
    for sid, defn in SOURCE_DEFS.items():
        if defn.get("kind") != "newsnow":
            continue
        if only_sources and sid not in only_sources and str(defn.get("newsnow_id") or "") not in only_sources:
            continue
        newsnow_id = defn.get("newsnow_id")
        if not newsnow_id:
            continue
        enabled_key = f"source.{sid}.enabled"
        enabled = config.get(enabled_key, "")
        if enabled == "":
            if defn.get("default_enabled"):
                channels.append((sid, str(newsnow_id)))
        elif enabled.lower() == "true":
            channels.append((sid, str(newsnow_id)))
    return channels


__all__ = ["NewsNowFetcher"]
