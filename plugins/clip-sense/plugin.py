"""ClipSense Video Editor — AI-powered video editing plugin.

Modes: highlight_extract, silence_clean, topic_split, talking_polish.
Cloud intelligence (DashScope Paraformer + Qwen) + local execution (ffmpeg).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Plugin:
    """Skeleton — business logic added in Phase 4."""

    def on_load(self, api):  # type: ignore[no-untyped-def]
        self._api = api
        logger.info("clip-sense plugin loaded (skeleton)")

    async def on_unload(self) -> None:
        logger.info("clip-sense plugin unloaded (skeleton)")
