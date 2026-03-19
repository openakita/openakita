"""BP 数据模型 — 枚举类型与运行时快照。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────────────


class RunMode(Enum):
    MANUAL = "manual"
    AUTO = "auto"


class BPStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SubtaskStatus(Enum):
    PENDING = "pending"
    CURRENT = "current"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"


class TriggerType(Enum):
    COMMAND = "command"
    CONTEXT = "context"
    CRON = "cron"
    EVENT = "event"
    UI_CLICK = "ui_click"


# ── Config dataclasses ─────────────────────────────────────────


@dataclass
class TriggerConfig:
    type: TriggerType
    pattern: str = ""
    conditions: list[str] = field(default_factory=list)
    cron: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = TriggerType(self.type)


@dataclass
class SubtaskConfig:
    id: str
    name: str
    agent_profile: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    input_mapping: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None
    max_retries: int = 0


@dataclass
class BestPracticeConfig:
    id: str
    name: str
    subtasks: list[SubtaskConfig]
    description: str = ""
    triggers: list[TriggerConfig] = field(default_factory=list)
    final_output_schema: dict[str, Any] | None = None
    default_run_mode: RunMode = RunMode.MANUAL

    def __post_init__(self) -> None:
        if isinstance(self.default_run_mode, str):
            self.default_run_mode = RunMode(self.default_run_mode)


# ── Runtime snapshot ───────────────────────────────────────────


@dataclass
class PendingContextSwitch:
    """由 bp_switch_task 创建，由 Agent._pre_reasoning_hook() 消费。"""
    suspended_instance_id: str
    target_instance_id: str
    created_at: float = field(default_factory=time.time)


@dataclass
class BPInstanceSnapshot:
    """单个 BP 实例的完整运行时状态快照。"""
    bp_id: str
    instance_id: str
    session_id: str
    status: BPStatus = BPStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    suspended_at: float | None = None
    current_subtask_index: int = 0
    run_mode: RunMode = RunMode.MANUAL
    subtask_statuses: dict[str, str] = field(default_factory=dict)
    initial_input: dict[str, Any] = field(default_factory=dict)
    subtask_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    context_summary: str = ""
    bp_config: BestPracticeConfig | None = field(default=None, repr=False)

    @staticmethod
    def new_instance_id() -> str:
        return f"bp-{uuid.uuid4().hex[:8]}"

    def serialize(self) -> dict[str, Any]:
        """序列化为可持久化的 dict（排除 bp_config 运行时引用）。"""
        return {
            "bp_id": self.bp_id,
            "instance_id": self.instance_id,
            "session_id": self.session_id,
            "status": self.status.value if isinstance(self.status, BPStatus) else self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "suspended_at": self.suspended_at,
            "current_subtask_index": self.current_subtask_index,
            "run_mode": self.run_mode.value if isinstance(self.run_mode, RunMode) else self.run_mode,
            "subtask_statuses": dict(self.subtask_statuses),
            "initial_input": dict(self.initial_input),
            "subtask_outputs": {k: dict(v) for k, v in self.subtask_outputs.items()},
            "context_summary": self.context_summary,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> BPInstanceSnapshot:
        """从 dict 反序列化（bp_config 需调用方回填）。"""
        return cls(
            bp_id=data["bp_id"],
            instance_id=data["instance_id"],
            session_id=data["session_id"],
            status=BPStatus(data.get("status", "active")),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at"),
            suspended_at=data.get("suspended_at"),
            current_subtask_index=data.get("current_subtask_index", 0),
            run_mode=RunMode(data.get("run_mode", "manual")),
            subtask_statuses=dict(data.get("subtask_statuses", {})),
            initial_input=dict(data.get("initial_input", {})),
            subtask_outputs={k: dict(v) for k, v in data.get("subtask_outputs", {}).items()},
            context_summary=data.get("context_summary", ""),
            bp_config=None,
        )
