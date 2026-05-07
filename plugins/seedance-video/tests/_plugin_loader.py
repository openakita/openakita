"""Load the seedance-video ``plugin.py`` without cross-plugin module bleed.

Several plugins expose top-level modules named ``plugin.py``.  When pytest
collects multiple plugin test suites in one process, importing ``plugin`` can
reuse a sibling plugin from ``sys.modules``.  These helpers force the local
plugin directory to the front and load seedance-video under a unique module
name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_MODULE = PLUGIN_DIR / "plugin.py"
MODULE_NAME = "_openakita_seedance_video_plugin_for_tests"


def load_seedance_plugin() -> ModuleType:
    plugin_dir = str(PLUGIN_DIR)
    while plugin_dir in sys.path:
        sys.path.remove(plugin_dir)
    sys.path.insert(0, plugin_dir)

    for module_name in (
        "plugin",
        "ark_client",
        "long_video",
        "models",
        "prompt_optimizer",
        "task_manager",
    ):
        sys.modules.pop(module_name, None)
    sys.modules.pop(MODULE_NAME, None)

    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_MODULE)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load seedance-video plugin from {PLUGIN_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
