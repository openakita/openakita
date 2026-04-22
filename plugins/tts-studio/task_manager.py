"""tts-studio — task manager."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


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
