"""storyboard — task manager subclass."""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

from _shared import BaseTaskManager


class StoryboardTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("script_text", "TEXT NOT NULL DEFAULT ''"),
            ("storyboard_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("self_check_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self):
        return {
            "default_duration_sec": "30",
            "default_style": "短视频 / vlog",
        }
