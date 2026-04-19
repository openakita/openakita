"""
Consolidation Time Tracker

Records the timestamp of each memory consolidation and system self-check,
so that the next run can determine the time range to process
(from the last consolidation to the current time).

Also tracks installation time to determine whether the user is in the
new-user onboarding period.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.atomic_io import safe_json_write

logger = logging.getLogger(__name__)


class ConsolidationTracker:
    """
    Consolidation Time Tracker

    Persisted to data/scheduler/consolidation_tracker.json
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
        """First installation / usage time"""
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
        """Whether the user is in the new-user onboarding period"""
        elapsed = datetime.now() - self.install_time
        return elapsed < timedelta(days=onboarding_days)

    def get_onboarding_elapsed_days(self) -> float:
        """Number of days elapsed since installation"""
        elapsed = datetime.now() - self.install_time
        return elapsed.total_seconds() / 86400

    # ==================== Memory Consolidation ====================

    @property
    def last_memory_consolidation(self) -> datetime | None:
        """Time of the last memory consolidation"""
        ts = self._state.get("last_memory_consolidation")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        return None

    def record_memory_consolidation(self, result: dict | None = None) -> None:
        """Record a memory consolidation run"""
        now = datetime.now()
        self._state["last_memory_consolidation"] = now.isoformat()

        history = self._state.setdefault("memory_consolidation_history", [])
        entry = {"timestamp": now.isoformat()}
        if result:
            entry["summary"] = {
                k: result.get(k, 0)
                for k in [
                    "unextracted_processed",
                    "duplicates_removed",
                    "memories_decayed",
                    "sessions_processed",
                    "memories_extracted",
                    "memories_added",
                ]
            }
        history.append(entry)

        if len(history) > 100:
            self._state["memory_consolidation_history"] = history[-100:]

        self._save()
        logger.info(f"Recorded memory consolidation at {now.isoformat()}")

    def get_memory_consolidation_time_range(self) -> tuple[datetime | None, datetime]:
        """
        Get the time range this memory consolidation run should process.

        Returns:
            (since, until) — since=None means first run, process everything
        """
        return self.last_memory_consolidation, datetime.now()

    # ==================== System Self-Check ====================

    @property
    def last_selfcheck(self) -> datetime | None:
        """Time of the last system self-check"""
        ts = self._state.get("last_selfcheck")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        return None

    def record_selfcheck(self, result: dict | None = None) -> None:
        """Record a system self-check run"""
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
        Get the log time range this self-check should analyze.

        Returns:
            (since, until) — since=None means first run
        """
        return self.last_selfcheck, datetime.now()

    # ==================== Onboarding Consolidation Interval ====================

    def should_consolidate_now(
        self,
        onboarding_days: int = 7,
        onboarding_interval_hours: int = 3,
    ) -> bool:
        """
        Determine whether memory consolidation should run now.

        During onboarding: once every onboarding_interval_hours hours.
        After onboarding: controlled by cron (this method always returns True).
        """
        if not self.is_onboarding(onboarding_days):
            return True

        last = self.last_memory_consolidation
        if last is None:
            return True

        elapsed = datetime.now() - last
        return elapsed >= timedelta(hours=onboarding_interval_hours)
