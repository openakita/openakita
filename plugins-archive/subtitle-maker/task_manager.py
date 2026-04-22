"""subtitle-maker — task manager subclass."""
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


class SubtitleTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("source_path", "TEXT"),
            ("srt_path", "TEXT"),
            ("vtt_path", "TEXT"),
            ("burned_video_path", "TEXT"),
            ("language", "TEXT"),
        ]

    def default_config(self):
        return {
            "asr_provider": "auto",
            "asr_region": "cn",
            "asr_model": "base",
            "asr_language": "auto",
            "asr_binary": "whisper-cli",
            "dashscope_api_key": "",
            "default_format": "srt",   # srt | vtt | both
            "burn_into_video": "false",
            "ffmpeg_path": "ffmpeg",
        }
