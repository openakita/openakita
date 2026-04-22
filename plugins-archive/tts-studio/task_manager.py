"""tts-studio — task manager."""
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


class StudioTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("script_text", "TEXT NOT NULL DEFAULT ''"),
            ("merged_audio_path", "TEXT"),
            ("segment_count", "INTEGER NOT NULL DEFAULT 0"),
        ]

    def default_config(self):
        return {
            "default_voice": "Cherry",
            "preferred_provider": "auto",
            "dashscope_api_key": "",
            "openai_api_key": "",
            "ffmpeg_path": "ffmpeg",
        }
