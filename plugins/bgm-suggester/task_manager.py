"""bgm-suggester — task manager subclass.

Adds plugin-specific columns on top of the SDK's ``BaseTaskManager`` so
the standard CRUD / cancel / list helpers all just work.

The two extra ``_json`` columns (brief / self_check) are duplicated
into the result dict at runtime (see ``plugin.py::_run``) so consumers
that read either via ``GET /tasks/{id}`` or via the raw row can pull
the same data — no schema split.
"""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class BgmTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("scene_text", "TEXT NOT NULL DEFAULT ''"),
            ("mood_text", "TEXT NOT NULL DEFAULT ''"),
            ("brief_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("self_check_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self):
        return {
            "default_duration_sec": "30",
            "default_language": "auto",
            "default_tempo_hint": "",
        }
