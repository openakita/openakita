"""Per-plugin test bootstrap for smart-poster-grid.

Mirrors the video-color-grade / bgm-mixer convention: insert the
plugin root into ``sys.path`` so tests can ``import grid_engine`` and
``import plugin`` without depending on the host's package layout, and
pop sibling-plugin modules from ``sys.modules`` so a previous test
that imported, say, ``plugins/bgm-mixer/task_manager.py`` does not
shadow ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "grid_engine",
    "task_manager",
    "plugin",
    # Sibling-plugin modules with overlapping names — drop the cache
    # so the first import resolves to THIS plugin's copy.
    "grade_engine",
    "mixer_engine",
    "transcribe_engine",
    "templates",
    "poster_engine",
    "providers",
    # Aliased poster-maker modules (loaded lazily in grid_engine):
    "_oa_pm_templates",
    "_oa_pm_poster_engine",
):
    sys.modules.pop(_m, None)
