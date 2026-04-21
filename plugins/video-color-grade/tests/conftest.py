"""Per-plugin test bootstrap for video-color-grade.

Mirrors the bgm-mixer / transcribe-archive convention: insert the plugin
root into ``sys.path`` so tests can ``import grade_engine`` without
depending on the host's package layout, and pop sibling-plugin modules
from ``sys.modules`` so a previous test that imported, say,
``plugins/bgm-mixer/task_manager.py`` does not shadow ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "grade_engine",
    "task_manager",
    "plugin",
    # Sibling-plugin modules with overlapping names — drop the cache so
    # the first import resolves to THIS plugin's copy.
    "mixer_engine",
    "transcribe_engine",
    "bgm_engine",
):
    sys.modules.pop(_m, None)
