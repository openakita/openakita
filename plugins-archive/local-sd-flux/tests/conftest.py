"""Per-plugin test bootstrap for local-sd-flux.

Insert the plugin root into ``sys.path`` so tests can ``import
image_engine`` without depending on the host's package layout, and
pop sibling-plugin modules from ``sys.modules`` so the cache from a
previous test that imported, say, ``plugins/ppt-to-video/task_manager.py``
does not shadow ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "image_engine",
    "comfy_client",
    "workflow_presets",
    "task_manager",
    "plugin",
    # Sibling-plugin modules with overlapping basenames.
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
):
    sys.modules.pop(_m, None)
