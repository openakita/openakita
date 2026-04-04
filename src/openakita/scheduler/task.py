"""
定时任务定义

定义任务的数据结构和状态
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """触发器类型"""

    ONCE = "once"  # 一次性（指定时间执行）
    INTERVAL = "interval"  # 间隔（每 N 分钟/小时）
    CRON = "cron"  # Cron 表达式


class TaskType(Enum):
    """任务类型"""

    REMINDER = "reminder"  # 简单提醒（到时间直接发送消息，不需要 LLM 处理）
    TASK = "task"  # 复杂任务（需要 LLM 执行，会发送开始/结束通知）


class TaskSource(Enum):
    """任务来源，用于区分聊天生成、插件生成和系统内置任务。"""

    MANUAL = "manual"
    CHAT = "chat"
    PLUGIN = "plugin"
    SYSTEM = "system"
    IMPORT = "import"


class TaskDurability(Enum):
    """任务持久化级别。当前调度器默认都是持久化任务。"""

    PERSISTENT = "persistent"
    SESSION = "session"


class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"  # 等待首次执行
    SCHEDULED = "scheduled"  # 已调度（等待触发）
    RUNNING = "running"  # 执行中
    COMPLETED = "completed"  # 已完成（一次性任务）
    FAILED = "failed"  # 失败
    DISABLED = "disabled"  # 已禁用
    CANCELLED = "cancelled"  # 已取消
    MISSED = "missed"  # 错过执行（程序停机期间过期的一次性任务）


@dataclass
class TaskExecution:
    """任务执行记录"""

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
                f"TaskExecution {self.id}: finish() called again "
                f"(already {self.status}), ignoring"
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
    定时任务

    表示一个可调度的任务

    任务类型 (task_type):
    - REMINDER: 简单提醒，到时间直接发送 reminder_message
    - TASK: 复杂任务，需要 LLM 执行 prompt，会发送开始/结束通知
    """

    id: str
    name: str
    description: str  # LLM 理解的任务描述

    # 触发配置
    trigger_type: TriggerType
    trigger_config: dict  # 触发器配置

    # 任务类型配置
    task_type: TaskType = TaskType.TASK  # 任务类型: reminder/task
    reminder_message: str | None = None  # 简单提醒的消息内容（仅 REMINDER 类型使用）

    # 执行内容
    prompt: str = ""  # 发送给 Agent 的 prompt（仅 TASK 类型使用）
    script_path: str | None = None  # 预置脚本路径
    action: str | None = None  # 系统动作标识（如 system:daily_memory）

    # 通知配置
    channel_id: str | None = None  # 结果发送的通道
    chat_id: str | None = None  # 结果发送的聊天 ID
    user_id: str | None = None  # 创建者

    # 多 Agent 配置（单 Agent 模式下始终为 "default"，无功能影响）
    agent_profile_id: str = "default"

    # 领域边界
    task_source: TaskSource = TaskSource.MANUAL
    durability: TaskDurability = TaskDurability.PERSISTENT

    # 状态
    enabled: bool = True
    status: TaskStatus = TaskStatus.PENDING
    deletable: bool = True  # 是否允许删除（系统任务设为 False）

    # 执行记录
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0
    fail_count: int = 0

    # 时间戳
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # 元数据
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
        """创建新任务"""
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
        创建简单提醒任务

        Args:
            name: 提醒名称
            description: 提醒描述
            run_at: 提醒时间
            message: 要发送的提醒消息
        """
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": run_at.isoformat()},
            prompt="",  # 简单提醒不需要 prompt
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
        """创建一次性任务"""
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
        """创建间隔任务"""
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
        """创建 Cron 任务"""
        return cls.create(
            name=name,
            description=description,
            trigger_type=TriggerType.CRON,
            trigger_config={"cron": cron_expression},
            prompt=prompt,
            **kwargs,
        )

    # 合法状态转换表：当前状态 → 允许的目标状态集合
    _VALID_TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.PENDING: {
            TaskStatus.SCHEDULED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.DISABLED,
        },
        TaskStatus.SCHEDULED: {
            TaskStatus.RUNNING, TaskStatus.DISABLED, TaskStatus.CANCELLED,
            TaskStatus.COMPLETED, TaskStatus.MISSED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SCHEDULED, TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
        TaskStatus.FAILED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
        TaskStatus.DISABLED: {TaskStatus.SCHEDULED, TaskStatus.CANCELLED},
        TaskStatus.CANCELLED: {TaskStatus.SCHEDULED},
        TaskStatus.MISSED: {TaskStatus.SCHEDULED, TaskStatus.DISABLED, TaskStatus.CANCELLED},
    }

    def _check_transition(self, target: TaskStatus) -> bool:
        """检查状态转换是否合法。不合法时记录警告并返回 False。"""
        allowed = self._VALID_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            logger.warning(
                f"Task {self.id}: invalid state transition {self.status.value} → {target.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )
            return False
        return True

    def enable(self) -> None:
        """启用任务"""
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
        """禁用任务"""
        if not self._check_transition(TaskStatus.DISABLED):
            return
        self.enabled = False
        self.status = TaskStatus.DISABLED
        self.updated_at = datetime.now()

    def cancel(self) -> None:
        """取消任务"""
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
                f"Task {self.id}: force_reset_to_scheduled called "
                f"in {self.status.value}, no-op"
            )

    def mark_running(self) -> None:
        """标记为执行中"""
        if not self._check_transition(TaskStatus.RUNNING):
            return
        self.status = TaskStatus.RUNNING
        self.updated_at = datetime.now()

    def mark_completed(self, next_run: datetime | None = None) -> None:
        """标记执行完成"""
        if self.status != TaskStatus.RUNNING:
            logger.warning(
                f"Task {self.id}: mark_completed called from {self.status.value}, "
                f"expected RUNNING"
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
        """标记执行失败"""
        if self.status != TaskStatus.RUNNING:
            logger.warning(
                f"Task {self.id}: mark_failed called from {self.status.value}, "
                f"expected RUNNING"
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
        """是否活跃（可被调度）"""
        return self.enabled and self.status in (TaskStatus.PENDING, TaskStatus.SCHEDULED)

    @property
    def is_one_time(self) -> bool:
        """是否一次性任务"""
        return self.trigger_type == TriggerType.ONCE

    @property
    def is_reminder(self) -> bool:
        """是否是简单提醒任务"""
        return self.task_type == TaskType.REMINDER

    def to_dict(self) -> dict:
        """序列化"""
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
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        """反序列化（对损坏/残缺数据尽量容错）"""
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
            metadata=metadata,
        )

    def __str__(self) -> str:
        return f"Task({self.id}: {self.name}, {self.trigger_type.value}, {self.status.value})"
