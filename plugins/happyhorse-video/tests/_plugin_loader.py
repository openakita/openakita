"""Load happyhorse-video's ``plugin.py`` under a unique name for tests.

When pytest collects multiple plugin test suites in one process the bare
``plugin`` module from a previous test can shadow ours via sys.modules.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_MODULE = PLUGIN_DIR / "plugin.py"
MODULE_NAME = "_openakita_happyhorse_video_plugin_for_tests"


def load_happyhorse_plugin() -> ModuleType:
    plugin_dir = str(PLUGIN_DIR)
    while plugin_dir in sys.path:
        sys.path.remove(plugin_dir)
    sys.path.insert(0, plugin_dir)

    for module_name in (
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
        sys.modules.pop(module_name, None)
    sys.modules.pop(MODULE_NAME, None)

    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_MODULE)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load happyhorse-video plugin from {PLUGIN_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
