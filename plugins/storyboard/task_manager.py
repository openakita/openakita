"""storyboard — task manager subclass."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class StoryboardTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("script_text", "TEXT NOT NULL DEFAULT ''"),
            ("storyboard_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("self_check_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self):
        return {
            "default_duration_sec": "30",
            "default_style": "短视频 / vlog",
        }
