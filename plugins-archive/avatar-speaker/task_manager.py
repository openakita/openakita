"""avatar-speaker — task manager subclass."""
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


class AvatarSpeakerTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("text_input", "TEXT NOT NULL DEFAULT ''"),
            ("audio_path", "TEXT"),
            ("avatar_video_path", "TEXT"),
            ("voice", "TEXT"),
            ("provider", "TEXT"),
        ]

    def default_config(self):
        return {
            "preferred_provider": "auto",
            "default_voice": "Cherry",
            "default_rate": "+0%",
            "default_pitch": "+0Hz",
            "avatar_provider": "none",
            # API keys; sourced via _tm.get_config and surfaced through
            # /settings. Empty default lets bootstrap fall back to env.
            "dashscope_api_key": "",
            "openai_api_key": "",
        }
