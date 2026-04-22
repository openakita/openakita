"""poster-maker — task manager."""
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


class PosterTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("template_id", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("background_image_path", "TEXT"),
        ]

    def default_config(self):
        return {
            "default_template": "social-square",
            "ai_enhance_default": "off",  # off / on (off = no API call)
        }
