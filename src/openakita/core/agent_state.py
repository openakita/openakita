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
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .abort_scope import AbortScope

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
    TaskStatus.MODEL_SWITCHING: {TaskStatus.REASONING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.WAITING_USER: {TaskStatus.REASONING, TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: {TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.IDLE, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: {TaskStatus.IDLE},
}


class IllegalReasoningEntry(RuntimeError):
    """v1.28.3 S5-A: raised by :meth:`TaskState.ensure_ready_for_reasoning`
    when called on a terminal state (COMPLETED / FAILED / CANCELLED).

    Post-S1+S3+S4 contract: this should NEVER happen in production.
    The preempt protocol in :meth:`Agent._preempt_or_queue_prev_task`
    is the only legitimate way to start reasoning on a session that
    previously had an active task, and it always calls
    :meth:`AgentState.begin_task` (returning a fresh IDLE TaskState)
    before any reasoning entry.  If you see this exception in the
    wild, S1's preempt protocol was bypassed somewhere — alarm and
    fix the bypass, do **not** silently force-write ``state.status``
    as the pre-v1.28.3 code did (that is the root cause of the
    "completed -> reasoning" crash in issue #572).

    The exception is converted to an SSE ``error`` event with code
    ``illegal_state`` by ``ReasoningEngine.reason_stream``'s outer
    ``try/except`` and accompanied by an
    ``inc_illegal_reasoning_entry`` counter increment so ops can
    pager-alert on it.
    """


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

    # Issue #608: whether this task belongs to a delegated sub-agent.  Set by
    # the reasoning engine right after the TaskState is resolved.  The cancel
    # funnel reads it to decide whether to persist a resumable working_messages
    # snapshot — sub-agents share the parent conversation_id, so persisting on
    # a sub-agent cancel would clobber the parent turn's resume state.
    is_sub_agent: bool = False

    # 任务定义（来自 Prompt Compiler）
    task_definition: str = ""
    task_query: str = ""

    # 取消机制
    cancelled: bool = False
    cancel_reason: str = ""
    # v1.28 S3 plan: cancel signal moved into a hierarchical AbortScope tree so
    # tool workers and sub-agents propagate cancel automatically (see
    # ``core/abort_scope.py``).  ``cancel_event`` is kept as a property below
    # delegating to ``abort_root.event`` — preserves the 11+ existing read
    # call sites (``task.cancel_event.wait() / .is_set()``) zero-change.
    abort_root: AbortScope = field(default_factory=lambda: AbortScope(name="root"))

    # Settle 机制（v1.27.14, plan: conversation concurrency v1.28, S1.5）
    # ``settled_event`` 由 reasoning_engine 在任意出口路径（正常完成 / cancel /
    # max_iter / exception）的 finally 中调用 :meth:`mark_settled` 设置，
    # 用于 S1.4 抢占协议的 "wait until old task is finished" 语义。
    # ``abandoned`` 由抢占方在 ``preempt_settle_timeout_ms`` 超时后置 True，
    # reason_stream / run() 在每轮迭代头部检查到 True 时立即返回，避免老协程
    # 继续写入共享 state（这是 issue #572 类崩溃的另一条来源）。
    settled_event: asyncio.Event = field(default_factory=asyncio.Event)
    abandoned: bool = False

    # Partial assistant text accumulator (v1.27.15, plan v1.28 S2 P0-3).
    # ``reason_stream`` outer wrapper appends every ``text_delta.content``
    # here while a turn is streaming; ``_preempt_or_queue_prev_task``
    # reads it on cancel/preempt to persist a ``marker_type="aborted_partial"``
    # message into session history — so a user who got cut off mid-answer
    # still sees the 500 chars they already received, instead of an opaque
    # "task was interrupted" placeholder.
    #
    # Capped at ``_PARTIAL_TEXT_CAP`` chars to prevent runaway memory on
    # very long answers (we only need enough to make the UI honest, not
    # the full transcript — that flows through ``Session.add_message`` on
    # the normal completion path).  Older content is dropped silently
    # once the cap is exceeded; ``partial_truncated`` flips True so the
    # marker can render an "…(truncated)" hint.
    partial_text: str = ""
    partial_thinking: str = ""
    partial_truncated: bool = False

    # 单步跳过机制
    skip_event: asyncio.Event = field(default_factory=asyncio.Event)
    skip_reason: str = ""

    # v1.28 S4: in-flight tool tracking for INTERRUPT-policy downgrade.
    # ``_preempt_or_queue_prev_task`` reads ``get_in_flight_tools()`` snapshot
    # when an INTERRUPT request arrives.  If any in-flight tool has
    # ``interrupt_behavior == "block"`` (write_file, run_shell, browser_click,
    # …) the policy is auto-downgraded to QUEUE — we wait for the unsafe
    # tools to finish instead of yanking the rug out from under them.
    # See ``core/tool_interrupt_behavior.py`` for the classification table.
    #
    # No lock needed: ``tool_executor.execute_tool`` runs on a single
    # asyncio event loop per Agent; even parallel batch execution
    # (``execute_batch`` with max_parallel>1) serializes through ``await``
    # points, so ``list.append`` / ``list.remove`` calls from begin/end
    # are sequential.  Snapshot via ``list(...)`` for the reader so a
    # concurrent end_tool can't mutate the returned slice.
    in_flight_tools: list[str] = field(default_factory=list)

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
        """执行状态转换，带合法性验证。

        Args:
            new_status: 目标状态

        Raises:
            ValueError: 非法状态转换

        CONTRACT (post-v1.28.3 S5):

        * ``ValueError`` 表示非法转换，**fatal**——调用方 MUST NOT 静默吞。
          v1.28.3 之前 reasoning_engine 里有 20 处 ``except ValueError: pass``
          或 ``except ValueError: self.status = X`` 静默吞 / 强写，是
          issue #572 "completed -> reasoning" 崩溃链的根因；那些路径在
          v1.28.3 灰度后会被逐步删除。
        * 唯一合法的 ``except ValueError: pass`` 在 :meth:`cancel` 里——
          cancel 是 idempotent（cancel after cancel 等价 no-op），所以
          强写 ``CANCELLED`` 是安全降级。
        * 如果你需要"开新轮 reasoning"语义，**不要** force-write
          ``state.status = REASONING``——改调
          :meth:`AgentState.begin_task` （返回全新 IDLE TaskState）或
          :meth:`ensure_ready_for_reasoning` （从 IDLE 转 REASONING，
          已是 REASONING 时 idempotent，terminal 时抛
          :class:`IllegalReasoningEntry`）。
        * 任何 future contributor 重新加 ``except ValueError: state.status = X``
          会被 ``test_no_force_write_state_transitions`` test 拦下。
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

    def ensure_ready_for_reasoning(self) -> None:
        """v1.28.3 S5-A: idempotent entry guard for reasoning loops.

        Semantics:

        * Already ``REASONING`` → no-op (idempotent for retry loops).
        * Terminal (``COMPLETED`` / ``FAILED`` / ``CANCELLED``) → raise
          :class:`IllegalReasoningEntry`.  The S1 preempt protocol
          guarantees a fresh IDLE TaskState before any reasoning entry;
          a terminal state here means the protocol was bypassed.
        * Anything else (IDLE / OBSERVING / etc.) → transition to
          REASONING through the validated state-machine path; any
          illegal transition still raises ``ValueError`` (also fatal —
          see :meth:`transition` contract).

        Use this whenever ``reason_stream`` or ``run`` re-enters the
        REASONING phase on the same TaskState (continuation, retry,
        post-tool-loop).  Don't use ``self.status = REASONING`` for the
        same purpose — it bypasses the state machine and is exactly
        what S5 is removing.
        """
        if self.status is TaskStatus.REASONING:
            return
        if self.is_terminal:
            raise IllegalReasoningEntry(
                f"ensure_ready_for_reasoning on {self.status.value} state "
                f"(task_id={self.task_id[:8]}); caller must call "
                f"AgentState.begin_task() first to obtain a fresh TaskState"
            )
        self.transition(TaskStatus.REASONING)

    # v1.28 S3: ``cancel_event`` is now a thin alias for ``abort_root.event``.
    # All existing readers (``task.cancel_event.wait()``, ``.is_set()``) and
    # the four ``state.cancel_event = asyncio.Event()`` reset points in
    # ``reasoning_engine`` keep working through the setter.
    @property
    def cancel_event(self) -> asyncio.Event:
        return self.abort_root.event

    @cancel_event.setter
    def cancel_event(self, ev: asyncio.Event) -> None:
        # Reset path used by ``reasoning_engine`` LLM retry: swap the underlying
        # event so the next iteration starts fresh.  Children scopes (tool
        # scopes spawned during the previous attempt) keep their own event
        # references, so they are not affected — which is the intended behaviour
        # for a retry: don't un-cancel an in-flight tool.
        self.abort_root.event = ev
        self.abort_root._aborted_by = None
        if not ev.is_set():
            self.abort_root.reason = ""

    def cancel(self, reason: str = "用户请求停止") -> None:
        """Cancel this task and fan out to every tool / sub-agent scope below.

        Fan-out is handled by :meth:`AbortScope.abort` walking the children
        tree (registered at tool dispatch / sub-agent delegation time).
        ``_safe_event_set`` is still used on the root event so callers on a
        different event loop (e.g. signal handler, IM gateway thread) can
        invoke ``cancel()`` without raising "no current event loop".
        """
        prev_status = self.status.value if hasattr(self.status, "value") else str(self.status)
        self.cancelled = True
        self.cancel_reason = reason

        # Synchronous fan-out: walk the AbortScope tree and set each event.
        # ``_safe_event_set`` on the root handles cross-loop dispatch; children
        # are walked synchronously here because they live in the same
        # ``TaskState`` object and asyncio.Event.set() itself is loop-safe to
        # call from any thread per CPython source (it's a state flip + waiter
        # wake; the wake is the loop-sensitive part and is deferred via
        # ``loop.call_soon_threadsafe`` inside the Event impl).
        _safe_event_set(self.abort_root.event)
        # Propagate reason/_aborted_by to children too. We don't go through
        # ``abort_root.abort()`` directly because we already set the root event
        # via the cross-loop helper above.
        self.abort_root.reason = reason
        for _child in list(self.abort_root.children):
            _child.abort(reason, _from=self.abort_root.name)

        if self.status != TaskStatus.CANCELLED:
            try:
                self.transition(TaskStatus.CANCELLED)
            except ValueError:  # cancel-idempotent-force-write
                # cancel() MUST be idempotent and reachable from any prior
                # state — even terminal states should re-affirm CANCELLED
                # rather than raise.  This is architecturally permanent
                # (not S5-B backlog); the syntax-guard test recognises
                # this token specifically.
                logger.warning(
                    f"[State] cancel() transition from {prev_status} not allowed, forcing CANCELLED"
                )
                self.status = TaskStatus.CANCELLED
        logger.info(
            f"[State] Task {self.task_id[:8]} cancel(): "
            f"prev_status={prev_status}, new_status={self.status.value}, "
            f"cancel_event.is_set={self.cancel_event.is_set()}, "
            f"abort_scope_children={len(self.abort_root.children)}, "
            f"reason={reason!r}"
        )

    # v1.27.15 (S2 P0-3) — cap chosen large enough to keep a typical
    # answer intact (≈3-4 typical paragraphs) but small enough not to
    # bloat per-task memory if a long generation is being abandoned.
    _PARTIAL_TEXT_CAP: int = 16_000

    def append_partial_text(self, content: str) -> None:
        """Accumulate streamed assistant text for later abort-marker use.

        Called by the ``reason_stream`` outer wrapper.  No-op when the
        cap has already been hit (just flips ``partial_truncated``).
        Cheap string concatenation — Python interns short repeated
        substrings, and our growth is bounded by the cap.
        """
        if not content:
            return
        if len(self.partial_text) >= self._PARTIAL_TEXT_CAP:
            self.partial_truncated = True
            return
        room = self._PARTIAL_TEXT_CAP - len(self.partial_text)
        if len(content) <= room:
            self.partial_text += content
        else:
            self.partial_text += content[:room]
            self.partial_truncated = True

    def append_partial_thinking(self, content: str) -> None:
        """Accumulate streamed thinking text. Same cap as ``append_partial_text``."""
        if not content:
            return
        if len(self.partial_thinking) >= self._PARTIAL_TEXT_CAP:
            self.partial_truncated = True
            return
        room = self._PARTIAL_TEXT_CAP - len(self.partial_thinking)
        if len(content) <= room:
            self.partial_thinking += content
        else:
            self.partial_thinking += content[:room]
            self.partial_truncated = True

    # v1.28 S4: in-flight tool tracking ───────────────────────────────
    def begin_tool(self, tool_name: str) -> None:
        """Register a tool that just started executing.

        Called by ``tool_executor.execute_tool`` at dispatch.  Idempotent
        on repeated names (parallel batches can run multiple instances of
        the same tool concurrently — each one ``end_tool``s separately).
        """
        if not tool_name:
            return
        self.in_flight_tools.append(tool_name)

    def end_tool(self, tool_name: str) -> None:
        """Unregister a tool whose execution just finished.

        Removes ONE instance of ``tool_name`` from the list — handles the
        parallel-batch case where the same tool appears multiple times.
        Silent no-op when the tool isn't registered (defensive: a
        misordered finally shouldn't crash the agent).
        """
        if not tool_name:
            return
        try:
            self.in_flight_tools.remove(tool_name)
        except ValueError:
            logger.debug(
                "[State] end_tool(%r) called but tool not in_flight (task=%s, "
                "current_list=%s) — likely double-finally or begin missed",
                tool_name,
                self.task_id[:8] if self.task_id else "?",
                self.in_flight_tools,
            )

    def get_in_flight_tools(self) -> list[str]:
        """Return a snapshot (shallow copy) of the in-flight tool list.

        Always returns a new list so the caller can iterate safely while
        another coroutine ``begin/end_tool``s concurrently — important for
        ``_preempt_or_queue_prev_task`` which evaluates this from a
        different chat-handler coroutine than the one running the tools.
        """
        return list(self.in_flight_tools)

    def mark_settled(self) -> None:
        """标记本任务已"settle"（推理循环所有清理已完成，可安全替换）。

        Reasoning engine 在 ``reason_stream`` / ``run`` 的最外层 finally 中
        无条件调用本方法；多次调用是幂等的。

        v1.27.14 (plan S1.5): 用于 S1.4 ``_preempt_or_queue`` 协议的
        "wait until old task is finished" 语义——抢占方通过
        :meth:`wait_until_settled` 等待这个 event，避免与未清理完的老协程
        共享 state。
        """
        _safe_event_set(self.settled_event)

    async def wait_until_settled(self) -> None:
        """异步等待本任务 settled。

        Caller 负责包裹 :func:`asyncio.wait_for`/timeout；本方法本身是
        无超时阻塞等待。

        如果调用方在等待超时后想"放弃"老任务（不再让它写入共享 state），
        应该设置 ``self.abandoned = True``；reason_stream / run 在每轮
        迭代头部检测到 abandoned=True 后会立即退出。
        """
        await self.settled_event.wait()

    def request_skip(self, reason: str = "用户请求跳过当前步骤") -> None:
        """请求跳过当前正在执行的工具/步骤（不终止整个任务，跨循环安全）"""
        self.skip_reason = reason
        _safe_event_set(self.skip_event)
        logger.info(f"[State] Task {self.task_id[:8]} skip requested: {reason}")

    def clear_skip(self) -> None:
        """重置跳过标志（每次工具执行开始时调用，跨循环安全）"""
        _safe_event_clear(self.skip_event)
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

    @staticmethod
    def build_user_insert_message(text: str) -> dict:
        """把一条用户插入消息包装成可追加进 working_messages 的 user 消息。

        单一来源：post-tool drain（:meth:`process_post_tool_signals`）和
        final-answer done-drain（reasoning_engine 在循环终止前的二次 drain）
        都用这同一段措辞，避免两处文案漂移。
        """
        return {
            "role": "user",
            "content": (
                f"[用户插入消息] {text}\n"
                "[系统提示] 以上是用户在任务执行期间插入的消息。"
                "请判断: 1) 这是对当前任务的补充（融入决策继续）"
                "还是 2) 一个全新任务（告知用户收到，完成当前任务后执行）。"
                "如不确定，使用 ask_user 工具向用户确认。"
            ),
        }

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
            working_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[系统提示-用户跳过步骤] 用户跳过了上述工具执行。原因: {_skip_reason}\n"
                        "请反思: 该步骤是否有问题？是否需要换个方法继续？"
                        "请整理思路后继续完成任务。"
                    ),
                }
            )
            logger.info(f"[SkipReflect] Injected skip reflection prompt: {_skip_reason}")

        # 2) 检查用户插入消息
        _inserts = await self.drain_user_inserts()
        for _ins_text in _inserts:
            working_messages.append(self.build_user_insert_message(_ins_text))
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
        self._tasks_lock = threading.RLock()
        self._last_task_key: str = ""

        self.interrupt_enabled: bool = True
        self.initialized: bool = False
        self.running: bool = False

        self.current_session: Any = None
        self.current_task_monitor: Any = None

    @property
    def current_task(self) -> TaskState | None:
        """向后兼容：返回最近创建 / 唯一的任务"""
        with self._tasks_lock:
            if self._last_task_key and self._last_task_key in self._tasks:
                return self._tasks[self._last_task_key]
            if len(self._tasks) == 1:
                return next(iter(self._tasks.values()))
            return None

    @current_task.setter
    def current_task(self, value: TaskState | None) -> None:
        """向后兼容：直接赋值（仅用于旧代码 / reset_task）"""
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
        """获取指定会话的任务"""
        with self._tasks_lock:
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
        """重置任务状态（任务结束后调用）"""
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

    def cancel_task(self, reason: str = "用户请求停止", session_id: str | None = None) -> None:
        """取消任务。如果指定 session_id，仅取消该会话的任务。"""
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
        self, reason: str = "用户请求跳过当前步骤", session_id: str | None = None
    ) -> None:
        """跳过当前正在执行的步骤（不终止任务）"""
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
        """向任务注入用户消息"""
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
