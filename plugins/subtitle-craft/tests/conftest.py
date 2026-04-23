"""Per-plugin test bootstrap for subtitle-craft.

Pushes the plugin directory to the front of sys.path so flat imports resolve
against this plugin, and invalidates any cached modules that share names with
sibling plugins (e.g. ``plugin``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "plugin",
    "subtitle_models",
    "subtitle_task_manager",
    "subtitle_asr_client",
    "subtitle_renderer",
    "subtitle_pipeline",
):
    sys.modules.pop(_m, None)
