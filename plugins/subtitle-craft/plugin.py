"""Subtitle Craft — AI subtitle full-lifecycle plugin.

Phase 0 skeleton: backend entry point, no business logic yet. Phases 1–6 will
fill in models / task manager / clients / pipeline / routes / UI per
``docs/subtitle-craft-plan.md``.

Architectural rules baked in from day 1 (red-line guardrails):

- Self-contained: imports only ``openakita.*`` (host SDK) and
  ``subtitle_craft_inline.*`` (vendored helpers). **No** ``from
  plugins-archive``, ``from _shared``, ``from sdk.contrib`` imports — see
  ``tests/test_skeleton.py`` for the grep guards.
- **No Handoff in v1.0**: no ``/handoff/*`` routes, no ``*_handoff_*`` tools,
  no ``subtitle_handoff.py`` module. Schema-only reservation in Phase 1
  (``tasks.origin_*`` fields, ``assets_bus`` table) is invisible to v1.0
  pipeline. v2.0 will fill in the routes/UI without data migration.
- **Playwright lazy import** (P0-13): never import ``playwright`` at module
  scope; only inside the ``subtitle_renderer.burn_subtitles_html`` function
  (Phase 2b). On unload, close the singleton via ``_PlaywrightSingleton.close()``.
"""

from __future__ import annotations

import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class Plugin(PluginBase):
    """Subtitle Craft plugin entry.

    Phase 0 only wires up ``on_load`` / ``on_unload``. All routes, task
    manager, pipeline runners, and clients are added in Phases 1–4.
    """

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._data_dir = api.get_data_dir()
        logger.info(
            "subtitle-craft on_load: skeleton ready, data_dir=%s",
            self._data_dir,
        )

    def on_unload(self) -> None:
        # Phase 2b will add ``_PlaywrightSingleton.close()`` here once the
        # renderer module exists. Keep this method present from day 1 so
        # the contract is never forgotten.
        logger.info("subtitle-craft on_unload: skeleton shutdown")
