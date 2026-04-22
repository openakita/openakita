"""avatar-speaker — task manager subclass."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


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
