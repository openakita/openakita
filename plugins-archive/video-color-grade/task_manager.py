"""video-color-grade — task manager subclass.

Mirrors :class:`MixerTaskManager` (``plugins/bgm-mixer/task_manager.py``):
the SDK's :class:`BaseTaskManager` does the heavy lifting — schema
bootstrap, JSON round-trip, WAL, cancel — and we add the two columns
specific to a grade job.
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


class GradeTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("input_path", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("mode", "TEXT NOT NULL DEFAULT 'auto'"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_mode": "auto",          # "auto" | "preset:<name>"
            "default_clamp_pct": "0.08",     # SDK constant default
            "default_sample_window_sec": "10",
            "default_sample_frames": "10",
            "default_crf": "18",
            "default_preset": "fast",        # libx264 preset
            "render_timeout_sec": "1800",    # 30 min hard ceiling
        }


__all__ = ["GradeTaskManager"]
