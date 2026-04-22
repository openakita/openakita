"""smart-poster-grid — task manager subclass.

Mirrors :class:`GradeTaskManager` (``plugins/video-color-grade``) and
:class:`PosterTaskManager` (``plugins/poster-maker``): the SDK's
:class:`BaseTaskManager` does the heavy lifting (schema bootstrap,
JSON round-trip, WAL, cancel) and we just add the columns specific to
a multi-ratio poster job.
"""
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


class GridTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("output_dir", "TEXT"),
            ("background_image_path", "TEXT"),
            ("ratio_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("renders_json", "TEXT NOT NULL DEFAULT '[]'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_ratios_csv": "1x1,3x4,9x16,16x9",
            "render_timeout_sec": "600",   # 10 min ceiling for the whole grid
        }


__all__ = ["GridTaskManager"]
