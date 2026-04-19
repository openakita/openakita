"""
Scheduled Task Definition

Defines task data structures and states
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """Trigger type"""

    ONCE = "once"  # One-time (execute at specified time)
    INTERVAL = "interval"  # Interval (every N minutes/hours)
    CRON = "cron"  # Cron expression


class TaskType(Enum):
    """Task type"""

    REMINDER = "reminder"  # Simple reminder (send message at time, no LLM processing)
    TASK = "task"  # Complex task (requires LLM execution, sends start/end notifications)


class TaskSource(Enum):
    """Task source, to distinguish between chat-generated, plugin-generated, and system-built tasks."""

    MANUAL = "manual"
    CHAT = "chat"
    PLUGIN = "plugin"
    SYSTEM = "system"
    IMPORT = "import"


class TaskDurability(Enum):
    """Task persistence level. Current scheduler defaults to persistent tasks."""

    PERSISTENT = "persistent"
    SESSION = "session"


class TaskStatus(Enum):
    """Task status"""

    PENDING = "pending"  # Waiting for first execution
    SCHEDULED = "scheduled"  # Scheduled (waiting for trigger)
    RUNNING = "running"  # Running
    COMPLETED = "completed"  # Completed (one-time task)
    FAILED = "failed"  # Failed
    DISABLED = "disabled"  # Disabled
    CANCELLED = "cancelled"  # Cancelled
    MISSED = "missed"  # Missed (one-time task expired during program downtime)


@dataclass
class TaskExecution:
    """Task execution record"""

    id: str
    task_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"  # running/success/failed/timeout
    result: str | None = None
    error: str | None = None
    duration_seconds: float | None = None

    @classmethod
    def create(cls, task_id: str) -> "TaskExecution":
        return cls(
            id=f"exec_{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            started_at=datetime.now(),
        )

    def finish(self, success: bool, result: str = None, error: str = None) -> None:
        if self.finished_at is not None:
            logger.warning(
                f"TaskExecution {self.id}: finish() called again (already {self.status}), ignoring"
            )
            return
        self.finished_at = datetime.now()
        self.status = "success" if success else "failed"
        self.result = result
        self.error = error
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskExecution":
        exec_id = data.get("id")
        task_id = data.get("task_id")
        started_at_str = data.get("started_at")

        if not exec_id or not task_id or not started_at_str:
            raise ValueError(
                f"TaskExecution missing required fields: "
                f"id={exec_id!r}, task_id={task_id!r}, started_at={started_at_str!r}"
            )

        duration = data.get("duration_seconds")
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                duration = None

        return cls(
            id=exec_id,
            task_id=task_id,
            started_at=datetime.fromisoformat(started_at_str),
            finished_at=datetime.fromisoformat(data["finished_at"])
            if data.get("finished_at")
            else None,
            status=data.get("status", "running"),
            result=data.get("result"),
            error=data.get("error"),
            duration_seconds=duration,
        )


@dataclass
class ScheduledTask:
    """
    Scheduled Task

    Represents a schedulable task

    Task type (task_type):
    - REMINDER: simple reminder, send reminder_message directly at time
    - TASK: complex task, requires LLM to execute prompt, sends start/end notifications
    """

    id: str
    name: str
    description: str  # Task description understood by LLM

    # Trigger configuration
    trigger_type: TriggerType
    trigger_config: dict  # Trigger configuration

    # Task type configuration
    task_type: TaskType = TaskType.TASK  # Task type: reminder/task
    reminder_message: str | None = None  # Message content for simple reminder (REMINDER type only)

    # Execution content
    prompt: str = ""  # Prompt sent to Agent (TASK type only)
    script_path: str | None = None  # Preset script path
    action: str | None = None  # System action identifier (e.g., system:daily_memory)

    # Notification configuration
    channel_id: str | None = None  # Channel for sending results
    chat_id: str | None = None  # Chat ID for sending results
    user_id: str | None = None  # Creator

    # Multi-agent configuration (always "default" in single-agent mode, no functional impact)
    agent_profile_id: str = "default"

    # Domain boundaries
    task_source: TaskSource = TaskSource.MANUAL
    durability: TaskDurability = TaskDurability.PERSISTENT

    # Status
    enabled: bool = True
    status: TaskStatus = TaskStatus.PENDING
    deletable: bool = True  # Whether deletion is allowed (system tasks set to False)

    # Execution record
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0
    fail_count: int = 0

    # Timestamp
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Cron enhancement configuration
    silent: bool = False  # [SILENT] Suppress: execute but do not send result notifications
    no_schedule_tools: bool = False  # Prevent recursion: disallow creating scheduled tasks within the task
    skill_ids: list[str] = field(default_factory=list)  # Skill binding: load only specified skills

    # Metadata
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        trigger_type: TriggerType,
        trigger_config: dict,
        prompt: str,
        task_type: TaskType = TaskType.TASK,
        reminder_message: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ) -> "ScheduledTask":
        """Create a new task"""
        return cls(
            id=f"task_{uuid.uuid4().hex[:12]}",
            name=name,
            description=description,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            task_type=task_type,
            reminder_message=reminder_message,
            prompt=prompt,
            user_id=user_id,
            **kwargs,
        )

    @classmethod
    def create_reminder(
        cls,
        name: str,
        description: str,
        run_at: datetime,
        message: str,
        **kwargs,
    ) -> "ScheduledTask":
        """
        Create a simple reminder task

        Args:
            name: Reminder name
            description: Reminder description
            run_at: Reminder time
            message: Reminder message to send
        """
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": run_at.isoformat()},
            prompt="",  # Simple reminder does not need prompt
            task_type=TaskType.REMINDER,
            reminder_message=message,
            **kwargs,
        )

    @classmethod
    def create_once(
        cls,
        name: str,
        description: str,
        run_at: datetime,
        prompt: str,
        **kwargs,
    ) -> "ScheduledTask":
        """Create a one-time task"""
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": run_at.isoformat()},
            prompt=prompt,
            **kwargs,
        )

    @classmethod
    def create_interval(
        cls,
        name: str,
        description: str,
        interval_minutes: int,
        prompt: str,
        **kwargs,
    ) -> "ScheduledTask":
        """Create an interval task"""
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"interval_minutes": interval_minutes},
            prompt=prompt,
            **kwargs,
        )

    @classmethod
    def create_cron(
        cls,
        name: str,
        description: str,
        cron_expression: str,
        prompt: str,
        **kwargs,
    ) -> "ScheduledTask":
        """Create a Cron task"""
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": cron_expression},
            prompt=prompt,
            **kwargs,
        )

    # Valid state transition table: current status → set of allowed target statuses
    _VALID_TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.PENDING: {
            TaskStatus.SCHEDULED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.DISABLED,
        },
        TaskStatus.SCHEDULED: {
            TaskStatus.RUNNING,
            TaskStatus.DISABLED,
            TaskStatus.CANCELLED,
            TaskStatus.COMPLETED,
            TaskStatus.MISSED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.SCHEDULED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
        TaskStatus.FAILED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
        TaskStatus.DISABLED: {TaskStatus.SCHEDULED, TaskStatus.CANCELLED},
        TaskStatus.CANCELLED: {TaskStatus.SCHEDULED},
        TaskStatus.MISSED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
    }

    def _check_transition(self, target: TaskStatus) -> bool:
        """Check if state transition is valid. Log warning and return False if invalid."""
        allowed = self._VALID_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            logger.warning(
                f"Task {self.id}: invalid state transition {self.status.value} → {target.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )
            return False
        return True

    def enable(self) -> None:
        """Enable task"""
        if self.status == TaskStatus.COMPLETED and self.trigger_type == TriggerType.ONCE:
            logger.warning(
                f"Task {self.id}: cannot re-enable completed one-time task "
                f"(run_at has already passed)"
            )
            return
        if self.status == TaskStatus.SCHEDULED and self.enabled:
            return
        if self.status == TaskStatus.SCHEDULED and not self.enabled:
            self.enabled = True
            self.updated_at = datetime.now()
            return
        if not self._check_transition(TaskStatus.SCHEDULED):
            return
        self.enabled = True
        self.status = TaskStatus.SCHEDULED
        self.updated_at = datetime.now()

    def disable(self) -> None:
        """Disable task"""
        if not self._check_transition(TaskStatus.DISABLED):
            return
        self.enabled = False
        self.status = TaskStatus.DISABLED
        self.updated_at = datetime.now()

    def cancel(self) -> None:
        """Cancel task"""
        if not self._check_transition(TaskStatus.CANCELLED):
            return
        self.enabled = False
        self.status = TaskStatus.CANCELLED
        self.updated_at = datetime.now()

    def force_reset_to_scheduled(self, reason: str = "") -> None:
        """Force-reset from RUNNING to SCHEDULED (for shutdown/recovery).

        Uses the state machine when possible, falls back to direct assignment
        only if the transition is blocked, and always logs the audit trail.
        """
        if self.status == TaskStatus.RUNNING:
            if not self._check_transition(TaskStatus.SCHEDULED):
                logger.warning(
                    f"Task {self.id}: force_reset bypassing state machine "
                    f"({self.status.value} → scheduled), reason={reason}"
                )
            self.status = TaskStatus.SCHEDULED
            self.updated_at = datetime.now()
            logger.info(f"Task {self.id}: force-reset to SCHEDULED, reason={reason}")
        else:
            logger.debug(
                f"Task {self.id}: force_reset_to_scheduled called in {self.status.value}, no-op"
            )

    def mark_running(self) -> None:
        """Mark as running"""
        if not self._check_transition(TaskStatus.RUNNING):
            return
        self.status = TaskStatus.RUNNING
        self.updated_at = datetime.now()

    def mark_completed(self, next_run: datetime | None = None) -> None:
        """Mark execution as completed"""
        if self.status != TaskStatus.RUNNING:
            logger.warning(
                f"Task {self.id}: mark_completed called from {self.status.value}, expected RUNNING"
            )
            return

        self.last_run = datetime.now()
        self.run_count += 1
        self.fail_count = 0
        self.updated_at = datetime.now()

        if self.trigger_type == TriggerType.ONCE:
            self.status = TaskStatus.COMPLETED
            self.enabled = False
        else:
            self.status = TaskStatus.SCHEDULED
            self.next_run = next_run

    def mark_failed(self, error: str = None) -> None:
        """Mark execution as failed"""
        if self.status != TaskStatus.RUNNING:
            logger.warning(
                f"Task {self.id}: mark_failed called from {self.status.value}, expected RUNNING"
            )
            return

        self.last_run = datetime.now()
        self.fail_count += 1
        self.updated_at = datetime.now()
        if error:
            if not self.metadata:
                self.metadata = {}
            self.metadata["last_error"] = error

        if self.fail_count >= 5:
            self.status = TaskStatus.FAILED
            self.enabled = False
            logger.warning(f"Task {self.id} disabled after {self.fail_count} consecutive failures")
        else:
            self.status = TaskStatus.SCHEDULED

    @property
    def is_active(self) -> bool:
        """Whether task is active (can be scheduled)"""
        return self.enabled and self.status in (TaskStatus.PENDING, TaskStatus.SCHEDULED)

    @property
    def is_one_time(self) -> bool:
        """Whether task is one-time"""
        return self.trigger_type == TriggerType.ONCE

    @property
    def is_reminder(self) -> bool:
        """Whether task is a simple reminder"""
        return self.task_type == TaskType.REMINDER

    def to_dict(self) -> dict:
        """Serialize"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trigger_type": self.trigger_type.value,
            "trigger_config": self.trigger_config,
            "task_type": self.task_type.value,
            "reminder_message": self.reminder_message,
            "prompt": self.prompt,
            "script_path": self.script_path,
            "action": self.action,
            "channel_id": self.channel_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "agent_profile_id": self.agent_profile_id,
            "task_source": self.task_source.value,
            "durability": self.durability.value,
            "enabled": self.enabled,
            "status": self.status.value,
            "deletable": self.deletable,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "silent": self.silent,
            "no_schedule_tools": self.no_schedule_tools,
            "skill_ids": self.skill_ids,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        """Deserialize (with fault tolerance for corrupted/incomplete data)"""
        task_id = data.get("id")
        name = data.get("name")
        trigger_type_str = data.get("trigger_type")

        if not task_id or not name or not trigger_type_str:
            raise ValueError(
                f"ScheduledTask missing required fields: "
                f"id={task_id!r}, name={name!r}, trigger_type={trigger_type_str!r}"
            )

        trigger_config = data.get("trigger_config", {})
        if not isinstance(trigger_config, dict):
            trigger_config = {}

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        def _safe_int(val, default=0):
            try:
                return int(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        now_iso = datetime.now().isoformat()

        try:
            trigger_type = TriggerType(trigger_type_str)
        except ValueError:
            raise ValueError(f"Unknown trigger_type: {trigger_type_str!r}")

        try:
            task_type = TaskType(data.get("task_type", "task"))
        except ValueError:
            task_type = TaskType.TASK

        try:
            task_source = TaskSource(data.get("task_source", "manual"))
        except ValueError:
            task_source = TaskSource.MANUAL

        try:
            durability = TaskDurability(data.get("durability", "persistent"))
        except ValueError:
            durability = TaskDurability.PERSISTENT

        try:
            status = TaskStatus(data.get("status", "pending"))
        except ValueError:
            status = TaskStatus.PENDING

        def _parse_dt(val: str | None, fallback: str | None = None) -> datetime | None:
            if not val:
                return datetime.fromisoformat(fallback) if fallback else None
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                return datetime.fromisoformat(fallback) if fallback else None

        return cls(
            id=task_id,
            name=name,
            description=data.get("description", ""),
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            task_type=task_type,
            reminder_message=data.get("reminder_message"),
            prompt=data.get("prompt", ""),
            script_path=data.get("script_path"),
            action=data.get("action"),
            channel_id=data.get("channel_id"),
            chat_id=data.get("chat_id"),
            user_id=data.get("user_id"),
            agent_profile_id=data.get("agent_profile_id", "default"),
            task_source=task_source,
            durability=durability,
            enabled=data.get("enabled", True),
            status=status,
            deletable=data.get("deletable", True),
            last_run=_parse_dt(data.get("last_run")),
            next_run=_parse_dt(data.get("next_run")),
            run_count=_safe_int(data.get("run_count"), 0),
            fail_count=_safe_int(data.get("fail_count"), 0),
            created_at=_parse_dt(data.get("created_at"), now_iso),
            updated_at=_parse_dt(data.get("updated_at"), now_iso),
            silent=bool(data.get("silent", False)),
            no_schedule_tools=bool(data.get("no_schedule_tools", False)),
            skill_ids=data.get("skill_ids") or [],
            metadata=metadata,
        )

    def __str__(self) -> str:
        return f"Task({self.id}: {self.name}, {self.trigger_type.value}, {self.status.value})"
