"""Media Post — publishing-kit plugin (Phase 0 skeleton).

This is the Phase 0 scaffold per ``docs/media-post-plan.md`` §11 Phase 0.
The actual lifecycle (``on_load``/``on_unload``) plus the 22 FastAPI
routes will be wired up in Phase 4. Keeping this entry minimal lets the
host import-smoke test succeed (``Gate 0``) without pulling in mode
modules that don't exist yet.

Architectural rules baked in from day 0 (red-line guardrails per §13):

- Self-contained imports — only the host plugin API surface plus
  sibling ``mediapost_*`` / ``mediapost_inline.*`` modules. Imports
  from the legacy archive tree or the removed SDK contrib subpackage
  are forbidden and grep-guarded by ``tests/test_skeleton.py``.
- Cross-plugin dispatch routes (the v2.0 handoff layer) are absent
  from this file in v1.0; the absence is grep-guarded by the same
  test module. v2.0 will add the layer with zero data migration
  thanks to the ``assets_bus`` schema reservation in Phase 1.
- Playwright lazy import — ``playwright`` is never imported at module
  scope; only inside ``mediapost_chapter_renderer.render_chapter_card``.
"""

from __future__ import annotations

import logging

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "media-post"


class Plugin(PluginBase):
    """Media Post plugin — Phase 0 skeleton.

    Phases 1-6 will progressively flesh out:

    - Phase 1: ``mediapost_models`` + ``mediapost_task_manager`` (6 tables).
    - Phase 2b: ``mediapost_vlm_client`` (Qwen-VL-max + Qwen-Plus).
    - Phase 3: 4 mode modules + ``mediapost_pipeline`` (8-step orchestrator).
    - Phase 4: 22 FastAPI routes wired into this class via ``on_load``.
    - Phase 5: ``ui/dist/index.html`` (~2700 lines, 4-tab UI).
    - Phase 6: integration tests + README/SKILL/CHANGELOG.
    """

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        api.log(f"{PLUGIN_ID} plugin loaded (Phase 0 skeleton — no routes yet)")

    async def on_unload(self) -> None:
        self._api.log(f"{PLUGIN_ID} plugin unloaded")
