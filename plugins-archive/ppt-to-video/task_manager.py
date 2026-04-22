"""ppt-to-video — task manager subclass.

Mirrors :class:`MattingTaskManager` (``plugins/video-bg-remove``):
the SDK's :class:`BaseTaskManager` does the heavy lifting (schema
bootstrap, JSON round-trip, WAL, cancel) and we add the columns
specific to a PPT → video job.
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


class PptVideoTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("input_path", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("slide_count", "INTEGER NOT NULL DEFAULT 0"),
            ("notes_total_chars", "INTEGER NOT NULL DEFAULT 0"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_voice": "zh-CN-XiaoxiaoNeural",
            "default_tts_provider": "auto",
            "default_silent_slide_sec": "2.0",
            "default_fps": "25",
            "default_crf": "20",
            "default_libx264_preset": "fast",
            "render_timeout_sec": "1800",
        }


__all__ = ["PptVideoTaskManager"]
