"""Per-plugin test bootstrap for ppt-to-video.

Mirrors the video-bg-remove convention: insert the plugin root into
``sys.path`` so tests can ``import slide_engine`` and ``import plugin``
without depending on the host's package layout, and pop sibling-plugin
modules from ``sys.modules`` so a previous test that imported, say,
``plugins/video-bg-remove/task_manager.py`` does not shadow ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "slide_engine",
    "task_manager",
    "plugin",
    # Sibling-plugin modules with overlapping names — drop the cache.
    "matting_engine",
    "grade_engine",
    "grid_engine",
    "mixer_engine",
    "transcribe_engine",
    "studio_engine",
    "templates",
    "poster_engine",
    "providers",
):
    sys.modules.pop(_m, None)
