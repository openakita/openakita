"""Per-plugin test bootstrap for avatar-studio.

Mirrors the isolation approach used by ``plugins/seedance-video/tests/conftest.py``:
multiple plugins ship top-level modules with the SAME name (``task_manager``,
``plugin``, ``models`` ...). Pytest collects across plugin trees, so Python's
import cache will happily return the first one it loaded — leading to
``ImportError: cannot import name '...'`` on the second plugin.

We:

1. Push *this* plugin directory to the front of ``sys.path`` so flat imports
   like ``from avatar_models import MODES`` resolve against this plugin.
2. Invalidate any cached modules that share names with sibling plugins so the
   first import inside a test pulls THIS plugin's copy, not stale bytecode.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# Names colliding with other plugins / SDK modules. Wipe so the first import
# inside a test pulls THIS plugin's copy.
for _m in (
    "plugin",
    "avatar_models",
    "avatar_task_manager",
    "avatar_dashscope_client",
    "avatar_pipeline",
):
    sys.modules.pop(_m, None)
