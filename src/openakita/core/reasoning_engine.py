"""
推理-行动引擎 (ReAct Pattern)

从 agent.py 的 _chat_with_tools_and_context 重构为显式的
Reason -> Act -> Observe 三阶段循环。

核心职责:
- 显式推理循环管理（Reason / Act / Observe）
- LLM 响应解析与 Decision 分类
- 工具调用编排（委托给 ToolExecutor）
- 上下文压缩触发（委托给 ContextManager）
- 循环检测（签名重复、自检间隔、安全阈值）
- 模型切换逻辑
- 任务完成度验证（委托给 ResponseHandler）
"""

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import settings
from ..tracing.tracer import get_tracer
from .agent_state import AgentState, TaskState, TaskStatus
from .context_manager import ContextManager
from .context_manager import _CancelledError as _CtxCancelledError
from .errors import UserCancelledError
from .response_handler import (
    ResponseHandler,
    clean_llm_response,
    parse_intent_tag,
    strip_thinking_tags,
)
from .resource_budget import BudgetAction, ResourceBudget, create_budget_from_settings
from .supervisor import RuntimeSupervisor, UNPRODUCTIVE_ADMIN_TOOLS as _ADMIN_TOOL_NAMES
from .token_tracking import TokenTrackingContext, reset_tracking_context, set_tracking_context
from .tool_executor import ToolExecutor
from ..api.routes.websocket import broadcast_event
from ..llm.converters.tools import PARSE_ERROR_KEY

logger = logging.getLogger(__name__)

_SSE_RESULT_PREVIEW_CHARS = 32000

# ---------------------------------------------------------------------------
# Mode-based tool filtering
# ---------------------------------------------------------------------------

from .permission import (
    disabled as permission_disabled,
    PLAN_MODE_RULESET,
    ASK_MODE_RULESET,
    DEFAULT_RULESET,
    Ruleset as PermissionRuleset,
)


def _get_mode_ruleset(mode: str) -> PermissionRuleset:
    """Get the permission ruleset for the given mode."""
    if mode == "plan":
        return PLAN_MODE_RULESET
    elif mode == "ask":
        return ASK_MODE_RULESET
    return DEFAULT_RULESET


def _filter_tools_by_mode(tools: list[dict], mode: str) -> list[dict]:
    """Filter tool list based on the active mode using the permission system.

    Uses PermissionRuleset.disabled() to determine which tools to remove.
    - agent: DEFAULT_RULESET (all tools allowed)
    - ask: ASK_MODE_RULESET (write tools removed)
    - plan: PLAN_MODE_RULESET (write tools visible but path-restricted at runtime)
    """
    if mode == "agent" or not tools:
        return tools

    ruleset = _get_mode_ruleset(mode)

    tool_names = []
    for tool in tools:
        name = tool.get("name", "")
        if not name:
            fn = tool.get("function", {})
            name = fn.get("name", "")
        tool_names.append(name)

    disabled_set = permission_disabled(tool_names, ruleset)

    filtered = []
    for tool, name in zip(tools, tool_names):
        if name not in disabled_set:
            filtered.append(tool)

    if disabled_set:
        logger.info(
            f"[ToolFilter] mode={mode}: {len(tools)} -> {len(filtered)} tools "
            f"(disabled: {sorted(disabled_set)})"
        )
    return filtered


_SHELL_WRITE_PATTERNS = re.compile(
    r'(?:'
    r'>\s*["\'/\w]'
    r'|>>'
    r'|\btee\b'
    r'|\bsed\s+-i'
    r'|\bdd\b'
    r'|\brm\s'
    r'|\bmv\s'
    r'|\bcp\s'
    r'|\bmkdir\b'
    r'|\btouch\b'
    r'|\bchmod\b'
    r'|\bchown\b'
    r'|open\s*\([^)]*["\']w'
    r'|\.write\s*\('
    r'|echo\s+.*>'
    r'|\bpip\s+install'
    r'|\bnpm\s+install'
    r'|\bgit\s+(?:commit|push|checkout|merge|rebase|reset)'
    r'|\bOut-File\b'
    r'|\bSet-Content\b'
    r'|\bAdd-Content\b'
    r'|\bNew-Item\b'
    r'|\bRemove-Item\b'
    r'|\bMove-Item\b'
    r'|\bCopy-Item\b'
    r'|\bRename-Item\b'
    r'|\bInvoke-WebRequest\b.*-OutFile'
    r'|\bdel\s'
    r'|\bcopy\s'
    r'|\bmove\s'
    r'|\bren\s'
    r'|\btype\s.*>'
    r')',
    re.IGNORECASE,
)


def _is_shell_write_command(command: str) -> bool:
    """Check if a shell command appears to perform write operations."""
    return bool(_SHELL_WRITE_PATTERNS.search(command))


def _should_block_tool(
    tool_name: str,
    tool_input: Any,
    allowed_tool_names: set[str] | None,
    mode: str,
) -> str | None:
    """Check if a tool call should be blocked by mode restrictions.

    Returns None if allowed, or an error message string if blocked.
    """
    if allowed_tool_names is None:
        return None

    if tool_name not in allowed_tool_names:
        return (
            f"错误：{tool_name} 在当前 {mode} 模式下不可用。"
            "请使用已提供的工具列表中的工具，或建议用户切换到 agent 模式。"
        )

    if tool_name == "run_shell":
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", "")
        elif isinstance(tool_input, str):
            try:
                cmd = json.loads(tool_input).get("command", "")
            except Exception:
                pass
        if cmd and _is_shell_write_command(cmd):
            logger.warning(
                f"[ModeGuard] Blocked run_shell write command in {mode} mode: {cmd[:100]}"
            )
            return (
                f"错误：在 {mode} 模式下，run_shell 仅允许执行只读命令（如 cat、grep、ls、find 等）。"
                f"检测到写操作命令，已拦截。请使用只读命令，或建议用户切换到 agent 模式。"
            )

    return None


class DecisionType(Enum):
    """LLM 决策类型"""
    FINAL_ANSWER = "final_answer"  # 纯文本响应
    TOOL_CALLS = "tool_calls"  # 需要工具调用


@dataclass
class Decision:
    """LLM 推理决策"""
    type: DecisionType
    text_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking_content: str = ""
    raw_response: Any = None
    stop_reason: str = ""
    # 完整的 assistant_content（保留 thinking 块等）
    assistant_content: list[dict] = field(default_factory=list)


@dataclass
class Checkpoint:
    """
    决策检查点，用于多路径探索和回滚。

    在关键决策点保存消息历史和任务状态的快照，
    当检测到循环、连续失败等问题时可回滚到之前的检查点，
    附加失败经验提示后重新推理。
    """

    id: str
    messages_snapshot: list[dict]  # 深拷贝消息历史
    state_snapshot: dict  # 序列化的 TaskState 关键字段
    decision_summary: str  # 做出的决策摘要
    iteration: int  # 保存时的迭代次数
    timestamp: float = field(default_factory=time.time)
    tool_names: list[str] = field(default_factory=list)  # 该决策调用的工具


class ReasoningEngine:
    """
    显式推理-行动引擎。

    替代 agent.py 中的 _chat_with_tools_and_context()，
    将隐式循环重构为清晰的 Reason -> Act -> Observe 三阶段。
    支持 Checkpoint + Rollback 多路径探索。
    """

    # 检查点配置
    MAX_CHECKPOINTS = 5  # 保留最近 N 个检查点
    CONSECUTIVE_FAIL_THRESHOLD = 3  # 同一工具连续失败 N 次触发回滚

    def __init__(
        self,
        brain: Any,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        response_handler: ResponseHandler,
        agent_state: AgentState,
        memory_manager: Any = None,
    ) -> None:
        self._brain = brain
        self._tool_executor = tool_executor
        self._context_manager = context_manager
        self._response_handler = response_handler
        self._state = agent_state
        self._memory_manager = memory_manager
        self._plugin_hooks = None

        # Agent Harness: Runtime Supervisor + Resource Budget
        self._supervisor = RuntimeSupervisor(enabled=getattr(settings, "supervisor_enabled", True))
        self._budget: ResourceBudget = create_budget_from_settings()

        # Checkpoint 管理
        self._checkpoints: list[Checkpoint] = []
        self._tool_failure_counter: dict[str, int] = {}  # tool_name -> consecutive_failures
        self._consecutive_truncation_count: int = 0  # 连续截断计数（防止截断→回滚死循环）

        # 跨 rollback 的持久性失败计数器（rollback 不会清除）
        # 用于检测 "write_file 因截断反复失败" 等跨 rollback 循环
        self._persistent_tool_failures: dict[str, int] = {}
        self.PERSISTENT_FAIL_LIMIT = 5  # 同一工具跨 rollback 累计失败 N 次强制终止

        # 思维链: 暂存最近一次推理的 react_trace，供 agent_handler 读取
        self._last_react_trace: list[dict] = []

        # 暂存最近一次推理结束时的 working_messages，供 token 统计读取
        self._last_working_messages: list[dict] = []

        # 上一次推理的退出原因：normal / ask_user
        # _finalize_session 据此决定是否自动关闭 Plan
        self._last_exit_reason: str = "normal"

        # 上一次推理中 deliver_artifacts 的交付回执
        self._last_delivery_receipts: list[dict] = []

        # Checkpoint 数据中 messages_snapshot 可含大量工具结果，
        # 在 session 结束时清理以释放内存
        self._max_working_messages_kept = 0  # 清理时保留的条数（0=全部释放）

        # 浏览器"读页面状态"工具
        self._browser_page_read_tools = frozenset({
            "browser_get_content", "browser_screenshot",
        })

    # ==================== Failure Analysis (Agent Harness) ====================

    def _run_failure_analysis(
        self,
        react_trace: list[dict],
        exit_reason: str,
        task_description: str = "",
        task_id: str = "",
    ) -> None:
        """在任务失败时运行失败分析管线"""
        try:
            from ..config import settings
            from ..evolution.failure_analysis import FailureAnalyzer
            analyzer = FailureAnalyzer(output_dir=settings.data_dir / "failure_analysis")
            analyzer.analyze_task(
                task_id=task_id or "unknown",
                react_trace=react_trace,
                supervisor_events=[
                    {
                        "pattern": e.pattern.value,
                        "level": e.level.name,
                        "detail": e.detail,
                        "iteration": e.iteration,
                    }
                    for e in self._supervisor.events
                ],
                budget_summary=self._budget.get_summary(),
                exit_reason=exit_reason,
                task_description=task_description,
            )
        except Exception as e:
            logger.debug(f"[FailureAnalysis] Analysis error: {e}")

    # ==================== 内存管理 ====================

    def release_large_buffers(self) -> None:
        """释放推理结束后残留的大对象，防止内存泄漏。

        在 _cleanup_session_state 中调用。
        _last_working_messages 持有完整的 LLM 上下文（含 base64 截图、
        网页内容等工具结果），是最大的内存占用者，必须主动释放。
        _checkpoints 含 messages_snapshot 深拷贝，同样需要释放。

        注意：不清理 _last_react_trace — 它已被复制到 agent._last_finalized_trace，
        而 _last_finalized_trace 由 orchestrator / SSE 使用，需等到下次会话自然覆盖。
        """
        self._last_working_messages = []
        self._checkpoints.clear()
        self._tool_failure_counter.clear()
        self._supervisor.reset()

    # ==================== ask_user 等待用户回复 ====================

    async def _wait_for_user_reply(
        self,
        question: str,
        state: TaskState,
        *,
        timeout_seconds: int = 60,
        max_reminders: int = 1,
        poll_interval: float = 2.0,
    ) -> str | None:
        """
        等待用户回复 ask_user 的问题（仅 IM 模式生效）。

        利用 Gateway 的中断队列机制：IM 用户在 Agent 处理中发送的消息
        会被 Gateway 放入 interrupt_queue，本方法轮询该队列获取回复。

        流程:
        1. 通过 Gateway 发送问题给用户
        2. 轮询 interrupt_queue 等待回复（timeout_seconds 超时）
        3. 第一次超时 → 发送提醒，再等一轮
        4. 第二次超时 → 返回 None，由调用方注入系统消息让 LLM 自行决策

        Args:
            question: 要发送给用户的问题文本
            state: 当前任务状态（用于取消检查）
            timeout_seconds: 每轮等待超时（秒）
            max_reminders: 最大追问提醒次数
            poll_interval: 轮询间隔（秒）

        Returns:
            用户回复文本，或 None（超时/无 gateway/被取消）
        """
        # 获取 gateway 和 session 引用
        session = self._state.current_session
        if not session:
            return None

        gateway = session.get_metadata("_gateway") if hasattr(session, "get_metadata") else None
        session_key = session.get_metadata("_session_key") if gateway else None

        if not gateway or not session_key:
            # CLI 模式或无 gateway，不做等待
            return None

        # 发送问题到用户
        try:
            await gateway.send_to_session(session, question, role="assistant")
            logger.info(f"[ask_user] Question sent to user, waiting for reply (timeout={timeout_seconds}s)")
        except Exception as e:
            logger.warning(f"[ask_user] Failed to send question via gateway: {e}")
            return None

        reminders_sent = 0

        while reminders_sent <= max_reminders:
            # 轮询等待用户回复
            elapsed = 0.0

            while elapsed < timeout_seconds:
                # 检查任务是否被取消
                if state.cancelled:
                    logger.info("[ask_user] Task cancelled while waiting for reply")
                    return None

                # 检查中断队列
                try:
                    reply_msg = await gateway.check_interrupt(session_key)
                except Exception as e:
                    logger.warning(f"[ask_user] check_interrupt error: {e}")
                    reply_msg = None

                if reply_msg:
                    # 从 UnifiedMessage 提取文本
                    reply_text = (
                        reply_msg.plain_text.strip()
                        if hasattr(reply_msg, "plain_text") and reply_msg.plain_text
                        else str(reply_msg).strip()
                    )
                    if reply_text:
                        logger.info(f"[ask_user] User replied: {reply_text[:80]}")
                        # 记录到 session 历史
                        try:
                            session.add_message(role="user", content=reply_text, source="ask_user_reply")
                        except Exception:
                            pass
                        return reply_text

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # 本轮超时
            if reminders_sent < max_reminders:
                # 发送追问提醒
                reminders_sent += 1
                reminder = "⏰ 我在等你回复上面的问题哦，看到的话回复一下~"
                try:
                    await gateway.send_to_session(session, reminder, role="assistant")
                    logger.info(f"[ask_user] Timeout #{reminders_sent}, reminder sent")
                except Exception as e:
                    logger.warning(f"[ask_user] Failed to send reminder: {e}")
            else:
                # 追问次数用尽，返回 None
                logger.info(
                    f"[ask_user] Final timeout after {reminders_sent} reminder(s), "
                    f"total wait ~{timeout_seconds * (max_reminders + 1)}s"
                )
                return None

        return None

    # ==================== Checkpoint / Rollback ====================

    def _save_checkpoint(
        self,
        messages: list[dict],
        state: TaskState,
        decision: Decision,
        iteration: int,
    ) -> None:
        """
        在关键决策点保存检查点。

        仅在工具调用决策时保存（纯文本响应不需要回滚）。
        保留最近 MAX_CHECKPOINTS 个检查点以控制内存。
        """
        tool_names = [tc.get("name", "") for tc in decision.tool_calls]
        summary = f"iteration={iteration}, tools=[{', '.join(tool_names)}]"

        cp = Checkpoint(
            id=str(uuid.uuid4())[:8],
            messages_snapshot=copy.deepcopy(messages),
            state_snapshot={
                "iteration": state.iteration,
                "status": state.status.value,
                "executed_tools": list(state.tools_executed),
            },
            decision_summary=summary,
            iteration=iteration,
            tool_names=tool_names,
        )
        self._checkpoints.append(cp)

        # 保留最近 N 个
        if len(self._checkpoints) > self.MAX_CHECKPOINTS:
            self._checkpoints = self._checkpoints[-self.MAX_CHECKPOINTS:]

        logger.debug(f"[Checkpoint] Saved: {cp.id} at iteration {iteration}")

    def _record_tool_result(self, tool_name: str, success: bool) -> None:
        """记录工具执行结果，用于连续失败检测。"""
        if success:
            self._tool_failure_counter[tool_name] = 0
            # 成功时也重置持久计数器
            self._persistent_tool_failures.pop(tool_name, None)
        else:
            self._tool_failure_counter[tool_name] = (
                self._tool_failure_counter.get(tool_name, 0) + 1
            )
            self._persistent_tool_failures[tool_name] = (
                self._persistent_tool_failures.get(tool_name, 0) + 1
            )

    def _should_rollback(self, tool_results: list[dict]) -> tuple[bool, str]:
        """
        检查是否应该触发回滚。

        触发条件:
        1. 同一工具连续失败 >= CONSECUTIVE_FAIL_THRESHOLD 次
        2. 整批工具全部失败

        Returns:
            (should_rollback, reason)
        """
        if not self._checkpoints:
            return False, ""

        # 检查本批次工具执行结果
        batch_failures = []
        for result in tool_results:
            content = ""
            # 主信号: tool_result 的结构化 is_error 标志
            is_error_flag = False
            if isinstance(result, dict):
                content = str(result.get("content", ""))
                is_error_flag = result.get("is_error", False)
            elif isinstance(result, str):
                content = result

            # 兜底: 字符串标记匹配（handler 返回的错误字符串）
            has_error = is_error_flag or any(marker in content for marker in [
                "❌", "⚠️ 工具执行错误", "错误类型:", "ToolError", "⚠️ 策略拒绝:",
            ])
            has_success = any(marker in content for marker in [
                "✅", '"status": "delivered"', '"ok": true',
            ])

            # 部分成功（如 deliver_artifacts 2张图发了1张）不算失败，
            # 避免回滚已经发出的不可撤回内容
            is_failed = has_error and not has_success
            batch_failures.append(is_failed)

        # 整批全部失败
        if batch_failures and all(batch_failures):
            return True, "本轮所有工具调用均失败"

        # 单工具连续失败
        for tool_name, count in self._tool_failure_counter.items():
            if count >= self.CONSECUTIVE_FAIL_THRESHOLD:
                return True, f"工具 '{tool_name}' 连续失败 {count} 次"

        return False, ""

    def _rollback(self, reason: str) -> tuple[list[dict], int] | None:
        """
        执行回滚: 恢复到上一个检查点。

        在恢复的消息历史末尾附加失败经验提示，
        帮助 LLM 避免重蹈覆辙。

        Returns:
            (restored_messages, checkpoint_iteration) or None if no checkpoints
        """
        if not self._checkpoints:
            return None

        # 弹出最近的检查点（避免回滚到同一个点）
        cp = self._checkpoints.pop()
        restored_messages = copy.deepcopy(cp.messages_snapshot)

        # 附加失败经验
        failure_hint = (
            f"[系统提示] 之前的方案失败了（原因: {reason}）。"
            f"失败的决策: {cp.decision_summary}。"
            f"请尝试完全不同的方法来完成任务。"
            f"避免使用与之前相同的工具参数组合。"
            f"如果是因为工具参数被 API 截断（如 write_file 内容过长），"
            f"请将内容拆分为多次小写入。"
        )
        restored_messages.append({
            "role": "user",
            "content": failure_hint,
        })

        # 重置失败计数器
        self._tool_failure_counter.clear()

        logger.info(
            f"[Rollback] Rolled back to checkpoint {cp.id} "
            f"(iteration {cp.iteration}). Reason: {reason}"
        )

        return restored_messages, cp.iteration

    async def run(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system_prompt: str = "",
        base_system_prompt: str = "",
        task_description: str = "",
        task_monitor: Any = None,
        session_type: str = "cli",
        interrupt_check_fn: Any = None,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        progress_callback: Any = None,
        agent_profile_id: str = "default",
        endpoint_override: str | None = None,
        force_tool_retries: int | None = None,
        is_sub_agent: bool = False,
        mode: str = "agent",
    ) -> str:
        """
        主推理循环: Reason -> Act -> Observe。

        Args:
            messages: 初始消息列表
            tools: 工具定义列表
            system_prompt: 系统提示词
            base_system_prompt: 基础系统提示词（不含动态 Plan）
            task_description: 任务描述
            task_monitor: 任务监控器
            session_type: 会话类型
            interrupt_check_fn: 中断检查函数
            conversation_id: 对话 ID
            thinking_mode: 思考模式覆盖 ('auto'/'on'/'off'/None)
            thinking_depth: 思考深度 ('low'/'medium'/'high'/None)
            progress_callback: 进度回调 async fn(str) -> None，用于 IM 实时输出思维链
            endpoint_override: 端点覆盖（来自 Agent profile 或 API 请求）
            force_tool_retries: Intent-driven override for max ForceToolCall retries
                (None = use default from settings, 0 = disable ForceToolCall)

        Returns:
            最终响应文本
        """
        self._last_exit_reason = "normal"
        self._last_react_trace = []
        self._last_delivery_receipts: list[dict] = []
        self._supervisor.reset()
        self._budget = create_budget_from_settings()
        self._budget.start()
        _session_key = conversation_id or ""
        state = self._state.get_task_for_session(_session_key) if _session_key else self._state.current_task

        if not state or not state.is_active:
            state = self._state.begin_task(session_id=_session_key)
        elif state.status == TaskStatus.ACTING:
            logger.warning(
                f"[State] Previous task stuck in {state.status.value}, force resetting for new message"
            )
            state = self._state.begin_task(session_id=_session_key)

        if state.cancelled:
            logger.error(
                f"[State] CRITICAL: fresh task {state.task_id[:8]} has cancelled=True, "
                f"reason={state.cancel_reason!r}. Force clearing."
            )
            state.cancelled = False
            state.cancel_reason = ""
            state.cancel_event = asyncio.Event()

        self._context_manager.set_cancel_event(state.cancel_event)

        tracer = get_tracer()
        tracer.begin_trace(session_id=state.session_id, metadata={
            "task_description": task_description[:200] if task_description else "",
            "session_type": session_type,
            "model": self._brain.model,
        })

        max_iterations = settings.max_iterations
        self._empty_content_retries = 0

        # 进度回调辅助（安全调用，忽略异常）
        async def _emit_progress(text: str) -> None:
            if progress_callback and text:
                try:
                    await progress_callback(text)
                except Exception:
                    pass

        # 保存原始用户消息（用于模型切换时重置上下文）
        state.original_user_messages = [
            msg for msg in messages if self._is_human_user_message(msg)
        ]

        working_messages = list(messages)
        current_model = self._brain.model

        # === 端点覆盖 ===
        if endpoint_override:
            if not conversation_id:
                conversation_id = f"_run_{uuid.uuid4().hex[:12]}"
            llm_client = getattr(self._brain, "_llm_client", None)
            if llm_client and hasattr(llm_client, "switch_model"):
                ok, msg = llm_client.switch_model(
                    endpoint_name=endpoint_override,
                    hours=0.05,
                    reason=f"agent profile endpoint override: {endpoint_override}",
                    conversation_id=conversation_id,
                )
                if ok:
                    _provider = llm_client._providers.get(endpoint_override)
                    if _provider:
                        current_model = _provider.model
                    logger.info(f"[EndpointOverride] Switched to {endpoint_override} for {conversation_id}")
                else:
                    logger.warning(f"[EndpointOverride] Failed to switch to {endpoint_override}: {msg}, using default")

        # ForceToolCall 配置
        im_floor = max(0, int(getattr(settings, "force_tool_call_im_floor", 1)))
        configured = int(
            getattr(self, "_force_tool_override", None)
            or getattr(settings, "force_tool_call_max_retries", 0)
        )
        if session_type == "im":
            base_force_retries = max(im_floor, configured)
        else:
            base_force_retries = max(0, configured)

        max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)

        # Intent-driven override (from IntentAnalyzer)
        if force_tool_retries is not None:
            max_no_tool_retries = force_tool_retries
            logger.info(f"[ForceToolCall] Intent override: max_retries={force_tool_retries}")

        max_verify_retries = 1
        max_confirmation_text_retries = max(0, int(getattr(settings, "confirmation_text_max_retries", 1)))

        # 追踪变量
        executed_tool_names: list[str] = []
        delivery_receipts: list[dict] = []
        _last_browser_url = ""

        # 循环计数器
        consecutive_tool_rounds = 0
        no_tool_call_count = 0
        verify_incomplete_count = 0
        no_confirmation_text_count = 0
        tools_executed_in_task = False
        _supervisor_intervened = False
        _tool_call_counter: dict[str, int] = {}
        _MAX_SAME_TOOL_PER_TASK = 5

        def _build_effective_system_prompt() -> str:
            """动态追加活跃 Plan"""
            try:
                from ..tools.handlers.plan import get_active_todo_prompt
                _cid = conversation_id
                prompt = base_system_prompt or system_prompt
                if _cid:
                    plan_section = get_active_todo_prompt(_cid)
                    if plan_section:
                        prompt += f"\n\n{plan_section}\n"
                return prompt
            except Exception:
                return base_system_prompt or system_prompt

        def _make_tool_signature(tc: dict) -> str:
            """生成工具签名"""
            nonlocal _last_browser_url
            name = tc.get("name", "")
            inp = tc.get("input", {})

            if name == "browser_navigate":
                _last_browser_url = inp.get("url", "")

            try:
                param_str = json.dumps(inp, sort_keys=True, ensure_ascii=False)
            except Exception:
                param_str = str(inp)

            if name in self._browser_page_read_tools and len(param_str) <= 20 and _last_browser_url:
                param_str = f"{param_str}|url={_last_browser_url}"

            param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
            return f"{name}({param_hash})"

        # Mode-based tool filtering (same as reason_stream)
        tools = _filter_tools_by_mode(tools, mode)
        _allowed_tool_names = {t.get("name", "") for t in tools} if mode != "agent" else None
        self._tool_executor._current_mode = mode

        # ==================== 主循环 ====================
        logger.info(f"[ReAct] === Loop started (max_iterations={max_iterations}, model={current_model}) ===")

        react_trace: list[dict] = []
        _trace_started_at = datetime.now().isoformat()

        for iteration in range(max_iterations):
            self._last_working_messages = working_messages
            state.iteration = iteration

            # 检查取消
            if state.cancelled:
                logger.info(f"[ReAct] Task cancelled at iteration start: {state.cancel_reason}")
                self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration})
                return await self._cancel_farewell(
                    working_messages, _build_effective_system_prompt(), current_model, state
                )

            # Resource Budget 检查
            self._budget.record_iteration()
            budget_status = self._budget.check()
            if budget_status.action == BudgetAction.PAUSE:
                logger.warning(f"[Budget] PAUSE: {budget_status.message}")
                self._save_react_trace(react_trace, conversation_id, session_type, "budget_exceeded", _trace_started_at)
                tracer.end_trace(metadata={
                    "result": "budget_exceeded",
                    "iterations": iteration,
                    "budget_dimension": budget_status.dimension,
                })
                self._run_failure_analysis(
                    react_trace, "budget_exceeded",
                    task_description=task_description,
                    task_id=state.task_id,
                )
                return (
                    f"⚠️ 任务资源预算已用尽（{budget_status.dimension}: "
                    f"{budget_status.usage_ratio:.0%}），任务暂停。\n"
                    f"已完成的工作进度已保存，请调整预算后继续。"
                )
            elif budget_status.action in (BudgetAction.WARNING, BudgetAction.DOWNGRADE):
                logger.info("[Budget] %s: %s — logged only, not injected",
                            budget_status.dimension, budget_status.message)

            # 任务监控
            if task_monitor:
                task_monitor.begin_iteration(iteration + 1, current_model)
                # 模型切换检查
                switch_result = self._check_model_switch(
                    task_monitor, state, working_messages, current_model
                )
                if switch_result:
                    current_model, working_messages = switch_result
                    no_tool_call_count = 0
                    tools_executed_in_task = False
                    _supervisor_intervened = False
                    verify_incomplete_count = 0
                    executed_tool_names = []
                    consecutive_tool_rounds = 0
                    no_confirmation_text_count = 0

            _ctx_compressed_info: dict | None = None
            if len(working_messages) > 2:
                _before_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                try:
                    working_messages = await self._context_manager.compress_if_needed(
                        working_messages,
                        system_prompt=_build_effective_system_prompt(),
                        tools=tools,
                        memory_manager=self._memory_manager,
                        conversation_id=conversation_id,
                    )
                except _CtxCancelledError:
                    # 仅当任务状态明确为“用户取消”时，才把压缩取消升级为任务取消。
                    # 否则按压缩失败降级处理，避免误报 "Context compression cancelled by user"。
                    if state.cancelled or bool((state.cancel_reason or "").strip()):
                        raise UserCancelledError(
                            reason=state.cancel_reason or "用户请求停止",
                            source="context_compress",
                        )
                    logger.warning(
                        "[ReAct] Context compression cancelled without task cancellation "
                        "(session=%s). Fallback to uncompressed context.",
                        conversation_id or state.session_id,
                    )
                    state.cancel_event = asyncio.Event()
                    self._context_manager.set_cancel_event(state.cancel_event)
                _after_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                if _after_tokens < _before_tokens:
                    # Context Rewriting: 压缩后注入方向提示
                    _plan_sec = ""
                    try:
                        from ..tools.handlers.plan import get_active_todo_prompt
                        if conversation_id:
                            _plan_sec = get_active_todo_prompt(conversation_id) or ""
                    except Exception:
                        pass
                    _scratchpad = ""
                    if self._memory_manager:
                        try:
                            _sp = getattr(self._memory_manager, "get_scratchpad_summary", None)
                            if _sp:
                                _scratchpad = _sp() or ""
                        except Exception:
                            pass
                    working_messages = ContextManager.rewrite_after_compression(
                        working_messages,
                        plan_section=_plan_sec,
                        scratchpad_summary=_scratchpad,
                        completed_tools=executed_tool_names,
                        task_description=task_description,
                    )

                    _ctx_compressed_info = {
                        "before_tokens": _before_tokens,
                        "after_tokens": _after_tokens,
                    }
                    await _emit_progress(
                        f"📦 上下文压缩: {_before_tokens//1000}k → {_after_tokens//1000}k tokens"
                    )
                    logger.info(
                        f"[ReAct] Context compressed: {_before_tokens} → {_after_tokens} tokens"
                    )

            # ==================== REASON 阶段 ====================
            if state.cancelled:
                self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                return await self._cancel_farewell(
                    working_messages, _build_effective_system_prompt(), current_model, state
                )
            logger.info(f"[ReAct] Iter {iteration+1}/{max_iterations} — REASON (model={current_model})")
            await broadcast_event("pet-status-update", {"status": "thinking"})
            if state.status != TaskStatus.REASONING:
                try:
                    state.transition(TaskStatus.REASONING)
                except ValueError:
                    pass

            _thinking_t0 = time.time()  # 思维链: 记录 thinking 开始时间
            try:
                decision = await self._reason(
                    working_messages,
                    system_prompt=_build_effective_system_prompt(),
                    tools=tools,
                    current_model=current_model,
                    conversation_id=conversation_id,
                    thinking_mode=thinking_mode,
                    thinking_depth=thinking_depth,
                    iteration=iteration,
                    agent_profile_id=agent_profile_id,
                    cancel_event=state.cancel_event,
                )

                if task_monitor:
                    task_monitor.reset_retry_count()

            except UserCancelledError:
                raise
            except Exception as e:
                logger.error(f"[LLM] Brain call failed: {e}")
                retry_result = self._handle_llm_error(
                    e, task_monitor, state, working_messages, current_model
                )
                if retry_result == "retry":
                    _total_r = getattr(state, '_total_llm_retries', 1)
                    _retry_sleep = min(2 * _total_r, 15)
                    _sleep = asyncio.create_task(asyncio.sleep(_retry_sleep))
                    _cw = asyncio.create_task(state.cancel_event.wait())
                    _done, _pend = await asyncio.wait({_sleep, _cw}, return_when=asyncio.FIRST_COMPLETED)
                    for _t in _pend:
                        _t.cancel()
                        try:
                            await _t
                        except (asyncio.CancelledError, Exception):
                            pass
                    if _cw in _done:
                        raise UserCancelledError(reason=state.cancel_reason or "用户请求停止", source="retry_sleep")
                    continue
                elif isinstance(retry_result, tuple):
                    current_model, working_messages = retry_result
                    no_tool_call_count = 0
                    tools_executed_in_task = False
                    _supervisor_intervened = False
                    verify_incomplete_count = 0
                    executed_tool_names = []
                    consecutive_tool_rounds = 0
                    no_confirmation_text_count = 0
                    continue
                else:
                    await broadcast_event("pet-status-update", {"status": "error"})
                    raise

            _thinking_duration_ms = int((time.time() - _thinking_t0) * 1000)

            # === IM 进度: thinking 内容 ===
            if decision.thinking_content:
                _think_preview = decision.thinking_content[:200].strip().replace("\n", " ")
                if len(decision.thinking_content) > 200:
                    _think_preview += "..."
                await _emit_progress(f"💭 {_think_preview}")

            # === IM 进度: LLM 推理意图 ===
            _decision_text_run = (decision.text_content or "").strip().replace("\n", " ")
            if _decision_text_run and decision.type == DecisionType.TOOL_CALLS:
                _stripped = _decision_text_run.lstrip()
                _looks_like_json = _stripped[:1] in ("{", "[") or "```" in _stripped[:50]
                if not _looks_like_json:
                    _text_preview = _decision_text_run[:300]
                    if len(_decision_text_run) > 300:
                        _text_preview += "..."
                    await _emit_progress(_text_preview)

            if task_monitor:
                task_monitor.end_iteration(decision.text_content or "")

            # -- 收集 ReAct trace 数据 --
            # token 信息从 raw_response.usage 提取（Decision 本身不携带 token）
            _raw = decision.raw_response
            _usage = getattr(_raw, "usage", None) if _raw else None
            _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
            _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0

            # Resource Budget: 记录 token 消耗
            if _in_tokens or _out_tokens:
                self._budget.record_tokens(_in_tokens, _out_tokens)
            _iter_trace: dict = {
                "iteration": iteration + 1,
                "timestamp": datetime.now().isoformat(),
                "decision_type": decision.type.value if hasattr(decision.type, "value") else str(decision.type),
                "model": current_model,
                "thinking": decision.thinking_content,
                "thinking_duration_ms": _thinking_duration_ms,
                "text": decision.text_content,
                "tool_calls": [
                    {
                        "name": tc.get("name"),
                        "id": tc.get("id"),
                        "input": tc.get("input", {}),
                    }
                    for tc in (decision.tool_calls or [])
                ],
                "tool_results": [],  # 将在工具执行后填充
                "tokens": {
                    "input": _in_tokens,
                    "output": _out_tokens,
                },
                "context_compressed": _ctx_compressed_info,
            }
            tool_names_for_log = [tc.get("name", "?") for tc in (decision.tool_calls or [])]
            logger.info(
                f"[ReAct] Iter {iteration+1} — decision={_iter_trace['decision_type']}, "
                f"tools={tool_names_for_log}, "
                f"tokens_in={_in_tokens}, tokens_out={_out_tokens}"
            )

            # ==================== stop_reason=max_tokens 检测 ====================
            # 当 LLM 输出被 max_tokens 限制截断时，工具调用的 JSON 可能不完整。
            # 检测此情况并记录明确警告，帮助排查。
            if decision.stop_reason == "max_tokens":
                logger.warning(
                    f"[ReAct] Iter {iteration+1} — ⚠️ LLM output truncated (stop_reason=max_tokens). "
                    f"The response hit the max_tokens limit ({self._brain.max_tokens}). "
                    f"Tool calls may have incomplete JSON arguments. "
                    f"Consider increasing endpoint max_tokens or reducing tool argument size."
                )
                _iter_trace["truncated"] = True

                # 自动扩容 max_tokens 并重试被完全截断的工具调用
                if decision.type == DecisionType.TOOL_CALLS:
                    truncated_calls = [
                        tc for tc in decision.tool_calls
                        if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                    ]
                    _current_max = self._brain.max_tokens or 16384
                    _max_ceiling = min(_current_max * 3, 65536)
                    if truncated_calls and len(truncated_calls) == len(decision.tool_calls):
                        _new_max = min(_current_max * 2, _max_ceiling)
                        if _new_max > _current_max:
                            logger.warning(
                                f"[ReAct] Iter {iteration+1} — All {len(truncated_calls)} tool "
                                f"calls truncated. Auto-increasing max_tokens: "
                                f"{_current_max} → {_new_max} and retrying"
                            )
                            self._brain.max_tokens = _new_max
                            react_trace.append(_iter_trace)
                            continue
                    elif truncated_calls:
                        _new_max = min(int(_current_max * 1.5), _max_ceiling)
                        if _new_max > _current_max:
                            logger.warning(
                                f"[ReAct] Iter {iteration+1} — "
                                f"{len(truncated_calls)}/{len(decision.tool_calls)} tool calls "
                                f"truncated. Increasing max_tokens for next iteration: "
                                f"{_current_max} → {_new_max}"
                            )
                            self._brain.max_tokens = _new_max

            # ==================== 决策分支 ====================

            if decision.type == DecisionType.FINAL_ANSWER:
                # 纯文本响应 - 处理完成度验证
                logger.info(f"[ReAct] Iter {iteration+1} — FINAL_ANSWER: \"{(decision.text_content or '').replace(chr(10), ' ')}\"")
                consecutive_tool_rounds = 0

                result = await self._handle_final_answer(
                    decision=decision,
                    working_messages=working_messages,
                    original_messages=messages,
                    tools_executed_in_task=tools_executed_in_task,
                    executed_tool_names=executed_tool_names,
                    delivery_receipts=delivery_receipts,
                    no_tool_call_count=no_tool_call_count,
                    verify_incomplete_count=verify_incomplete_count,
                    no_confirmation_text_count=no_confirmation_text_count,
                    max_no_tool_retries=max_no_tool_retries,
                    max_verify_retries=max_verify_retries,
                    max_confirmation_text_retries=max_confirmation_text_retries,
                    base_force_retries=base_force_retries,
                    conversation_id=conversation_id,
                    supervisor_intervened=_supervisor_intervened,
                )

                if isinstance(result, str):
                    react_trace.append(_iter_trace)
                    logger.info(
                        f"[ReAct] === COMPLETED after {iteration+1} iterations, "
                        f"tools: {list(set(executed_tool_names))} ==="
                    )
                    self._save_react_trace(react_trace, conversation_id, session_type, "completed", _trace_started_at)
                    try:
                        state.transition(TaskStatus.COMPLETED)
                    except ValueError:
                        pass
                    tracer.end_trace(metadata={
                        "result": "completed",
                        "iterations": iteration + 1,
                        "tools_used": list(set(executed_tool_names)),
                    })
                    await broadcast_event("pet-status-update", {"status": "success"})
                    return result
                else:
                    # 需要继续循环（验证不通过）
                    await _emit_progress("🔄 任务尚未完成，继续处理...")
                    logger.info(f"[ReAct] Iter {iteration+1} — VERIFY: incomplete, continuing loop")
                    react_trace.append(_iter_trace)
                    try:
                        state.transition(TaskStatus.VERIFYING)
                    except ValueError:
                        pass
                    (
                        working_messages,
                        no_tool_call_count,
                        verify_incomplete_count,
                        no_confirmation_text_count,
                        max_no_tool_retries,
                    ) = result
                    continue

            elif decision.type == DecisionType.TOOL_CALLS:
                # ==================== ACT 阶段 ====================

                # Runtime mode guard: block tools not in the filtered set (defense-in-depth)
                _mode_blocked_results: list[dict] = []
                if _allowed_tool_names is not None:
                    _guarded_calls = []
                    for tc in decision.tool_calls:
                        _tc_name = tc.get("name", "")
                        _tc_id = tc.get("id", "")
                        _tc_input = tc.get("input", tc.get("arguments", {}))
                        _block_reason = _should_block_tool(
                            _tc_name, _tc_input, _allowed_tool_names, mode
                        )
                        if _block_reason:
                            logger.warning(
                                f"[ModeGuard] Blocked '{_tc_name}' in {mode} mode"
                            )
                            _mode_blocked_results.append({
                                "type": "tool_result",
                                "tool_use_id": _tc_id,
                                "content": _block_reason,
                                "is_error": True,
                            })
                        else:
                            _guarded_calls.append(tc)
                    if not _guarded_calls:
                        working_messages.append({
                            "role": "assistant",
                            "content": decision.assistant_content,
                            "reasoning_content": decision.thinking_content or None,
                        })
                        working_messages.append({
                            "role": "user",
                            "content": _mode_blocked_results,
                        })
                        continue
                    decision.tool_calls = _guarded_calls

                tool_names = [tc.get("name", "?") for tc in decision.tool_calls]
                logger.info(f"[ReAct] Iter {iteration+1} — ACT: {tool_names}")
                await broadcast_event("pet-status-update", {"status": "tool_execution", "tool_name": ", ".join(tool_names)})
                try:
                    state.transition(TaskStatus.ACTING)
                except ValueError:
                    pass

                # ---- ask_user 拦截 ----
                # 如果 LLM 调用了 ask_user，立即中断循环，将问题返回给用户
                ask_user_calls = [tc for tc in decision.tool_calls if tc.get("name") == "ask_user"]
                other_calls = [tc for tc in decision.tool_calls if tc.get("name") != "ask_user"]

                if ask_user_calls:
                    logger.info(
                        f"[ReAct] Iter {iteration+1} — ask_user intercepted, "
                        f"pausing for user input (other_tools={[tc.get('name') for tc in other_calls]})"
                    )

                    # 添加 assistant 消息（保留完整的 tool_use 内容用于上下文连贯）
                    working_messages.append({
                        "role": "assistant",
                        "content": decision.assistant_content,
                        "reasoning_content": decision.thinking_content or None,
                    })

                    # 如果同时还有其他工具调用，先执行它们
                    # 收集其他工具的 tool_result（Claude API 要求每个 tool_use 都有对应 tool_result）
                    other_tool_results: list[dict] = []
                    if other_calls:
                        other_results, other_executed, other_receipts = (
                            await self._tool_executor.execute_batch(
                                other_calls,
                                state=state,
                                task_monitor=task_monitor,
                                allow_interrupt_checks=self._state.interrupt_enabled,
                                capture_delivery_receipts=True,
                            )
                        )
                        if other_executed:
                            if any(t not in _ADMIN_TOOL_NAMES for t in other_executed):
                                tools_executed_in_task = True
                            executed_tool_names.extend(other_executed)
                            state.record_tool_execution(other_executed)
                        if other_receipts:
                            delivery_receipts = other_receipts
                            self._last_delivery_receipts = other_receipts
                        # 保留其他工具的 tool_result 内容
                        other_tool_results = other_results if other_results else []
                    if _mode_blocked_results:
                        other_tool_results.extend(_mode_blocked_results)

                    # 提取 ask_user 的问题文本（兼容 input/arguments + JSON 字符串参数）
                    ask_raw = ask_user_calls[0].get("input")
                    if not ask_raw:
                        ask_raw = ask_user_calls[0].get("arguments", {})
                    ask_input = ask_raw
                    if isinstance(ask_input, str):
                        try:
                            ask_input = json.loads(ask_input)
                        except Exception:
                            ask_input = {}
                    if not isinstance(ask_input, dict):
                        ask_input = {}
                    question = ask_input.get("question", "")
                    ask_tool_id = ask_user_calls[0].get("id", "ask_user_0")

                    # 合并 LLM 的文本回复 + 问题
                    text_part = strip_thinking_tags(decision.text_content or "").strip()
                    if text_part and question:
                        final_text = f"{text_part}\n\n{question}"
                    elif question:
                        final_text = question
                    else:
                        final_text = text_part or "（等待用户回复）"

                    # IM 通道：将结构化选项追加到问题文本
                    ask_opts = ask_input.get("options", [])
                    if ask_opts and isinstance(ask_opts, list):
                        opt_lines = []
                        for o in ask_opts:
                            if isinstance(o, dict) and o.get("id") and o.get("label"):
                                opt_lines.append(f"  {o['id']}: {o['label']}")
                        if opt_lines:
                            final_text += "\n\n选项：\n" + "\n".join(opt_lines)

                    try:
                        state.transition(TaskStatus.WAITING_USER)
                    except ValueError:
                        pass

                    await broadcast_event("pet-status-update", {"status": "idle"})

                    # ---- IM 模式：等待用户回复（超时 + 追问） ----
                    user_reply = await self._wait_for_user_reply(
                        final_text, state, timeout_seconds=60, max_reminders=1,
                    )

                    # 构建 tool_result 消息（其他工具结果 + ask_user 结果必须在同一条 user 消息中）
                    def _build_ask_user_tool_results(
                        ask_user_content: str,
                        _other_results: list[dict] = other_tool_results,
                        _ask_id: str = ask_tool_id,
                    ) -> list[dict]:
                        """构建包含所有 tool_result 的 user 消息 content"""
                        results = list(_other_results)  # 其他工具的 tool_result
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": _ask_id,
                            "content": ask_user_content,
                        })
                        return results

                    if user_reply:
                        # 用户在超时内回复了 → 注入回复，继续 ReAct 循环
                        logger.info(
                            f"[ReAct] Iter {iteration+1} — ask_user: user replied, resuming loop"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append({
                            "role": "user",
                            "content": _build_ask_user_tool_results(f"用户回复：{user_reply}"),
                        })
                        try:
                            state.transition(TaskStatus.REASONING)
                        except ValueError:
                            pass
                        continue  # 继续 ReAct 循环

                    elif user_reply is None and self._state.current_session and (
                        self._state.current_session.get_metadata("_gateway")
                        if hasattr(self._state.current_session, "get_metadata")
                        else None
                    ):
                        # IM 模式，用户超时未回复 → 注入系统提示让 LLM 自行决策
                        logger.info(
                            f"[ReAct] Iter {iteration+1} — ask_user: user timeout, "
                            f"injecting auto-decide prompt"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append({
                            "role": "user",
                            "content": _build_ask_user_tool_results(
                                "[系统] 用户 2 分钟内未回复你的提问。"
                                "请自行决策：如果能合理推断用户意图，继续执行任务；"
                                "否则终止当前任务并告知用户你需要什么信息。"
                            ),
                        })
                        try:
                            state.transition(TaskStatus.REASONING)
                        except ValueError:
                            pass
                        continue  # 继续 ReAct 循环，让 LLM 自行决策

                    else:
                        # CLI 模式或无 gateway → 直接返回问题文本
                        tracer.end_trace(metadata={
                            "result": "waiting_user",
                            "iterations": iteration + 1,
                            "tools_used": list(set(executed_tool_names)),
                        })
                        react_trace.append(_iter_trace)
                        self._save_react_trace(react_trace, conversation_id, session_type, "waiting_user", _trace_started_at)
                        self._last_exit_reason = "ask_user"
                        logger.info(
                            f"[ReAct] === WAITING_USER (CLI) after {iteration+1} iterations ==="
                        )
                        return final_text

                # 保存检查点（在工具执行前）
                self._save_checkpoint(working_messages, state, decision, iteration)

                # 添加 assistant 消息
                working_messages.append({
                    "role": "assistant",
                    "content": decision.assistant_content,
                    "reasoning_content": decision.thinking_content or None,
                })

                # 检查取消
                if state.cancelled:
                    react_trace.append(_iter_trace)
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )

                # === IM 进度: 描述即将执行的工具 ===
                for tc in (decision.tool_calls or []):
                    _tc_name = tc.get("name", "unknown")
                    _tc_args = tc.get("input", tc.get("arguments", {}))
                    await _emit_progress(f"🔧 {self._describe_tool_call(_tc_name, _tc_args)}")

                # 同名工具频率限制：超阈值的调用跳过执行，返回提示
                _rate_limited_results: list[dict] = []
                _calls_to_execute = []
                for tc in (decision.tool_calls or []):
                    _tc_name = tc.get("name", "")
                    _tool_call_counter[_tc_name] = _tool_call_counter.get(_tc_name, 0) + 1
                    if _tool_call_counter[_tc_name] > _MAX_SAME_TOOL_PER_TASK:
                        logger.warning(
                            f"[RateLimit] Tool '{_tc_name}' called "
                            f"{_tool_call_counter[_tc_name]} times (limit={_MAX_SAME_TOOL_PER_TASK}), "
                            f"skipping execution"
                        )
                        _rate_limited_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.get("id", ""),
                            "content": (
                                f"[系统] 工具 {_tc_name} 已在本任务中调用 "
                                f"{_tool_call_counter[_tc_name] - 1} 次，已达上限。"
                                f"请整合操作或继续下一步。"
                            ),
                        })
                    else:
                        _calls_to_execute.append(tc)
                decision.tool_calls = _calls_to_execute

                # 执行工具
                tool_results, executed, receipts = await self._tool_executor.execute_batch(
                    decision.tool_calls,
                    state=state,
                    task_monitor=task_monitor,
                    allow_interrupt_checks=self._state.interrupt_enabled,
                    capture_delivery_receipts=True,
                )
                if _rate_limited_results:
                    tool_results.extend(_rate_limited_results)

                if executed:
                    if any(t not in _ADMIN_TOOL_NAMES for t in executed):
                        tools_executed_in_task = True
                    executed_tool_names.extend(executed)
                    state.record_tool_execution(executed)
                    self._budget.record_tool_calls(len(executed))

                if self._plugin_hooks and tool_results:
                    try:
                        await self._plugin_hooks.dispatch(
                            "on_tool_result",
                            tool_calls=decision.tool_calls,
                            tool_results=tool_results,
                            executed=executed,
                        )
                    except Exception as _hook_err:
                        logger.debug(f"on_tool_result hook error: {_hook_err}")

                # 记录工具成功/失败状态 + IM 进度
                # 使用 decision.tool_calls / tool_results 对齐遍历，
                # 避免 executed（仅含成功名）与 tool_results 长度不一致
                for i, tc in enumerate(decision.tool_calls):
                    _tc_name = tc.get("name", "")
                    result_content = ""
                    is_error = False
                    if i < len(tool_results):
                        r = tool_results[i]
                        result_content = str(r.get("content", "")) if isinstance(r, dict) else str(r)
                        # 主信号: tool_result 的结构化 is_error 标志
                        is_error = r.get("is_error", False) if isinstance(r, dict) else False
                    # 兜底: 字符串标记匹配（handler 返回的错误字符串）
                    if not is_error and result_content:
                        is_error = any(m in result_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"])
                    self._record_tool_result(_tc_name, success=not is_error)
                    _r_summary = self._summarize_tool_result(_tc_name, result_content)
                    if _r_summary:
                        _icon = "❌" if is_error else "✅"
                        await _emit_progress(f"{_icon} {_r_summary}")

                if receipts:
                    delivery_receipts = receipts
                    self._last_delivery_receipts = receipts

                if _mode_blocked_results:
                    tool_results.extend(_mode_blocked_results)

                # exit_plan_mode: stop the loop in non-streaming path too
                if "exit_plan_mode" in (executed or []):
                    logger.info(
                        "[ReAct] exit_plan_mode called — ending turn, "
                        "waiting for user review"
                    )
                    working_messages.append({"role": "user", "content": tool_results})
                    react_trace.append(_iter_trace)
                    self._save_react_trace(
                        react_trace, conversation_id, session_type,
                        "plan_exit", _trace_started_at,
                    )
                    return (
                        "Plan completed and waiting for user review. "
                        "The user can approve the plan to switch to Agent mode, "
                        "or request changes to continue refining."
                    )

                # ==================== OBSERVE 阶段 ====================
                logger.info(
                    f"[ReAct] Iter {iteration+1} — OBSERVE: "
                    f"{len(tool_results)} results from {executed or []}"
                )
                if state.cancelled:
                    working_messages.append({"role": "user", "content": tool_results})
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )
                try:
                    state.transition(TaskStatus.OBSERVING)
                except ValueError:
                    pass

                # 收集工具结果到 trace（保存完整内容，不截断）
                _iter_trace["tool_results"] = [
                    {
                        "tool_use_id": tr.get("tool_use_id", ""),
                        "result_content": str(tr.get("content", "")),
                    }
                    for tr in tool_results
                    if isinstance(tr, dict)
                ]
                for tr in tool_results:
                    if isinstance(tr, dict):
                        t_id = tr.get("tool_use_id", "")
                        r_len = len(str(tr.get("content", "")))
                        logger.info(f"[ReAct] Iter {iteration+1} — tool_result id={t_id} len={r_len}")
                react_trace.append(_iter_trace)

                # 持久性失败检测：跨 rollback 累计同一工具失败达上限时，
                # 注入强制策略切换提示而非继续回滚（防止截断导致的无限循环）
                _persistent_exceeded = {
                    name: count for name, count in self._persistent_tool_failures.items()
                    if count >= self.PERSISTENT_FAIL_LIMIT
                }
                if _persistent_exceeded:
                    _tool_names = ", ".join(_persistent_exceeded.keys())
                    _hint = (
                        f"[系统提示] 工具 {_tool_names} 累计失败已达 {self.PERSISTENT_FAIL_LIMIT} 次"
                        f"（含跨回滚），通常是因为参数过长被 API 截断。"
                        "你必须改用完全不同的策略：\n"
                        "- 使用 run_shell 执行 Python 脚本来生成大文件\n"
                        "- 将内容拆分成多次小写入\n"
                        "- 先写骨架，再逐步填充\n"
                        "禁止再次用同样方式调用该工具。"
                    )
                    working_messages.append({"role": "user", "content": tool_results})
                    working_messages.append({"role": "user", "content": _hint})
                    logger.warning(
                        f"[PersistentFail] {_tool_names} exceeded persistent fail limit "
                        f"({self.PERSISTENT_FAIL_LIMIT}), injecting strategy switch"
                    )
                    for name in _persistent_exceeded:
                        self._persistent_tool_failures[name] = 0
                    self._tool_failure_counter.clear()
                    continue

                # 检测截断错误（PARSE_ERROR_KEY）— 截断导致的失败不应触发回滚，
                # 因为回滚会丢弃错误反馈，导致 LLM 重复生成同样的超长内容形成死循环
                _has_truncation = any(
                    isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                    for tc in decision.tool_calls
                )
                if _has_truncation:
                    self._consecutive_truncation_count += 1
                    for tc in decision.tool_calls:
                        if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]:
                            self._tool_failure_counter.pop(tc.get("name", ""), None)
                    logger.info(
                        f"[ReAct] Iter {iteration+1} — Tool args truncated "
                        f"(count: {self._consecutive_truncation_count}), "
                        f"skipping rollback to preserve error feedback"
                    )
                else:
                    self._consecutive_truncation_count = 0

                # 检查是否应该回滚 — 截断错误不回滚
                should_rb, rb_reason = self._should_rollback(tool_results)
                if should_rb and not _has_truncation:
                    rollback_result = self._rollback(rb_reason)
                    if rollback_result:
                        working_messages, _ = rollback_result
                        logger.info("[Rollback] 回滚成功，将用不同方法重新推理")
                        continue

                if state.cancelled:
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )

                # 添加工具结果
                working_messages.append({
                    "role": "user",
                    "content": tool_results,
                })

                # 连续截断 >= 2 次：注入强制分拆指导，打破死循环
                if _has_truncation and self._consecutive_truncation_count >= 2:
                    _split_guidance = (
                        "⚠️ 你的工具调用参数因内容过长被 API 反复截断（已连续 "
                        f"{self._consecutive_truncation_count} 次）。你必须立即改变策略：\n"
                        "1. 将大文件拆分为多次 write_file 调用（每次不超过 2000 行）\n"
                        "2. 先创建文件框架，再用 edit_file 逐段补充内容\n"
                        "3. 减少内联 CSS/JS，使用简洁实现\n"
                        "4. 如果内容确实很长，考虑用 Markdown 替代 HTML"
                    )
                    working_messages.append({"role": "user", "content": _split_guidance})
                    logger.warning(
                        f"[ReAct] Injected split guidance after "
                        f"{self._consecutive_truncation_count} consecutive truncations"
                    )

                # Supervisor: 记录工具调用数据
                # 使用 decision.tool_calls 和 tool_results 按索引对齐，
                # 避免 executed（仅含成功工具名）与 tool_results 长度不一致导致错配
                for i, tc in enumerate(decision.tool_calls):
                    _tc_name = tc.get("name", "")
                    result_content = ""
                    is_error = False
                    if i < len(tool_results):
                        r = tool_results[i]
                        result_content = str(r.get("content", "")) if isinstance(r, dict) else str(r)
                        is_error = r.get("is_error", False) if isinstance(r, dict) else False
                    if not is_error and result_content:
                        is_error = any(m in result_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"])
                    self._supervisor.record_tool_call(
                        tool_name=_tc_name, params=tc.get("input", {}),
                        success=not is_error, iteration=iteration,
                    )

                # Supervisor: 记录响应文本和 token 用量
                self._supervisor.record_response(decision.text_content or "")
                if _in_tokens or _out_tokens:
                    self._supervisor.record_token_usage(_in_tokens + _out_tokens)

                # 循环检测
                consecutive_tool_rounds += 1
                self._supervisor.record_consecutive_tool_rounds(consecutive_tool_rounds)

                # stop_reason 检查
                if decision.stop_reason == "end_turn":
                    cleaned_text = strip_thinking_tags(decision.text_content)
                    _, cleaned_text = parse_intent_tag(cleaned_text)
                    if cleaned_text and cleaned_text.strip():
                        logger.info(f"[LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds")
                        self._save_react_trace(react_trace, conversation_id, session_type, "completed_end_turn", _trace_started_at)
                        try:
                            state.transition(TaskStatus.COMPLETED)
                        except ValueError:
                            pass
                        tracer.end_trace(metadata={
                            "result": "completed_end_turn",
                            "iterations": iteration + 1,
                            "tools_used": list(set(executed_tool_names)),
                        })
                        return cleaned_text

                # 工具签名循环检测 (Supervisor-based)
                round_signatures = [_make_tool_signature(tc) for tc in decision.tool_calls]
                round_sig_str = "+".join(sorted(round_signatures))
                self._supervisor.record_tool_signature(round_sig_str)

                # Supervisor 综合评估
                _has_todo = self._has_active_todo_pending(conversation_id)
                _todo_step = ""
                try:
                    from ..tools.handlers.plan import get_active_todo_prompt
                    if conversation_id:
                        _todo_step = get_active_todo_prompt(conversation_id) or ""
                except Exception:
                    pass

                intervention = self._supervisor.evaluate(
                    iteration,
                    has_active_todo=_has_todo,
                    plan_current_step=_todo_step,
                )

                if intervention:
                    _supervisor_intervened = True
                    max_no_tool_retries = 0

                    if intervention.should_terminate:
                        cleaned = strip_thinking_tags(decision.text_content)
                        self._save_react_trace(react_trace, conversation_id, session_type, "loop_terminated", _trace_started_at)
                        try:
                            state.transition(TaskStatus.FAILED)
                        except ValueError:
                            pass
                        tracer.end_trace(metadata={
                            "result": "loop_terminated",
                            "iterations": iteration + 1,
                            "supervisor_pattern": intervention.pattern.value,
                        })
                        self._run_failure_analysis(
                            react_trace, "loop_terminated",
                            task_description=task_description,
                            task_id=state.task_id,
                        )
                        return cleaned or "⚠️ 检测到工具调用陷入死循环，任务已自动终止。请重新描述您的需求。"

                    if intervention.should_rollback:
                        rollback_result = self._rollback(intervention.message)
                        if rollback_result:
                            working_messages, _ = rollback_result
                            if intervention.should_inject_prompt and intervention.prompt_injection:
                                working_messages.append({
                                    "role": "user",
                                    "content": intervention.prompt_injection,
                                })
                            logger.info(f"[Supervisor] Rollback + strategy switch: {intervention.message}")
                            continue

                    if intervention.should_inject_prompt and intervention.prompt_injection:
                        working_messages.append({
                            "role": "user",
                            "content": intervention.prompt_injection,
                        })
                        tools = []
                        max_no_tool_retries = 0
                        logger.info(
                            f"[Supervisor] NUDGE: tools stripped to force text response "
                            f"(iter={iteration}, pattern={intervention.pattern.value})"
                        )

        self._last_working_messages = working_messages
        self._save_react_trace(react_trace, conversation_id, session_type, "max_iterations", _trace_started_at)
        try:
            state.transition(TaskStatus.FAILED)
        except ValueError:
            pass
        tracer.end_trace(metadata={"result": "max_iterations", "iterations": max_iterations})
        self._run_failure_analysis(
            react_trace, "max_iterations",
            task_description=task_description,
            task_id=state.task_id,
        )
        await broadcast_event("pet-status-update", {"status": "error"})
        if max_iterations < 30:
            return (
                f"已达到最大迭代次数（{max_iterations}）。"
                f"当前 MAX_ITERATIONS={max_iterations} 设置过低，"
                f"建议调整为 100~300 以支持复杂任务。"
            )
        return "已达到最大工具调用次数，请重新描述您的需求。"

    # ==================== 流式输出 (SSE) ====================

    async def reason_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        base_system_prompt: str = "",
        task_description: str = "",
        task_monitor: Any = None,
        session_type: str = "desktop",
        plan_mode: bool = False,
        mode: str = "agent",
        endpoint_override: str | None = None,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        agent_profile_id: str = "default",
        session: Any = None,
        force_tool_retries: int | None = None,
        is_sub_agent: bool = False,
    ):
        """
        流式推理循环，为 HTTP API (SSE) 设计。

        与 run() 保持特性对齐：TaskMonitor、循环检测、模型切换、
        LLM 错误重试、任务完成度验证、Rollback 等。

        调用方（如 Agent.chat_with_session_stream）需传入 tools 和 system_prompt，
        新增参数均 optional，向后兼容老的调用方式。

        Yields dict events:
        - {"type": "iteration_start", "iteration": N}
        - {"type": "context_compressed", "before_tokens": N, "after_tokens": M}
        - {"type": "thinking_start"} / {"type": "thinking_delta"} / {"type": "thinking_end"}
        - {"type": "text_delta", "content": "..."}
        - {"type": "tool_call_start"} / {"type": "tool_call_end"}
        - {"type": "todo_created"} / {"type": "todo_step_updated"}
        - {"type": "ask_user", "question": "..."}
        - {"type": "error", "message": "..."}
        - {"type": "done"}
        """
        tools = tools or []
        self._last_exit_reason = "normal"
        self._last_react_trace = []
        self._last_delivery_receipts = []
        self._supervisor.reset()
        self._budget = create_budget_from_settings()
        self._budget.start()
        react_trace: list[dict] = []
        _trace_started_at = datetime.now().isoformat()
        _endpoint_switched = False

        _session_key = conversation_id or ""
        state = self._state.get_task_for_session(_session_key) if _session_key else self._state.current_task

        if not state or not state.is_active:
            state = self._state.begin_task(session_id=_session_key)
        elif state.status == TaskStatus.ACTING:
            logger.warning(
                f"[State] Previous task stuck in {state.status.value}, force resetting for new message"
            )
            state = self._state.begin_task(session_id=_session_key)

        if state.cancelled:
            logger.error(
                f"[State] CRITICAL: fresh task {state.task_id[:8]} has cancelled=True, "
                f"reason={state.cancel_reason!r}. Force clearing."
            )
            state.cancelled = False
            state.cancel_reason = ""
            state.cancel_event = asyncio.Event()

        self._context_manager.set_cancel_event(state.cancel_event)

        try:
            # === 动态 System Prompt（追加活跃 Plan） ===
            _base_sp = base_system_prompt or system_prompt

            def _build_effective_prompt() -> str:
                try:
                    from ..tools.handlers.plan import get_active_todo_prompt
                    prompt = _base_sp
                    if conversation_id:
                        plan_section = get_active_todo_prompt(conversation_id)
                        if plan_section:
                            prompt += f"\n\n{plan_section}\n"
                    return prompt
                except Exception:
                    return _base_sp

            effective_prompt = _build_effective_prompt()

            # Backward compat: plan_mode bool → mode string
            _effective_mode = mode
            if plan_mode and _effective_mode == "agent":
                _effective_mode = "plan"

            # Mode-specific prompt injection
            if _effective_mode == "plan":
                from ..prompt.builder import _build_mode_rules
                _plan_rules = _build_mode_rules("plan")
                if _plan_rules:
                    effective_prompt += f"\n\n{_plan_rules}"
            elif _effective_mode == "ask":
                from ..prompt.builder import _build_mode_rules
                _ask_rules = _build_mode_rules("ask")
                if _ask_rules:
                    effective_prompt += f"\n\n{_ask_rules}"

            # Tool filtering by mode — restrict available tools based on current mode
            tools = _filter_tools_by_mode(tools, _effective_mode)
            _allowed_tool_names = (
                {t.get("name", "") for t in tools} if _effective_mode != "agent" else None
            )
            self._tool_executor._current_mode = _effective_mode

            # === 端点覆盖 ===
            _endpoint_switched = False
            if endpoint_override:
                if not conversation_id:
                    conversation_id = f"_stream_{uuid.uuid4().hex[:12]}"
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client and hasattr(llm_client, "switch_model"):
                    ok, msg = llm_client.switch_model(
                        endpoint_name=endpoint_override,
                        hours=0.05,
                        reason=f"chat endpoint override: {endpoint_override}",
                        conversation_id=conversation_id,
                    )
                    if not ok:
                        yield {"type": "error", "message": f"端点切换失败: {msg}"}
                        yield {"type": "done"}
                        return
                    _endpoint_switched = True

            current_model = self._brain.model
            if _endpoint_switched and endpoint_override:
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client:
                    _provider = llm_client._providers.get(endpoint_override)
                    if _provider:
                        current_model = _provider.model

            # === 与 run() 一致的循环控制变量 ===
            state.original_user_messages = [
                msg for msg in messages if self._is_human_user_message(msg)
            ]
            max_iterations = settings.max_iterations
            self._empty_content_retries = 0
            working_messages = list(messages)

            # ForceToolCall 配置
            im_floor = max(0, int(getattr(settings, "force_tool_call_im_floor", 1)))
            configured = int(
                getattr(self, "_force_tool_override", None)
                or getattr(settings, "force_tool_call_max_retries", 0)
            )
            if session_type == "im":
                base_force_retries = max(im_floor, configured)
            else:
                base_force_retries = max(0, configured)

            max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)

            # Intent-driven override (from IntentAnalyzer)
            if force_tool_retries is not None:
                max_no_tool_retries = force_tool_retries
                logger.info(f"[ForceToolCall/Stream] Intent override: max_retries={force_tool_retries}")

            max_verify_retries = 1
            max_confirmation_text_retries = max(0, int(getattr(settings, "confirmation_text_max_retries", 1)))

            executed_tool_names: list[str] = []
            delivery_receipts: list[dict] = []
            _last_browser_url = ""
            consecutive_tool_rounds = 0
            no_tool_call_count = 0
            verify_incomplete_count = 0
            no_confirmation_text_count = 0
            tools_executed_in_task = False
            _supervisor_intervened = False
            _tool_call_counter: dict[str, int] = {}
            _MAX_SAME_TOOL_PER_TASK = 5

            def _make_tool_sig(tc: dict) -> str:
                nonlocal _last_browser_url
                name = tc.get("name", "")
                inp = tc.get("input", {})
                if name == "browser_navigate":
                    _last_browser_url = inp.get("url", "")
                try:
                    param_str = json.dumps(inp, sort_keys=True, ensure_ascii=False)
                except Exception:
                    param_str = str(inp)
                if name in self._browser_page_read_tools and len(param_str) <= 20 and _last_browser_url:
                    param_str = f"{param_str}|url={_last_browser_url}"
                param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
                return f"{name}({param_hash})"

            # ==================== 主循环 ====================
            logger.info(
                f"[ReAct-Stream] === Loop started (max_iterations={max_iterations}, model={current_model}) ==="
            )

            for _iteration in range(max_iterations):
                self._last_working_messages = working_messages
                state.iteration = _iteration

                # --- 取消检查 ---
                if state.cancelled:
                    logger.info(f"[ReAct-Stream] Task cancelled at iteration start: {state.cancel_reason}")
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    yield {"type": "text_delta", "content": "✅ 任务已停止。"}
                    yield {"type": "done"}
                    return

                # --- Resource Budget 检查（与 run() 一致） ---
                self._budget.record_iteration()
                budget_status = self._budget.check()
                if budget_status.action == BudgetAction.PAUSE:
                    logger.warning(f"[Budget-Stream] PAUSE: {budget_status.message}")
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "budget_exceeded", _trace_started_at
                    )
                    self._run_failure_analysis(
                        react_trace, "budget_exceeded",
                        task_description=task_description,
                        task_id=state.task_id,
                    )
                    msg = (
                        f"⚠️ 任务资源预算已用尽（{budget_status.dimension}: "
                        f"{budget_status.usage_ratio:.0%}），任务暂停。\n"
                        f"已完成的工作进度已保存，请调整预算后继续。"
                    )
                    yield {"type": "text_delta", "content": msg}
                    yield {"type": "done"}
                    return
                elif budget_status.action in (BudgetAction.WARNING, BudgetAction.DOWNGRADE):
                    logger.info("[Budget] %s: %s — logged only, not injected",
                                budget_status.dimension, budget_status.message)

                # --- TaskMonitor: 迭代开始 + 模型切换检查 ---
                if task_monitor:
                    task_monitor.begin_iteration(_iteration + 1, current_model)
                    switch_result = self._check_model_switch(
                        task_monitor, state, working_messages, current_model
                    )
                    if switch_result:
                        current_model, working_messages = switch_result
                        no_tool_call_count = 0
                        tools_executed_in_task = False
                        _supervisor_intervened = False
                        verify_incomplete_count = 0
                        executed_tool_names = []
                        consecutive_tool_rounds = 0
                        no_confirmation_text_count = 0

                logger.info(
                    f"[ReAct-Stream] Iter {_iteration+1}/{max_iterations} — REASON (model={current_model})"
                )

                # --- 状态转换: REASONING（与 run() 一致） ---
                if state.status != TaskStatus.REASONING:
                    state.transition(TaskStatus.REASONING)

                _ctx_compressed_info: dict | None = None
                if len(working_messages) > 2:
                    effective_prompt = _build_effective_prompt()
                    _before_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                    try:
                        working_messages = await self._context_manager.compress_if_needed(
                            working_messages,
                            system_prompt=effective_prompt,
                            tools=tools,
                            memory_manager=self._memory_manager,
                            conversation_id=conversation_id,
                        )
                    except _CtxCancelledError:
                        # 与 run() 保持一致：只在明确用户取消时终止。
                        if state.cancelled or bool((state.cancel_reason or "").strip()):
                            async for ev in self._stream_cancel_farewell(
                                working_messages, effective_prompt, current_model, state
                            ):
                                yield ev
                            yield {"type": "done"}
                            return
                        logger.warning(
                            "[ReAct-Stream] Context compression cancelled without task cancellation "
                            "(session=%s). Fallback to uncompressed context.",
                            conversation_id or state.session_id,
                        )
                        state.cancel_event = asyncio.Event()
                        self._context_manager.set_cancel_event(state.cancel_event)
                    _after_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                    if _after_tokens < _before_tokens:
                        _plan_sec = ""
                        try:
                            from ..tools.handlers.plan import get_active_todo_prompt
                            if conversation_id:
                                _plan_sec = get_active_todo_prompt(conversation_id) or ""
                        except Exception:
                            pass
                        _scratchpad = ""
                        if self._memory_manager:
                            try:
                                _sp = getattr(self._memory_manager, "get_scratchpad_summary", None)
                                if _sp:
                                    _scratchpad = _sp() or ""
                            except Exception:
                                pass
                        working_messages = ContextManager.rewrite_after_compression(
                            working_messages,
                            plan_section=_plan_sec,
                            scratchpad_summary=_scratchpad,
                            completed_tools=executed_tool_names,
                            task_description=task_description,
                        )
                        _ctx_compressed_info = {
                            "before_tokens": _before_tokens,
                            "after_tokens": _after_tokens,
                        }
                        logger.info(
                            f"[ReAct-Stream] Context compressed: {_before_tokens} → {_after_tokens} tokens"
                        )
                        yield {
                            "type": "context_compressed",
                            "before_tokens": _before_tokens,
                            "after_tokens": _after_tokens,
                        }

                # --- 思维链: 迭代开始事件 ---
                yield {"type": "iteration_start", "iteration": _iteration + 1}

                # --- Reason phase (真流式) ---
                _thinking_t0 = time.time()
                yield {"type": "thinking_start"}
                await broadcast_event("pet-status-update", {"status": "thinking"})
                _streamed_text = False
                _streamed_thinking = False
                _stream_usage: dict | None = None
                _raw_streamed_text: str = ""

                try:
                    decision = None
                    async for stream_event in self._reason_stream_iter(
                        working_messages,
                        system_prompt=effective_prompt,
                        tools=tools,
                        current_model=current_model,
                        conversation_id=conversation_id,
                        thinking_mode=thinking_mode,
                        thinking_depth=thinking_depth,
                        iteration=_iteration,
                        agent_profile_id=agent_profile_id,
                    ):
                        _evt_type = stream_event.get("type")
                        if _evt_type == "heartbeat":
                            yield {"type": "heartbeat"}
                        elif _evt_type == "text_delta":
                            yield stream_event
                            _streamed_text = True
                        elif _evt_type == "thinking_delta":
                            yield stream_event
                            _streamed_thinking = True
                        elif _evt_type == "decision":
                            decision = stream_event["decision"]
                            _stream_usage = stream_event.get("usage")
                            _raw_streamed_text = stream_event.get("raw_streamed_text", "")
                    if decision is None:
                        raise RuntimeError("_reason_stream returned no decision")

                    if task_monitor:
                        task_monitor.reset_retry_count()

                except UserCancelledError as uce:
                    # --- 用户取消中断：发起轻量 LLM 收尾 ---
                    logger.info(f"[ReAct-Stream] LLM call interrupted by user cancel: {uce.reason}")
                    _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                    yield {"type": "thinking_end", "duration_ms": _thinking_duration}

                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                    )
                    async for ev in self._stream_cancel_farewell(
                        working_messages, effective_prompt, current_model, state
                    ):
                        yield ev
                    yield {"type": "done"}
                    return

                except Exception as e:
                    # --- LLM Error Handling（与 run() 一致） ---
                    retry_result = self._handle_llm_error(
                        e, task_monitor, state, working_messages, current_model
                    )
                    _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                    yield {"type": "thinking_end", "duration_ms": _thinking_duration}

                    if retry_result == "retry":
                        _total_r = getattr(state, '_total_llm_retries', 1)
                        _retry_sleep = min(2 * _total_r, 15)
                        _sleep = asyncio.create_task(asyncio.sleep(_retry_sleep))
                        _cw = asyncio.create_task(state.cancel_event.wait())
                        _done, _pend = await asyncio.wait({_sleep, _cw}, return_when=asyncio.FIRST_COMPLETED)
                        for _t in _pend:
                            _t.cancel()
                            try:
                                await _t
                            except (asyncio.CancelledError, Exception):
                                pass
                        if _cw in _done:
                            async for ev in self._stream_cancel_farewell(
                                working_messages, effective_prompt, current_model, state
                            ):
                                yield ev
                            yield {"type": "done"}
                            return
                        continue
                    elif isinstance(retry_result, tuple):
                        current_model, working_messages = retry_result
                        no_tool_call_count = 0
                        tools_executed_in_task = False
                        _supervisor_intervened = False
                        verify_incomplete_count = 0
                        executed_tool_names = []
                        consecutive_tool_rounds = 0
                        no_confirmation_text_count = 0
                        continue
                    else:
                        self._save_react_trace(
                            react_trace, conversation_id, session_type,
                            f"reason_error: {str(e)[:100]}", _trace_started_at,
                        )
                        yield {"type": "error", "message": f"推理失败: {str(e)[:300]}"}
                        yield {"type": "done"}
                        return

                # Emit thinking content (已在流式过程中逐步发出; 兜底: 非流式 fallback)
                _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                _has_thinking = bool(decision.thinking_content)
                if _has_thinking and not _streamed_thinking:
                    yield {"type": "thinking_delta", "content": decision.thinking_content}
                yield {
                    "type": "thinking_end",
                    "duration_ms": _thinking_duration,
                    "has_thinking": _has_thinking,
                }

                # chain_text: 文本已通过 text_delta 实时推送; 仅在未流式时 fallback
                if not _streamed_text:
                    _decision_text = (decision.text_content or "").strip()
                    if _decision_text and decision.type == DecisionType.TOOL_CALLS:
                        yield {"type": "chain_text", "content": _decision_text[:2000]}
                elif _raw_streamed_text != (decision.text_content or ""):
                    yield {
                        "type": "text_replace",
                        "content": decision.text_content or "",
                    }

                if task_monitor:
                    task_monitor.end_iteration(decision.text_content or "")

                # -- 收集 ReAct trace + Budget 记录 token --
                # 流式模式: usage 来自 StreamAccumulator (_stream_usage dict)
                # 非流式 fallback: usage 来自 decision.raw_response
                _raw = decision.raw_response
                _usage = getattr(_raw, "usage", None) if _raw else None
                _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
                _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0
                if not (_in_tokens or _out_tokens) and _stream_usage:
                    _in_tokens = _stream_usage.get("input_tokens", 0)
                    _out_tokens = _stream_usage.get("output_tokens", 0)
                if _in_tokens or _out_tokens:
                    self._budget.record_tokens(_in_tokens, _out_tokens)
                _iter_trace: dict = {
                    "iteration": _iteration + 1,
                    "timestamp": datetime.now().isoformat(),
                    "decision_type": decision.type.value if hasattr(decision.type, "value") else str(decision.type),
                    "model": current_model,
                    "thinking": decision.thinking_content,
                    "thinking_duration_ms": _thinking_duration,
                    "text": decision.text_content,
                    "tool_calls": [
                        {
                            "name": tc.get("name"),
                            "id": tc.get("id"),
                            "input": tc.get("input", {}),
                        }
                        for tc in (decision.tool_calls or [])
                    ],
                    "tool_results": [],
                    "tokens": {"input": _in_tokens, "output": _out_tokens},
                    "context_compressed": _ctx_compressed_info,
                }
                tool_names_log = [tc.get("name", "?") for tc in (decision.tool_calls or [])]
                logger.info(
                    f"[ReAct-Stream] Iter {_iteration+1} — decision={_iter_trace['decision_type']}, "
                    f"tools={tool_names_log}, tokens_in={_in_tokens}, tokens_out={_out_tokens}"
                )

                # ==================== stop_reason=max_tokens 检测（与 run() 一致）====================
                if decision.stop_reason == "max_tokens":
                    logger.warning(
                        f"[ReAct-Stream] Iter {_iteration+1} — ⚠️ LLM output truncated (stop_reason=max_tokens). "
                        f"The response hit the max_tokens limit ({self._brain.max_tokens}). "
                        f"Tool calls may have incomplete JSON arguments."
                    )
                    _iter_trace["truncated"] = True

                    # 自动扩容 max_tokens 并重试（与 run() 一致）
                    if decision.type == DecisionType.TOOL_CALLS:
                        truncated_calls = [
                            tc for tc in decision.tool_calls
                            if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                        ]
                        _current_max = self._brain.max_tokens or 16384
                        _max_ceiling = min(_current_max * 3, 65536)
                        if truncated_calls and len(truncated_calls) == len(decision.tool_calls):
                            _new_max = min(_current_max * 2, _max_ceiling)
                            if _new_max > _current_max:
                                logger.warning(
                                    f"[ReAct-Stream] Iter {_iteration+1} — All "
                                    f"{len(truncated_calls)} tool calls truncated. "
                                    f"Auto-increasing max_tokens: "
                                    f"{_current_max} → {_new_max} and retrying"
                                )
                                self._brain.max_tokens = _new_max
                                react_trace.append(_iter_trace)
                                continue
                        elif truncated_calls:
                            _new_max = min(int(_current_max * 1.5), _max_ceiling)
                            if _new_max > _current_max:
                                logger.warning(
                                    f"[ReAct-Stream] Iter {_iteration+1} — "
                                    f"{len(truncated_calls)}/{len(decision.tool_calls)} tool "
                                    f"calls truncated. Increasing max_tokens for next "
                                    f"iteration: {_current_max} → {_new_max}"
                                )
                                self._brain.max_tokens = _new_max

                # ==================== FINAL_ANSWER ====================
                if decision.type == DecisionType.FINAL_ANSWER:
                    consecutive_tool_rounds = 0

                    # 任务完成度验证（与 run() 一致）
                    result = await self._handle_final_answer(
                        decision=decision,
                        working_messages=working_messages,
                        original_messages=messages,
                        tools_executed_in_task=tools_executed_in_task,
                        executed_tool_names=executed_tool_names,
                        delivery_receipts=delivery_receipts,
                        no_tool_call_count=no_tool_call_count,
                        verify_incomplete_count=verify_incomplete_count,
                        no_confirmation_text_count=no_confirmation_text_count,
                        max_no_tool_retries=max_no_tool_retries,
                        max_verify_retries=max_verify_retries,
                        max_confirmation_text_retries=max_confirmation_text_retries,
                        base_force_retries=base_force_retries,
                        conversation_id=conversation_id,
                        supervisor_intervened=_supervisor_intervened,
                    )

                    if isinstance(result, str):
                        react_trace.append(_iter_trace)
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "completed", _trace_started_at
                        )
                        try:
                            state.transition(TaskStatus.COMPLETED)
                        except ValueError:
                            state.status = TaskStatus.COMPLETED
                        logger.info(
                            f"[ReAct-Stream] === COMPLETED after {_iteration+1} iterations ==="
                        )
                        if _streamed_text:
                            if result != _raw_streamed_text:
                                yield {"type": "text_replace", "content": result}
                        else:
                            chunk_size = 20
                            for i in range(0, len(result), chunk_size):
                                yield {"type": "text_delta", "content": result[i:i + chunk_size]}
                                await asyncio.sleep(0.01)
                        await broadcast_event("pet-status-update", {"status": "success"})
                        yield {"type": "done"}
                        return
                    else:
                        # 验证不通过 → 继续循环; 清除前端已展示的流式文本
                        logger.info(
                            f"[ReAct-Stream] Iter {_iteration+1} — VERIFY: incomplete, continuing loop"
                        )
                        if _streamed_text:
                            yield {"type": "text_replace", "content": ""}
                        yield {"type": "chain_text", "content": "任务尚未完成，继续处理..."}
                        react_trace.append(_iter_trace)
                        try:
                            state.transition(TaskStatus.VERIFYING)
                        except ValueError:
                            state.status = TaskStatus.VERIFYING
                        (
                            working_messages,
                            no_tool_call_count,
                            verify_incomplete_count,
                            no_confirmation_text_count,
                            max_no_tool_retries,
                        ) = result
                        continue

                # ==================== TOOL_CALLS ====================
                elif decision.type == DecisionType.TOOL_CALLS and decision.tool_calls:
                    try:
                        state.transition(TaskStatus.ACTING)
                    except ValueError:
                        state.status = TaskStatus.ACTING

                    working_messages.append({
                        "role": "assistant",
                        "content": decision.assistant_content or [{"type": "text", "text": ""}],
                        "reasoning_content": decision.thinking_content or None,
                    })

                    # ---- ask_user 拦截 ----
                    ask_user_calls = [tc for tc in decision.tool_calls if tc.get("name") == "ask_user"]
                    other_tool_calls = [tc for tc in decision.tool_calls if tc.get("name") != "ask_user"]

                    if ask_user_calls:
                        # 先执行非 ask_user 工具
                        tool_results_for_msg: list[dict] = []
                        for tc in other_tool_calls:
                            t_name = tc.get("name", "unknown")
                            t_args = tc.get("input", tc.get("arguments", {}))
                            t_id = tc.get("id", str(uuid.uuid4()))
                            # Runtime mode guard
                            _blocked_msg = _should_block_tool(
                                t_name, t_args, _allowed_tool_names, _effective_mode
                            )
                            if _blocked_msg:
                                logger.warning(
                                    f"[ModeGuard] Blocked '{t_name}' in {_effective_mode} mode"
                                )
                                yield {"type": "tool_call_start", "tool": t_name, "name": t_name, "args": t_args, "id": t_id}
                                yield {
                                    "type": "tool_call_end", "tool": t_name,
                                    "result": _blocked_msg[:_SSE_RESULT_PREVIEW_CHARS],
                                    "id": t_id, "is_error": True,
                                }
                                tool_results_for_msg.append({
                                    "type": "tool_result", "tool_use_id": t_id,
                                    "content": _blocked_msg, "is_error": True,
                                })
                                continue
                            # chain_text: 工具描述
                            yield {"type": "chain_text", "content": self._describe_tool_call(t_name, t_args)}
                            yield {"type": "tool_call_start", "tool": t_name, "name": t_name, "args": t_args, "id": t_id}
                            await broadcast_event("pet-status-update", {"status": "tool_execution", "tool_name": t_name})
                            # PolicyEngine 检查
                            from .policy import PolicyDecision, get_policy_engine
                            _pe = get_policy_engine()
                            _pr = _pe.assert_tool_allowed(t_name, t_args if isinstance(t_args, dict) else {})
                            if _pr.decision == PolicyDecision.DENY:
                                r = f"⚠️ 策略拒绝: {_pr.reason}"
                                _tool_is_error = True
                            else:
                                _tool_is_error = False
                                try:
                                    r = await self._tool_executor.execute_tool(
                                        tool_name=t_name,
                                        tool_input=t_args if isinstance(t_args, dict) else {},
                                        session_id=conversation_id,
                                    )
                                    r = str(r) if r else ""
                                except Exception as exc:
                                    r = f"Tool error: {exc}"
                                    _tool_is_error = True
                            yield {"type": "tool_call_end", "tool": t_name, "result": r[:_SSE_RESULT_PREVIEW_CHARS], "id": t_id, "is_error": _tool_is_error}
                            # chain_text: 结果摘要
                            _ask_result_summary = self._summarize_tool_result(t_name, r)
                            if _ask_result_summary:
                                yield {"type": "chain_text", "content": _ask_result_summary}
                            tool_results_for_msg.append({
                                "type": "tool_result", "tool_use_id": t_id, "content": r,
                            })

                        # ask_user 事件
                        ask_raw = ask_user_calls[0].get("input")
                        if not ask_raw:
                            ask_raw = ask_user_calls[0].get("arguments", {})
                        ask_input = ask_raw
                        if isinstance(ask_input, str):
                            try:
                                ask_input = json.loads(ask_input)
                            except Exception:
                                ask_input = {}
                        if not isinstance(ask_input, dict):
                            ask_input = {}
                        ask_q = ask_input.get("question", "")
                        ask_options = ask_input.get("options")
                        ask_allow_multiple = ask_input.get("allow_multiple", False)
                        ask_questions = ask_input.get("questions")
                        text_part = decision.text_content or ""
                        question_text = f"{text_part}\n\n{ask_q}".strip() if text_part else ask_q
                        event: dict = {
                            "type": "ask_user",
                            "question": question_text,
                            "conversation_id": conversation_id,
                        }
                        if ask_options and isinstance(ask_options, list):
                            event["options"] = [
                                {"id": str(o.get("id", "")), "label": str(o.get("label", ""))}
                                for o in ask_options
                                if isinstance(o, dict) and o.get("id") and o.get("label")
                            ]
                        if ask_allow_multiple:
                            event["allow_multiple"] = True
                        if ask_questions and isinstance(ask_questions, list):
                            parsed_questions = []
                            for q in ask_questions:
                                if not isinstance(q, dict) or not q.get("id") or not q.get("prompt"):
                                    continue
                                pq: dict = {"id": str(q["id"]), "prompt": str(q["prompt"])}
                                q_options = q.get("options")
                                if q_options and isinstance(q_options, list):
                                    pq["options"] = [
                                        {"id": str(o.get("id", "")), "label": str(o.get("label", ""))}
                                        for o in q_options
                                        if isinstance(o, dict) and o.get("id") and o.get("label")
                                    ]
                                if q.get("allow_multiple"):
                                    pq["allow_multiple"] = True
                                parsed_questions.append(pq)
                            if parsed_questions:
                                event["questions"] = parsed_questions

                        await broadcast_event("pet-status-update", {"status": "idle"})
                        yield event
                        react_trace.append(_iter_trace)
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "ask_user", _trace_started_at
                        )
                        self._last_exit_reason = "ask_user"
                        try:
                            state.transition(TaskStatus.WAITING_USER)
                        except ValueError:
                            state.status = TaskStatus.WAITING_USER
                        yield {"type": "done"}
                        return

                    # ---- 正常工具执行（支持 cancel_event / skip_event 三路竞速中断） ----
                    tool_results_for_msg: list[dict] = []
                    _non_denied_tool_names: list[str] = []
                    _stream_cancelled = False
                    _stream_skipped = False
                    cancel_event = state.cancel_event if state else asyncio.Event()
                    skip_event = state.skip_event if state else asyncio.Event()
                    for tc in decision.tool_calls:
                        # 每个工具执行前检查取消
                        if state and state.cancelled:
                            _stream_cancelled = True
                            break

                        tool_name = tc.get("name", "unknown")
                        tool_args = tc.get("input", tc.get("arguments", {}))
                        tool_id = tc.get("id", str(uuid.uuid4()))

                        # 同名工具频率限制
                        _tool_call_counter[tool_name] = _tool_call_counter.get(tool_name, 0) + 1
                        if _tool_call_counter[tool_name] > _MAX_SAME_TOOL_PER_TASK:
                            logger.warning(
                                f"[RateLimit] Tool '{tool_name}' called "
                                f"{_tool_call_counter[tool_name]} times "
                                f"(limit={_MAX_SAME_TOOL_PER_TASK}), skipping"
                            )
                            _rl_msg = (
                                f"[系统] 工具 {tool_name} 已在本任务中调用 "
                                f"{_tool_call_counter[tool_name] - 1} 次，已达上限。"
                                f"请整合操作或继续下一步。"
                            )
                            yield {"type": "tool_call_start", "tool": tool_name, "name": tool_name, "args": tool_args, "id": tool_id}
                            yield {
                                "type": "tool_call_end", "tool": tool_name,
                                "result": _rl_msg[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id, "is_error": False,
                            }
                            tool_results_for_msg.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": _rl_msg,
                            })
                            continue

                        # Runtime mode guard
                        _blocked_msg = _should_block_tool(
                            tool_name, tool_args, _allowed_tool_names, _effective_mode
                        )
                        if _blocked_msg:
                            logger.warning(
                                f"[ModeGuard] Blocked '{tool_name}' in {_effective_mode} mode"
                            )
                            yield {"type": "tool_call_start", "tool": tool_name, "name": tool_name, "args": tool_args, "id": tool_id}
                            yield {
                                "type": "tool_call_end", "tool": tool_name,
                                "result": _blocked_msg[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id, "is_error": True,
                            }
                            tool_results_for_msg.append({
                                "type": "tool_result", "tool_use_id": tool_id,
                                "content": _blocked_msg, "is_error": True,
                            })
                            continue

                        _tool_desc = self._describe_tool_call(tool_name, tool_args)
                        yield {"type": "chain_text", "content": _tool_desc}

                        yield {"type": "tool_call_start", "tool": tool_name, "name": tool_name, "args": tool_args, "id": tool_id}
                        await broadcast_event("pet-status-update", {"status": "tool_execution", "tool_name": tool_name})

                        # PolicyEngine 检查（与 execute_batch 一致）
                        from .policy import PolicyDecision, get_policy_engine
                        _pe = get_policy_engine()
                        _tool_args_dict = tool_args if isinstance(tool_args, dict) else {}
                        _pr = _pe.assert_tool_allowed(tool_name, _tool_args_dict)
                        if _pr.decision == PolicyDecision.DENY:
                            result_text = f"⚠️ 策略拒绝: {_pr.reason}"
                            yield {
                                "type": "tool_call_end", "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id, "is_error": True,
                            }
                            _deny_summary = self._summarize_tool_result(tool_name, result_text)
                            if _deny_summary:
                                yield {"type": "chain_text", "content": _deny_summary}
                            tool_results_for_msg.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_text,
                                "is_error": True,
                            })
                            continue

                        if _pr.decision == PolicyDecision.CONFIRM:
                            _risk = _pr.metadata.get("risk_level", "HIGH")
                            _needs_sb = _pr.metadata.get("needs_sandbox", False)
                            _pe.store_ui_pending(tool_id, tool_name, _tool_args_dict, session_id=conversation_id or "")
                            yield {
                                "type": "security_confirm",
                                "tool": tool_name,
                                "args": _tool_args_dict,
                                "id": tool_id,
                                "reason": _pr.reason,
                                "risk_level": _risk,
                                "needs_sandbox": _needs_sb,
                            }
                            result_text = (
                                f"⚠️ 需要用户确认: {_pr.reason}\n"
                                "请使用 ask_user 工具询问用户是否允许此操作，"
                                "得到用户同意后再重新调用此工具。"
                            )
                            yield {
                                "type": "tool_call_end", "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id, "is_error": True,
                            }
                            tool_results_for_msg.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_text,
                                "is_error": True,
                            })
                            continue

                        _non_denied_tool_names.append(tool_name)

                        # 将工具执行与 cancel_event / skip_event 三路竞速
                        # 注意: 不在此处 clear_skip()，让已到达的 skip 信号自然被竞速消费
                        try:
                            tool_exec_task = asyncio.create_task(
                                self._tool_executor.execute_tool(
                                    tool_name=tool_name,
                                    tool_input=tool_args if isinstance(tool_args, dict) else {},
                                    session_id=conversation_id,
                                )
                            )
                            cancel_waiter = asyncio.create_task(cancel_event.wait())
                            skip_waiter = asyncio.create_task(skip_event.wait())

                            pending_set = {tool_exec_task, cancel_waiter, skip_waiter}
                            done_set: set[asyncio.Task] = set()
                            while not done_set:
                                done_set, pending_set = await asyncio.wait(
                                    pending_set,
                                    timeout=self._HEARTBEAT_INTERVAL,
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if not done_set:
                                    yield {"type": "heartbeat"}

                            for t in pending_set:
                                t.cancel()
                                try:
                                    await t
                                except (asyncio.CancelledError, Exception):
                                    pass

                            if cancel_waiter in done_set and tool_exec_task not in done_set:
                                result_text = f"[工具 {tool_name} 被用户中断]"
                                _stream_cancelled = True
                            elif skip_waiter in done_set and tool_exec_task not in done_set:
                                _skip_reason = state.skip_reason if state else "用户请求跳过"
                                if state:
                                    state.clear_skip()
                                result_text = f"[用户跳过了此步骤: {_skip_reason}]"
                                _stream_skipped = True
                                logger.info(f"[SkipStep-Stream] Tool {tool_name} skipped: {_skip_reason}")
                            elif tool_exec_task in done_set:
                                result_text = tool_exec_task.result()
                                result_text = str(result_text) if result_text else ""
                            else:
                                result_text = f"[工具 {tool_name} 被用户中断]"
                                _stream_cancelled = True
                        except Exception as exc:
                            result_text = f"Tool error: {exc}"

                        _tool_is_error = result_text.startswith("Tool error:")
                        # Emit agent_handoff events from session.context.handoff_events (set by orchestrator.delegate)
                        if session and hasattr(session, "context") and hasattr(session.context, "handoff_events"):
                            for h in session.context.handoff_events:
                                yield {"type": "agent_handoff", "from_agent": h.get("from_agent", ""), "to_agent": h.get("to_agent", ""), "reason": h.get("reason", "")}
                            session.context.handoff_events.clear()
                        # 跳过时发送 tool_call_skipped 事件通知前端
                        if _stream_skipped:
                            yield {"type": "tool_call_end", "tool": tool_name, "result": result_text[:_SSE_RESULT_PREVIEW_CHARS], "id": tool_id, "skipped": True, "is_error": False}
                        else:
                            yield {"type": "tool_call_end", "tool": tool_name, "result": result_text[:_SSE_RESULT_PREVIEW_CHARS], "id": tool_id, "is_error": _tool_is_error}

                        if _stream_cancelled:
                            tool_results_for_msg.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_text,
                                "is_error": True,
                            })
                            break

                        if _stream_skipped:
                            tool_results_for_msg.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_text,
                            })
                            _stream_skipped = False
                            continue

                        # === chain_text: 简述工具返回结果 ===
                        _result_summary = self._summarize_tool_result(tool_name, result_text)
                        if _result_summary:
                            yield {"type": "chain_text", "content": _result_summary}

                        # deliver_artifacts 回执收集（与 run() 一致）
                        if tool_name == "deliver_artifacts" and result_text:
                            try:
                                _rt = result_text
                                _lm = "\n\n[执行日志]"
                                if _lm in _rt:
                                    _rt = _rt[:_rt.index(_lm)]
                                _receipts_data = json.loads(_rt)
                                if isinstance(_receipts_data, dict) and "receipts" in _receipts_data:
                                    delivery_receipts = _receipts_data["receipts"]
                                    self._last_delivery_receipts = delivery_receipts
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Plan 事件
                        if tool_name == "create_todo" and isinstance(tool_args, dict):
                            raw_steps = tool_args.get("steps", [])
                            plan_steps = []
                            for idx, s in enumerate(raw_steps):
                                if isinstance(s, dict):
                                    plan_steps.append({
                                        "id": str(s.get("id", f"step_{idx + 1}")),
                                        "description": str(s.get("description", s.get("id", ""))),
                                        "status": "pending",
                                    })
                                else:
                                    plan_steps.append({"id": f"step_{idx + 1}", "description": str(s), "status": "pending"})
                            yield {"type": "todo_created", "plan": {
                                "id": str(uuid.uuid4()),
                                "taskSummary": tool_args.get("task_summary", ""),
                                "steps": plan_steps,
                                "status": "in_progress",
                            }}
                        elif tool_name == "update_todo_step" and isinstance(tool_args, dict):
                            step_id = tool_args.get("step_id", "")
                            yield {"type": "todo_step_updated", "stepId": step_id, "status": tool_args.get("status", "completed")}
                        elif tool_name == "complete_todo":
                            yield {"type": "todo_completed"}

                        tool_results_for_msg.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result_text,
                        })

                        # exit_plan_mode: stop the loop after this tool
                        if tool_name == "exit_plan_mode" and not _tool_is_error:
                            _plan_exit_stop = True
                            break

                    # exit_plan_mode was called → end the turn
                    if locals().get("_plan_exit_stop"):
                        logger.info(
                            "[ReAct-Stream] exit_plan_mode called — ending turn, "
                            "waiting for user review"
                        )
                        working_messages.append({"role": "user", "content": tool_results_for_msg})
                        _summary_text = (
                            "Plan completed and waiting for user review. "
                            "The user can approve the plan to switch to Agent mode, "
                            "or request changes to continue refining."
                        )
                        yield {"type": "text_delta", "content": _summary_text}
                        self._save_react_trace(
                            react_trace, conversation_id, session_type,
                            "plan_exit", _trace_started_at,
                        )
                        yield {"type": "done"}
                        return

                    if decision.tool_calls:
                        if _non_denied_tool_names:
                            if any(t not in _ADMIN_TOOL_NAMES for t in _non_denied_tool_names):
                                tools_executed_in_task = True
                            executed_tool_names.extend(_non_denied_tool_names)
                            state.record_tool_execution(_non_denied_tool_names)
                            self._budget.record_tool_calls(len(_non_denied_tool_names))

                        # 记录工具成功/失败状态（遍历 decision.tool_calls 保持索引对齐，
                        # 包含策略拒绝的工具，与 run() 一致）
                        for i, tc_rec in enumerate(decision.tool_calls):
                            _tc_name = tc_rec.get("name", "")
                            r_content = ""
                            if i < len(tool_results_for_msg):
                                r_content = str(tool_results_for_msg[i].get("content", ""))
                            is_error = any(m in r_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"])
                            self._record_tool_result(_tc_name, success=not is_error)

                    # 收集工具结果到 trace（保存完整内容，不截断）
                    _iter_trace["tool_results"] = [
                        {
                            "tool_use_id": tr.get("tool_use_id", ""),
                            "result_content": str(tr.get("content", "")),
                        }
                        for tr in tool_results_for_msg
                    ]
                    react_trace.append(_iter_trace)

                    try:
                        state.transition(TaskStatus.OBSERVING)
                    except ValueError:
                        state.status = TaskStatus.OBSERVING

                    # --- 截断检测（与 run() 一致）---
                    _has_truncation = any(
                        isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                        for tc in decision.tool_calls
                    )
                    if _has_truncation:
                        self._consecutive_truncation_count += 1
                        for tc in decision.tool_calls:
                            if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]:
                                self._tool_failure_counter.pop(tc.get("name", ""), None)
                        logger.info(
                            f"[ReAct-Stream] Iter {_iteration+1} — Tool args truncated "
                            f"(count: {self._consecutive_truncation_count}), "
                            f"skipping rollback"
                        )
                    else:
                        self._consecutive_truncation_count = 0

                    # --- Rollback 检查（与 run() 一致）— 截断错误不回滚 ---
                    should_rb, rb_reason = self._should_rollback(tool_results_for_msg)
                    if should_rb and not _has_truncation:
                        rollback_result = self._rollback(rb_reason)
                        if rollback_result:
                            working_messages, _ = rollback_result
                            logger.info("[ReAct-Stream][Rollback] 回滚成功，将用不同方法重新推理")
                            continue

                    # 取消检查（升级为带 LLM 收尾的取消处理）
                    if state.cancelled or _stream_cancelled:
                        # 将工具结果添加到上下文
                        working_messages.append({"role": "user", "content": tool_results_for_msg})
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                        )
                        async for ev in self._stream_cancel_farewell(
                            working_messages, effective_prompt, current_model, state
                        ):
                            yield ev
                        yield {"type": "done"}
                        return

                    working_messages.append({
                        "role": "user",
                        "content": tool_results_for_msg,
                    })

                    # 连续截断 >= 2 次：注入强制分拆指导（与 run() 一致）
                    if _has_truncation and self._consecutive_truncation_count >= 2:
                        _split_guidance = (
                            "⚠️ 你的工具调用参数因内容过长被 API 反复截断（已连续 "
                            f"{self._consecutive_truncation_count} 次）。你必须立即改变策略：\n"
                            "1. 将大文件拆分为多次 write_file 调用（每次不超过 2000 行）\n"
                            "2. 先创建文件框架，再用 edit_file 逐段补充内容\n"
                            "3. 减少内联 CSS/JS，使用简洁实现\n"
                            "4. 如果内容确实很长，考虑用 Markdown 替代 HTML"
                        )
                        working_messages.append({"role": "user", "content": _split_guidance})
                        logger.warning(
                            f"[ReAct-Stream] Injected split guidance after "
                            f"{self._consecutive_truncation_count} consecutive truncations"
                        )

                    # === 统一处理 skip 反思 + 用户插入消息 ===
                    if state:
                        _msg_count_before = len(working_messages)
                        await state.process_post_tool_signals(working_messages)
                        for _new_msg in working_messages[_msg_count_before:]:
                            _content = _new_msg.get("content", "")
                            if "[系统提示-用户跳过步骤]" in _content:
                                yield {"type": "chain_text", "content": "用户跳过了当前步骤"}
                            elif "[用户插入消息]" in _content:
                                _preview = _content.split("]")[1].split("\n")[0].strip() if "]" in _content else _content[:60]
                                yield {"type": "chain_text", "content": f"用户插入消息: {_preview[:60]}"}

                    # --- Supervisor: 记录工具数据（遍历 decision.tool_calls 保持索引对齐，与 run() 一致） ---
                    for _si, _stc in enumerate(decision.tool_calls or []):
                        _stn = _stc.get("name", "")
                        _sr_content = ""
                        if _si < len(tool_results_for_msg):
                            _sr = tool_results_for_msg[_si]
                            _sr_content = str(_sr.get("content", "")) if isinstance(_sr, dict) else str(_sr)
                        _sr_err = any(m in _sr_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"])
                        self._supervisor.record_tool_call(
                            tool_name=_stn, params=_stc.get("input", {}),
                            success=not _sr_err, iteration=_iteration,
                        )
                    self._supervisor.record_response(decision.text_content or "")
                    if _in_tokens or _out_tokens:
                        self._supervisor.record_token_usage(_in_tokens + _out_tokens)

                    # --- 循环检测（Supervisor-based, 与 run() 一致） ---
                    consecutive_tool_rounds += 1
                    self._supervisor.record_consecutive_tool_rounds(consecutive_tool_rounds)

                    # stop_reason 检查
                    if decision.stop_reason == "end_turn":
                        cleaned_text = strip_thinking_tags(decision.text_content)
                        _, cleaned_text = parse_intent_tag(cleaned_text)
                        if cleaned_text and cleaned_text.strip():
                            logger.info(
                                f"[ReAct-Stream][LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds"
                            )
                            self._save_react_trace(
                                react_trace, conversation_id, session_type,
                                "completed_end_turn", _trace_started_at,
                            )
                            if _streamed_text:
                                if cleaned_text != _raw_streamed_text:
                                    yield {"type": "text_replace", "content": cleaned_text}
                            else:
                                chunk_size = 20
                                for i in range(0, len(cleaned_text), chunk_size):
                                    yield {"type": "text_delta", "content": cleaned_text[i:i + chunk_size]}
                                    await asyncio.sleep(0.01)
                            yield {"type": "done"}
                            return

                    # Supervisor 综合评估
                    round_signatures = [_make_tool_sig(tc) for tc in decision.tool_calls]
                    round_sig_str = "+".join(sorted(round_signatures))
                    self._supervisor.record_tool_signature(round_sig_str)

                    _has_todo_s = self._has_active_todo_pending(conversation_id)
                    _todo_step_s = ""
                    try:
                        from ..tools.handlers.plan import get_active_todo_prompt
                        if conversation_id:
                            _todo_step_s = get_active_todo_prompt(conversation_id) or ""
                    except Exception:
                        pass
                    intervention = self._supervisor.evaluate(
                        _iteration, has_active_todo=_has_todo_s,
                        plan_current_step=_todo_step_s,
                    )

                    if intervention:
                        _supervisor_intervened = True
                        max_no_tool_retries = 0

                        if intervention.should_terminate:
                            cleaned = strip_thinking_tags(decision.text_content)
                            self._save_react_trace(
                                react_trace, conversation_id, session_type,
                                "loop_terminated", _trace_started_at,
                            )
                            try:
                                state.transition(TaskStatus.FAILED)
                            except ValueError:
                                state.status = TaskStatus.FAILED
                            self._run_failure_analysis(
                                react_trace, "loop_terminated",
                                task_description=task_description,
                                task_id=state.task_id,
                            )
                            msg = cleaned or "⚠️ 检测到工具调用陷入死循环，任务已自动终止。请重新描述您的需求。"
                            yield {"type": "text_delta", "content": msg}
                            yield {"type": "done"}
                            return

                        if intervention.should_rollback:
                            rollback_result = self._rollback(intervention.message)
                            if rollback_result:
                                working_messages, _ = rollback_result

                        if intervention.should_inject_prompt and intervention.prompt_injection:
                            working_messages.append({
                                "role": "user",
                                "content": intervention.prompt_injection,
                            })
                            tools = []
                            max_no_tool_retries = 0
                            logger.info(
                                f"[Supervisor] NUDGE: tools stripped to force text response "
                                f"(iter={_iteration}, pattern={intervention.pattern.value})"
                            )

                    continue  # Next iteration

            # max_iterations
            self._last_working_messages = working_messages
            self._save_react_trace(
                react_trace, conversation_id, session_type, "max_iterations", _trace_started_at
            )
            try:
                state.transition(TaskStatus.FAILED)
            except ValueError:
                state.status = TaskStatus.FAILED
            logger.info(f"[ReAct-Stream] === MAX_ITERATIONS reached ({max_iterations}) ===")
            self._run_failure_analysis(
                react_trace, "max_iterations",
                task_description=task_description,
                task_id=state.task_id,
            )
            if max_iterations < 30:
                hint = (
                    f"\n\n（已达到最大迭代次数 {max_iterations}。"
                    f"当前 MAX_ITERATIONS={max_iterations} 设置过低，"
                    f"建议在设置中调整为 100~300 以支持复杂任务）"
                )
            else:
                hint = "\n\n（已达到最大迭代次数）"
            yield {"type": "text_delta", "content": hint}
            yield {"type": "done"}

        except Exception as e:
            logger.error(f"reason_stream error: {e}", exc_info=True)
            self._last_working_messages = working_messages
            self._save_react_trace(
                react_trace, conversation_id, session_type,
                f"error: {str(e)[:100]}", _trace_started_at,
            )
            yield {"type": "error", "message": str(e)[:500]}
            await broadcast_event("pet-status-update", {"status": "error"})
            yield {"type": "done"}

        finally:
            # 清理 per-conversation endpoint override
            if _endpoint_switched and conversation_id:
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client and hasattr(llm_client, "restore_default"):
                    try:
                        llm_client.restore_default(conversation_id=conversation_id)
                    except Exception:
                        pass

    # ==================== Unified Async Generator Interface ====================

    async def run_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        base_system_prompt: str = "",
        task_description: str = "",
        task_monitor: Any = None,
        session_type: str = "desktop",
        mode: str = "agent",
        endpoint_override: str | None = None,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        agent_profile_id: str = "default",
        session: Any = None,
        force_tool_retries: int | None = None,
        is_sub_agent: bool = False,
    ):
        """
        统一流式接口: 将 reason_stream 包装为标准化异步生成器。

        所有流式事件通过 async for 消费，调用方无需关注内部循环细节。
        与 run() 保持相同的功能集（重试、回滚、取消等），同时支持:
        - Token 预算警告注入
        - 可观测性 metrics
        - 标准化事件格式

        Yields dict events (same format as reason_stream).
        """
        try:
            from .token_budget import TokenBudget

            budget = TokenBudget()

            # Parse budget from last user message
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        from .token_budget import parse_token_budget
                        parsed = parse_token_budget(content)
                        if parsed:
                            budget.total_limit = parsed
                    break
        except ImportError:
            budget = None

        async for event in self.reason_stream(
            messages,
            tools=tools,
            system_prompt=system_prompt,
            base_system_prompt=base_system_prompt,
            task_description=task_description,
            task_monitor=task_monitor,
            session_type=session_type,
            mode=mode,
            endpoint_override=endpoint_override,
            conversation_id=conversation_id,
            thinking_mode=thinking_mode,
            thinking_depth=thinking_depth,
            agent_profile_id=agent_profile_id,
            session=session,
            force_tool_retries=force_tool_retries,
            is_sub_agent=is_sub_agent,
        ):
            # Track token usage for budget
            if budget and event.get("type") == "usage":
                tokens = event.get("total_tokens", 0)
                if tokens:
                    budget.record(tokens)
                    warning = budget.get_warning_message()
                    if warning:
                        yield {"type": "budget_warning", "message": warning}
                    if budget.is_exceeded:
                        yield {
                            "type": "budget_exceeded",
                            "message": f"Token budget exceeded: "
                                       f"{budget.used:,}/{budget.total_limit:,}",
                        }
                        yield {"type": "done", "reason": "budget_exceeded"}
                        return

            yield event

    # ==================== 思维链叙事辅助 ====================

    @staticmethod
    def _describe_tool_call(tool_name: str, tool_args: dict) -> str:
        """为工具调用生成人类可读的叙事描述。"""
        args = tool_args if isinstance(tool_args, dict) else {}
        match tool_name:
            case "read_file":
                path = args.get("path") or args.get("file") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "文件"
                return f"正在读取 {fname}..."
            case "write_file":
                path = args.get("path") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "文件"
                return f"正在写入 {fname}..."
            case "edit_file":
                path = args.get("path") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "文件"
                return f"正在编辑 {fname}..."
            case "grep" | "search" | "ripgrep" | "search_files":
                pattern = str(args.get("pattern") or args.get("query") or "")[:50]
                return f'搜索 "{pattern}"...'
            case "web_search":
                query = str(args.get("query") or "")[:50]
                return f'在网上搜索 "{query}"...'
            case "execute_code" | "run_code" | "run_command":
                cmd = str(args.get("command") or args.get("code") or "")[:60]
                return f"执行命令: {cmd}..." if cmd else "执行代码..."
            case "browser_navigate":
                url = str(args.get("url") or "")[:60]
                return f"访问 {url}..."
            case "browser_screenshot":
                return "截取页面截图..."
            case "create_todo":
                summary = str(args.get("task_summary") or "")[:40]
                return f"制定计划: {summary}..."
            case "update_todo_step":
                idx = args.get("step_index", "")
                status = args.get("status", "")
                return f"更新计划步骤 {idx} → {status}"
            case "switch_persona":
                preset = args.get("preset_name", "")
                return f"切换角色: {preset}..."
            case "get_persona_profile":
                return "获取当前人格配置..."
            case "ask_user":
                q = str(args.get("question") or "")[:40]
                return f'向用户提问: "{q}"...'
            case "list_files" | "list_dir":
                path = str(args.get("path") or args.get("directory") or ".")
                return f"列出目录 {path}..."
            case "deliver_artifacts":
                return "交付文件..."
            case _:
                params = ", ".join(f"{k}" for k in list(args.keys())[:3])
                return f"调用 {tool_name}({params})..."

    @staticmethod
    def _summarize_tool_result(tool_name: str, result_text: str) -> str:
        """为工具结果生成简短叙事摘要。"""
        if not result_text:
            return ""
        r = result_text.strip()
        is_error = any(m in r[:200] for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "Tool error:", "⚠️ 策略拒绝:"])
        if is_error:
            # 提取第一行错误信息
            first_line = r.split("\n")[0][:120]
            return f"出错: {first_line}"
        r_len = len(r)
        match tool_name:
            case "read_file":
                lines = r.count("\n") + 1
                return f"已读取 ({lines} 行, {r_len} 字符)"
            case "grep" | "search" | "ripgrep" | "search_files":
                matches = r.count("\n") + 1 if r else 0
                return f"找到 {matches} 条结果" if matches > 0 else "无匹配结果"
            case "web_search":
                return f"搜索完成 ({r_len} 字符)"
            case "execute_code" | "run_code" | "run_command":
                lines = r.count("\n") + 1
                preview = r[:80].replace("\n", " ")
                return f"执行完成: {preview}{'...' if r_len > 80 else ''}"
            case "write_file" | "edit_file":
                return "写入成功" if "成功" in r or "ok" in r.lower() or r_len < 100 else f"完成 ({r_len} 字符)"
            case "browser_screenshot":
                return "截图已获取"
            case "desktop_screenshot":
                return "桌面截图已保存"
            case "deliver_artifacts":
                try:
                    import json as _json
                    _d = _json.loads(r)
                    _n = len(_d.get("receipts", []))
                    return f"已交付 {_n} 个文件" if _n else ""
                except Exception:
                    return ""
            case "switch_persona":
                return "切换完成"
            case _:
                if r_len < 100:
                    return r[:100]
                return f"完成 ({r_len} 字符)"

    # ==================== ReAct 推理链保存 ====================

    def _save_react_trace(
        self,
        react_trace: list[dict],
        conversation_id: str | None,
        session_type: str,
        result: str,
        started_at: str,
        working_messages: list[dict] | None = None,
    ) -> None:
        """
        保存完整的 ReAct 推理链到文件。

        同时暂存到 self._last_react_trace 供 agent_handler 读取（思维链功能）。
        若传入 working_messages，一并暂存供 token 统计读取。

        路径: data/react_traces/{date}/trace_{conversation_id}_{timestamp}.json
        """
        # 思维链: 暂存 trace 供外部读取（即使为空也更新，清除旧数据）
        self._last_react_trace = react_trace or []
        if working_messages is not None:
            self._last_working_messages = working_messages

        _tc_count = sum(len(t.get("tool_calls", [])) for t in (react_trace or []))
        _tr_count = sum(len(t.get("tool_results", [])) for t in (react_trace or []))
        logger.debug(
            f"[ReAct] _save_react_trace: result={result}, "
            f"iterations={len(react_trace or [])}, "
            f"tool_calls={_tc_count}, tool_results={_tr_count}"
        )

        if not react_trace:
            return

        try:
            date_str = datetime.now().strftime("%Y%m%d")
            trace_dir = Path("data/react_traces") / date_str
            trace_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%H%M%S")
            cid_part = (conversation_id or "unknown")[:16].replace(":", "_")
            trace_file = trace_dir / f"trace_{cid_part}_{timestamp}.json"

            # 汇总统计
            total_in = sum(it.get("tokens", {}).get("input", 0) for it in react_trace)
            total_out = sum(it.get("tokens", {}).get("output", 0) for it in react_trace)
            all_tools = []
            for it in react_trace:
                for tc in it.get("tool_calls", []):
                    name = tc.get("name")
                    if name and name not in all_tools:
                        all_tools.append(name)

            trace_data = {
                "conversation_id": conversation_id or "",
                "session_type": session_type,
                "model": react_trace[0].get("model", "") if react_trace else "",
                "started_at": started_at,
                "ended_at": datetime.now().isoformat(),
                "total_iterations": len(react_trace),
                "total_tokens": {"input": total_in, "output": total_out},
                "tools_used": all_tools,
                "result": result,
                "iterations": react_trace,
            }

            with open(trace_file, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(
                f"[ReAct] Trace saved: {trace_file} "
                f"(iterations={len(react_trace)}, tools={all_tools}, "
                f"tokens_in={total_in}, tokens_out={total_out})"
            )

            # 清理超过 7 天的旧 trace 文件
            self._cleanup_old_traces(Path("data/react_traces"), max_age_days=7)

        except Exception as e:
            logger.warning(f"[ReAct] Failed to save trace: {e}")

    def _cleanup_old_traces(self, base_dir: Path, max_age_days: int = 7) -> None:
        """清理超过指定天数的旧 trace 日期目录"""
        try:
            if not base_dir.exists():
                return
            cutoff = time.time() - max_age_days * 86400
            for date_dir in base_dir.iterdir():
                if date_dir.is_dir() and date_dir.stat().st_mtime < cutoff:
                    import shutil
                    shutil.rmtree(date_dir, ignore_errors=True)
        except Exception:
            pass

    # ==================== 取消收尾工具 ====================

    def _reset_structural_cooldown_after_farewell(self):
        """farewell 调用失败后清除 structural cooldown，防止毒化后续正常请求。"""
        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            if not llm_client:
                return
            providers = getattr(llm_client, "_providers", {})
            for name, provider in providers.items():
                if not provider.is_healthy and provider.error_category == "structural":
                    provider.reset_cooldown()
                    logger.info(
                        f"[CancelFarewell] Reset structural cooldown for endpoint {name}"
                    )
        except Exception as exc:
            logger.debug(f"[CancelFarewell] Failed to reset cooldown: {exc}")

    @staticmethod
    def _sanitize_messages_for_farewell(messages: list[dict]) -> list[dict]:
        """
        清理 working_messages 使其可安全发送给 LLM 的 farewell 调用。

        问题：assistant 消息包含 tool_calls 但缺少对应的 tool result 时，
        LLM API 会返回 400：'tool_calls must be followed by tool messages'。
        这可能出现在尾部（中断时最后一轮未完成）或中间（rollback 后残留）。

        策略：全量扫描，收集所有 tool_call_id 及其 tool result 匹配情况，
        移除所有未闭合的 assistant(tool_calls) 及其孤立的 tool result。
        """
        if not messages:
            return messages

        answered_tool_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                answered_tool_ids.add(msg["tool_call_id"])

        result: list[dict] = []
        skip_tool_call_ids: set[str] = set()

        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant" and msg.get("tool_calls"):
                tc_ids = [
                    tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")
                ]
                missing = [tid for tid in tc_ids if tid not in answered_tool_ids]
                if missing:
                    skip_tool_call_ids.update(tc_ids)
                    continue
                result.append(msg)
            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in skip_tool_call_ids:
                    continue
                result.append(msg)
            else:
                result.append(msg)

        if not result:
            result = [{"role": "user", "content": "（对话上下文不可用）"}]

        return result

    async def _cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        state: TaskState | None = None,
    ) -> str:
        """非流式场景下的取消收尾：立即返回默认文本，后台异步发起 LLM 收尾。"""
        cancel_reason = (state.cancel_reason if state else "") or "用户请求停止"
        logger.info(
            f"[ReAct][CancelFarewell] 进入收尾流程: cancel_reason={cancel_reason!r}, "
            f"model={current_model}, msg_count={len(working_messages)}"
        )

        default_farewell = "✅ 好的，已停止当前任务。"

        asyncio.create_task(
            self._background_cancel_farewell(
                list(working_messages), system_prompt, current_model, cancel_reason
            )
        )

        return default_farewell

    # ==================== 取消收尾（流式） ====================

    async def _stream_cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        state: TaskState | None = None,
    ):
        """流式场景下的取消收尾：立即返回默认文本，后台异步发起 LLM 收尾。

        Yields:
            {"type": "user_insert", ...} 和 {"type": "text_delta", ...} 事件
        """
        cancel_reason = (state.cancel_reason if state else "") or "用户请求停止"
        logger.info(
            f"[ReAct-Stream][CancelFarewell] 进入收尾流程: cancel_reason={cancel_reason!r}, "
            f"model={current_model}, msg_count={len(working_messages)}"
        )

        user_text = ""
        if cancel_reason.startswith("用户发送停止指令: "):
            user_text = cancel_reason[len("用户发送停止指令: "):]
        elif cancel_reason.startswith("用户发送跳过指令: "):
            user_text = cancel_reason[len("用户发送跳过指令: "):]
        if user_text:
            logger.info(f"[ReAct-Stream][CancelFarewell] 回传用户指令文本: {user_text!r}")
            yield {"type": "user_insert", "content": user_text}

        default_farewell = "✅ 好的，已停止当前任务。"
        yield {"type": "text_delta", "content": default_farewell}

        asyncio.create_task(
            self._background_cancel_farewell(
                list(working_messages), system_prompt, current_model, cancel_reason
            )
        )

    async def _background_cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        cancel_reason: str,
    ) -> None:
        """后台执行 LLM 收尾调用，将结果持久化到上下文（不阻塞用户）。"""
        try:
            cancel_msg = (
                f"[系统通知] 用户发送了停止指令「{cancel_reason}」，"
                "请立即停止当前操作，简要告知用户已停止以及当前进度（1~2 句话即可）。"
                "不要调用任何工具。"
            )
            farewell_messages = self._sanitize_messages_for_farewell(working_messages)
            farewell_messages.append({"role": "user", "content": cancel_msg})

            _tt = set_tracking_context(TokenTrackingContext(
                operation_type="farewell", channel="api",
            ))
            try:
                farewell_response = await asyncio.wait_for(
                    self._brain.messages_create_async(
                        model=current_model,
                        max_tokens=200,
                        system=system_prompt,
                        tools=[],
                        messages=farewell_messages,
                    ),
                    timeout=5.0,
                )
                for block in farewell_response.content:
                    if block.type == "text" and block.text.strip():
                        logger.info(
                            f"[ReAct-Stream][BgFarewell] LLM farewell 完成: "
                            f"{block.text.strip()[:100]}"
                        )
                        break
            except TimeoutError:
                logger.warning("[ReAct-Stream][BgFarewell] LLM farewell 超时 (5s)")
            except Exception as e:
                logger.warning(f"[ReAct-Stream][BgFarewell] LLM farewell 失败: {e}")
                self._reset_structural_cooldown_after_farewell()
            finally:
                reset_tracking_context(_tt)
        except Exception as e:
            logger.warning(f"[ReAct-Stream][BgFarewell] 后台收尾异常: {e}")

    # ==================== 流式推理 ====================

    _HEARTBEAT_INTERVAL = 15  # 秒：无事件时心跳间隔

    async def _reason_stream_iter(
        self,
        messages: list[dict],
        *,
        system_prompt: str,
        tools: list[dict],
        current_model: str,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        iteration: int = 0,
        agent_profile_id: str = "default",
    ):
        """流式推理迭代器：即时 yield text/thinking delta，流结束后 yield Decision。

        参考 Claude Code (claude.ts) 的 for-await 事件循环模式：
        - 每个 LLM token 到达时即通过 StreamAccumulator 产出高层事件
        - 流结束后从累积状态构建 Decision 对象

        Yields:
            {"type": "text_delta", "content": "..."}
            {"type": "thinking_delta", "content": "..."}
            {"type": "heartbeat"}
            {"type": "decision", "decision": Decision}
        """
        import time as _time

        from .stream_accumulator import StreamAccumulator, post_process_streamed_decision

        acc = StreamAccumulator()
        last_yield_time = _time.monotonic()

        state = (
            self._state.get_task_for_session(conversation_id)
            if conversation_id
            else None
        ) or self._state.current_task
        cancel_event = state.cancel_event if state else asyncio.Event()

        use_thinking = None
        if thinking_mode == "on":
            use_thinking = True
        elif thinking_mode == "off":
            use_thinking = False

        tracer = get_tracer()
        with tracer.llm_span(model=current_model) as span:
            async for raw_event in self._brain.messages_create_stream(
                use_thinking=use_thinking,
                thinking_depth=thinking_depth,
                model=current_model,
                max_tokens=self._brain.max_tokens,
                system=system_prompt,
                tools=tools,
                messages=messages,
                conversation_id=conversation_id,
                iteration=iteration,
                agent_profile_id=agent_profile_id,
            ):
                if cancel_event.is_set():
                    cancel_reason = state.cancel_reason if state else "用户请求停止"
                    raise UserCancelledError(
                        reason=cancel_reason,
                        source="llm_stream",
                    )

                for high_event in acc.feed(raw_event):
                    yield high_event
                    last_yield_time = _time.monotonic()

                now = _time.monotonic()
                if now - last_yield_time > self._HEARTBEAT_INTERVAL:
                    yield {"type": "heartbeat"}
                    last_yield_time = now

            # 流结束 → 构建 Decision
            decision = acc.build_decision()
            raw_streamed_text = decision.text_content or ""
            post_process_streamed_decision(decision)

            if acc.usage:
                in_tok = acc.usage.get("input_tokens", 0)
                out_tok = acc.usage.get("output_tokens", 0)
                span.set_attribute("input_tokens", in_tok)
                span.set_attribute("output_tokens", out_tok)

            span.set_attribute("decision_type", decision.type.value)
            span.set_attribute("tool_count", len(decision.tool_calls))

            yield {
                "type": "decision",
                "decision": decision,
                "usage": acc.usage,
                "raw_streamed_text": raw_streamed_text,
            }

    # ==================== 心跳保活（非流式路径使用） ====================

    async def _reason_with_heartbeat(
        self,
        messages: list[dict],
        *,
        system_prompt: str,
        tools: list[dict],
        current_model: str,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        iteration: int = 0,
        agent_profile_id: str = "default",
    ):
        """
        包装 _reason()，在等待 LLM 响应期间每隔 HEARTBEAT_INTERVAL 秒
        产出 heartbeat 事件，防止前端 SSE idle timeout。

        同时监听 cancel_event，当用户取消时立即中断 LLM 调用并抛出 UserCancelledError。

        Yields:
            {"type": "heartbeat"} 或 {"type": "decision", "decision": Decision}
        """
        queue: asyncio.Queue = asyncio.Queue()

        # 获取当前 session 对应的 cancel_event（避免跨会话误取消）
        state = (
            self._state.get_task_for_session(conversation_id)
            if conversation_id
            else None
        ) or self._state.current_task
        cancel_event = state.cancel_event if state else asyncio.Event()

        async def _do_reason():
            try:
                decision = await self._reason(
                    messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    current_model=current_model,
                    conversation_id=conversation_id,
                    thinking_mode=thinking_mode,
                    thinking_depth=thinking_depth,
                    iteration=iteration,
                    agent_profile_id=agent_profile_id,
                    cancel_event=cancel_event,
                )
                await queue.put(("result", decision))
            except Exception as exc:
                await queue.put(("error", exc))

        async def _heartbeat_loop():
            try:
                while True:
                    await asyncio.sleep(self._HEARTBEAT_INTERVAL)
                    await queue.put(("heartbeat", None))
            except asyncio.CancelledError:
                pass

        async def _cancel_watcher():
            """监听 cancel_event，触发时通过 queue 通知主循环"""
            try:
                await cancel_event.wait()
                await queue.put(("cancelled", None))
            except asyncio.CancelledError:
                pass

        reason_task = asyncio.create_task(_do_reason())
        hb_task = asyncio.create_task(_heartbeat_loop())
        cancel_task = asyncio.create_task(_cancel_watcher())

        try:
            while True:
                typ, data = await queue.get()
                if typ == "heartbeat":
                    yield {"type": "heartbeat"}
                elif typ == "cancelled":
                    cancel_reason = state.cancel_reason if state else "用户请求停止"
                    raise UserCancelledError(
                        reason=cancel_reason,
                        source="llm_call_stream",
                    )
                elif typ == "error":
                    raise data  # 传播 _reason 的异常
                else:
                    yield {"type": "decision", "decision": data}
                    break
        finally:
            hb_task.cancel()
            cancel_task.cancel()
            if not reason_task.done():
                reason_task.cancel()
                try:
                    await reason_task
                except (asyncio.CancelledError, Exception):
                    pass

    # ==================== 推理阶段 ====================

    async def _reason(
        self,
        messages: list[dict],
        *,
        system_prompt: str,
        tools: list[dict],
        current_model: str,
        conversation_id: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        iteration: int = 0,
        agent_profile_id: str = "default",
        cancel_event: asyncio.Event | None = None,
    ) -> Decision:
        """
        推理阶段: 调用 LLM，返回结构化 Decision。
        """
        # 根据 thinking_mode 决定 use_thinking 参数
        use_thinking = None  # None = 让 Brain 使用默认逻辑
        if thinking_mode == "on":
            use_thinking = True
        elif thinking_mode == "off":
            use_thinking = False
        # "auto" 或 None: use_thinking=None → Brain 使用自身默认逻辑

        tracer = get_tracer()
        with tracer.llm_span(model=current_model) as span:
            _tt = set_tracking_context(TokenTrackingContext(
                session_id=conversation_id or "",
                operation_type="chat_react_iteration",
                channel="api",
                iteration=iteration,
                agent_profile_id=agent_profile_id,
            ))
            try:
                response = await self._brain.messages_create_async(
                    use_thinking=use_thinking,
                    thinking_depth=thinking_depth,
                    cancel_event=cancel_event,
                    model=current_model,
                    max_tokens=self._brain.max_tokens,
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                    conversation_id=conversation_id,
                )
            finally:
                reset_tracking_context(_tt)

            # 记录 token 使用
            if hasattr(response, "usage"):
                span.set_attribute("input_tokens", getattr(response.usage, "input_tokens", 0))
                span.set_attribute("output_tokens", getattr(response.usage, "output_tokens", 0))

            decision = self._parse_decision(response)
            span.set_attribute("decision_type", decision.type.value)
            span.set_attribute("tool_count", len(decision.tool_calls))
            return decision

    def _parse_decision(self, response: Any) -> Decision:
        """解析 LLM 响应为 Decision"""
        tool_calls = []
        text_content = ""
        thinking_content = ""
        assistant_content = []

        for block in response.content:
            if block.type == "thinking":
                thinking_text = block.thinking if hasattr(block, "thinking") else str(block)
                thinking_content += thinking_text if isinstance(thinking_text, str) else str(thinking_text)
                assistant_content.append({
                    "type": "thinking",
                    "thinking": thinking_text,
                })
            elif block.type == "text":
                raw_text = block.text
                # brain.py 将 OpenAI-compatible 的 reasoning_content 包装为 <thinking> 标签
                # 嵌入 TextBlock；Qwen3/MiniMax 可能产出 <think> 标签。
                # 将其正确路由到 thinking_content 避免原始标签泄漏到前端，
                # assistant_content 保留原文（消息历史需要标签用于下轮提取）。
                if "<thinking>" in raw_text or "<think>" in raw_text:
                    display_text = strip_thinking_tags(raw_text)
                    if display_text != raw_text and not thinking_content:
                        import re
                        m = re.search(r"<think(?:ing)?>(.*?)</think(?:ing)?>", raw_text, re.DOTALL)
                        if m:
                            thinking_content = m.group(1).strip()
                else:
                    display_text = raw_text
                text_content += display_text
                assistant_content.append({"type": "text", "text": raw_text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # 防御层：如果 provider 层未能从 thinking 内容中提取嵌入的工具调用，
        # 在此做最后一次检查（MiniMax-M2.5 已知会将 <minimax:tool_call> 嵌入 thinking 块）
        if not tool_calls and thinking_content:
            try:
                from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls
                if has_text_tool_calls(thinking_content):
                    _, embedded_tool_calls = parse_text_tool_calls(thinking_content)
                    if embedded_tool_calls:
                        for tc in embedded_tool_calls:
                            tool_calls.append({
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.input,
                            })
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.input,
                            })
                        logger.warning(
                            f"[_parse_decision] Recovered {len(embedded_tool_calls)} tool calls "
                            f"from thinking content (provider-level extraction missed)"
                        )
            except Exception as e:
                logger.debug(f"[_parse_decision] Thinking tool-call check failed: {e}")

        # 防御层：从 text_content 中提取嵌入的工具调用（Python dot-style 等）。
        # 部分模型（如 qwen3-coder, qwen3.5）不使用原生 function calling，
        # 而是在文本中输出 .web_search(query="...") 风格的工具调用。
        if not tool_calls and text_content:
            try:
                from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls
                if has_text_tool_calls(text_content):
                    _clean, embedded_tool_calls = parse_text_tool_calls(text_content)
                    if embedded_tool_calls:
                        text_content = _clean
                        for tc in embedded_tool_calls:
                            tool_calls.append({
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.input,
                            })
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.input,
                            })
                        logger.warning(
                            f"[_parse_decision] Recovered {len(embedded_tool_calls)} tool calls "
                            f"from text content: {[tc.name for tc in embedded_tool_calls]}"
                        )
            except Exception as e:
                logger.debug(f"[_parse_decision] Text tool-call check failed: {e}")

        # 防御层：剥离 text_content 末尾的裸工具名。
        # 部分模型会在 content 中输出 "用户原文\nbrowser_open" 这类垃圾，
        # 其中裸工具名既不是合法工具调用（无参数/格式），也不是有意义的回复。
        # 仅在 text_content 较短（<200 字符）时触发，避免误伤正常长文本。
        if text_content and len(text_content.strip()) < 200:
            import re
            _lines = text_content.strip().split("\n")
            _last = _lines[-1].strip() if _lines else ""
            if re.match(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$", _last):
                text_content = "\n".join(_lines[:-1]).strip()
                logger.warning(
                    f"[_parse_decision] Stripped bare tool name '{_last}' from text_content"
                )

        decision_type = DecisionType.TOOL_CALLS if tool_calls else DecisionType.FINAL_ANSWER

        return Decision(
            type=decision_type,
            text_content=text_content,
            tool_calls=tool_calls,
            thinking_content=thinking_content,
            raw_response=response,
            stop_reason=getattr(response, "stop_reason", ""),
            assistant_content=assistant_content,
        )

    @staticmethod
    def _build_fallback_summary(
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
    ) -> str | None:
        """当 LLM 多次未返回可见文本时，从工具执行记录构建 fallback 摘要。"""
        parts: list[str] = []

        if delivery_receipts:
            for r in delivery_receipts:
                desc = r.get("description") or r.get("summary") or r.get("title") or ""
                if desc:
                    parts.append(f"• {desc}")
            if parts:
                return "已完成以下操作：\n" + "\n".join(parts)

        if executed_tool_names:
            unique = list(dict.fromkeys(executed_tool_names))
            tool_summary = "、".join(unique[:10])
            if len(unique) > 10:
                tool_summary += f" 等共 {len(unique)} 项"
            return f"任务已执行完毕（使用了工具：{tool_summary}），但模型未生成文本总结。如需详情请重新提问。"

        return None

    # ==================== 最终答案处理 ====================

    async def _handle_final_answer(
        self,
        *,
        decision: Decision,
        working_messages: list[dict],
        original_messages: list[dict],
        tools_executed_in_task: bool,
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
        no_tool_call_count: int,
        verify_incomplete_count: int,
        no_confirmation_text_count: int,
        max_no_tool_retries: int,
        max_verify_retries: int,
        max_confirmation_text_retries: int,
        base_force_retries: int,
        conversation_id: str | None,
        supervisor_intervened: bool = False,
    ) -> str | tuple:
        """
        处理纯文本响应（无工具调用）。

        Returns:
            str: 最终答案
            tuple: (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries) - 需要继续循环
        """
        if tools_executed_in_task:
            cleaned_text = strip_thinking_tags(decision.text_content)
            _, cleaned_text = parse_intent_tag(cleaned_text)
            if cleaned_text and len(cleaned_text.strip()) > 0:
                is_completed = await self._response_handler.verify_task_completion(
                    user_request=ResponseHandler.get_last_user_request(original_messages),
                    assistant_response=cleaned_text,
                    executed_tools=executed_tool_names,
                    delivery_receipts=delivery_receipts,
                    conversation_id=conversation_id,
                    bypass=supervisor_intervened,
                )

                if is_completed:
                    return cleaned_text

                verify_incomplete_count += 1

                has_todo_pending = self._has_active_todo_pending(conversation_id)
                effective_max = max_verify_retries + 1 if has_todo_pending else max_verify_retries

                is_in_progress_promise = self._is_in_progress_promise(cleaned_text)

                if verify_incomplete_count >= effective_max:
                    if is_in_progress_promise and verify_incomplete_count <= effective_max + 1:
                        logger.warning(
                            "[TaskVerify] Verify retries exhausted but response is an "
                            "in-progress promise (no actual execution). "
                            "Forcing one final tool-execution round."
                        )
                        working_messages.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": decision.text_content}],
                            "reasoning_content": decision.thinking_content or None,
                        })
                        working_messages.append({
                            "role": "user",
                            "content": (
                                "[系统] ⚠️ 严重警告：你已经连续多轮只是在描述将要做什么，"
                                "但从未实际调用工具执行。系统日志确认你没有生成任何文件。"
                                "文字描述≠实际执行。"
                                "请立即调用 run_shell 或 write_file 等工具来完成实际操作，"
                                "不要再输出任何描述性文字。"
                            ),
                        })
                        return (working_messages, no_tool_call_count, verify_incomplete_count,
                                no_confirmation_text_count, max_no_tool_retries)
                    return cleaned_text

                # 继续循环
                working_messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": decision.text_content}],
                    "reasoning_content": decision.thinking_content or None,
                })

                if has_todo_pending:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 当前 Plan 仍有未完成的步骤。"
                            "请立即继续执行下一个 pending 步骤。"
                        ),
                    })
                elif is_in_progress_promise:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统] ⚠️ 你的上一条回复只是在描述将要执行的操作，"
                            "但系统日志确认你没有调用任何工具（tool_calls=0）。"
                            "文字描述不等于实际执行。"
                            "请立即调用所需工具来完成任务，不要只输出文字说明。"
                        ),
                    })
                else:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 根据复核判断，用户请求可能还有未完成的部分。"
                            "如果确实还有剩余步骤，请继续调用工具执行；"
                            "如果已全部完成，请给用户一个包含结果的总结回复。"
                        ),
                    })
                return (working_messages, no_tool_call_count, verify_incomplete_count,
                        no_confirmation_text_count, max_no_tool_retries)
            else:
                # 无可见文本
                no_confirmation_text_count += 1
                if no_confirmation_text_count <= max_confirmation_text_retries:
                    if no_confirmation_text_count == 1:
                        retry_prompt = (
                            "[系统] 你已执行过工具，但你刚才没有输出任何用户可见的文字确认。"
                            "请基于已产生的 tool_result 证据，给出最终答复。"
                        )
                    else:
                        retry_prompt = (
                            "[系统] 警告：你已连续多次未输出可见文字。"
                            "请立即用一两句话简要总结你完成了什么，不要调用任何工具，不要输出思考过程。"
                        )
                    working_messages.append({
                        "role": "user",
                        "content": retry_prompt,
                    })
                    return (working_messages, no_tool_call_count, verify_incomplete_count,
                            no_confirmation_text_count, max_no_tool_retries)

                # 所有重试用尽，尝试从工具执行记录构建 fallback 摘要
                fallback = self._build_fallback_summary(executed_tool_names, delivery_receipts)
                if fallback:
                    logger.warning(
                        "[ForceToolCall] LLM returned empty confirmation; using fallback summary from tool history"
                    )
                    return fallback

                # thinking 内容不为空时，从 thinking 中提取可用信息
                if decision.thinking_content:
                    thinking_text = decision.thinking_content.strip()
                    if len(thinking_text) > 20:
                        logger.warning(
                            "[ForceToolCall] LLM returned empty visible text but has thinking content; "
                            "extracting summary from thinking"
                        )
                        preview = thinking_text[:500]
                        return f"（以下为模型内部推理摘要，原始回复未生成可见文本）\n\n{preview}"

                return (
                    "⚠️ 大模型返回异常：工具已执行，但多次未返回任何可见文本确认，任务已中断。"
                    "请重试、或切换到更稳定的端点/模型后再继续。"
                )

        # 未执行过工具 — 解析意图声明标记
        intent, stripped_text = parse_intent_tag(decision.text_content or "")
        logger.info(
            f"[IntentTag] intent={intent or 'NONE'}, "
            f"has_tool_calls=False, tools_executed_in_task=False, "
            f"text_preview=\"{(stripped_text or '')[:80].replace(chr(10), ' ')}\""
        )

        # Model glitch: LLM returned empty content (content: []) but consumed
        # output tokens on internal reasoning. Retry silently without counting
        # against the ForceToolCall budget.
        _empty_retry_attr = "_empty_content_retries"
        empty_retries = getattr(self, _empty_retry_attr, 0)
        if (
            not stripped_text
            and not decision.thinking_content
            and intent is None
            and empty_retries < 2
        ):
            setattr(self, _empty_retry_attr, empty_retries + 1)
            logger.warning(
                f"[EmptyContent] LLM returned empty content (attempt {empty_retries + 1}/2), "
                f"silent retry without counting against ForceToolCall budget"
            )
            working_messages.append({
                "role": "user",
                "content": "[系统] 你的上一次回复为空。请直接回复用户的问题。",
            })
            return (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries)

        # ── 对话式回复放行（参照 claude-code needsFollowUp 模式） ──
        if intent is None and stripped_text:
            _clean = stripped_text.strip()
            _action_claims_quick = (
                "已完成", "已执行", "已保存", "已发送", "已创建", "已修改",
                "已删除", "文件已", "脚本已", "命令已",
            )
            _has_action_claim = any(kw in _clean for kw in _action_claims_quick)
            if len(_clean) > 80 and not _has_action_claim:
                logger.info(
                    f"[IntentTag] No tag, substantial text reply "
                    f"(len={len(_clean)}), accepting as valid response"
                )
                return clean_llm_response(stripped_text)
            if self._is_conversational_reply(stripped_text, working_messages):
                logger.info(
                    f"[IntentTag] No tag, conversational reply detected, "
                    f"accepting as valid response"
                )
                return clean_llm_response(stripped_text)

        if intent == "REPLY" and stripped_text and len(stripped_text.strip()) > 10:
            logger.info(
                "[IntentTag] REPLY intent with substantial text, "
                "accepting as valid response (no ForceToolCall)"
            )
            return clean_llm_response(stripped_text)

        max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)
        no_tool_call_count += 1

        if no_tool_call_count <= max_no_tool_retries:
            if stripped_text:
                working_messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": stripped_text}],
                    "reasoning_content": decision.thinking_content or None,
                })
            if intent == "REPLY":
                logger.warning(
                    f"[IntentTag] REPLY intent but text too short — "
                    f"ForceToolCall retry ({no_tool_call_count}/{max_no_tool_retries})"
                )
                retry_msg = (
                    "[系统] 你的回复过于简短，请提供更详细的回答。"
                )
            elif intent == "ACTION":
                logger.warning(
                    "[IntentTag] ACTION intent declared but no tool calls — "
                    "hallucination detected, forcing retry"
                )
                retry_msg = (
                    "[系统] ⚠️ 你声明了 [ACTION] 意图但没有调用任何工具。"
                    "请立即调用所需的工具来完成用户请求，不要只描述你会做什么。"
                )
            else:
                logger.warning(
                    f"[IntentTag] No intent tag, short text with action claims, tool_calls=0 — "
                    f"ForceToolCall retry "
                    f"({no_tool_call_count}/{max_no_tool_retries})"
                )
                retry_msg = (
                    "[系统] ⚠️ 你的上一条回复没有调用任何工具（系统日志确认 tool_calls=0）。"
                    "文字描述不等于实际执行。请立即调用工具完成用户的请求。"
                )
            working_messages.append({"role": "user", "content": retry_msg})
            return (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries)

        # 追问次数用尽
        cleaned_text = clean_llm_response(stripped_text)
        return cleaned_text or (
            "⚠️ 大模型返回异常：未产生可用输出。任务已中断。"
            "请重试、或更换端点/模型后再执行。"
        )

    # ==================== 循环检测 ====================

    # ==================== 模型切换 ====================

    def _check_model_switch(
        self,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> tuple[str, list[dict]] | None:
        """检查是否需要模型切换。返回 (new_model, new_messages) 或 None"""
        if not task_monitor or not task_monitor.should_switch_model:
            return None

        new_model = task_monitor.fallback_model
        self._switch_llm_endpoint(new_model, reason="task_monitor timeout fallback")
        task_monitor.switch_model(
            new_model,
            "任务超时后切换",
            reset_context=True,
        )

        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            current = llm_client.get_current_model() if llm_client else None
            new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append({
            "role": "user",
            "content": (
                "[系统提示] 发生模型切换：之前的 tool_use/tool_result 历史已清除。"
                "请从头开始处理用户请求。"
            ),
        })

        # 注意：_check_model_switch 不做状态转换，因为它不使用 continue，
        # 执行后自然走到主循环的 REASONING 转换逻辑。
        state.reset_for_model_switch()
        return new_model, new_messages

    # 最大模型切换次数（防止死循环）
    MAX_MODEL_SWITCHES = 2

    # 跨模型切换的全局重试上限：达到后立即终止并告知用户
    MAX_TOTAL_LLM_RETRIES = 3

    @staticmethod
    def _strip_heavy_content(messages: list[dict]) -> tuple[list[dict], bool]:
        """从消息中剥离重型多媒体内容（视频/大 data URL），替换为文字描述。

        Returns:
            (处理后的消息列表, 是否有内容被剥离)
        """
        DATA_URL_SIZE_THRESHOLD = 5 * 1024 * 1024  # 5MB
        stripped = False
        result = []

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                result.append(msg)
                continue

            new_parts = []
            for part in content:
                part_type = part.get("type", "")

                if part_type == "video_url":
                    url = (part.get("video_url") or {}).get("url", "")
                    if len(url) > DATA_URL_SIZE_THRESHOLD:
                        new_parts.append({
                            "type": "text",
                            "text": "[视频内容已移除：视频文件过大，超过 API data-uri 限制。请发送更小的视频文件。]",
                        })
                        stripped = True
                        continue

                elif part_type == "video":
                    source = part.get("source", {})
                    data = source.get("data", "")
                    if len(data) > DATA_URL_SIZE_THRESHOLD:
                        new_parts.append({
                            "type": "text",
                            "text": "[视频内容已移除：视频文件过大，超过 API data-uri 限制。请发送更小的视频文件。]",
                        })
                        stripped = True
                        continue

                elif part_type == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if len(url) > DATA_URL_SIZE_THRESHOLD:
                        new_parts.append({
                            "type": "text",
                            "text": "[图片内容已移除：文件过大，超过 API 限制。]",
                        })
                        stripped = True
                        continue

                new_parts.append(part)

            result.append({**msg, "content": new_parts})

        return result, stripped

    @staticmethod
    def _strip_tool_results_for_content_safety(
        messages: list[dict],
    ) -> tuple[list[dict], bool]:
        """Strip recent tool result content that may have triggered content safety filters.

        When the LLM API rejects a request due to content inspection (e.g. DashScope
        DataInspectionFailed), the cause is typically inappropriate text in the most
        recent batch of tool results (e.g. web search returning NSFW content).

        This method finds the last user message containing tool_results and replaces
        each tool_result's content with a safe placeholder, allowing the LLM to
        continue reasoning with the remaining context.
        """
        _PLACEHOLDER = (
            "[工具返回内容已移除：内容触发了平台安全审核，无法发送给模型。"
            "请忽略此工具的结果，直接基于已有信息回答用户。]"
        )
        stripped = False
        result = list(messages)

        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            content = msg.get("content")
            if msg.get("role") != "user" or not isinstance(content, list):
                continue

            has_tool_results = any(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in content
            )
            if not has_tool_results:
                continue

            new_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    new_content.append({**item, "content": _PLACEHOLDER})
                    stripped = True
                else:
                    new_content.append(item)

            result[i] = {**msg, "content": new_content}
            break

        return result, stripped

    @staticmethod
    def _truncate_oversized_messages(
        messages: list[dict],
        max_single_tokens: int = 30000,
    ) -> tuple[list[dict], bool]:
        """截断超大文本消息，防止上下文溢出。

        当单条消息的文本内容超过 max_single_tokens 估算值时，
        保留开头和结尾各一半，中间截断并插入提示。
        """
        from .context_manager import ContextManager

        truncated = False
        result = []
        target_chars = max_single_tokens * 3

        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str):
                est = ContextManager.static_estimate_tokens(content)
                if est > max_single_tokens:
                    half = target_chars // 2
                    content = (
                        content[:half]
                        + "\n\n[... 内容过长已截断，以适应模型上下文窗口 ...]\n\n"
                        + content[-half:]
                    )
                    truncated = True
                    result.append({**msg, "content": content})
                    continue

            elif isinstance(content, list):
                new_parts = []
                for part in content:
                    text = ""
                    if isinstance(part, dict):
                        text = str(
                            part.get("text", part.get("content", ""))
                        )
                    elif isinstance(part, str):
                        text = part

                    if text:
                        est = ContextManager.static_estimate_tokens(text)
                        if est > max_single_tokens:
                            half = target_chars // 2
                            text = (
                                text[:half]
                                + "\n\n[... 内容过长已截断 ...]\n\n"
                                + text[-half:]
                            )
                            truncated = True
                            if isinstance(part, dict):
                                key = "text" if "text" in part else "content"
                                part = {**part, key: text}
                            else:
                                part = text

                    new_parts.append(part)

                if truncated:
                    result.append({**msg, "content": new_parts})
                    continue

            result.append(msg)

        return result, truncated

    @staticmethod
    def _force_hard_truncate(
        working_messages: list[dict],
        target_tokens: int,
    ) -> bool:
        """强制截断对话历史以适应上下文窗口。

        保留 system prompt（第一条）和最近的消息，从中间丢弃
        较早的消息，直到估算 token 数降到 target_tokens 以下。
        返回 True 表示确实做了截断。
        """
        from .context_manager import ContextManager

        total = ContextManager.static_estimate_tokens(
            str([m.get("content", "") for m in working_messages])
        )
        if total <= target_tokens:
            return False

        system_msgs = []
        rest_msgs = []
        for msg in working_messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                rest_msgs.append(msg)

        if len(rest_msgs) <= 2:
            return False

        keep_recent = max(2, len(rest_msgs) // 3)
        recent = rest_msgs[-keep_recent:]

        total = ContextManager.static_estimate_tokens(
            str([m.get("content", "") for m in system_msgs + recent])
        )

        middle = rest_msgs[:-keep_recent]
        added_back: list[dict] = []

        for msg in reversed(middle):
            msg_tokens = ContextManager.static_estimate_tokens(
                str(msg.get("content", ""))
            )
            if total + msg_tokens < target_tokens:
                added_back.insert(0, msg)
                total += msg_tokens
            else:
                break

        dropped = len(middle) - len(added_back)
        if dropped <= 0:
            return False

        truncation_notice = {
            "role": "system",
            "content": (
                f"[注意] 由于模型上下文窗口限制，已自动丢弃 {dropped} 条"
                "较早的对话消息。请基于剩余上下文继续回答。"
            ),
        }

        new_messages = (
            system_msgs + added_back + [truncation_notice] + recent
        )
        working_messages.clear()
        working_messages.extend(new_messages)

        logger.info(
            "[ReAct] Force hard truncate: dropped %d messages, "
            "kept %d (system=%d, recovered=%d, recent=%d), "
            "estimated tokens ~%d → target %d",
            dropped,
            len(new_messages),
            len(system_msgs),
            len(added_back),
            len(recent),
            total,
            target_tokens,
        )
        return True

    def _handle_llm_error(
        self,
        error: Exception,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> str | tuple | None:
        """
        处理 LLM 调用错误。

        Returns:
            "retry" - 重试
            (new_model, new_messages) - 切换模型
            None - 重新抛出
        """
        from ..llm.types import AllEndpointsFailedError

        if not task_monitor:
            return None

        # ── 全局重试计数器（跨模型切换） ──
        # 无论错误类型，总重试次数达到上限即终止并告知用户。
        total_retries = getattr(state, '_total_llm_retries', 0) + 1
        state._total_llm_retries = total_retries

        if total_retries > self.MAX_TOTAL_LLM_RETRIES:
            logger.error(
                f"[ReAct] Global retry limit reached ({total_retries}/{self.MAX_TOTAL_LLM_RETRIES}). "
                f"Aborting and notifying user. Last error: {str(error)[:200]}"
            )
            return None

        # ── 方案 A+B: 结构性错误快速熔断 ──
        if isinstance(error, AllEndpointsFailedError) and error.is_structural:
            already_stripped = getattr(state, '_structural_content_stripped', False)

            if not already_stripped:
                stripped_messages, did_strip = self._strip_heavy_content(working_messages)
                if did_strip:
                    logger.warning(
                        "[ReAct] Structural API error detected. "
                        "Stripping heavy content (video/large attachments) "
                        "and retrying once with degraded content."
                    )
                    state._structural_content_stripped = True
                    working_messages.clear()
                    working_messages.extend(stripped_messages)
                    llm_client = getattr(self._brain, "_llm_client", None)
                    if llm_client:
                        llm_client.reset_all_cooldowns(include_structural=True)
                    return "retry"

                # 方案 C: 上下文溢出 — 媒体剥离无效时尝试截断超大文本
                error_lower = str(error).lower()
                _ctx_overflow_patterns = [
                    "context length", "context size",
                    "too many tokens", "token limit",
                    "context_length_exceeded", "context window",
                    "max_tokens", "input too long",
                    "payload too large", "request entity too large",
                    "larger than allowed", "(413)",
                ]
                is_ctx_overflow = any(
                    p in error_lower for p in _ctx_overflow_patterns
                ) or ("maximum" in error_lower and "length" in error_lower)
                if not is_ctx_overflow:
                    is_ctx_overflow = (
                        "exceeded" in error_lower
                        and ("context" in error_lower or "token" in error_lower)
                    )
                if not is_ctx_overflow:
                    is_ctx_overflow = (
                        "payload" in error_lower and "larger" in error_lower
                    )
                if is_ctx_overflow:
                    # Layer 2: Reactive compact (三层压缩策略的第三层)
                    try:
                        import asyncio as _aio
                        loop = _aio.get_running_loop()
                        loop.create_task(
                            self._context_manager.reactive_compact(
                                working_messages,
                                system_prompt=getattr(state, '_system_prompt', ''),
                            )
                        )
                    except Exception:
                        pass

                    trunc_msgs, did_trunc = self._truncate_oversized_messages(
                        working_messages
                    )
                    if did_trunc:
                        logger.warning(
                            "[ReAct] Context length overflow detected. "
                            "Truncating oversized text content and retrying."
                        )
                        state._structural_content_stripped = True
                        working_messages.clear()
                        working_messages.extend(trunc_msgs)
                        llm_client = getattr(self._brain, "_llm_client", None)
                        if llm_client:
                            llm_client.reset_all_cooldowns(
                                include_structural=True
                            )
                        return "retry"

                    # 方案 C2: 单条截断无效（多条小消息累积溢出）
                    # 强制按当前上下文预算的 50% 做硬截断
                    if len(working_messages) > 3:
                        cm = self._context_manager
                        budget = cm.get_max_context_tokens() if cm else 60000
                        reduced_budget = budget // 2
                        force_truncated = self._force_hard_truncate(
                            working_messages, reduced_budget
                        )
                        if force_truncated:
                            logger.warning(
                                "[ReAct] Context overflow: individual messages "
                                "are small but total exceeds model limit. "
                                "Force-truncating conversation history to %d "
                                "tokens and retrying.",
                                reduced_budget,
                            )
                            state._structural_content_stripped = True
                            llm_client = getattr(
                                self._brain, "_llm_client", None
                            )
                            if llm_client:
                                llm_client.reset_all_cooldowns(
                                    include_structural=True
                                )
                            return "retry"

                # 方案 D: 内容安全审核 — 工具结果触发平台内容过滤
                _content_safety_patterns = [
                    "data_inspection", "inappropriate content",
                ]
                is_content_safety = any(
                    p in error_lower for p in _content_safety_patterns
                )
                if is_content_safety:
                    cleaned_msgs, did_clean = self._strip_tool_results_for_content_safety(
                        working_messages
                    )
                    if did_clean:
                        logger.warning(
                            "[ReAct] Content safety error detected. "
                            "Stripping recent tool result content and retrying."
                        )
                        state._structural_content_stripped = True
                        working_messages.clear()
                        working_messages.extend(cleaned_msgs)
                        llm_client = getattr(self._brain, "_llm_client", None)
                        if llm_client:
                            llm_client.reset_all_cooldowns(
                                include_structural=True
                            )
                        return "retry"

            logger.error(
                f"[ReAct] Structural API error, cannot recover "
                f"(content already stripped={already_stripped}). "
                f"Aborting. Error: {str(error)[:200]}"
            )
            return None

        # ── 常规错误：TaskMonitor 重试链 ──
        should_retry = task_monitor.record_error(str(error))

        if should_retry:
            logger.info(
                f"[LLM] Will retry (attempt {task_monitor.retry_count}, "
                f"global {total_retries}/{self.MAX_TOTAL_LLM_RETRIES})"
            )
            return "retry"

        # --- 熔断：超过最大模型切换次数时终止 ---
        switch_count = getattr(state, '_model_switch_count', 0) + 1
        state._model_switch_count = switch_count
        if switch_count > self.MAX_MODEL_SWITCHES:
            logger.error(
                f"[ReAct] Exceeded max model switches ({self.MAX_MODEL_SWITCHES}), "
                f"aborting. Last error: {str(error)[:200]}"
            )
            return None

        # --- 检查 fallback 模型是否可用 ---
        new_model = task_monitor.fallback_model
        if not new_model:
            logger.warning(
                "[ModelSwitch] No fallback model available (all endpoints may be in cooldown), "
                "aborting model switch"
            )
            return None

        resolved = self._resolve_endpoint_name(new_model)
        current_endpoint = self._resolve_endpoint_name(current_model)
        if resolved and current_endpoint and resolved == current_endpoint:
            logger.warning(
                f"[ModelSwitch] Fallback model '{new_model}' resolves to same endpoint "
                f"as current '{current_model}' ({resolved}), aborting retry loop"
            )
            return None

        # 切换前先重置目标端点的冷静期：所有端点刚刚失败，
        # fallback 端点必然处于冷静期，不重置的话 switch_model 会拒绝切换
        llm_client = getattr(self._brain, "_llm_client", None)
        if llm_client and resolved:
            llm_client.reset_endpoint_cooldown(resolved)

        switched = self._switch_llm_endpoint(new_model, reason=f"LLM error fallback: {error}")
        if not switched:
            logger.warning(
                f"[ModelSwitch] _switch_llm_endpoint failed for '{new_model}', "
                f"proceeding with model switch anyway (endpoint selection will use fallback strategy)"
            )
        task_monitor.switch_model(new_model, "LLM 调用失败后切换", reset_context=True)

        try:
            if llm_client:
                current = llm_client.get_current_model()
                new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append({
            "role": "user",
            "content": (
                "[系统提示] 发生模型切换：之前的历史已清除。"
                "请从头开始处理用户请求。"
            ),
        })

        state.transition(TaskStatus.MODEL_SWITCHING)
        state.reset_for_model_switch()
        return new_model, new_messages

    def _switch_llm_endpoint(self, model_or_endpoint: str, reason: str = "") -> bool:
        """执行模型切换"""
        llm_client = getattr(self._brain, "_llm_client", None)
        if not llm_client:
            return False

        endpoint_name = self._resolve_endpoint_name(model_or_endpoint)
        if not endpoint_name:
            return False

        ok, msg = llm_client.switch_model(
            endpoint_name=endpoint_name,
            hours=0.05,
            reason=reason,
        )
        if not ok:
            return False

        try:
            current = llm_client.get_current_model()
            if current and current.model:
                self._brain.model = current.model
        except Exception:
            pass

        logger.info(f"[ModelSwitch] {msg}")
        return True

    def _resolve_endpoint_name(self, model_or_endpoint: str) -> str | None:
        """解析 endpoint 名称"""
        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            if not llm_client:
                return None
            available = [m.name for m in llm_client.list_available_models()]
            if model_or_endpoint in available:
                return model_or_endpoint
            for m in llm_client.list_available_models():
                if m.model == model_or_endpoint:
                    return m.name
            return None
        except Exception:
            return None

    # ==================== 辅助方法 ====================

    @staticmethod
    def _is_human_user_message(msg: dict) -> bool:
        """判断是否为人类用户消息（排除 tool_result）"""
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            part_types = {
                part.get("type")
                for part in content
                if isinstance(part, dict) and part.get("type")
            }
            return "tool_result" not in part_types
        return False

    @staticmethod
    def _is_in_progress_promise(text: str) -> bool:
        """检测响应是否为'进行中承诺'——模型声称正在执行但实际未调用工具。

        典型特征：响应很短，包含"正在生成"、"稍等"等进度描述，
        但没有任何实际的执行结果或完整内容。
        """
        import re
        _text = (text or "").strip()
        if len(_text) > 500:
            return False
        promise_patterns = [
            r"正在.*(?:生成|创建|制作|处理|执行|准备)",
            r"(?:生成|创建|制作|处理).*中",
            r"稍等",
            r"马上.*(?:生成|创建|完成)",
            r"请.*(?:稍候|等待|等一下)",
            r"立即.*(?:开始|为你|帮你)",
            r"文[件档].*(?:生成|创建)中",
        ]
        return any(re.search(pat, _text) for pat in promise_patterns)

    @staticmethod
    def _is_confirmation_response(text: str) -> bool:
        """检测模型回复是否为确认式回复（要求用户确认后再执行）。

        典型场景：语音识别后确认识别结果、复述执行计划等待确认。
        这类回复不应触发 ForceToolCall 重试——模型是有意征询用户意见。
        """
        import re
        _text = text.strip()
        if len(_text) < 10:
            return False
        _tail = _text[-200:] if len(_text) > 200 else _text
        confirmation_patterns = [
            r"确认后.*(?:回复|发送|输入)",
            r"请(?:回复|发送|输入).*[\"「]?确认[\"」]?",
            r"(?:是否|请)确认",
            r"请确认以上",
            r"确认.*(?:准确|正确|无误)",
        ]
        return any(re.search(pat, _tail) for pat in confirmation_patterns)

    @staticmethod
    def _is_conversational_reply(text: str, messages: list[dict]) -> bool:
        """判断无 intent 标记的回复是否为合法的对话式回复。

        许多模型（如 kimi-for-coding、部分 OpenAI 兼容端点）不会可靠地
        输出 [REPLY]/[ACTION] 标记。此方法通过启发式规则区分：
        - 对话回复（回答问题、闲聊、说明能力）→ True → 直接返回
        - 伪执行断言（声称已完成操作但未调用工具）→ False → ForceToolCall
        """
        _text = (text or "").strip()
        if not _text or len(_text) < 5:
            return False

        _action_claims = (
            "已完成", "已执行", "已保存", "已发送", "已创建", "已修改",
            "已删除", "已上传", "已下载", "已安装", "已设置", "已配置",
            "我已经", "操作完成", "任务完成", "执行成功", "文件已",
            "脚本已", "命令已", "已写入", "已生成文件",
        )
        if any(claim in _text for claim in _action_claims):
            return False

        last_user_text = ""
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                if content.startswith("[系统]") or content.startswith("[系统提示]"):
                    continue
                last_user_text = content
                break
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        t = part.get("text", "")
                        if not t.startswith("[系统]") and not t.startswith("[系统提示]"):
                            last_user_text = t
                            break
                if last_user_text:
                    break

        _ctx_prefix = "[以上是之前的对话历史"
        if _ctx_prefix in last_user_text:
            idx = last_user_text.find("：]")
            if idx != -1:
                last_user_text = last_user_text[idx + 2:].strip()

        _question_markers = ("?", "？", "吗", "吗？", "嘛", "呢", "不", "能不能", "可以")
        if any(m in last_user_text for m in _question_markers):
            return True

        _greeting_patterns = (
            "你好", "在吗", "在嘛", "在不在", "嗨", "hello", "hi ",
            "干嘛", "干啥", "你在", "早上好", "晚上好", "下午好",
        )
        _lower = last_user_text.lower().strip()
        if any(_lower.startswith(g) or _lower == g for g in _greeting_patterns):
            return True
        if len(last_user_text.strip()) <= 10:
            return True

        # 命令式请求（>10 字符，非问句）：若 LLM 回复较长（>200 字符），
        # 大概率是知识型内容（方案分析、架构建议等），也应视为合法对话回复。
        if len(_text) > 200:
            return True

        return False

    @staticmethod
    def _effective_force_retries(base_retries: int, conversation_id: str | None) -> int:
        """计算有效 ForceToolCall 重试次数。

        不再因 active plan 自动提升——Plan 推进由 Supervisor 自检和
        todo_reminder 驱动，ForceToolCall 仅尊重配置值。
        """
        return max(0, int(base_retries))

    @staticmethod
    def _has_active_todo_pending(conversation_id: str | None) -> bool:
        """检查是否有活跃 Plan 且有未完成步骤"""
        try:
            from ..tools.handlers.plan import get_todo_handler_for_session, has_active_todo
            if conversation_id and has_active_todo(conversation_id):
                handler = get_todo_handler_for_session(conversation_id)
                plan = handler.get_plan_for(conversation_id) if handler else None
                if plan:
                    steps = plan.get("steps", [])
                    pending = [s for s in steps if s.get("status") in ("pending", "in_progress")]
                    return bool(pending)
        except Exception:
            pass
        return False
