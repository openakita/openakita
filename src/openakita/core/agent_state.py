"""
Agent 状态管理模块

提供结构化的状态管理，替代 agent.py 中分散的实例变量。
包含:
- TaskStatus: 任务执行状态枚举（显式 ReAct 循环）
- TaskState: 单次任务的完整执行状态
- AgentState: Agent 全局状态管理 + 状态机转换验证
"""

import asyncio
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
        TaskStatus.REASONING,  # 恢复路径：上次任务卡在 ACTING 后新消息需回到 REASONING
        TaskStatus.WAITING_USER,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.OBSERVING: {
        TaskStatus.REASONING,
        TaskStatus.VERIFYING,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
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
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    # 单步跳过机制
    skip_event: asyncio.Event = field(default_factory=asyncio.Event)
    skip_reason: str = ""

    # 用户消息插入队列（任务执行期间用户发送的非指令消息）
    pending_user_inserts: list[str] = field(default_factory=list)
    _insert_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        """取消任务，同时触发 cancel_event 通知所有等待方"""
        prev_status = self.status.value if hasattr(self.status, "value") else str(self.status)
        self.cancelled = True
        self.cancel_reason = reason
        self.cancel_event.set()
        if self.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.IDLE):
            self.status = TaskStatus.CANCELLED
        logger.info(
            f"[State] Task {self.task_id[:8]} cancel(): "
            f"prev_status={prev_status}, new_status={self.status.value}, "
            f"cancel_event.is_set={self.cancel_event.is_set()}, "
            f"reason={reason!r}"
        )

    def request_skip(self, reason: str = "用户请求跳过当前步骤") -> None:
        """请求跳过当前正在执行的工具/步骤（不终止整个任务）"""
        self.skip_reason = reason
        self.skip_event.set()
        logger.info(f"[State] Task {self.task_id[:8]} skip requested: {reason}")

    def clear_skip(self) -> None:
        """重置跳过标志（每次工具执行开始时调用）"""
        self.skip_event.clear()
        self.skip_reason = ""

    async def add_user_insert(self, text: str) -> None:
        """线程安全地添加用户插入消息"""
        async with self._insert_lock:
            self.pending_user_inserts.append(text)
            logger.info(f"[State] User insert queued: {text[:50]}...")

    async def drain_user_inserts(self) -> list[str]:
        """取出所有待处理的用户插入消息（清空队列）"""
        async with self._insert_lock:
            msgs = list(self.pending_user_inserts)
            self.pending_user_inserts.clear()
            return msgs

    async def process_post_tool_signals(self, working_messages: list[dict]) -> None:
        """工具执行后的统一信号处理：skip 反思提示 + 用户插入消息注入。

        各执行循环在每轮工具执行完毕后调用此方法，
        避免在 4+ 个地方重复同样的逻辑。

        Args:
            working_messages: 当前工作消息列表（会被就地追加）
        """
        # 1) 检查 skip: 如果本轮有工具被跳过，注入反思提示
        if self.skip_event.is_set():
            _skip_reason = self.skip_reason or "用户认为该步骤耗时过长或不正确"
            self.clear_skip()
            working_messages.append({
                "role": "user",
                "content": (
                    f"[系统提示-用户跳过步骤] 用户跳过了上述工具执行。原因: {_skip_reason}\n"
                    "请反思: 该步骤是否有问题？是否需要换个方法继续？"
                    "请整理思路后继续完成任务。"
                ),
            })
            logger.info(f"[SkipReflect] Injected skip reflection prompt: {_skip_reason}")

        # 2) 检查用户插入消息
        _inserts = await self.drain_user_inserts()
        for _ins_text in _inserts:
            working_messages.append({
                "role": "user",
                "content": (
                    f"[用户插入消息] {_ins_text}\n"
                    "[系统提示] 以上是用户在任务执行期间插入的消息。"
                    "请判断: 1) 这是对当前任务的补充（融入决策继续）"
                    "还是 2) 一个全新任务（告知用户收到，完成当前任务后执行）。"
                    "如不确定，使用 ask_user 工具向用户确认。"
                ),
            })
            logger.info(f"[UserInsert] Injected user insert into context: {_ins_text[:60]}")

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

    支持多会话并发任务：通过 _tasks 字典按 session_id 隔离，
    current_task 属性保持向后兼容（返回最近创建的任务）。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._last_task_key: str = ""

        self.interrupt_enabled: bool = True
        self.initialized: bool = False
        self.running: bool = False

        self.current_session: Any = None
        self.current_task_monitor: Any = None

    @property
    def current_task(self) -> TaskState | None:
        """向后兼容：返回最近创建 / 唯一的任务"""
        if self._last_task_key and self._last_task_key in self._tasks:
            return self._tasks[self._last_task_key]
        if len(self._tasks) == 1:
            return next(iter(self._tasks.values()))
        return None

    @current_task.setter
    def current_task(self, value: TaskState | None) -> None:
        """向后兼容：直接赋值（仅用于旧代码 / reset_task）"""
        if value is None:
            if self._last_task_key in self._tasks:
                self._tasks.pop(self._last_task_key, None)
            self._last_task_key = ""
        else:
            key = value.session_id or value.task_id
            self._tasks[key] = value
            self._last_task_key = key

    def get_task_for_session(self, session_id: str) -> TaskState | None:
        """获取指定会话的任务"""
        return self._tasks.get(session_id)

    def begin_task(
        self,
        session_id: str = "",
        conversation_id: str = "",
        task_id: str | None = None,
    ) -> TaskState:
        """
        开始新任务，创建 TaskState。

        如果同一 session_id 已有旧任务，先清理它（不影响其他 session 的任务）。

        Args:
            session_id: 会话 ID
            conversation_id: 对话 ID
            task_id: 任务 ID（可选，默认自动生成）

        Returns:
            新创建的 TaskState
        """
        _tid = task_id or str(uuid.uuid4())
        key = session_id or _tid

        old = self._tasks.get(key)
        if old:
            old_status = old.status.value
            old_cancelled = old.cancelled
            if old.is_active:
                logger.warning(
                    f"[State] Starting new task while previous task {old.task_id[:8]} "
                    f"is still {old_status} (session={key}). Force resetting."
                )
            else:
                logger.info(
                    f"[State] Cleaning up previous task {old.task_id[:8]} "
                    f"(status={old_status}, cancelled={old_cancelled}) before new task"
                )
            self._tasks.pop(key, None)

        task = TaskState(
            task_id=_tid,
            session_id=session_id,
            conversation_id=conversation_id,
        )
        self._tasks[key] = task
        self._last_task_key = key
        logger.info(
            f"[State] New task created: {task.task_id[:8]} "
            f"(session={key}, cancelled={task.cancelled})"
        )
        return task

    def reset_task(self, session_id: str | None = None) -> None:
        """重置任务状态（任务结束后调用）"""
        session_id = session_id or None
        if session_id and session_id in self._tasks:
            task = self._tasks.pop(session_id)
            logger.debug(
                f"[State] Task {task.task_id[:8]} reset "
                f"(was {task.status.value}, session={session_id})"
            )
            if self._last_task_key == session_id:
                self._last_task_key = ""
        elif not session_id:
            task = self.current_task
            if task:
                key = task.session_id or task.task_id
                self._tasks.pop(key, None)
                if self._last_task_key == key:
                    self._last_task_key = ""
                logger.debug(
                    f"[State] Task {task.task_id[:8]} reset "
                    f"(was {task.status.value}, key={key})"
                )
        self.current_task_monitor = None

    def cancel_task(self, reason: str = "用户请求停止", session_id: str | None = None) -> None:
        """取消任务。如果指定 session_id，仅取消该会话的任务。"""
        session_id = session_id or None
        if session_id:
            task = self._tasks.get(session_id)
            if task:
                task.cancel(reason)
                logger.info(
                    f"[State] Cancelled task {task.task_id[:8]} for session {session_id}"
                )
            else:
                logger.warning(
                    f"[State] cancel_task: no task found for session {session_id}, "
                    f"active sessions: {list(self._tasks.keys())}"
                )
        elif self.current_task:
            self.current_task.cancel(reason)

    def skip_current_step(self, reason: str = "用户请求跳过当前步骤", session_id: str | None = None) -> None:
        """跳过当前正在执行的步骤（不终止任务）"""
        session_id = session_id or None
        task = self._tasks.get(session_id) if session_id else self.current_task
        if task:
            task.request_skip(reason)

    async def insert_user_message(self, text: str, session_id: str | None = None) -> None:
        """向任务注入用户消息"""
        session_id = session_id or None
        task = self._tasks.get(session_id) if session_id else self.current_task
        if task:
            await task.add_user_insert(text)

    @property
    def is_task_cancelled(self) -> bool:
        """当前任务是否已取消"""
        return self.current_task is not None and self.current_task.cancelled

    @property
    def task_cancel_reason(self) -> str:
        """当前任务的取消原因（无任务时返回空字符串）"""
        if self.current_task and self.current_task.cancelled:
            return self.current_task.cancel_reason
        return ""

    @property
    def has_active_task(self) -> bool:
        """是否有活跃任务"""
        return self.current_task is not None and self.current_task.is_active
