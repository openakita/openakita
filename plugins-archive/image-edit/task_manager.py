"""image-edit — task manager subclass."""
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


class ImageEditTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("source_image_path", "TEXT"),
            ("mask_image_path", "TEXT"),
            ("output_paths_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("provider", "TEXT"),
        ]

    def default_config(self):
        return {
            "preferred_provider": "auto",       # auto | openai | dashscope | stub
            "default_size": "1024x1024",
            "default_n": "1",
            "auto_open_after_done": "false",
        }
