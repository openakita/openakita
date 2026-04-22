"""poster-maker — task manager."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class PosterTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("template_id", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("background_image_path", "TEXT"),
        ]

    def default_config(self):
        return {
            "default_template": "social-square",
            "ai_enhance_default": "off",  # off / on (off = no API call)
        }
