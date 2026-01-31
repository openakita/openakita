"""
定时任务定义

定义任务的数据结构和状态
"""

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """触发器类型"""
    ONCE = "once"           # 一次性（指定时间执行）
    INTERVAL = "interval"   # 间隔（每 N 分钟/小时）
    CRON = "cron"           # Cron 表达式


class TaskType(Enum):
    """任务类型"""
    REMINDER = "reminder"   # 简单提醒（到时间直接发送消息，不需要 LLM 处理）
    TASK = "task"           # 复杂任务（需要 LLM 执行，会发送开始/结束通知）


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"       # 等待首次执行
    SCHEDULED = "scheduled"   # 已调度（等待触发）
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 已完成（一次性任务）
    FAILED = "failed"         # 失败
    DISABLED = "disabled"     # 已禁用
    CANCELLED = "cancelled"   # 已取消


@dataclass
class TaskExecution:
    """任务执行记录"""
    id: str
    task_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = "running"  # running/success/failed/timeout
    result: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    
    @classmethod
    def create(cls, task_id: str) -> "TaskExecution":
        return cls(
            id=f"exec_{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            started_at=datetime.now(),
        )
    
    def finish(self, success: bool, result: str = None, error: str = None) -> None:
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
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            finished_at=datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None,
            status=data.get("status", "running"),
            result=data.get("result"),
            error=data.get("error"),
            duration_seconds=data.get("duration_seconds"),
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
    description: str               # LLM 理解的任务描述
    
    # 触发配置
    trigger_type: TriggerType
    trigger_config: dict           # 触发器配置
    
    # 任务类型配置
    task_type: TaskType = TaskType.TASK  # 任务类型: reminder/task
    reminder_message: Optional[str] = None  # 简单提醒的消息内容（仅 REMINDER 类型使用）
    
    # 执行内容
    prompt: str = ""               # 发送给 Agent 的 prompt（仅 TASK 类型使用）
    script_path: Optional[str] = None  # 预置脚本路径
    
    # 通知配置
    channel_id: Optional[str] = None   # 结果发送的通道
    chat_id: Optional[str] = None      # 结果发送的聊天 ID
    user_id: Optional[str] = None      # 创建者
    
    # 状态
    enabled: bool = True
    status: TaskStatus = TaskStatus.PENDING
    
    # 执行记录
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
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
        reminder_message: Optional[str] = None,
        user_id: Optional[str] = None,
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
    
    def enable(self) -> None:
        """启用任务"""
        self.enabled = True
        self.status = TaskStatus.SCHEDULED
        self.updated_at = datetime.now()
    
    def disable(self) -> None:
        """禁用任务"""
        self.enabled = False
        self.status = TaskStatus.DISABLED
        self.updated_at = datetime.now()
    
    def cancel(self) -> None:
        """取消任务"""
        self.enabled = False
        self.status = TaskStatus.CANCELLED
        self.updated_at = datetime.now()
    
    def mark_running(self) -> None:
        """标记为执行中"""
        self.status = TaskStatus.RUNNING
        self.updated_at = datetime.now()
    
    def mark_completed(self, next_run: Optional[datetime] = None) -> None:
        """标记执行完成"""
        self.last_run = datetime.now()
        self.run_count += 1
        self.updated_at = datetime.now()
        
        if self.trigger_type == TriggerType.ONCE:
            self.status = TaskStatus.COMPLETED
            self.enabled = False
        else:
            self.status = TaskStatus.SCHEDULED
            self.next_run = next_run
    
    def mark_failed(self, error: str = None) -> None:
        """标记执行失败"""
        self.last_run = datetime.now()
        self.fail_count += 1
        self.updated_at = datetime.now()
        
        # 失败后仍然保持调度（除非连续失败太多次）
        if self.fail_count >= 5:
            self.status = TaskStatus.FAILED
            self.enabled = False
            logger.warning(f"Task {self.id} disabled after {self.fail_count} failures")
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
            "channel_id": self.channel_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "enabled": self.enabled,
            "status": self.status.value,
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
        """反序列化"""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            trigger_type=TriggerType(data["trigger_type"]),
            trigger_config=data["trigger_config"],
            task_type=TaskType(data.get("task_type", "task")),
            reminder_message=data.get("reminder_message"),
            prompt=data.get("prompt", ""),
            script_path=data.get("script_path"),
            channel_id=data.get("channel_id"),
            chat_id=data.get("chat_id"),
            user_id=data.get("user_id"),
            enabled=data.get("enabled", True),
            status=TaskStatus(data.get("status", "pending")),
            last_run=datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None,
            next_run=datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None,
            run_count=data.get("run_count", 0),
            fail_count=data.get("fail_count", 0),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            metadata=data.get("metadata", {}),
        )
    
    def __str__(self) -> str:
        return f"Task({self.id}: {self.name}, {self.trigger_type.value}, {self.status.value})"
