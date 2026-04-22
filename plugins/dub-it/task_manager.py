"""dub-it — task manager."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class DubItTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("source_video", "TEXT NOT NULL DEFAULT ''"),
            ("target_language", "TEXT NOT NULL DEFAULT ''"),
            ("output_video", "TEXT NOT NULL DEFAULT ''"),
            ("segment_count", "INTEGER NOT NULL DEFAULT 0"),
            ("bytes_output", "INTEGER NOT NULL DEFAULT 0"),
            ("review_passed", "INTEGER NOT NULL DEFAULT 0"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("segments_json", "TEXT NOT NULL DEFAULT '[]'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_target_language": "zh-CN",
            "default_output_format": "mp4",
            "default_duck_db": "-18",
            "default_keep_original_audio": "true",
            "default_ffmpeg_timeout_sec": "1800.0",
            "default_ffprobe_timeout_sec": "15.0",
        }


__all__ = ["DubItTaskManager"]
