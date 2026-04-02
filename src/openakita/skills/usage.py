"""
技能使用追踪

记录技能调用频率，用于:
- Catalog 排序（常用技能排前）
- 使用建议
- 技能健康度统计

采用 7 天半衰期衰减模型 + 60 秒防抖。
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 7.0
HALF_LIFE_SECONDS = HALF_LIFE_DAYS * 86400
DEBOUNCE_SECONDS = 60.0
_DECAY_LAMBDA = math.log(2) / HALF_LIFE_SECONDS


class SkillUsageTracker:
    """Track skill invocations with exponential decay scoring."""

    def __init__(self, storage_path: Path):
        self._path = storage_path
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except Exception as e:
                logger.warning("Failed to load skill usage data: %s", e)

    def _save(self) -> None:
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False
        except Exception as e:
            logger.warning("Failed to save skill usage data: %s", e)

    def record(self, skill_id: str) -> None:
        """Record a skill invocation with 60s debounce."""
        now = time.time()
        entry = self._data.get(skill_id, {})
        last_ts = entry.get("last_ts", 0)

        if now - last_ts < DEBOUNCE_SECONDS:
            return

        entry["last_ts"] = now
        entry["total"] = entry.get("total", 0) + 1

        invocations: list[float] = entry.get("invocations", [])
        invocations.append(now)
        if len(invocations) > 200:
            invocations = invocations[-200:]
        entry["invocations"] = invocations

        self._data[skill_id] = entry
        self._dirty = True
        self._save()

    def get_score(self, skill_id: str) -> float:
        """Calculate decayed usage score for a skill."""
        entry = self._data.get(skill_id)
        if not entry:
            return 0.0
        now = time.time()
        invocations: list[float] = entry.get("invocations", [])
        score = 0.0
        for ts in invocations:
            age = now - ts
            score += math.exp(-_DECAY_LAMBDA * age)
        return score

    def get_all_scores(self) -> dict[str, float]:
        """Return scores for all tracked skills, sorted descending."""
        scores = {sid: self.get_score(sid) for sid in self._data}
        return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))

    def get_stats(self, skill_id: str) -> dict[str, Any]:
        """Return usage statistics for a skill."""
        entry = self._data.get(skill_id, {})
        return {
            "total_invocations": entry.get("total", 0),
            "last_used": entry.get("last_ts", 0),
            "score": self.get_score(skill_id),
        }
