"""highlight-cutter — task manager (subclasses BaseTaskManager)."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class HighlightTaskManager(BaseTaskManager):
    """Adds source-video / output / segments columns on top of the base."""

    def extra_task_columns(self):
        return [
            ("source_video_path", "TEXT"),
            ("output_video_path", "TEXT"),
            ("segments_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("transcript_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("source_duration_sec", "REAL"),
        ]

    def default_config(self):
        return {
            "asr_provider": "auto",
            "asr_region": "cn",
            "asr_model": "base",
            "asr_language": "auto",
            "asr_binary": "whisper-cli",
            "dashscope_api_key": "",
            "min_segment_sec": "3",
            "max_segment_sec": "20",
            "target_segment_count": "5",
            "ffmpeg_path": "",
            "auto_open_after_done": "false",
        }
