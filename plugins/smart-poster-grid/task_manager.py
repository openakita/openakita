"""smart-poster-grid — task manager subclass.

Mirrors :class:`GradeTaskManager` (``plugins/video-color-grade``) and
:class:`PosterTaskManager` (``plugins/poster-maker``): the SDK's
:class:`BaseTaskManager` does the heavy lifting (schema bootstrap,
JSON round-trip, WAL, cancel) and we just add the columns specific to
a multi-ratio poster job.
"""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class GridTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("output_dir", "TEXT"),
            ("background_image_path", "TEXT"),
            ("ratio_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("renders_json", "TEXT NOT NULL DEFAULT '[]'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_ratios_csv": "1x1,3x4,9x16,16x9",
            "render_timeout_sec": "600",   # 10 min ceiling for the whole grid
        }


__all__ = ["GridTaskManager"]
