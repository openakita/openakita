"""image-edit — task manager subclass."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class ImageEditTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("source_image_path", "TEXT"),
            ("mask_image_path", "TEXT"),
            ("output_paths_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("provider", "TEXT"),
        ]

    def default_config(self):
        return {
            "preferred_provider": "auto",       # auto | openai | dashscope | stub
            "default_size": "1024x1024",
            "default_n": "1",
            "auto_open_after_done": "false",
        }
