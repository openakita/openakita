"""
Agent state management module

Provides structured state management, replacing scattered instance variables in agent.py.
Contains:
- TaskStatus: Task execution status enum (explicit ReAct loop)
- TaskState: Complete execution state for a single task
- AgentState: Global Agent state management + state machine transition validation
"""

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _safe_event_set(event: asyncio.Event) -> None:
    """Set an asyncio.Event safely, even from a different event loop thread."""
    from openakita.core.engine_bridge import _current_loop, get_engine_loop

    engine = get_engine_loop()
    current = _current_loop()
    if engine is not None and current is not engine:
        engine.call_soon_threadsafe(event.set)
    else:
        event.set()


def _safe_event_clear(event: asyncio.Event) -> None:
    """Clear an asyncio.Event safely, even from a different event loop thread."""
    from openakita.core.engine_bridge import _current_loop, get_engine_loop

    engine = get_engine_loop()
    current = _current_loop()
    if engine is not None and current is not engine:
        engine.call_soon_threadsafe(event.clear)
    else:
        event.clear()


class TaskStatus(Enum):
    """Task execution status (corresponds to phases of the ReAct loop)"""

    IDLE = "idle"  # Idle, waiting for a new task
    COMPILING = "compiling"  # Prompt Compiler phase
    REASONING = "reasoning"  # LLM reasoning/decision phase
    ACTING = "acting"  # Tool execution phase
    OBSERVING = "observing"  # Observing tool results phase
    VERIFYING = "verifying"  # Task completion verification phase
    MODEL_SWITCHING = "model_switching"  # Switching model
    WAITING_USER = "waiting_user"  # Waiting for user reply (triggered by ask_user tool)
    COMPLETED = "completed"  # Task completed
    FAILED = "failed"  # Task failed
    CANCELLED = "cancelled"  # Task cancelled


# Valid state transition table
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.IDLE: {TaskStatus.COMPILING, TaskStatus.REASONING, TaskStatus.CANCELLED},
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
        TaskStatus.REASONING,  # Recovery path: if a previous task was stuck in ACTING, a new message returns to REASONING
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
    TaskStatus.MODEL_SWITCHING: {TaskStatus.REASONING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.WAITING_USER: {TaskStatus.REASONING, TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: {TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: {TaskStatus.IDLE},
}


@dataclass
class TaskState:
    """
    Complete execution state for a single task.

    Each call to chat_with_session() creates a new TaskState,
    cleaned up after the task ends via AgentState.reset_task().
    """

    task_id: str
    session_id: str = ""
    conversation_id: str = ""
    status: TaskStatus = TaskStatus.IDLE

    # Task definition (from Prompt Compiler)
    task_definition: str = ""
    task_query: str = ""

    # Cancellation mechanism
    cancelled: bool = False
    cancel_reason: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Single-step skip mechanism
    skip_event: asyncio.Event = field(default_factory=asyncio.Event)
    skip_reason: str = ""

    # User message insertion queue (non-command messages sent by the user during task execution)
    pending_user_inserts: list[str] = field(default_factory=list)
    _insert_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Model state
    current_model: str = ""

    # Reasoning-action loop state
    iteration: int = 0
    consecutive_tool_rounds: int = 0
    tools_executed: list[str] = field(default_factory=list)
    tools_executed_in_task: bool = False
    delivery_receipts: list[dict] = field(default_factory=list)

    # ForceToolCall control
    no_tool_call_count: int = 0

    # Task verification control
    verify_incomplete_count: int = 0
    no_confirmation_text_count: int = 0

    # Loop detection
    recent_tool_signatures: list[str] = field(default_factory=list)
    tool_pattern_window: int = 8
    llm_self_check_interval: int = 10
    extreme_safety_threshold: int = 50
    last_browser_url: str = ""

    # Original user messages (used to reset context on model switch)
    original_user_messages: list[dict] = field(default_factory=list)

    def transition(self, new_status: TaskStatus) -> None:
        """
        Execute a state transition with validity checking.

        Args:
            new_status: Target state

        Raises:
            ValueError: Invalid state transition
        """
        valid_targets = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise ValueError(
                f"Invalid state transition: {self.status.value} -> {new_status.value}. "
                f"Valid targets: {[s.value for s in valid_targets]}"
            )
        old_status = self.status
        self.status = new_status
        logger.debug(f"[State] {old_status.value} -> {new_status.value} (task={self.task_id[:8]})")

    def cancel(self, reason: str = "User requested stop") -> None:
        """Cancel the task and trigger cancel_event to notify all waiters (cross-loop safe)"""
        prev_status = self.status.value if hasattr(self.status, "value") else str(self.status)
        self.cancelled = True
        self.cancel_reason = reason
        _safe_event_set(self.cancel_event)
        if self.status != TaskStatus.CANCELLED:
            try:
                self.transition(TaskStatus.CANCELLED)
            except ValueError:
                logger.warning(
                    f"[State] cancel() transition from {prev_status} not allowed, forcing CANCELLED"
                )
                self.status = TaskStatus.CANCELLED
        logger.info(
            f"[State] Task {self.task_id[:8]} cancel(): "
            f"prev_status={prev_status}, new_status={self.status.value}, "
            f"cancel_event.is_set={self.cancel_event.is_set()}, "
            f"reason={reason!r}"
        )

    def request_skip(self, reason: str = "User requested skipping the current step") -> None:
        """Request to skip the currently executing tool/step (does not terminate the whole task, cross-loop safe)"""
        self.skip_reason = reason
        _safe_event_set(self.skip_event)
        logger.info(f"[State] Task {self.task_id[:8]} skip requested: {reason}")

    def clear_skip(self) -> None:
        """Reset skip flag (called at the start of each tool execution, cross-loop safe)"""
        _safe_event_clear(self.skip_event)
        self.skip_reason = ""

    async def add_user_insert(self, text: str) -> None:
        """Thread-safely add a user-inserted message"""
        async with self._insert_lock:
            self.pending_user_inserts.append(text)
            logger.info(f"[State] User insert queued: {text[:50]}...")

    async def drain_user_inserts(self) -> list[str]:
        """Drain all pending user-inserted messages (clears the queue)"""
        async with self._insert_lock:
            msgs = list(self.pending_user_inserts)
            self.pending_user_inserts.clear()
            return msgs

    async def process_post_tool_signals(self, working_messages: list[dict]) -> None:
        """Unified signal handling after tool execution: skip reflection prompt + user-insert message injection.

        Each execution loop calls this method after finishing a round of tool execution,
        avoiding duplication of the same logic in 4+ places.

        Args:
            working_messages: Current working messages list (will be appended to in place)
        """
        # 1) Check skip: if any tool was skipped this round, inject a reflection prompt
        if self.skip_event.is_set():
            _skip_reason = self.skip_reason or "User felt the step was taking too long or was incorrect"
            self.clear_skip()
            working_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[System notice - user skipped step] The user skipped the above tool execution. Reason: {_skip_reason}\n"
                        "Please reflect: Is something wrong with that step? Do you need a different approach? "
                        "Gather your thoughts and continue with the task."
                    ),
                }
            )
            logger.info(f"[SkipReflect] Injected skip reflection prompt: {_skip_reason}")

        # 2) Check for user-inserted messages
        _inserts = await self.drain_user_inserts()
        for _ins_text in _inserts:
            working_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[User-inserted message] {_ins_text}\n"
                        "[System notice] The above is a message the user inserted during task execution. "
                        "Please decide: 1) Is this a supplement to the current task (incorporate it into your decision and continue), "
                        "or 2) is it an entirely new task (acknowledge to the user, complete the current task first, then execute it)? "
                        "If unsure, use the ask_user tool to confirm with the user."
                    ),
                }
            )
            logger.info(f"[UserInsert] Injected user insert into context: {_ins_text[:60]}")

    def reset_for_model_switch(self) -> None:
        """Reset loop-related state when switching models"""
        self.no_tool_call_count = 0
        self.tools_executed_in_task = False
        self.verify_incomplete_count = 0
        self.tools_executed = []
        self.consecutive_tool_rounds = 0
        self.recent_tool_signatures = []
        self.no_confirmation_text_count = 0

    def record_tool_execution(self, tool_names: list[str]) -> None:
        """Record tool execution"""
        if tool_names:
            self.tools_executed_in_task = True
            self.tools_executed.extend(tool_names)

    def record_tool_signature(self, signature: str) -> None:
        """Record tool signature for loop detection"""
        self.recent_tool_signatures.append(signature)
        if len(self.recent_tool_signatures) > self.tool_pattern_window:
            self.recent_tool_signatures = self.recent_tool_signatures[-self.tool_pattern_window :]

    @property
    def is_active(self) -> bool:
        """Whether the task is active (includes WAITING_USER, since IM mode is still waiting for a reply)"""
        return self.status not in (
            TaskStatus.IDLE,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    @property
    def is_terminal(self) -> bool:
        """Whether the task is in a terminal state (WAITING_USER is not terminal; IM mode may continue)"""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )


class AgentState:
    """
    Global Agent state management.

    Centrally manages all state variables that were scattered across Agent instances,
    and provides state transition methods with validation.

    Supports concurrent tasks across multiple sessions: isolated by session_id via the _tasks dict;
    the current_task property is kept for backward compatibility (returns the most recently created task).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._tasks_lock = threading.RLock()
        self._last_task_key: str = ""

        self.interrupt_enabled: bool = True
        self.initialized: bool = False
        self.running: bool = False

        self.current_session: Any = None
        self.current_task_monitor: Any = None

    @property
    def current_task(self) -> TaskState | None:
        """Backward compatible: returns the most recently created / only task"""
        with self._tasks_lock:
            if self._last_task_key and self._last_task_key in self._tasks:
                return self._tasks[self._last_task_key]
            if len(self._tasks) == 1:
                return next(iter(self._tasks.values()))
            return None

    @current_task.setter
    def current_task(self, value: TaskState | None) -> None:
        """Backward compatible: direct assignment (only for legacy code / reset_task)"""
        with self._tasks_lock:
            if value is None:
                if self._last_task_key in self._tasks:
                    self._tasks.pop(self._last_task_key, None)
                self._last_task_key = ""
            else:
                key = value.session_id or value.task_id
                self._tasks[key] = value
                self._last_task_key = key

    def get_task_for_session(self, session_id: str) -> TaskState | None:
        """Get the task for the given session"""
        with self._tasks_lock:
            return self._tasks.get(session_id)

    def begin_task(
        self,
        session_id: str = "",
        conversation_id: str = "",
        task_id: str | None = None,
    ) -> TaskState:
        """
        Start a new task, creating a TaskState.

        If a previous task already exists for the same session_id, clean it up first
        (without affecting tasks in other sessions).

        Args:
            session_id: Session ID
            conversation_id: Conversation ID
            task_id: Task ID (optional, auto-generated by default)

        Returns:
            The newly created TaskState
        """
        _tid = task_id or str(uuid.uuid4())
        key = session_id or _tid

        with self._tasks_lock:
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
        """Reset task state (call after a task ends)"""
        session_id = session_id or None
        with self._tasks_lock:
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

    def cancel_task(self, reason: str = "User requested stop", session_id: str | None = None) -> None:
        """Cancel a task. If session_id is specified, cancel only that session's task."""
        session_id = session_id or None
        with self._tasks_lock:
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

    def skip_current_step(
        self, reason: str = "User requested skipping the current step", session_id: str | None = None
    ) -> None:
        """Skip the currently executing step (does not terminate the task)"""
        session_id = session_id or None
        with self._tasks_lock:
            task = self._tasks.get(session_id) if session_id else self.current_task
        if task:
            task.request_skip(reason)
        else:
            logger.warning(
                f"[State] skip_current_step: no task found for session {session_id}, "
                f"active sessions: {list(self._tasks.keys())}"
            )

    async def insert_user_message(self, text: str, session_id: str | None = None) -> None:
        """Inject a user message into the task"""
        session_id = session_id or None
        with self._tasks_lock:
            task = self._tasks.get(session_id) if session_id else self.current_task
        if task:
            await task.add_user_insert(text)
        else:
            logger.warning(
                f"[State] insert_user_message: no task found for session {session_id}, "
                f"active sessions: {list(self._tasks.keys())}"
            )

    @property
    def is_task_cancelled(self) -> bool:
        """Whether the current task has been cancelled"""
        return self.current_task is not None and self.current_task.cancelled

    @property
    def task_cancel_reason(self) -> str:
        """Cancellation reason for the current task (empty string if no task)"""
        if self.current_task and self.current_task.cancelled:
            return self.current_task.cancel_reason
        return ""

    @property
    def has_active_task(self) -> bool:
        """Whether there is an active task"""
        return self.current_task is not None and self.current_task.is_active
