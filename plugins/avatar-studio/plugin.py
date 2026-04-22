"""avatar-studio — DashScope digital human studio (Phase 0 skeleton).

Full implementation lands in Phase 4. This skeleton only validates that the
plugin loads under the SDK 0.7.x contract (fully self-contained, zero
contrib imports, vendored helpers under ``avatar_studio_inline``).
"""

from __future__ import annotations

import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class Plugin(PluginBase):
    """Skeleton — Phase 4 will fill in routes / tools / pipeline wiring."""

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        logger.info("avatar-studio Phase 0 skeleton loaded (no routes yet)")

    def on_unload(self) -> None:  # pragma: no cover - skeleton
        logger.info("avatar-studio unloaded")
