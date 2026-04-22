"""video-translator — task manager."""
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


class TranslatorTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("source_video_path", "TEXT NOT NULL DEFAULT ''"),
            ("output_video_path", "TEXT"),
            ("srt_path", "TEXT"),
            ("translated_srt_path", "TEXT"),
            ("dubbed_audio_path", "TEXT"),
            ("target_language", "TEXT NOT NULL DEFAULT 'en'"),
        ]

    def default_config(self):
        return {
            "default_target_language": "en",
            "default_voice": "en-US-AriaNeural",
            "burn_subtitles_default": "false",
            "keep_original_audio_volume": "0.15",
            "asr_provider": "auto",
            "asr_region": "cn",
            "asr_model": "base",
            "asr_language": "auto",
            "asr_binary": "whisper-cli",
            "tts_provider": "auto",
            "dashscope_api_key": "",
            "openai_api_key": "",
            "ffmpeg_path": "ffmpeg",
        }
