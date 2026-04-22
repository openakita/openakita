"""local-sd-flux — task manager.

Mirrors the other Sprint 11-15 plugins: subclass the SDK's
:class:`BaseTaskManager` and add columns specific to image generation.
"""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class SDFluxTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("preset_id", "TEXT NOT NULL DEFAULT ''"),
            ("prompt_id", "TEXT NOT NULL DEFAULT ''"),
            ("output_dir", "TEXT NOT NULL DEFAULT ''"),
            ("image_count", "INTEGER NOT NULL DEFAULT 0"),
            ("bytes_total", "INTEGER NOT NULL DEFAULT 0"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("image_paths_json", "TEXT NOT NULL DEFAULT '[]'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_preset_id": "sdxl_basic",
            "default_base_url": "http://127.0.0.1:8188",
            "default_output_format": "png",
            "default_poll_interval_sec": "1.0",
            "default_timeout_sec": "300.0",
            "default_auth_token": "",
        }


__all__ = ["SDFluxTaskManager"]
