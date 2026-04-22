"""Per-plugin test bootstrap for bgm-mixer.

Mirrors the seedance-video / transcribe-archive convention: insert the
plugin root into ``sys.path`` so tests can ``import mixer_engine``
without depending on the host's package layout, and pop sibling-plugin
modules from ``sys.modules`` so a previous test that imported
``plugins/bgm-suggester/bgm_engine.py`` does not shadow ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "mixer_engine",
    "task_manager",
    "plugin",
    # Sibling-plugin modules with overlapping names — drop the cache
    # so the first import resolves to THIS plugin's copy.
    "bgm_engine",
    "transcribe_engine",
):
    sys.modules.pop(_m, None)
