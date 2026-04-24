"""Per-plugin test bootstrap for footage-gate.

Mirrors the isolation approach used by ``plugins/seedance-video/tests/conftest.py``
and ``plugins/subtitle-craft/tests/conftest.py``: multiple plugins ship
top-level modules with the SAME name (``task_manager``, ``plugin``, ``models``
...).  Pytest collects across plugin trees, so Python's import cache will
happily return the first one it loaded — leading to confusing
``ImportError: cannot import name 'TaskManager'`` errors on the second
plugin.

We:

1. Push *this* plugin directory to the front of ``sys.path`` so flat imports
   like ``from footage_gate_task_manager import FootageGateTaskManager``
   resolve against this plugin.
2. Invalidate any cached modules that share names with sibling plugins so
   the first import inside a test pulls THIS plugin's copy, not stale
   bytecode from a sibling already collected by pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# Names that may collide with other plugins / SDK modules. The
# ``footage_gate_*`` prefix is unique to this plugin (intentionally — see
# the v1.0 plan's "Naming Decisions" section), but we still flush the cache
# so a previous test that imported a sibling's ``plugin`` / ``task_manager``
# does not shadow ours.
for _m in (
    "plugin",
    "task_manager",
    "models",
    "footage_gate_models",
    "footage_gate_task_manager",
    "footage_gate_ffmpeg",
    "footage_gate_grade",
    "footage_gate_silence",
    "footage_gate_review",
    "footage_gate_qc",
    "footage_gate_pipeline",
):
    sys.modules.pop(_m, None)
