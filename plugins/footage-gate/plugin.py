"""Footage Gate — final-cut quality gate plugin (skeleton).

The full plugin entry point — ``FootageGatePlugin`` with the seven-step
``on_load`` ritual, the 16 REST routes, and the 5 AI tools — is implemented
incrementally across Phases 1–4 of the v1.0 plan. This file currently
provides only the bare ``Plugin`` class so the host can discover the plugin
and so import-time syntax errors surface in CI before any pipeline code
is added. See ``plugin_atoms_catalog.md`` and the v1.0 implementation plan
for the full design.
"""

from __future__ import annotations

import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class Plugin(PluginBase):
    """Footage Gate plugin skeleton.

    Lifecycle hooks are intentionally empty in Phase 0 — they are filled in
    Phase 4 once the data layer (Phase 1), FFmpeg tool layer (Phase 2), and
    pipeline (Phase 3) are in place.
    """

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        api.log("Footage Gate plugin loaded (skeleton — Phase 0)")

    async def on_unload(self) -> None:
        # Real teardown (TaskManager.close + SystemDepsManager.aclose +
        # cancel polling) lands in Phase 4.
        return None
