"""shorts-batch — task manager."""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

from _shared import BaseTaskManager


class ShortsBatchTaskManager(BaseTaskManager):
    def extra_task_columns(self) -> list[tuple[str, str]]:
        return [
            ("brief_count", "INTEGER NOT NULL DEFAULT 0"),
            ("succeeded_count", "INTEGER NOT NULL DEFAULT 0"),
            ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
            ("total_cost_usd", "REAL NOT NULL DEFAULT 0.0"),
            ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("plans_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("results_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("risk_distribution_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]

    def default_config(self) -> dict[str, str]:
        return {
            "default_aspect": "9:16",
            "default_duration_sec": "15.0",
            "default_style": "vlog",
            "default_language": "zh-CN",
            "default_min_shots": "3",
            "default_max_shots": "12",
            "default_risk_block_threshold": "",
        }


__all__ = ["ShortsBatchTaskManager"]
