"""Per-plugin test bootstrap — keep media-post's modules import-isolated.

Mirrors the pattern used by ``plugins/tongyi-image/tests/conftest.py``
and ``plugins/subtitle-craft/tests/conftest.py``: prepend the plugin
directory to ``sys.path`` so ``import mediapost_models`` works during
pytest collection, then evict any cached ``mediapost_*`` / shared-name
modules from sibling plugins so this plugin's tests get a clean import
surface.
"""

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in (
    "mediapost_models",
    "mediapost_task_manager",
    "mediapost_vlm_client",
    "mediapost_recompose",
    "mediapost_cover_picker",
    "mediapost_seo_generator",
    "mediapost_chapter_renderer",
    "mediapost_pipeline",
    "mediapost_inline",
    "task_manager",
    "providers",
    "templates",
):
    sys.modules.pop(_m, None)
