"""Per-plugin test bootstrap: keep modules isolated from sibling plugins."""
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

for _m in ("providers", "highlight_engine", "subtitle_engine", "studio_engine",
          "poster_engine", "translator_engine", "templates", "task_manager",
          "storyboard_engine"):
    sys.modules.pop(_m, None)
