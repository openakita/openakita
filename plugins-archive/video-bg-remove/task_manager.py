"""video-bg-remove — task manager subclass.

Mirrors :class:`GradeTaskManager` (``plugins/video-color-grade``):
the SDK's :class:`BaseTaskManager` does the heavy lifting (schema
bootstrap, JSON round-trip, WAL, cancel) and we add the columns
specific to a matting job.
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


class MattingTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("input_path", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("background_kind", "TEXT NOT NULL DEFAULT 'color'"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_background_kind": "color",     # color | image | transparent
            "default_background_color": "0,177,64", # chroma-key green
            "default_downsample_ratio": "0.25",     # RVM official 1080p default
            "default_crf": "18",
            "default_libx264_preset": "fast",
            "render_timeout_sec": "1800",           # 30 min ceiling
        }


__all__ = ["MattingTaskManager"]
