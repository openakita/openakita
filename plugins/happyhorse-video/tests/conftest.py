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
3. Stub the ``openakita.plugins.api`` package because the production
   import requires the host process to be initialised, which we don't
   need for the unit tests.
"""

from __future__ import annotations

import sys
import types
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

# Stub openakita.plugins.api so plugin.py can be imported standalone.
if "openakita.plugins.api" not in sys.modules:
    fake_pkg = types.ModuleType("openakita")
    fake_plugins = types.ModuleType("openakita.plugins")
    fake_api = types.ModuleType("openakita.plugins.api")

    class _StubBase:  # noqa: D401 — test stub
        pass

    fake_api.PluginAPI = _StubBase
    fake_api.PluginBase = _StubBase
    sys.modules["openakita"] = fake_pkg
    sys.modules["openakita.plugins"] = fake_plugins
    sys.modules["openakita.plugins.api"] = fake_api

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
