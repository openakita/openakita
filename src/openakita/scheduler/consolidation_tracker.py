"""
整理时间追踪器

记录每次记忆整理和系统自检的时间戳，供下次运行时
确定需要处理的时间范围（上次整理到当前时间）。

同时追踪安装时间，判断是否处于新用户适应期。
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.atomic_io import safe_json_write

logger = logging.getLogger(__name__)


class ConsolidationTracker:
    """
    整理时间追踪器

    持久化到 data/scheduler/consolidation_tracker.json
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tracker_file = self.data_dir / "consolidation_tracker.json"
        self._state = self._load()

    def _load(self) -> dict:
        if self.tracker_file.exists():
            try:
                with open(self.tracker_file, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning(
                        f"Consolidation tracker file contains {type(data).__name__}, "
                        f"expected dict. Using empty state."
                    )
                    return {}
                return data
            except Exception as e:
                logger.error(f"Failed to load consolidation tracker: {e}")
        return {}

    def _save(self) -> None:
        try:
            safe_json_write(self.tracker_file, self._state)
        except Exception as e:
            logger.error(f"Failed to save consolidation tracker: {e}")

    @property
    def install_time(self) -> datetime:
        """首次安装/使用时间"""
        ts = self._state.get("install_time")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        now = datetime.now()
        self._state["install_time"] = now.isoformat()
        self._save()
        return now

    def is_onboarding(self, onboarding_days: int = 7) -> bool:
        """是否处于新用户适应期"""
        elapsed = datetime.now() - self.install_time
        return elapsed < timedelta(days=onboarding_days)

    def get_onboarding_elapsed_days(self) -> float:
        """距离安装已经过了多少天"""
        elapsed = datetime.now() - self.install_time
        return elapsed.total_seconds() / 86400

    # ==================== 记忆整理 ====================

    @property
    def last_memory_consolidation(self) -> datetime | None:
        """上次记忆整理时间"""
        ts = self._state.get("last_memory_consolidation")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        return None

    def record_memory_consolidation(self, result: dict | None = None) -> None:
        """记录一次记忆整理"""
        now = datetime.now()
        self._state["last_memory_consolidation"] = now.isoformat()

        history = self._state.setdefault("memory_consolidation_history", [])
        entry = {"timestamp": now.isoformat()}
        if result:
            entry["summary"] = {
                k: result.get(k, 0)
                for k in ["unextracted_processed", "duplicates_removed",
                           "memories_decayed", "sessions_processed",
                           "memories_extracted", "memories_added"]
            }
        history.append(entry)

        if len(history) > 100:
            self._state["memory_consolidation_history"] = history[-100:]

        self._save()
        logger.info(f"Recorded memory consolidation at {now.isoformat()}")

    def get_memory_consolidation_time_range(self) -> tuple[datetime | None, datetime]:
        """
        获取本次记忆整理应处理的时间范围

        Returns:
            (since, until) — since=None 表示首次运行，处理全部
        """
        return self.last_memory_consolidation, datetime.now()

    # ==================== 系统自检 ====================

    @property
    def last_selfcheck(self) -> datetime | None:
        """上次系统自检时间"""
        ts = self._state.get("last_selfcheck")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        return None

    def record_selfcheck(self, result: dict | None = None) -> None:
        """记录一次系统自检"""
        now = datetime.now()
        self._state["last_selfcheck"] = now.isoformat()

        history = self._state.setdefault("selfcheck_history", [])
        entry = {"timestamp": now.isoformat()}
        if result:
            entry["summary"] = {
                "total_errors": result.get("total_errors", 0),
                "fix_success": result.get("fix_success", 0),
            }
        history.append(entry)

        if len(history) > 100:
            self._state["selfcheck_history"] = history[-100:]

        self._save()
        logger.info(f"Recorded selfcheck at {now.isoformat()}")

    def get_selfcheck_time_range(self) -> tuple[datetime | None, datetime]:
        """
        获取本次自检应分析的日志时间范围

        Returns:
            (since, until) — since=None 表示首次运行
        """
        return self.last_selfcheck, datetime.now()

    # ==================== 适应期整理间隔判断 ====================

    def should_consolidate_now(
        self,
        onboarding_days: int = 7,
        onboarding_interval_hours: int = 3,
    ) -> bool:
        """
        判断现在是否应该执行记忆整理

        适应期内: 每 onboarding_interval_hours 小时一次
        正常期: 由 cron 控制（此方法不做判断，返回 True）
        """
        if not self.is_onboarding(onboarding_days):
            return True

        last = self.last_memory_consolidation
        if last is None:
            return True

        elapsed = datetime.now() - last
        return elapsed >= timedelta(hours=onboarding_interval_hours)
