"""
Scheduled task module

Provides scheduled task management:
- ScheduledTask: Task definitions
- TaskScheduler: Scheduler
- Supports three trigger types: once/interval/cron
- ConsolidationTracker: Consolidation time tracking
"""

from .consolidation_tracker import ConsolidationTracker
from .executor import TaskExecutor
from .scheduler import TaskScheduler
from .task import ScheduledTask, TaskStatus, TriggerType
from .triggers import CronTrigger, IntervalTrigger, OnceTrigger, Trigger

__all__ = [
    "ScheduledTask",
    "TriggerType",
    "TaskStatus",
    "Trigger",
    "OnceTrigger",
    "IntervalTrigger",
    "CronTrigger",
    "TaskScheduler",
    "TaskExecutor",
    "ConsolidationTracker",
    "get_active_scheduler",
    "set_active_scheduler",
]

# Global scheduler singleton — set by the master Agent, consumed by pool agents
_active_scheduler: TaskScheduler | None = None
_active_executor: TaskExecutor | None = None


def set_active_scheduler(
    scheduler: TaskScheduler | None,
    executor: TaskExecutor | None = None,
) -> None:
    global _active_scheduler, _active_executor
    _active_scheduler = scheduler
    _active_executor = executor


def get_active_scheduler() -> TaskScheduler | None:
    return _active_scheduler


def get_active_executor() -> TaskExecutor | None:
    return _active_executor
