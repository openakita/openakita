"""Per-plugin test bootstrap for manga-studio.

Mirrors the isolation approach used by ``plugins/avatar-studio/tests/conftest.py``:
multiple plugins ship top-level modules with the SAME name (``task_manager``,
``plugin``, ``models`` ...). Pytest collects across plugin trees, so Python's
import cache will happily return the first one it loaded — leading to
``ImportError: cannot import name '...'`` on the second plugin.

We:

1. Push *this* plugin directory to the front of ``sys.path`` so flat imports
   like ``from manga_models import VISUAL_STYLES`` resolve against this plugin.
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
    "manga_models",
    "manga_task_manager",
    "manga_pipeline",
    "direct_ark_client",
    "direct_wanxiang_client",
    "tts_client",
    "comfy_client",  # phase 3.1 — workflow backend
    "script_writer",
    "prompt_assembler",
    "ffmpeg_service",
    "manga_templates",  # phase 4.4 — story templates
):
    sys.modules.pop(_m, None)


def pytest_configure(config):  # type: ignore[no-untyped-def]
    """Register custom markers so ``pytest -m integration`` doesn't warn."""
    config.addinivalue_line(
        "markers",
        "integration: opt-in tests that hit real Ark / DashScope / "
        "RunningHub endpoints (requires ARK_API_KEY / DASHSCOPE_API_KEY / "
        "RUNNINGHUB_API_KEY)",
    )


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    """Skip integration tests by default unless ``-m integration`` is set."""
    if config.getoption("-m") == "integration":
        return
    skip_integration = __import__("pytest").mark.skip(
        reason="integration test — pass ``-m integration`` to run"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
