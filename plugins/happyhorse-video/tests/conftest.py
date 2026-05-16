"""Per-plugin test bootstrap for happyhorse-video.

Mirrors ``plugins/seedance-video/tests/conftest.py`` — multiple plugins
ship top-level modules with the same bare name (e.g. ``plugin``,
``happyhorse_models``, ``happyhorse_dashscope_client``) and pytest
collects across plugin trees, so we have to:

1. Push *this* plugin directory to the front of ``sys.path`` so the
   ``from happyhorse_models import ...`` style imports inside
   plugin.py / pipeline / client resolve against this plugin.
2. Drop any cached sibling modules from ``sys.modules`` so the first
   import inside a test pulls THIS plugin's copy.
3. Push the per-test ``tests/`` dir to ``sys.path`` so
   ``from _plugin_loader import ...`` resolves cleanly.

We deliberately do NOT stub ``openakita.plugins.api`` — the
``openakita`` package is installed by ``pip install -e .[dev]`` and
both seedance-video and happyhorse-video import ``PluginAPI``/
``PluginBase`` from the real package, so stubbing here causes
collisions with the root-level ``tests/conftest.py`` when the entire
suite is collected together.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_PLUGIN_DIR_STR = str(_PLUGIN_DIR)
while _PLUGIN_DIR_STR in sys.path:
    sys.path.remove(_PLUGIN_DIR_STR)
sys.path.insert(0, _PLUGIN_DIR_STR)

_TESTS_DIR_STR = str(Path(__file__).resolve().parent)
while _TESTS_DIR_STR in sys.path:
    sys.path.remove(_TESTS_DIR_STR)
sys.path.insert(0, _TESTS_DIR_STR)

# Drop any sibling-plugin modules from previous collections.
for _m in (
    "plugin",
    "happyhorse_models",
    "happyhorse_model_registry",
    "happyhorse_task_manager",
    "happyhorse_dashscope_client",
    "happyhorse_pipeline",
    "happyhorse_long_video",
    "happyhorse_prompt_optimizer",
    "happyhorse_tts_edge",
):
    sys.modules.pop(_m, None)
