"""Per-plugin test bootstrap: keep modules isolated from sibling plugins.

Several plugins (tongyi-image / video-translator / poster-maker / ...) ship
top-level modules with the SAME name (``task_manager``, ``providers``,
``templates`` ...).  When pytest collects across plugin trees, Python's
import cache happily returns the first one it loaded — leading to
``ImportError: cannot import name 'BgmTaskManager'`` on the second plugin.

We invalidate the caches at conftest-load time so each plugin gets a clean
import surface (the matching ``plugins/*/tests/conftest.py`` files do the
same).  This must mirror the namespace used by the plugin under test.
"""
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in ("providers", "highlight_engine", "subtitle_engine", "studio_engine",
          "poster_engine", "translator_engine", "templates", "task_manager",
          "storyboard_engine",
          "tongyi_task_manager", "tongyi_prompt_optimizer",
          "tongyi_dashscope_client", "tongyi_models",
          "bgm_engine"):
    sys.modules.pop(_m, None)
