"""Per-plugin test bootstrap for shorts-batch."""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "shorts_engine",
    "task_manager",
    "plugin",
    # Sibling-plugin modules that share basenames.
    "image_engine",
    "comfy_client",
    "workflow_presets",
    "matting_engine",
    "grade_engine",
    "grid_engine",
    "mixer_engine",
    "transcribe_engine",
    "studio_engine",
    "slide_engine",
    "templates",
    "poster_engine",
    "providers",
    "dub_engine",
):
    sys.modules.pop(_m, None)
