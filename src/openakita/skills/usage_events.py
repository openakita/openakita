"""技能用量事件日志（Skill Usage Event Log）。

记录技能的「加载（load）」和「编辑（edit）」事件，用于监控面板的
「技能用量」统计：趋势图、概览卡片、热门技能榜。

设计取舍：
- 采用追加式 JSONL（每行一个事件），与 ``data/tool_experience.jsonl``
  的既有约定一致，避免 token 统计那种常驻守护线程 + SQLite 的复杂度。
- 写入尽力而为（best-effort），任何异常都吞掉并记日志，绝不影响主流程，
  与 ``skills/usage.py`` 的容错风格保持一致。
- 读取时按时间窗口（天）过滤并聚合，因此支持 7/30/90/365 天的趋势查询。

每行事件结构::

    {"ts": <unix int>, "skill": "<skill-name>", "action": "load" | "edit"}
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 允许记录的事件类型。其它值在聚合时会被忽略。
VALID_ACTIONS = ("load", "edit")


class SkillUsageEventLog:
    """追加式技能用量事件日志，支持按天聚合。"""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, skill: str, action: str) -> None:
        """追加一条技能用量事件（尽力而为，绝不抛出）。

        Args:
            skill: 技能名称。
            action: 事件类型，应为 ``"load"`` 或 ``"edit"``。
        """
        skill_name = str(skill or "").strip()
        if not skill_name:
            return
        if action not in VALID_ACTIONS:
            return
        entry = {"ts": int(time.time()), "skill": skill_name, "action": action}
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 - 监控埋点不得影响主流程
            logger.warning("Failed to record skill usage event: %s", e)

    def _read_events(self, since_ts: int) -> list[dict[str, Any]]:
        """读取 ``ts >= since_ts`` 的事件。容忍坏行 / 缺失文件。"""
        if not self._path.exists():
            return []
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            logger.debug("[SkillUsage] failed to read %s: %s", self._path, exc)
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            ts = obj.get("ts")
            action = obj.get("action")
            skill = obj.get("skill")
            if not isinstance(ts, int | float):
                continue
            if int(ts) < since_ts:
                continue
            if action not in VALID_ACTIONS:
                continue
            if not isinstance(skill, str) or not skill:
                continue
            out.append({"ts": int(ts), "skill": skill, "action": action})
        return out

    def aggregate(self, days: int) -> dict[str, Any]:
        """按时间窗口聚合技能用量，返回前端监控面板所需的结构。

        Args:
            days: 统计窗口（天），调用方应已裁剪到合理范围。

        Returns:
            包含 ``period_days``、``summary``、``by_day``、``top_skills`` 的字典。
        """
        days = max(1, int(days))
        now = int(time.time())
        since_ts = now - days * 86400
        events = self._read_events(since_ts)

        total_loads = 0
        total_edits = 0
        # 每技能聚合：load / edit / 最近使用时间
        per_skill_load: dict[str, int] = defaultdict(int)
        per_skill_edit: dict[str, int] = defaultdict(int)
        per_skill_last: dict[str, int] = {}
        # 每天聚合：day -> {"load": int, "edit": int, skills: {skill: {load, edit}}}
        per_day: dict[str, dict[str, Any]] = {}

        for ev in events:
            ts = ev["ts"]
            skill = ev["skill"]
            action = ev["action"]
            day_key = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")

            day_bucket = per_day.setdefault(
                day_key, {"load": 0, "edit": 0, "skills": defaultdict(lambda: {"load": 0, "edit": 0})}
            )

            if action == "load":
                total_loads += 1
                per_skill_load[skill] += 1
                day_bucket["load"] += 1
                day_bucket["skills"][skill]["load"] += 1
            else:  # edit
                total_edits += 1
                per_skill_edit[skill] += 1
                day_bucket["edit"] += 1
                day_bucket["skills"][skill]["edit"] += 1

            if skill not in per_skill_last or ts > per_skill_last[skill]:
                per_skill_last[skill] = ts

        total_actions = total_loads + total_edits
        distinct_skills = set(per_skill_load) | set(per_skill_edit)

        # by_day：补齐窗口内的所有日期（含 0 值），按日期升序排列
        by_day: list[dict[str, Any]] = []
        start_day = datetime.fromtimestamp(since_ts, tz=UTC).date()
        end_day = datetime.fromtimestamp(now, tz=UTC).date()
        cursor = start_day
        while cursor <= end_day:
            day_key = cursor.strftime("%Y-%m-%d")
            bucket = per_day.get(day_key)
            if bucket:
                skills = [
                    {
                        "skill": sk,
                        "load_count": counts["load"],
                        "edit_count": counts["edit"],
                        "total_count": counts["load"] + counts["edit"],
                    }
                    for sk, counts in bucket["skills"].items()
                ]
                skills.sort(key=lambda r: r["total_count"], reverse=True)
                load_count = bucket["load"]
                edit_count = bucket["edit"]
            else:
                skills = []
                load_count = 0
                edit_count = 0
            by_day.append(
                {
                    "date": day_key,
                    "load_count": load_count,
                    "edit_count": edit_count,
                    "total_count": load_count + edit_count,
                    "skills": skills,
                }
            )
            cursor = cursor.fromordinal(cursor.toordinal() + 1)

        # top_skills：按总操作数降序
        top_skills: list[dict[str, Any]] = []
        for skill in distinct_skills:
            load_count = per_skill_load.get(skill, 0)
            edit_count = per_skill_edit.get(skill, 0)
            skill_total = load_count + edit_count
            percentage = (skill_total / total_actions * 100) if total_actions > 0 else 0.0
            top_skills.append(
                {
                    "skill": skill,
                    "load_count": load_count,
                    "edit_count": edit_count,
                    "total_count": skill_total,
                    "percentage": round(percentage, 1),
                    "last_used_at": per_skill_last.get(skill),
                }
            )
        top_skills.sort(key=lambda r: r["total_count"], reverse=True)

        return {
            "period_days": days,
            "summary": {
                "total_skill_loads": total_loads,
                "total_skill_edits": total_edits,
                "total_skill_actions": total_actions,
                "distinct_skills_used": len(distinct_skills),
            },
            "by_day": by_day,
            "top_skills": top_skills,
        }


_log: SkillUsageEventLog | None = None


def get_skill_usage_log() -> SkillUsageEventLog:
    """返回进程内共享的 :class:`SkillUsageEventLog` 单例。"""
    global _log
    if _log is None:
        from ..config import settings

        _log = SkillUsageEventLog(settings.project_root / "data" / "skill_usage_events.jsonl")
    return _log
