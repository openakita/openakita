"""Unified NewsNow aggregator fetcher.

Iterates all ``kind=="newsnow"`` sources in :data:`SOURCE_DEFS`, calls
:func:`fetch_from_newsnow` for each enabled channel, and returns the
merged item list.  Individual channel failures are captured per-source
so one broken upstream never blocks the rest.
"""

from __future__ import annotations

import logging
from typing import Any

from finpulse_fetchers._http import jittered_sleep
from finpulse_fetchers.base import BaseFetcher, FetchReport, NormalizedItem
from finpulse_fetchers.newsnow_base import (
    NewsNowTransportError,
    fetch_from_newsnow,
    newsnow_mode,
)
from finpulse_models import SOURCE_DEFS

logger = logging.getLogger(__name__)

_MAX_TOTAL_ITEMS = 2000


class NewsNowFetcher(BaseFetcher):
    """Fetch all enabled NewsNow channels in a single pass."""

    source_id = "newsnow"

    async def fetch(self, **_: Any) -> list[NormalizedItem]:
        mode = newsnow_mode(self._config)
        if mode not in {"public", "self_host"}:
            return []

        channels = _resolve_channels(self._config)
        if not channels:
            return []

        out: list[NormalizedItem] = []
        self._channel_reports: list[dict[str, Any]] = []

        for sid, newsnow_id in channels:
            if len(out) >= _MAX_TOTAL_ITEMS:
                break
            try:
                items = await fetch_from_newsnow(
                    platform_id=newsnow_id,
                    source_id=sid,
                    config=self._config,
                    timeout_sec=self._timeout_sec,
                )
                out.extend(items)
                self._channel_reports.append(
                    {"source_id": sid, "count": len(items), "error": None}
                )
                logger.info(
                    "newsnow channel %s (%s): %d items",
                    sid, newsnow_id, len(items),
                )
            except NewsNowTransportError as exc:
                logger.warning(
                    "newsnow channel %s (%s) failed: [%s] %s",
                    sid, newsnow_id, exc.kind, exc,
                )
                self._channel_reports.append(
                    {"source_id": sid, "count": 0, "error": exc.kind}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "newsnow channel %s (%s) unexpected error: %s",
                    sid, newsnow_id, exc,
                )
                self._channel_reports.append(
                    {"source_id": sid, "count": 0, "error": str(exc)[:120]}
                )

            if len(out) < _MAX_TOTAL_ITEMS:
                await jittered_sleep(80, 120)

        return out


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
