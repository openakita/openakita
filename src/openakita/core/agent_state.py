"""
Agent 状态管理模块

提供结构化的状态管理，替代 agent.py 中分散的实例变量。
包含:
- TaskStatus: 任务执行状态枚举（显式 ReAct 循环）
- TaskState: 单次任务的完整执行状态
- AgentState: Agent 全局状态管理 + 状态机转换验证
"""

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务执行状态（对应 ReAct 循环的各阶段）"""

    IDLE = "idle"  # 空闲，等待新任务
    COMPILING = "compiling"  # Prompt Compiler 阶段
    REASONING = "reasoning"  # LLM 推理决策阶段
    ACTING = "acting"  # 工具执行阶段
    OBSERVING = "observing"  # 观察工具结果阶段
    VERIFYING = "verifying"  # 任务完成度验证阶段
    MODEL_SWITCHING = "model_switching"  # 模型切换中
    WAITING_USER = "waiting_user"  # 等待用户回复（ask_user 工具触发）
    COMPLETED = "completed"  # 任务完成
    FAILED = "failed"  # 任务失败
    CANCELLED = "cancelled"  # 任务被取消


# 合法的状态转换表
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.IDLE: {TaskStatus.COMPILING, TaskStatus.REASONING},
    TaskStatus.COMPILING: {TaskStatus.REASONING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.REASONING: {
        TaskStatus.ACTING,
        TaskStatus.OBSERVING,
        TaskStatus.VERIFYING,
        TaskStatus.COMPLETED,
        TaskStatus.WAITING_USER,
        TaskStatus.CANCELLED,
        TaskStatus.MODEL_SWITCHING,
        TaskStatus.FAILED,
    },
    TaskStatus.ACTING: {
        TaskStatus.OBSERVING,
        TaskStatus.WAITING_USER,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.OBSERVING: {
        TaskStatus.REASONING,
        TaskStatus.VERIFYING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.COMPLETED,
        TaskStatus.REASONING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.MODEL_SWITCHING: {TaskStatus.REASONING, TaskStatus.FAILED},
    TaskStatus.WAITING_USER: {TaskStatus.REASONING, TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: {TaskStatus.IDLE},
    TaskStatus.FAILED: {TaskStatus.IDLE},
    TaskStatus.CANCELLED: {TaskStatus.IDLE},
}


@dataclass
class TaskState:
    """
    单次任务的完整执行状态。

    每次 chat_with_session() 调用创建一个新的 TaskState，
    任务结束后通过 AgentState.reset_task() 清理。
    """

    task_id: str
    session_id: str = ""
    conversation_id: str = ""
    status: TaskStatus = TaskStatus.IDLE

    # 任务定义（来自 Prompt Compiler）
    task_definition: str = ""
    task_query: str = ""

    # 取消机制
    cancelled: bool = False
    cancel_reason: str = ""

    # 模型状态
    current_model: str = ""

    # 推理-行动循环状态
    iteration: int = 0
    consecutive_tool_rounds: int = 0
    tools_executed: list[str] = field(default_factory=list)
    tools_executed_in_task: bool = False
    delivery_receipts: list[dict] = field(default_factory=list)

    # ForceToolCall 控制
    no_tool_call_count: int = 0

    # 任务验证控制
    verify_incomplete_count: int = 0
    no_confirmation_text_count: int = 0

    # 循环检测
    recent_tool_signatures: list[str] = field(default_factory=list)
    tool_pattern_window: int = 8
    llm_self_check_interval: int = 10
    extreme_safety_threshold: int = 50
    last_browser_url: str = ""

    # 原始用户消息（用于模型切换时重置上下文）
    original_user_messages: list[dict] = field(default_factory=list)

    def transition(self, new_status: TaskStatus) -> None:
        """
        执行状态转换，带合法性验证。

        Args:
            new_status: 目标状态

        Raises:
            ValueError: 非法状态转换
        """
        valid_targets = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise ValueError(
                f"非法状态转换: {self.status.value} -> {new_status.value}. "
                f"合法目标: {[s.value for s in valid_targets]}"
            )
        old_status = self.status
        self.status = new_status
        logger.debug(f"[State] {old_status.value} -> {new_status.value} (task={self.task_id[:8]})")

    def cancel(self, reason: str = "用户请求停止") -> None:
        """取消任务"""
        self.cancelled = True
        self.cancel_reason = reason
        # 允许从任何活跃状态取消
        if self.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.IDLE):
            self.status = TaskStatus.CANCELLED
            logger.info(f"[State] Task {self.task_id[:8]} cancelled: {reason}")

    def reset_for_model_switch(self) -> None:
        """模型切换时重置循环相关状态"""
        self.no_tool_call_count = 0
        self.tools_executed_in_task = False
        self.verify_incomplete_count = 0
        self.tools_executed = []
        self.consecutive_tool_rounds = 0
        self.recent_tool_signatures = []
        self.no_confirmation_text_count = 0

    def record_tool_execution(self, tool_names: list[str]) -> None:
        """记录工具执行"""
        if tool_names:
            self.tools_executed_in_task = True
            self.tools_executed.extend(tool_names)

    def record_tool_signature(self, signature: str) -> None:
        """记录工具签名用于循环检测"""
        self.recent_tool_signatures.append(signature)
        if len(self.recent_tool_signatures) > self.tool_pattern_window:
            self.recent_tool_signatures = self.recent_tool_signatures[-self.tool_pattern_window :]

    @property
    def is_active(self) -> bool:
        """任务是否处于活跃状态（包含 WAITING_USER，因为 IM 模式下仍在等待回复）"""
        return self.status not in (
            TaskStatus.IDLE,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    @property
    def is_terminal(self) -> bool:
        """任务是否处于终态（WAITING_USER 不算终态，IM 模式下可继续）"""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )


class AgentState:
    """
    Agent 全局状态管理。

    集中管理所有散落在 Agent 实例中的状态变量，
    提供带验证的状态转换方法。
    """

    def __init__(self) -> None:
        # 当前任务状态
        self.current_task: TaskState | None = None

        # 全局控制标志
        self.interrupt_enabled: bool = True
        self.initialized: bool = False
        self.running: bool = False

        # 当前会话引用（用于中断检查）
        self.current_session: Any = None

        # 当前任务监控器
        self.current_task_monitor: Any = None

    def begin_task(
        self,
        session_id: str = "",
        conversation_id: str = "",
        task_id: str | None = None,
    ) -> TaskState:
        """
        开始新任务，创建 TaskState。

        Args:
            session_id: 会话 ID
            conversation_id: 对话 ID
            task_id: 任务 ID（可选，默认自动生成）

        Returns:
            新创建的 TaskState
        """
        if self.current_task and self.current_task.is_active:
            logger.warning(
                f"[State] Starting new task while previous task {self.current_task.task_id[:8]} "
                f"is still {self.current_task.status.value}. Force resetting."
            )
            self.reset_task()

        self.current_task = TaskState(
            task_id=task_id or str(uuid.uuid4()),
            session_id=session_id,
            conversation_id=conversation_id,
        )
        logger.debug(f"[State] New task created: {self.current_task.task_id[:8]}")
        return self.current_task

    def reset_task(self) -> None:
        """重置当前任务状态（任务结束后调用）"""
        if self.current_task:
            logger.debug(
                f"[State] Task {self.current_task.task_id[:8]} reset "
                f"(was {self.current_task.status.value})"
            )
        self.current_task = None
        self.current_task_monitor = None

    def cancel_task(self, reason: str = "用户请求停止") -> None:
        """取消当前任务"""
        if self.current_task:
            self.current_task.cancel(reason)

    @property
    def is_task_cancelled(self) -> bool:
        """当前任务是否已取消"""
        return self.current_task is not None and self.current_task.cancelled

    @property
    def has_active_task(self) -> bool:
        """是否有活跃任务"""
        return self.current_task is not None and self.current_task.is_active
