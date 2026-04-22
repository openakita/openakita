"""bgm-mixer — task manager subclass.

Mirrors the storyboard / tts-studio convention (uses the SDK's
:class:`BaseTaskManager`) — keeps the bgm-mixer plugin's footprint
small and consistent with the rest of the plugin suite.

Why this is a different shape than transcribe-archive's
``TranscribeTaskManager``: bgm-mixer jobs have a small fixed result
shape (one mix file path + one verification dict) and do NOT need
the per-chunk progress / chunks_total bookkeeping that justified the
custom manager in transcribe-archive.  Defaulting to BaseTaskManager
inherits the JSON blob round-trip + WAL + cancel_task semantics that
the rest of the suite relies on.

Plugin-specific columns we add via ``extra_task_columns()``:

* ``voice_path``       — the foreground audio the user uploaded
* ``bgm_path``         — the background music file
* ``output_path``      — where the final mix lives once succeeded
* ``verification_json`` — D2.10 envelope rendered at success time so
                          the API can serve it without recomputing
* ``plan_json``         — the full :class:`MixPlan` dict for replay /
                          debugging
"""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class MixerTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("voice_path", "TEXT NOT NULL DEFAULT ''"),
            ("bgm_path", "TEXT NOT NULL DEFAULT ''"),
            ("output_path", "TEXT"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_bpm_hint": "120",
            "default_duck_db": "-10",
            "default_fade_in_sec": "0.3",
            "default_fade_out_sec": "0.5",
            "default_voice_gain_db": "0",
            "default_bgm_gain_db": "-3",
            "default_beat_tracker": "stub",  # stub | madmom
            "snap_tolerance_sec": "0.5",
        }


__all__ = ["MixerTaskManager"]
