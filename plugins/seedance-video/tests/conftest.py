"""Per-plugin test bootstrap for seedance-video.

Mirrors the isolation approach used by ``plugins/tongyi-image/tests/conftest.py``:
multiple plugins ship top-level modules with the SAME name (``task_manager``,
``plugin``, ``ark_client``, ``long_video`` ...).  Pytest collects across plugin
trees, so Python's import cache will happily return the first one it loaded —
leading to ``ImportError: cannot import name 'TaskManager'`` on the second
plugin.

We:

1. Push *this* plugin directory to the front of ``sys.path`` so flat imports
   like ``from task_manager import TaskManager`` resolve against this plugin.
2. Invalidate any cached modules that share names with sibling plugins so the
   first import inside a test pulls THIS plugin's copy, not stale bytecode.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# Names colliding with other plugins / SDK modules.  ``ark_client`` /
# ``long_video`` / ``models`` / ``prompt_optimizer`` are unique to this plugin
# but pytest still benefits from a clean slate so a previous test that imported
# the SDK ``contrib.prompt_optimizer`` module does not shadow our local one.
for _m in (
    "task_manager",
    "plugin",
    "ark_client",
    "long_video",
    "models",
    "prompt_optimizer",
):
    sys.modules.pop(_m, None)
