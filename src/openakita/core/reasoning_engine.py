"""
Reason-Act Engine (ReAct pattern)

Refactored from agent.py's _chat_with_tools_and_context into an explicit
three-phase Reason -> Act -> Observe loop.

Core responsibilities:
- Explicit reasoning-loop management (Reason / Act / Observe)
- LLM response parsing and Decision classification
- Tool-call orchestration (delegated to ToolExecutor)
- Context-compression triggering (delegated to ContextManager)
- Loop detection (signature repetition, self-check intervals, safety thresholds)
- Model-switching logic
- Task-completion verification (delegated to ResponseHandler)
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

from ..api.routes.websocket import broadcast_event
from ..config import settings
from ..llm.converters.tools import PARSE_ERROR_KEY
from ..tracing.tracer import get_tracer
from .agent_state import AgentState, TaskState, TaskStatus
from .context_manager import ContextManager
from .context_manager import _CancelledError as _CtxCancelledError
from .errors import UserCancelledError
from .resource_budget import BudgetAction, ResourceBudget, create_budget_from_settings
from .response_handler import (
    ResponseHandler,
    clean_llm_response,
    parse_intent_tag,
    strip_thinking_tags,
)
from .supervisor import UNPRODUCTIVE_ADMIN_TOOLS as _ADMIN_TOOL_NAMES
from .supervisor import RuntimeSupervisor
from .token_tracking import TokenTrackingContext, reset_tracking_context, set_tracking_context
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

_SSE_RESULT_PREVIEW_CHARS = 32000
_MAX_TOOL_RESULTS_TOTAL_CHARS = 200_000


def _apply_tool_result_budget(
    tool_results: list[dict],
    max_total: int = _MAX_TOOL_RESULTS_TOTAL_CHARS,
) -> list[dict]:
    """Proportionally truncate tool results if total exceeds budget."""
    total = sum(len(str(r.get("content", ""))) for r in tool_results)
    if total <= max_total:
        return tool_results

    ratio = max_total / total
    for r in tool_results:
        content = str(r.get("content", ""))
        if len(content) > 1000:
            budget = max(500, int(len(content) * ratio))
            if len(content) > budget:
                half = budget // 2
                r["content"] = (
                    content[:half]
                    + f"\n\n... [{len(content) - budget} chars truncated] ...\n\n"
                    + content[-half:]
                )
    return tool_results


# ---------------------------------------------------------------------------
# Mode-based tool filtering
# ---------------------------------------------------------------------------

from .permission import (
    ASK_MODE_RULESET,
    COORDINATOR_MODE_RULESET,
    DEFAULT_RULESET,
    PLAN_MODE_RULESET,
)
from .permission import (
    Ruleset as PermissionRuleset,
)
from .permission import (
    disabled as permission_disabled,
)


def _get_mode_ruleset(mode: str) -> PermissionRuleset:
    """Get the permission ruleset for the given mode."""
    if mode == "plan":
        return PLAN_MODE_RULESET
    elif mode == "ask":
        return ASK_MODE_RULESET
    elif mode == "coordinator":
        return COORDINATOR_MODE_RULESET
    return DEFAULT_RULESET


def _filter_tools_by_mode(tools: list[dict], mode: str) -> list[dict]:
    """Filter tool list based on the active mode using the permission system.

    Uses PermissionRuleset.disabled() to determine which tools to remove.
    - agent: DEFAULT_RULESET (all tools allowed)
    - ask: ASK_MODE_RULESET (write tools removed)
    - plan: PLAN_MODE_RULESET (write tools visible but path-restricted at runtime)
    - coordinator: COORDINATOR_MODE_RULESET (delegation/planning tools only)
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
    for tool, name in zip(tools, tool_names, strict=False):
        if name not in disabled_set:
            filtered.append(tool)

    if disabled_set:
        logger.info(
            f"[ToolFilter] mode={mode}: {len(tools)} -> {len(filtered)} tools "
            f"(disabled: {sorted(disabled_set)})"
        )
    return filtered


_SHELL_WRITE_PATTERNS = re.compile(
    r"(?:"
    r'>\s*["\'/\w]'
    r"|>>"
    r"|\btee\b"
    r"|\bsed\s+-i"
    r"|\bdd\b"
    r"|\brm\s"
    r"|\bmv\s"
    r"|\bcp\s"
    r"|\bmkdir\b"
    r"|\btouch\b"
    r"|\bchmod\b"
    r"|\bchown\b"
    r'|open\s*\([^)]*["\']w'
    r"|\.write\s*\("
    r"|echo\s+.*>"
    r"|\bpip\s+install"
    r"|\bnpm\s+install"
    r"|\bgit\s+(?:commit|push|checkout|merge|rebase|reset)"
    r"|\bOut-File\b"
    r"|\bSet-Content\b"
    r"|\bAdd-Content\b"
    r"|\bNew-Item\b"
    r"|\bRemove-Item\b"
    r"|\bMove-Item\b"
    r"|\bCopy-Item\b"
    r"|\bRename-Item\b"
    r"|\bInvoke-WebRequest\b.*-OutFile"
    r"|\bdel\s"
    r"|\bcopy\s"
    r"|\bmove\s"
    r"|\bren\s"
    r"|\btype\s.*>"
    r")",
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
            f"Error: {tool_name} is not available in the current {mode} mode."
            "Use a tool from the provided tool list, or suggest the user switch to agent mode."
        )

    if tool_name in ("run_shell", "run_powershell"):
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
                f"[ModeGuard] Blocked {tool_name} write command in {mode} mode: {cmd[:100]}"
            )
            return (
                f"Error: in {mode} mode, {tool_name} is only allowed to run read-only commands (e.g. cat, grep, ls, find)."
                f"Write operation detected and blocked. Use read-only commands, or suggest the user switch to agent mode."
            )

    return None


class DecisionType(Enum):
    """LLM decision type"""

    FINAL_ANSWER = "final_answer"  # plain-text response
    TOOL_CALLS = "tool_calls"  # tool calls required


@dataclass
class Decision:
    """LLM reasoning decision"""

    type: DecisionType
    text_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking_content: str = ""
    raw_response: Any = None
    stop_reason: str = ""
    # Full assistant_content (preserving thinking blocks, etc.)
    assistant_content: list[dict] = field(default_factory=list)


@dataclass
class Checkpoint:
    """
    Decision checkpoint, used for multi-path exploration and rollback.

    Save a snapshot of message history and task state at key decision points,
    so that when loops, consecutive failures, or similar issues are detected we can roll back to an earlier checkpoint,
    append a failure-experience hint, and re-reason.
    """

    id: str
    messages_snapshot: list[dict]  # deep-copied message history
    state_snapshot: dict  # serialized key fields of TaskState
    decision_summary: str  # summary of the decision taken
    iteration: int  # iteration count at save time
    timestamp: float = field(default_factory=time.time)
    tool_names: list[str] = field(default_factory=list)  # tools invoked by this decision


def _get_action_claim_re() -> "re.Pattern[str]":
    """Compiled regex that detects Chinese action-claim phrases.

    Matches patterns like "已帮你保存", "已完成", "成功发送", "已经删除" -- these
    indicate the LLM is *claiming* it performed an operation rather than merely
    analysing or describing content.  Used by the implicit-REPLY heuristic to
    avoid accepting hallucinated action descriptions.
    """
    import re as _re

    pat = getattr(_get_action_claim_re, "_cached", None)
    if pat is not None:
        return pat
    verbs = (
        "保存|发送|创建|删除|修改|上传|下载|执行|生成|导出|复制|移动|"
        "写入|添加|设置|配置|安装|部署|打包|编译|构建|启动|重启|停止|关闭"
    )
    pat = _re.compile(
        rf"(?:已[经]?|成功|顺利)(?:帮你?|为你|给你)?(?:{verbs})"
    )
    _get_action_claim_re._cached = pat  # type: ignore[attr-defined]
    return pat


class ReasoningEngine:
    """
    Explicit Reason-Act engine.

    Replaces _chat_with_tools_and_context() in agent.py,
    refactoring the implicit loop into a clean three-phase Reason -> Act -> Observe.
    Supports Checkpoint + Rollback multi-path exploration.
    """

    # Checkpoint configuration
    MAX_CHECKPOINTS = 5  # keep the most recent N checkpoints
    CONSECUTIVE_FAIL_THRESHOLD = 3  # trigger rollback after N consecutive failures of the same tool

    def __init__(
        self,
        brain: Any,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        response_handler: ResponseHandler,
        agent_state: AgentState,
        memory_manager: Any = None,
        plan_exit_pending: dict | None = None,
    ) -> None:
        self._brain = brain
        self._tool_executor = tool_executor
        self._context_manager = context_manager
        self._response_handler = response_handler
        self._state = agent_state
        self._memory_manager = memory_manager
        self._plan_exit_pending = plan_exit_pending
        self._plugin_hooks = None

        # Agent Harness: Runtime Supervisor + Resource Budget
        self._supervisor = RuntimeSupervisor(enabled=getattr(settings, "supervisor_enabled", True))
        self._budget: ResourceBudget = create_budget_from_settings()

        # Checkpoint management
        self._checkpoints: list[Checkpoint] = []
        self._tool_failure_counter: dict[str, int] = {}  # tool_name -> consecutive_failures
        self._consecutive_truncation_count: int = 0  # consecutive-truncation counter (prevents truncation->rollback deadlock)

        # Persistent failure counter across rollbacks (not cleared by rollback)
        # Used to detect cross-rollback loops like "write_file repeatedly fails due to truncation"
        self._persistent_tool_failures: dict[str, int] = {}
        self.PERSISTENT_FAIL_LIMIT = 5  # force-terminate when the same tool accumulates N cross-rollback failures

        # Reasoning chain: cache the most recent react_trace for agent_handler to read
        self._last_react_trace: list[dict] = []

        # Cache the working_messages from the end of the last reasoning run for token-stat reads
        self._last_working_messages: list[dict] = []

        # Exit reason of the last reasoning run: normal / ask_user / loop_terminated / max_iterations / verify_incomplete
        # _finalize_session uses this to decide whether to auto-close the Plan; OrgRuntime uses it to distinguish
        # between task_completed / task_failed / task_terminated events
        self._last_exit_reason: str = "normal"

        # Delivery receipts from deliver_artifacts in the last reasoning run
        self._last_delivery_receipts: list[dict] = []

        # messages_snapshot in checkpoint data can contain large tool results,
        # cleared at session end to free memory
        self._max_working_messages_kept = 0  # number to keep on cleanup (0 = release all)

        # Browser "read page state" tool
        self._browser_page_read_tools = frozenset(
            {
                "browser_get_content",
                "browser_screenshot",
            }
        )

    # ==================== Failure Analysis (Agent Harness) ====================

    def _run_failure_analysis(
        self,
        react_trace: list[dict],
        exit_reason: str,
        task_description: str = "",
        task_id: str = "",
    ) -> None:
        """Run the failure-analysis pipeline when a task fails"""
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

    # ==================== Memory management ====================

    def release_large_buffers(self) -> None:
        """Free large objects left over after reasoning to prevent memory leaks.

        Called from _cleanup_session_state.
        _last_working_messages holds the full LLM context (including base64 screenshots,
        web content, and other tool results); it's the largest memory consumer and must be released explicitly.
        _checkpoints contains deep-copied messages_snapshot and must also be released.

        Note: do not clear _last_react_trace -- it has already been copied to agent._last_finalized_trace,
        and _last_finalized_trace is used by the orchestrator / SSE; wait for the next session to overwrite it naturally.
        """
        self._last_working_messages = []
        self._checkpoints.clear()
        self._tool_failure_counter.clear()
        self._supervisor.reset()

    # ==================== ask_user wait-for-user-reply ====================

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
        Wait for the user's reply to an ask_user question (IM mode only).

        Uses the Gateway's interrupt-queue mechanism: IM messages sent while the Agent is processing
        are placed into interrupt_queue by the Gateway; this method polls that queue for replies.

        Flow:
        1. Send the question to the user via the Gateway
        2. Poll interrupt_queue for a reply (with timeout_seconds timeout)
        3. On first timeout -> send a reminder and wait another round
        4. On second timeout -> return None; the caller injects a system message so the LLM can decide on its own

        Args:
            question: question text to send to the user
            state: current task state (used for cancel checks)
            timeout_seconds: per-round wait timeout (seconds)
            max_reminders: maximum number of follow-up reminders
            poll_interval: polling interval (seconds)

        Returns:
            The user reply text, or None (on timeout / no gateway / cancelled)
        """
        # Obtain gateway and session references
        session = self._state.current_session
        if not session:
            return None

        gateway = session.get_metadata("_gateway") if hasattr(session, "get_metadata") else None
        session_key = session.get_metadata("_session_key") if gateway else None

        if not gateway or not session_key:
            # CLI mode or no gateway -> do not wait
            return None

        # Flush the progress buffer first so thinking/tool progress is delivered before the question
        if hasattr(gateway, "flush_progress"):
            try:
                await gateway.flush_progress(session)
            except Exception:
                pass

        # Send the question to the user
        try:
            await gateway.send_to_session(session, question, role="assistant")
            logger.info(
                f"[ask_user] Question sent to user, waiting for reply (timeout={timeout_seconds}s)"
            )
        except Exception as e:
            logger.warning(f"[ask_user] Failed to send question via gateway: {e}")
            return None

        reminders_sent = 0

        while reminders_sent <= max_reminders:
            # Poll waiting for the user reply
            elapsed = 0.0

            while elapsed < timeout_seconds:
                # Check whether the task was cancelled
                if state.cancelled:
                    logger.info("[ask_user] Task cancelled while waiting for reply")
                    return None

                # Check the interrupt queue
                try:
                    reply_msg = await gateway.check_interrupt(session_key)
                except Exception as e:
                    logger.warning(f"[ask_user] check_interrupt error: {e}")
                    reply_msg = None

                if reply_msg:
                    # Extract text from UnifiedMessage
                    reply_text = (
                        reply_msg.plain_text.strip()
                        if hasattr(reply_msg, "plain_text") and reply_msg.plain_text
                        else str(reply_msg).strip()
                    )
                    if reply_text:
                        logger.info(f"[ask_user] User replied: {reply_text[:80]}")
                        # Record to session history
                        try:
                            session.add_message(
                                role="user", content=reply_text, source="ask_user_reply"
                            )
                        except Exception:
                            pass
                        return reply_text

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # This round timed out
            if reminders_sent < max_reminders:
                # Send a follow-up reminder
                reminders_sent += 1
                reminder = "⏰ Waiting for your reply to the question above — please respond when you're ready."
                try:
                    await gateway.send_to_session(session, reminder, role="assistant")
                    logger.info(f"[ask_user] Timeout #{reminders_sent}, reminder sent")
                except Exception as e:
                    logger.warning(f"[ask_user] Failed to send reminder: {e}")
            else:
                # Follow-up attempts exhausted; return None
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
        Save a checkpoint at a key decision point.

        Only saved on tool-call decisions (plain-text responses do not need rollback).
        Keep the most recent MAX_CHECKPOINTS checkpoints to bound memory usage.
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

        # Keep the most recent N
        if len(self._checkpoints) > self.MAX_CHECKPOINTS:
            self._checkpoints = self._checkpoints[-self.MAX_CHECKPOINTS :]

        logger.debug(f"[Checkpoint] Saved: {cp.id} at iteration {iteration}")

    def _record_tool_result(self, tool_name: str, success: bool) -> None:
        """Record tool execution results for consecutive-failure detection."""
        if success:
            self._tool_failure_counter[tool_name] = 0
            # Also reset the persistent counter on success
            self._persistent_tool_failures.pop(tool_name, None)
        else:
            self._tool_failure_counter[tool_name] = self._tool_failure_counter.get(tool_name, 0) + 1
            self._persistent_tool_failures[tool_name] = (
                self._persistent_tool_failures.get(tool_name, 0) + 1
            )

    def _should_rollback(self, tool_results: list[dict]) -> tuple[bool, str]:
        """
        Check whether a rollback should be triggered.

        Trigger conditions:
        1. The same tool has failed consecutively >= CONSECUTIVE_FAIL_THRESHOLD times
        2. The entire tool batch failed

        Returns:
            (should_rollback, reason)
        """
        if not self._checkpoints:
            return False, ""

        # Check this batch's tool-execution results
        batch_failures = []
        for result in tool_results:
            content = ""
            # Primary signal: the structured is_error flag on tool_result
            is_error_flag = False
            if isinstance(result, dict):
                content = str(result.get("content", ""))
                is_error_flag = result.get("is_error", False)
            elif isinstance(result, str):
                content = result

            # If the tool carries behavioral guidance, skip rollback -- let the tool-returned constraint act on the model directly,
            # avoiding rollback-injected "try a completely different approach" overwriting the tool's "no substitutes" guidance
            if "[行为指引]" in content:
                return False, ""

            # Fallback: string-marker match (the error string returned by the handler)
            has_error = is_error_flag or any(
                marker in content
                for marker in [
                    "❌",
                    "⚠️ 工具执行错误",
                    "错误类型:",
                    "ToolError",
                    "⚠️ 策略拒绝:",
                ]
            )
            has_success = any(
                marker in content
                for marker in [
                    "✅",
                    '"status": "delivered"',
                    '"ok": true',
                ]
            )

            # Partial success (e.g. deliver_artifacts sent 1 of 2 images) does not count as failure,
            # to avoid rolling back content that has already been sent and cannot be retracted
            is_failed = has_error and not has_success
            batch_failures.append(is_failed)

        # Entire batch failed
        if batch_failures and all(batch_failures):
            return True, "all tool calls in this round failed"

        # Consecutive failures of a single tool
        for tool_name, count in self._tool_failure_counter.items():
            if count >= self.CONSECUTIVE_FAIL_THRESHOLD:
                return True, f"Tool '{tool_name}' failed {count} times consecutively"

        return False, ""

    def _rollback(self, reason: str) -> tuple[list[dict], int] | None:
        """
        Perform a rollback: restore the previous checkpoint.

        Append a failure-experience hint to the end of the restored message history,
        helping the LLM avoid repeating the same mistake.

        Returns:
            (restored_messages, checkpoint_iteration) or None if no checkpoints
        """
        if not self._checkpoints:
            return None

        # Pop the most recent checkpoint (to avoid rolling back to the same point)
        cp = self._checkpoints.pop()
        restored_messages = copy.deepcopy(cp.messages_snapshot)

        # Append failure experience
        failure_hint = (
            f"[System notice] The previous approach failed (reason: {reason})."
            f"Failed decision: {cp.decision_summary}."
            f"Please try a completely different approach to accomplish the task."
            f"Avoid using the same tool-parameter combinations as before."
            f"If this was because tool arguments were truncated by the API (e.g. write_file content too long),"
            f"split the content into several smaller writes."
        )
        restored_messages.append(
            {
                "role": "user",
                "content": failure_hint,
            }
        )

        # Reset the failure counter
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
        Main reasoning loop: Reason -> Act -> Observe.

        Args:
            messages: initial message list
            tools: tool-definition list
            system_prompt: system prompt
            base_system_prompt: base system prompt (without the dynamic Plan)
            task_description: task description
            task_monitor: task monitor
            session_type: session type
            interrupt_check_fn: interrupt-check function
            conversation_id: conversation ID
            thinking_mode: thinking-mode override ('auto'/'on'/'off'/None)
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)
            progress_callback: progress callback async fn(str) -> None, used to stream the IM reasoning chain
            endpoint_override: endpoint override (from the Agent profile or API request)
            force_tool_retries: Intent-driven override for max ForceToolCall retries
                (None = use default from settings, 0 = disable ForceToolCall)

        Returns:
            Final response text
        """
        self._last_exit_reason = "normal"
        self._last_react_trace = []
        self._last_delivery_receipts: list[dict] = []
        self._supervisor.reset()
        self._budget = create_budget_from_settings()
        self._budget.start()
        _session_key = conversation_id or ""
        state = (
            self._state.get_task_for_session(_session_key)
            if _session_key
            else self._state.current_task
        )

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
        tracer.begin_trace(
            session_id=state.session_id,
            metadata={
                "task_description": task_description[:200] if task_description else "",
                "session_type": session_type,
                "model": self._brain.model,
            },
        )

        max_iterations = getattr(self, "_max_iterations_override", None) or settings.max_iterations
        self._max_iterations_override = None  # consume once
        self._empty_content_retries = 0

        # progress-callback helper (safe invocation; exceptions are ignored)
        async def _emit_progress(text: str) -> None:
            if progress_callback and text:
                try:
                    await progress_callback(text)
                except Exception:
                    pass

        # Save the original user message (used when resetting context on model switch)
        state.original_user_messages = [msg for msg in messages if self._is_human_user_message(msg)]

        working_messages = list(messages)
        current_model = self._brain.model

        # === Endpoint override ===
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
                    logger.info(
                        f"[EndpointOverride] Switched to {endpoint_override} for {conversation_id}"
                    )
                else:
                    logger.warning(
                        f"[EndpointOverride] Failed to switch to {endpoint_override}: {msg}, using default"
                    )

        # ForceToolCall configuration
        im_floor = max(0, int(getattr(settings, "force_tool_call_im_floor", 2)))
        _override = getattr(self, "_force_tool_override", None)
        configured = int(
            _override if _override is not None
            else getattr(settings, "force_tool_call_max_retries", 2)
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
        max_confirmation_text_retries = max(
            0, int(getattr(settings, "confirmation_text_max_retries", 2))
        )

        # Tracking variables
        executed_tool_names: list[str] = []
        delivery_receipts: list[dict] = []
        _last_browser_url = ""

        # Loop counter
        consecutive_tool_rounds = 0
        no_tool_call_count = 0
        verify_incomplete_count = 0
        no_confirmation_text_count = 0
        tools_executed_in_task = False
        _supervisor_intervened = False
        _tool_call_counter: dict[str, int] = {}
        _MAX_SAME_TOOL_PER_TASK = 5

        def _build_effective_system_prompt() -> str:
            """Append the active Plan dynamically"""
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
            """Generate a tool signature"""
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
        _initial_tools = tools  # keep reference for refresh detection

        # ==================== Main loop ====================
        logger.info(
            f"[ReAct] === Loop started (max_iterations={max_iterations}, model={current_model}) ==="
        )

        react_trace: list[dict] = []
        all_tool_results: list[dict] = []
        _trace_started_at = datetime.now().isoformat()

        _last_discovered_snapshot: frozenset = frozenset()

        for iteration in range(max_iterations):
            self._last_working_messages = working_messages
            state.iteration = iteration

            # Check cancellation
            if state.cancelled:
                logger.info(f"[ReAct] Task cancelled at iteration start: {state.cancel_reason}")
                self._save_react_trace(
                    react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                )
                tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration})
                return await self._cancel_farewell(
                    working_messages, _build_effective_system_prompt(), current_model, state
                )

            # Resource Budget check
            self._budget.record_iteration()
            budget_status = self._budget.check()
            if budget_status.action == BudgetAction.PAUSE:
                logger.warning(f"[Budget] PAUSE: {budget_status.message}")
                self._save_react_trace(
                    react_trace, conversation_id, session_type, "budget_exceeded", _trace_started_at
                )
                tracer.end_trace(
                    metadata={
                        "result": "budget_exceeded",
                        "iterations": iteration,
                        "budget_dimension": budget_status.dimension,
                    }
                )
                self._run_failure_analysis(
                    react_trace,
                    "budget_exceeded",
                    task_description=task_description,
                    task_id=state.task_id,
                )
                return (
                    f"⚠️ Task resource budget exhausted ({budget_status.dimension}: "
                    f"{budget_status.usage_ratio:.0%}). Task paused.\n"
                    f"Progress saved — adjust the budget and continue."
                )
            elif budget_status.action in (BudgetAction.WARNING, BudgetAction.DOWNGRADE):
                logger.info(
                    "[Budget] %s: %s — logged only, not injected",
                    budget_status.dimension,
                    budget_status.message,
                )

            # Task monitoring
            if task_monitor:
                task_monitor.begin_iteration(iteration + 1, current_model)
                # Model-switch check
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
                working_messages = self._context_manager.pre_request_cleanup(working_messages)
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
                    # Only when task state is explicitly 'user cancelled' do we escalate a compression cancel to a task cancel.
                    # Otherwise, treat it as a compression failure to avoid misreporting "Context compression cancelled by user".
                    if state.cancelled or bool((state.cancel_reason or "").strip()):
                        raise UserCancelledError(
                            reason=state.cancel_reason or "user requested stop",
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
                    # Context Rewriting: inject a direction hint after compression
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
                        f"📦 Context compressed: {_before_tokens // 1000}k → {_after_tokens // 1000}k tokens"
                    )
                    logger.info(
                        f"[ReAct] Context compressed: {_before_tokens} → {_after_tokens} tokens"
                    )

            # ==================== REASON phase ====================
            if state.cancelled:
                self._save_react_trace(
                    react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                )
                tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                return await self._cancel_farewell(
                    working_messages, _build_effective_system_prompt(), current_model, state
                )
            logger.info(
                f"[ReAct] Iter {iteration + 1}/{max_iterations} — REASON (model={current_model})"
            )
            await broadcast_event("pet-status-update", {"status": "thinking"})
            if state.status != TaskStatus.REASONING:
                try:
                    state.transition(TaskStatus.REASONING)
                except ValueError:
                    pass

            # Refresh tools only when _discovered_tools actually changes
            # (not every iteration — otherwise Supervisor NUDGE that strips
            # tools to [] gets immediately overridden; see issue #443)
            _agent = getattr(self._tool_executor, "_agent_ref", None)
            if iteration > 0 and _agent and getattr(_agent, "_discovered_tools", None):
                _current_discovered = frozenset(getattr(_agent, "_discovered_tools", ()))
                if _current_discovered != _last_discovered_snapshot:
                    _last_discovered_snapshot = _current_discovered
                    refreshed = _filter_tools_by_mode(_agent._effective_tools, mode)
                    if {t.get("name") for t in refreshed} != {t.get("name") for t in tools}:
                        tools = refreshed
                        _allowed_tool_names = (
                            {t.get("name", "") for t in tools} if mode != "agent" else None
                        )
                        logger.info(
                            "[ReAct] tools refreshed after tool_search discovery (now %d tools)",
                            len(tools),
                        )

            _thinking_t0 = time.time()  # reasoning chain: record thinking start time
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
                retry_result = await self._handle_llm_error(
                    e, task_monitor, state, working_messages, current_model
                )
                if retry_result == "retry":
                    _total_r = getattr(state, "_total_llm_retries", 1)
                    await _emit_progress(
                        f"AI service error, retrying…"
                        f"（{_total_r}/{self.MAX_TOTAL_LLM_RETRIES}）..."
                    )
                    _retry_sleep = min(2 * _total_r, 15)
                    _sleep = asyncio.create_task(asyncio.sleep(_retry_sleep))
                    _cw = asyncio.create_task(state.cancel_event.wait())
                    _done, _pend = await asyncio.wait(
                        {_sleep, _cw}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for _t in _pend:
                        _t.cancel()
                        try:
                            await _t
                        except (asyncio.CancelledError, Exception):
                            pass
                    if _cw in _done:
                        raise UserCancelledError(
                            reason=state.cancel_reason or "user requested stop", source="retry_sleep"
                        )
                    continue
                elif isinstance(retry_result, tuple):
                    current_model, working_messages = retry_result
                    await _emit_progress(
                        "Current model unavailable, switching to fallback…"
                    )
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

            # === IM progress: thinking content ===
            if decision.thinking_content:
                _raw = decision.thinking_content[:600].strip()
                if len(decision.thinking_content) > 600:
                    _raw += "..."
                _think_preview = "> " + _raw.replace("\n", "\n> ")
                await _emit_progress(f"💭 **Thinking**\n{_think_preview}")

            # === IM progress: LLM reasoning intent ===
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

            # -- Collect ReAct trace data --
            # Token info is extracted from raw_response.usage (Decision itself does not carry token counts)
            _raw = decision.raw_response
            _usage = getattr(_raw, "usage", None) if _raw else None
            _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
            _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0

            # Resource Budget: record token consumption
            if _in_tokens or _out_tokens:
                self._budget.record_tokens(_in_tokens, _out_tokens)
            _iter_trace: dict = {
                "iteration": iteration + 1,
                "timestamp": datetime.now().isoformat(),
                "decision_type": decision.type.value
                if hasattr(decision.type, "value")
                else str(decision.type),
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
                "tool_results": [],  # populated after tool execution
                "tokens": {
                    "input": _in_tokens,
                    "output": _out_tokens,
                },
                "context_compressed": _ctx_compressed_info,
            }
            tool_names_for_log = [tc.get("name", "?") for tc in (decision.tool_calls or [])]
            logger.info(
                f"[ReAct] Iter {iteration + 1} — decision={_iter_trace['decision_type']}, "
                f"tools={tool_names_for_log}, "
                f"tokens_in={_in_tokens}, tokens_out={_out_tokens}"
            )

            # ==================== stop_reason=max_tokens detection ====================
            # When LLM output is truncated by max_tokens, the tool-call JSON may be incomplete.
            # Detect this case and log an explicit warning for troubleshooting.
            if decision.stop_reason == "max_tokens":
                logger.warning(
                    f"[ReAct] Iter {iteration + 1} — ⚠️ LLM output truncated (stop_reason=max_tokens). "
                    f"The response hit the max_tokens limit ({self._brain.max_tokens}). "
                    f"Tool calls may have incomplete JSON arguments. "
                    f"Consider increasing endpoint max_tokens or reducing tool argument size."
                )
                _iter_trace["truncated"] = True

                # Automatically raise max_tokens and retry the fully-truncated tool call
                if decision.type == DecisionType.TOOL_CALLS:
                    truncated_calls = [
                        tc
                        for tc in decision.tool_calls
                        if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                    ]
                    _current_max = self._brain.max_tokens or 16384
                    _max_ceiling = min(_current_max * 3, 65536)
                    if truncated_calls and len(truncated_calls) == len(decision.tool_calls):
                        _new_max = min(_current_max * 2, _max_ceiling)
                        if _new_max > _current_max:
                            logger.warning(
                                f"[ReAct] Iter {iteration + 1} — All {len(truncated_calls)} tool "
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
                                f"[ReAct] Iter {iteration + 1} — "
                                f"{len(truncated_calls)}/{len(decision.tool_calls)} tool calls "
                                f"truncated. Increasing max_tokens for next iteration: "
                                f"{_current_max} → {_new_max}"
                            )
                            self._brain.max_tokens = _new_max

            # ==================== Decision branch ====================

            if decision.type == DecisionType.FINAL_ANSWER:
                # Plain-text response -- handle completion verification
                logger.info(
                    f'[ReAct] Iter {iteration + 1} — FINAL_ANSWER: "{(decision.text_content or "").replace(chr(10), " ")}"'
                )

                # Automatically continue when FINAL_ANSWER is truncated by max_tokens (up to 2 times)
                if (
                    decision.stop_reason == "max_tokens"
                    and getattr(state, "_text_continuation_count", 0) < 2
                ):
                    state._text_continuation_count = getattr(state, "_text_continuation_count", 0) + 1
                    if not hasattr(state, "_accumulated_text_parts"):
                        state._accumulated_text_parts = []
                    state._accumulated_text_parts.append(decision.text_content or "")
                    logger.info(
                        f"[ReAct] FINAL_ANSWER truncated by max_tokens, "
                        f"auto-continuation #{state._text_continuation_count}"
                    )
                    working_messages.append({
                        "role": "assistant",
                        "content": decision.assistant_content or [{"type": "text", "text": decision.text_content or ""}],
                        **({"reasoning_content": decision.thinking_content} if decision.thinking_content else {}),
                    })
                    working_messages.append({
                        "role": "user",
                        "content": "Your response was cut off. Please continue directly from where you left off — do not repeat yourself or apologize.",
                    })
                    react_trace.append(_iter_trace)
                    continue

                # If a continuation occurred earlier, concatenate the complete text
                if hasattr(state, "_accumulated_text_parts") and state._accumulated_text_parts:
                    state._accumulated_text_parts.append(decision.text_content or "")
                    decision.text_content = "".join(state._accumulated_text_parts)
                    del state._accumulated_text_parts

                consecutive_tool_rounds = 0

                result = await self._handle_final_answer(
                    decision=decision,
                    working_messages=working_messages,
                    original_messages=messages,
                    tools_executed_in_task=tools_executed_in_task,
                    executed_tool_names=executed_tool_names,
                    delivery_receipts=delivery_receipts,
                    all_tool_results=all_tool_results,
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
                        f"[ReAct] === COMPLETED after {iteration + 1} iterations, "
                        f"tools: {list(set(executed_tool_names))} ==="
                    )
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "completed", _trace_started_at
                    )
                    try:
                        state.transition(TaskStatus.COMPLETED)
                    except ValueError:
                        pass
                    tracer.end_trace(
                        metadata={
                            "result": "completed",
                            "iterations": iteration + 1,
                            "tools_used": list(set(executed_tool_names)),
                        }
                    )
                    await broadcast_event("pet-status-update", {"status": "success"})
                    return result
                else:
                    # Continue looping (verification failed)
                    await _emit_progress("🔄 Task not yet complete, continuing…")
                    logger.info(
                        f"[ReAct] Iter {iteration + 1} — VERIFY: incomplete, continuing loop"
                    )
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
                # ==================== ACT phase ====================

                # Runtime mode guard: block tools not in the filtered set (defense-in-depth)
                _mode_blocked_results: list[dict] = []
                if _allowed_tool_names is not None:
                    _guarded_calls = []
                    for tc in decision.tool_calls:
                        _tc_name = self._tool_executor.canonicalize_tool_name(tc.get("name", ""))
                        _tc_id = tc.get("id", "")
                        _tc_input = tc.get("input", tc.get("arguments", {}))
                        _block_reason = _should_block_tool(
                            _tc_name, _tc_input, _allowed_tool_names, mode
                        )
                        if _block_reason:
                            logger.warning(f"[ModeGuard] Blocked '{_tc_name}' in {mode} mode")
                            _mode_blocked_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": _tc_id,
                                    "content": _block_reason,
                                    "is_error": True,
                                }
                            )
                        else:
                            _guarded_calls.append(tc)
                    if not _guarded_calls:
                        working_messages.append(
                            {
                                "role": "assistant",
                                "content": decision.assistant_content,
                                "reasoning_content": decision.thinking_content or None,
                            }
                        )
                        working_messages.append(
                            {
                                "role": "user",
                                "content": _mode_blocked_results,
                            }
                        )
                        continue
                    decision.tool_calls = _guarded_calls

                tool_names = [tc.get("name", "?") for tc in decision.tool_calls]
                logger.info(f"[ReAct] Iter {iteration + 1} — ACT: {tool_names}")
                await broadcast_event(
                    "pet-status-update",
                    {"status": "tool_execution", "tool_name": ", ".join(tool_names)},
                )
                try:
                    state.transition(TaskStatus.ACTING)
                except ValueError:
                    pass

                # ---- ask_user interception ----
                # If the LLM called ask_user, break the loop immediately and return the question to the user
                ask_user_calls = [tc for tc in decision.tool_calls if tc.get("name") == "ask_user"]
                other_calls = [tc for tc in decision.tool_calls if tc.get("name") != "ask_user"]

                if ask_user_calls:
                    logger.info(
                        f"[ReAct] Iter {iteration + 1} — ask_user intercepted, "
                        f"pausing for user input (other_tools={[tc.get('name') for tc in other_calls]})"
                    )

                    # Add the assistant message (preserve the complete tool_use content for context coherence)
                    working_messages.append(
                        {
                            "role": "assistant",
                            "content": decision.assistant_content,
                            "reasoning_content": decision.thinking_content or None,
                        }
                    )

                    # If other tool calls accompany it, execute them first
                    # Collect tool_result for the other tools (the Claude API requires each tool_use to have a corresponding tool_result)
                    other_tool_results: list[dict] = []
                    if other_calls:
                        (
                            other_results,
                            other_executed,
                            other_receipts,
                        ) = await self._tool_executor.execute_batch(
                            other_calls,
                            state=state,
                            task_monitor=task_monitor,
                            allow_interrupt_checks=self._state.interrupt_enabled,
                            capture_delivery_receipts=True,
                        )
                        if other_executed:
                            if any(t not in _ADMIN_TOOL_NAMES for t in other_executed):
                                tools_executed_in_task = True
                            executed_tool_names.extend(other_executed)
                            state.record_tool_execution(other_executed)
                        if other_receipts:
                            delivery_receipts = other_receipts
                            self._last_delivery_receipts = other_receipts
                        # Preserve the other tools' tool_result content
                        other_tool_results = other_results if other_results else []
                        all_tool_results.extend(other_tool_results)
                    if _mode_blocked_results:
                        other_tool_results.extend(_mode_blocked_results)

                    # Extract ask_user's question text (handles input/arguments + JSON-string arguments)
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

                    # Merge the LLM's text reply with the question
                    text_part = strip_thinking_tags(decision.text_content or "").strip()
                    if text_part and question:
                        final_text = f"{text_part}\n\n{question}"
                    elif question:
                        final_text = question
                    else:
                        final_text = text_part or "(waiting for user reply)"

                    # IM channel: append structured options to the question text
                    ask_opts = ask_input.get("options", [])
                    if ask_opts and isinstance(ask_opts, list):
                        opt_lines = []
                        for o in ask_opts:
                            if isinstance(o, dict) and o.get("id") and o.get("label"):
                                opt_lines.append(f"  {o['id']}: {o['label']}")
                        if opt_lines:
                            final_text += "\n\nOptions:\n" + "\n".join(opt_lines)

                    try:
                        state.transition(TaskStatus.WAITING_USER)
                    except ValueError:
                        pass

                    await broadcast_event("pet-status-update", {"status": "idle"})

                    # ---- IM mode: wait for user reply (with timeout + follow-up) ----
                    user_reply = await self._wait_for_user_reply(
                        final_text,
                        state,
                        timeout_seconds=60,
                        max_reminders=1,
                    )

                    # Build the tool_result message (other tools' results + ask_user result must be in the same user message)
                    def _build_ask_user_tool_results(
                        ask_user_content: str,
                        _other_results: list[dict] = other_tool_results,
                        _ask_id: str = ask_tool_id,
                    ) -> list[dict]:
                        """Build a user-message content containing all tool_result entries"""
                        results = list(_other_results)  # other tools' tool_result
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": _ask_id,
                                "content": ask_user_content,
                            }
                        )
                        return results

                    if user_reply:
                        # User replied within the timeout -> inject the reply and continue the ReAct loop
                        logger.info(
                            f"[ReAct] Iter {iteration + 1} — ask_user: user replied, resuming loop"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append(
                            {
                                "role": "user",
                                "content": _build_ask_user_tool_results(f"user reply：{user_reply}"),
                            }
                        )
                        try:
                            state.transition(TaskStatus.REASONING)
                        except ValueError:
                            pass
                        continue  # continue the ReAct loop

                    elif (
                        user_reply is None
                        and self._state.current_session
                        and (
                            self._state.current_session.get_metadata("_gateway")
                            if hasattr(self._state.current_session, "get_metadata")
                            else None
                        )
                    ):
                        # IM mode, user did not reply within timeout -> inject a system prompt so the LLM decides on its own
                        logger.info(
                            f"[ReAct] Iter {iteration + 1} — ask_user: user timeout, "
                            f"injecting auto-decide prompt"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append(
                            {
                                "role": "user",
                                "content": _build_ask_user_tool_results(
                                    "[System] The user did not reply to your question within 2 minutes."
                                    "Decide on your own: if you can reasonably infer the user's intent, continue executing the task;"
                                    "otherwise terminate the current task and tell the user what information you need."
                                ),
                            }
                        )
                        try:
                            state.transition(TaskStatus.REASONING)
                        except ValueError:
                            pass
                        continue  # continue the ReAct loop and let the LLM decide on its own

                    else:
                        # CLI mode or no gateway -> return the question text directly
                        tracer.end_trace(
                            metadata={
                                "result": "waiting_user",
                                "iterations": iteration + 1,
                                "tools_used": list(set(executed_tool_names)),
                            }
                        )
                        react_trace.append(_iter_trace)
                        self._save_react_trace(
                            react_trace,
                            conversation_id,
                            session_type,
                            "waiting_user",
                            _trace_started_at,
                        )
                        self._last_exit_reason = "ask_user"
                        logger.info(
                            f"[ReAct] === WAITING_USER (CLI) after {iteration + 1} iterations ==="
                        )
                        return final_text

                # Save checkpoint (before tool execution)
                self._save_checkpoint(working_messages, state, decision, iteration)

                # Add assistant message
                working_messages.append(
                    {
                        "role": "assistant",
                        "content": decision.assistant_content,
                        "reasoning_content": decision.thinking_content or None,
                    }
                )

                # Check cancellation
                if state.cancelled:
                    react_trace.append(_iter_trace)
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                    )
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )

                # === IM progress: describe the tool about to run ===
                for tc in decision.tool_calls or []:
                    _tc_name = self._tool_executor.canonicalize_tool_name(tc.get("name", "unknown"))
                    _tc_args = tc.get("input", tc.get("arguments", {}))
                    await _emit_progress(f"🔧 {self._describe_tool_call(_tc_name, _tc_args)}")

                # Same-name tool rate limit: calls over the threshold are skipped and return a hint
                _all_tool_calls = list(decision.tool_calls or [])
                _rate_limited_by_id: dict[str, dict] = {}
                _calls_to_execute = []
                for tc in _all_tool_calls:
                    _tc_name = self._tool_executor.canonicalize_tool_name(tc.get("name", ""))
                    _tool_call_counter[_tc_name] = _tool_call_counter.get(_tc_name, 0) + 1
                    if _tool_call_counter[_tc_name] > _MAX_SAME_TOOL_PER_TASK:
                        logger.warning(
                            f"[RateLimit] Tool '{_tc_name}' called "
                            f"{_tool_call_counter[_tc_name]} times (limit={_MAX_SAME_TOOL_PER_TASK}), "
                            f"skipping execution"
                        )
                        _rate_limited_by_id[tc.get("id", "")] = {
                            "type": "tool_result",
                            "tool_use_id": tc.get("id", ""),
                            "content": (
                                f"[System] Tool {_tc_name} has already been called "
                                f"{_tool_call_counter[_tc_name] - 1} times in this task, reaching the limit."
                                f"Please consolidate operations or move on to the next step."
                            ),
                        }
                    else:
                        _calls_to_execute.append(tc)
                decision.tool_calls = _calls_to_execute

                # Execute the tool
                tool_results, executed, receipts = await self._tool_executor.execute_batch(
                    decision.tool_calls,
                    state=state,
                    task_monitor=task_monitor,
                    allow_interrupt_checks=self._state.interrupt_enabled,
                    capture_delivery_receipts=True,
                )
                if _rate_limited_by_id:
                    _executed_by_id = {r.get("tool_use_id"): r for r in tool_results}
                    merged_results = []
                    for tc in _all_tool_calls:
                        tid = tc.get("id", "")
                        if tid in _rate_limited_by_id:
                            merged_results.append(_rate_limited_by_id[tid])
                        elif tid in _executed_by_id:
                            merged_results.append(_executed_by_id[tid])
                    tool_results = merged_results

                all_tool_results.extend(tool_results)

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

                # Record tool success/failure state + IM progress
                # Iterate over decision.tool_calls / tool_results in alignment,
                # avoiding length mismatch between executed (only successful names) and tool_results
                for i, tc in enumerate(decision.tool_calls):
                    _tc_name = tc.get("name", "")
                    result_content = ""
                    is_error = False
                    if i < len(tool_results):
                        r = tool_results[i]
                        result_content = (
                            str(r.get("content", "")) if isinstance(r, dict) else str(r)
                        )
                        # Primary signal: the structured is_error flag on tool_result
                        is_error = r.get("is_error", False) if isinstance(r, dict) else False
                    # Fallback: string-marker match (the error string returned by the handler)
                    if not is_error and result_content:
                        is_error = any(
                            m in result_content
                            for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"]
                        )
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
                        "[ReAct] exit_plan_mode called — ending turn, waiting for user review"
                    )
                    working_messages.append({"role": "user", "content": tool_results})
                    react_trace.append(_iter_trace)
                    self._save_react_trace(
                        react_trace,
                        conversation_id,
                        session_type,
                        "plan_exit",
                        _trace_started_at,
                    )
                    return (
                        "Plan completed and waiting for user review. "
                        "The user can approve the plan to switch to Agent mode, "
                        "or request changes to continue refining."
                    )

                # ==================== OBSERVE phase ====================
                logger.info(
                    f"[ReAct] Iter {iteration + 1} — OBSERVE: "
                    f"{len(tool_results)} results from {executed or []}"
                )
                if state.cancelled:
                    working_messages.append({"role": "user", "content": tool_results})
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                    )
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )
                try:
                    state.transition(TaskStatus.OBSERVING)
                except ValueError:
                    pass

                # Collect tool results into the trace (save full content; no truncation)
                _error_markers = ("❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:")
                _trace_results = []
                for tr in tool_results:
                    if not isinstance(tr, dict):
                        continue
                    _rc = str(tr.get("content", ""))
                    _is_err = tr.get("is_error", False) or any(
                        m in _rc for m in _error_markers
                    )
                    _trace_results.append({
                        "tool_use_id": tr.get("tool_use_id", ""),
                        "result_content": _rc,
                        "is_error": _is_err,
                    })
                    logger.info(
                        f"[ReAct] Iter {iteration + 1} — tool_result "
                        f"id={tr.get('tool_use_id', '')} len={len(_rc)}"
                    )
                _iter_trace["tool_results"] = _trace_results
                react_trace.append(_iter_trace)

                # Persistent-failure detection: when the same tool accumulates failures across rollbacks up to the limit,
                # inject a force-strategy-switch hint instead of rolling back again (prevents truncation-induced infinite loops)
                _persistent_exceeded = {
                    name: count
                    for name, count in self._persistent_tool_failures.items()
                    if count >= self.PERSISTENT_FAIL_LIMIT
                }
                if _persistent_exceeded:
                    _tool_names = ", ".join(_persistent_exceeded.keys())
                    _hint = (
                        f"[System notice] Tool {_tool_names} has accumulated {self.PERSISTENT_FAIL_LIMIT} failures"
                        f"(including across rollbacks); this is usually because arguments were truncated by the API."
                        "You must switch to a completely different strategy:\n"
                        "- Use run_shell to execute a Python script to generate a large file\n"
                        "- Split the content into multiple small writes\n"
                        "- Write a skeleton first, then fill in gradually\n"
                        "Do not invoke this tool the same way again."
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

                # Detect truncation errors (PARSE_ERROR_KEY) -- failures due to truncation should NOT trigger rollback,
                # because rollback discards the error feedback, causing the LLM to regenerate the same oversized content in a deadlock
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
                        f"[ReAct] Iter {iteration + 1} — Tool args truncated "
                        f"(count: {self._consecutive_truncation_count}), "
                        f"skipping rollback to preserve error feedback"
                    )
                else:
                    self._consecutive_truncation_count = 0

                # Check whether to roll back -- never on truncation errors
                should_rb, rb_reason = self._should_rollback(tool_results)
                if should_rb and not _has_truncation:
                    rollback_result = self._rollback(rb_reason)
                    if rollback_result:
                        working_messages, _ = rollback_result
                        logger.info("[Rollback] rollback succeeded; will re-reason with a different approach")
                        continue

                if state.cancelled:
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                    )
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return await self._cancel_farewell(
                        working_messages, _build_effective_system_prompt(), current_model, state
                    )

                # Add tool results (truncate oversized batches per budget)
                tool_results = _apply_tool_result_budget(tool_results)
                working_messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )

                # >= 2 consecutive truncations: inject mandatory-splitting guidance to break the deadlock
                if _has_truncation and self._consecutive_truncation_count >= 2:
                    _split_guidance = (
                        "WARNING: your tool-call arguments were repeatedly truncated by the API for being too long (consecutively "
                        f"{self._consecutive_truncation_count} times). You must change strategy immediately:\n"
                        "1. Split large files into multiple write_file calls (no more than 2000 lines each)\n"
                        "2. Create a file skeleton first, then fill in section-by-section with edit_file\n"
                        "3. Reduce inline CSS/JS; use concise implementations\n"
                        "4. If the content is truly long, consider Markdown instead of HTML"
                    )
                    working_messages.append({"role": "user", "content": _split_guidance})
                    logger.warning(
                        f"[ReAct] Injected split guidance after "
                        f"{self._consecutive_truncation_count} consecutive truncations"
                    )

                # Supervisor: record tool-call data
                # Align decision.tool_calls and tool_results by index
                # to avoid mismatch between executed (only successful tool names) and tool_results
                for i, tc in enumerate(decision.tool_calls):
                    _tc_name = tc.get("name", "")
                    result_content = ""
                    is_error = False
                    if i < len(tool_results):
                        r = tool_results[i]
                        result_content = (
                            str(r.get("content", "")) if isinstance(r, dict) else str(r)
                        )
                        is_error = r.get("is_error", False) if isinstance(r, dict) else False
                    if not is_error and result_content:
                        is_error = any(
                            m in result_content
                            for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"]
                        )
                    self._supervisor.record_tool_call(
                        tool_name=_tc_name,
                        params=tc.get("input", {}),
                        success=not is_error,
                        iteration=iteration,
                    )

                # Supervisor: record response text and token usage
                self._supervisor.record_response(decision.text_content or "")
                if _in_tokens or _out_tokens:
                    self._supervisor.record_token_usage(_in_tokens + _out_tokens)

                # Loop detection
                consecutive_tool_rounds += 1
                self._supervisor.record_consecutive_tool_rounds(consecutive_tool_rounds)

                # stop_reason check
                if decision.stop_reason == "end_turn":
                    cleaned_text = strip_thinking_tags(decision.text_content)
                    _, cleaned_text = parse_intent_tag(cleaned_text)
                    if cleaned_text and cleaned_text.strip():
                        logger.info(
                            f"[LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds"
                        )
                        self._save_react_trace(
                            react_trace,
                            conversation_id,
                            session_type,
                            "completed_end_turn",
                            _trace_started_at,
                        )
                        try:
                            state.transition(TaskStatus.COMPLETED)
                        except ValueError:
                            pass
                        tracer.end_trace(
                            metadata={
                                "result": "completed_end_turn",
                                "iterations": iteration + 1,
                                "tools_used": list(set(executed_tool_names)),
                            }
                        )
                        return cleaned_text

                # Tool-signature loop detection (Supervisor-based)
                round_signatures = [_make_tool_signature(tc) for tc in decision.tool_calls]
                round_sig_str = "+".join(sorted(round_signatures))
                self._supervisor.record_tool_signature(round_sig_str)

                # Supervisor holistic evaluation
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
                        self._save_react_trace(
                            react_trace,
                            conversation_id,
                            session_type,
                            "loop_terminated",
                            _trace_started_at,
                        )
                        try:
                            state.transition(TaskStatus.FAILED)
                        except ValueError:
                            pass
                        tracer.end_trace(
                            metadata={
                                "result": "loop_terminated",
                                "iterations": iteration + 1,
                                "supervisor_pattern": intervention.pattern.value,
                            }
                        )
                        self._run_failure_analysis(
                            react_trace,
                            "loop_terminated",
                            task_description=task_description,
                            task_id=state.task_id,
                        )
                        self._last_exit_reason = "loop_terminated"
                        return (
                            cleaned
                            or "WARNING: a tool-call deadlock was detected; the task has been auto-terminated. Please restate your request."
                        )

                    if intervention.should_rollback:
                        rollback_result = self._rollback(intervention.message)
                        if rollback_result:
                            working_messages, _ = rollback_result
                            if intervention.should_inject_prompt and intervention.prompt_injection:
                                working_messages.append(
                                    {
                                        "role": "user",
                                        "content": intervention.prompt_injection,
                                    }
                                )
                            logger.info(
                                f"[Supervisor] Rollback + strategy switch: {intervention.message}"
                            )
                            continue

                    if intervention.should_inject_prompt and intervention.prompt_injection:
                        working_messages.append(
                            {
                                "role": "user",
                                "content": intervention.prompt_injection,
                            }
                        )
                        if intervention.throttled_tool_names:
                            _blocked = set(intervention.throttled_tool_names)
                            tools = [t for t in tools if t.get("name") not in _blocked]
                            logger.info(
                                f"[Supervisor] NUDGE: removed throttled tools {_blocked}, "
                                f"{len(tools)} tools remain "
                                f"(iter={iteration}, pattern={intervention.pattern.value})"
                            )
                        else:
                            tools = []
                            logger.info(
                                f"[Supervisor] NUDGE: tools stripped to force text response "
                                f"(iter={iteration}, pattern={intervention.pattern.value})"
                            )
                        max_no_tool_retries = 0

        self._last_working_messages = working_messages
        self._save_react_trace(
            react_trace, conversation_id, session_type, "max_iterations", _trace_started_at
        )
        try:
            state.transition(TaskStatus.FAILED)
        except ValueError:
            pass
        tracer.end_trace(metadata={"result": "max_iterations", "iterations": max_iterations})
        self._run_failure_analysis(
            react_trace,
            "max_iterations",
            task_description=task_description,
            task_id=state.task_id,
        )
        await broadcast_event("pet-status-update", {"status": "error"})
        self._last_exit_reason = "max_iterations"
        if max_iterations < 30:
            return (
                f"Maximum iteration count reached ({max_iterations})."
                f"The current MAX_ITERATIONS={max_iterations} is set too low;"
                f"we recommend raising it to 100-300 to support complex tasks."
            )
        return "Maximum tool-call count reached. Please restate your request."

    # ==================== Streaming output (SSE) ====================

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
        Streaming reasoning loop, designed for the HTTP API (SSE).

        Feature-aligned with run(): TaskMonitor, loop detection, model switching,
        LLM error retries, task-completion verification, rollback, etc.

        Callers (e.g. Agent.chat_with_session_stream) must pass tools and system_prompt;
        all new parameters are optional, maintaining backward compatibility with older callers.

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
        all_tool_results: list[dict] = []
        _trace_started_at = datetime.now().isoformat()
        _endpoint_switched = False

        _session_key = conversation_id or ""
        state = (
            self._state.get_task_for_session(_session_key)
            if _session_key
            else self._state.current_task
        )

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
            # === Dynamic System Prompt (append active Plan) ===
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
                from ..prompt.builder import build_mode_rules

                _plan_rules = build_mode_rules("plan")
                if _plan_rules:
                    effective_prompt += f"\n\n{_plan_rules}"
            elif _effective_mode == "ask":
                from ..prompt.builder import build_mode_rules

                _ask_rules = build_mode_rules("ask")
                if _ask_rules:
                    effective_prompt += f"\n\n{_ask_rules}"
            elif _effective_mode == "coordinator":
                from ..prompt.builder import build_mode_rules

                _coordinator_rules = build_mode_rules("coordinator")
                if _coordinator_rules:
                    effective_prompt += f"\n\n{_coordinator_rules}"

            # Tool filtering by mode — restrict available tools based on current mode
            tools = _filter_tools_by_mode(tools, _effective_mode)
            _allowed_tool_names = (
                {t.get("name", "") for t in tools} if _effective_mode != "agent" else None
            )
            self._tool_executor._current_mode = _effective_mode

            # === Endpoint override ===
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
                        yield {"type": "error", "message": f"Endpoint switch failed: {msg}"}
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

            # === Loop-control variables matching run() ===
            state.original_user_messages = [
                msg for msg in messages if self._is_human_user_message(msg)
            ]
            max_iterations = (
                getattr(self, "_max_iterations_override", None) or settings.max_iterations
            )
            self._max_iterations_override = None  # consume once
            self._empty_content_retries = 0
            working_messages = list(messages)

            # ForceToolCall configuration
            im_floor = max(0, int(getattr(settings, "force_tool_call_im_floor", 2)))
            _override = getattr(self, "_force_tool_override", None)
            configured = int(
                _override if _override is not None
                else getattr(settings, "force_tool_call_max_retries", 2)
            )
            if session_type == "im":
                base_force_retries = max(im_floor, configured)
            else:
                base_force_retries = max(0, configured)

            max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)

            # Intent-driven override (from IntentAnalyzer)
            if force_tool_retries is not None:
                max_no_tool_retries = force_tool_retries
                logger.info(
                    f"[ForceToolCall/Stream] Intent override: max_retries={force_tool_retries}"
                )

            max_verify_retries = 1
            max_confirmation_text_retries = max(
                0, int(getattr(settings, "confirmation_text_max_retries", 2))
            )

            executed_tool_names: list[str] = []
            delivery_receipts: list[dict] = []
            _last_browser_url = ""
            _last_chain_text: str = ""
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
                if (
                    name in self._browser_page_read_tools
                    and len(param_str) <= 20
                    and _last_browser_url
                ):
                    param_str = f"{param_str}|url={_last_browser_url}"
                param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
                return f"{name}({param_hash})"

            # --- Restored Todo: replay SSE events so the frontend rebuilds the FloatingPlanBar ---
            if conversation_id:
                try:
                    from ..tools.handlers.plan import get_todo_handler_for_session, has_active_todo

                    if has_active_todo(conversation_id):
                        _rh = get_todo_handler_for_session(conversation_id)
                        _rp = _rh.get_plan_for(conversation_id) if _rh else None
                        if _rp and _rp.get("status") == "in_progress":
                            yield {
                                "type": "todo_created",
                                "restored": True,
                                "plan": {
                                    "id": _rp.get("id", ""),
                                    "taskSummary": _rp.get("task_summary", ""),
                                    "steps": [
                                        {
                                            "id": s.get("id", ""),
                                            "description": s.get("description", ""),
                                            "status": s.get("status", "pending"),
                                        }
                                        for s in _rp.get("steps", [])
                                    ],
                                    "status": "in_progress",
                                },
                            }
                except Exception:
                    pass

            # ==================== Main loop ====================
            logger.info(
                f"[ReAct-Stream] === Loop started (max_iterations={max_iterations}, model={current_model}) ==="
            )

            _last_discovered_snapshot: frozenset = frozenset()
            _death_switch_notified = False

            for _iteration in range(max_iterations):
                self._last_working_messages = working_messages
                state.iteration = _iteration

                # --- Cancellation check ---
                if state.cancelled:
                    logger.info(
                        f"[ReAct-Stream] Task cancelled at iteration start: {state.cancel_reason}"
                    )
                    self._save_react_trace(
                        react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                    )
                    yield {"type": "text_delta", "content": "✅ Task stopped."}
                    yield {"type": "done"}
                    return

                # --- Resource Budget check (matching run()) ---
                self._budget.record_iteration()
                budget_status = self._budget.check()
                if budget_status.action == BudgetAction.PAUSE:
                    logger.warning(f"[Budget-Stream] PAUSE: {budget_status.message}")
                    self._save_react_trace(
                        react_trace,
                        conversation_id,
                        session_type,
                        "budget_exceeded",
                        _trace_started_at,
                    )
                    self._run_failure_analysis(
                        react_trace,
                        "budget_exceeded",
                        task_description=task_description,
                        task_id=state.task_id,
                    )
                    msg = (
                        f"⚠️ Task resource budget exhausted ({budget_status.dimension}: "
                        f"{budget_status.usage_ratio:.0%}). Task paused.\n"
                        f"Progress saved — adjust the budget and continue."
                    )
                    yield {"type": "text_delta", "content": msg}
                    yield {"type": "done"}
                    return
                elif budget_status.action in (BudgetAction.WARNING, BudgetAction.DOWNGRADE):
                    logger.info(
                        "[Budget] %s: %s — logged only, not injected",
                        budget_status.dimension,
                        budget_status.message,
                    )

                # --- TaskMonitor: iteration start + model-switch check ---
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
                    f"[ReAct-Stream] Iter {_iteration + 1}/{max_iterations} — REASON (model={current_model})"
                )

                # --- State transition: REASONING (matching run()) ---
                if state.status != TaskStatus.REASONING:
                    state.transition(TaskStatus.REASONING)

                _ctx_compressed_info: dict | None = None
                if len(working_messages) > 2:
                    working_messages = self._context_manager.pre_request_cleanup(working_messages)
                    effective_prompt = _build_effective_prompt()
                    _before_tokens = self._context_manager.estimate_messages_tokens(
                        working_messages
                    )
                    try:
                        working_messages = await self._context_manager.compress_if_needed(
                            working_messages,
                            system_prompt=effective_prompt,
                            tools=tools,
                            memory_manager=self._memory_manager,
                            conversation_id=conversation_id,
                        )
                    except _CtxCancelledError:
                        # Matches run(): terminate only on explicit user cancellation.
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

                # --- Reasoning chain: iteration-start event ---
                yield {"type": "iteration_start", "iteration": _iteration + 1}

                # Refresh tools only when _discovered_tools actually changes
                # (not every iteration — otherwise Supervisor NUDGE that strips
                # tools to [] gets immediately overridden; see issue #443)
                _agent = getattr(self._tool_executor, "_agent_ref", None)
                if _iteration > 0 and _agent and getattr(_agent, "_discovered_tools", None):
                    _current_discovered = frozenset(getattr(_agent, "_discovered_tools", ()))
                    if _current_discovered != _last_discovered_snapshot:
                        _last_discovered_snapshot = _current_discovered
                        refreshed = _filter_tools_by_mode(_agent._effective_tools, _effective_mode)
                        if {t.get("name") for t in refreshed} != {t.get("name") for t in tools}:
                            tools = refreshed
                            _allowed_tool_names = (
                                {t.get("name", "") for t in tools}
                                if _effective_mode != "agent"
                                else None
                            )
                            logger.info(
                                "[ReAct-Stream] tools refreshed after tool_search discovery (now %d tools)",
                                len(tools),
                            )

                # --- Reason phase (true streaming) ---
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
                    # --- User cancellation interrupt: dispatch a lightweight LLM farewell ---
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
                    # --- LLM error handling (matching run()) ---
                    retry_result = await self._handle_llm_error(
                        e, task_monitor, state, working_messages, current_model
                    )
                    _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                    yield {"type": "thinking_end", "duration_ms": _thinking_duration}

                    if retry_result == "retry":
                        _total_r = getattr(state, "_total_llm_retries", 1)
                        yield {
                            "type": "chain_text",
                            "content": (
                                f"AI service error, retrying…"
                                f"（{_total_r}/{self.MAX_TOTAL_LLM_RETRIES}）..."
                            ),
                            "icon": "alert",
                        }
                        _retry_sleep = min(2 * _total_r, 15)
                        _sleep = asyncio.create_task(asyncio.sleep(_retry_sleep))
                        _cw = asyncio.create_task(state.cancel_event.wait())
                        _done, _pend = await asyncio.wait(
                            {_sleep, _cw}, return_when=asyncio.FIRST_COMPLETED
                        )
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
                        yield {
                            "type": "chain_text",
                            "content": "Current model unavailable, switching to fallback…",
                            "icon": "refresh",
                        }
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
                            react_trace,
                            conversation_id,
                            session_type,
                            f"reason_error: {str(e)[:100]}",
                            _trace_started_at,
                        )
                        err_msg = str(e)[:500]
                        user_msg = f"Reasoning failed: {err_msg[:300]}"
                        err_lower = err_msg.lower()
                        if "image" in err_lower and (
                            "width" in err_lower
                            or "height" in err_lower
                            or "size" in err_lower
                            or "dimension" in err_lower
                            or "larger than" in err_lower
                        ):
                            user_msg = (
                                "Image processing failed: image dimensions do not meet the model's requirements."
                                "Please retry with an image whose width and height are both greater than 10 pixels."
                            )
                        yield {"type": "error", "message": user_msg}
                        yield {"type": "done"}
                        return

                # Emit thinking content (already streamed incrementally; fallback: non-streaming path)
                _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                _has_thinking = bool(decision.thinking_content)
                if _has_thinking and not _streamed_thinking:
                    yield {"type": "thinking_delta", "content": decision.thinking_content}
                yield {
                    "type": "thinking_end",
                    "duration_ms": _thinking_duration,
                    "has_thinking": _has_thinking,
                }

                # chain_text: text is already pushed in real time via text_delta; only used as fallback when non-streaming
                if not _streamed_text:
                    _decision_text = (decision.text_content or "").strip()
                    if _decision_text and decision.type == DecisionType.TOOL_CALLS:
                        if _decision_text != _last_chain_text:
                            yield {"type": "chain_text", "content": _decision_text[:2000]}
                            _last_chain_text = _decision_text
                        else:
                            logger.info(
                                f"[ReAct-Stream] Iter {_iteration+1} — suppressed duplicate chain_text "
                                f"({len(_decision_text)} chars)"
                            )
                elif decision.type == DecisionType.TOOL_CALLS:
                    yield {"type": "text_replace", "content": ""}
                    _decision_text = (decision.text_content or "").strip()
                    if _decision_text:
                        if _decision_text != _last_chain_text:
                            yield {"type": "chain_text", "content": _decision_text[:2000]}
                            _last_chain_text = _decision_text
                        else:
                            logger.info(
                                f"[ReAct-Stream] Iter {_iteration+1} — suppressed duplicate chain_text "
                                f"({len(_decision_text)} chars)"
                            )
                elif _raw_streamed_text != (decision.text_content or ""):
                    yield {
                        "type": "text_replace",
                        "content": decision.text_content or "",
                    }

                if task_monitor:
                    task_monitor.end_iteration(decision.text_content or "")

                # -- Collect ReAct trace + Budget record token --
                # Streaming mode: usage comes from StreamAccumulator (_stream_usage dict)
                # Non-streaming fallback: usage comes from decision.raw_response
                _raw = decision.raw_response
                _usage = getattr(_raw, "usage", None) if _raw else None
                _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
                _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0
                _cache_read = 0
                _cache_create = 0
                if not (_in_tokens or _out_tokens) and _stream_usage:
                    _in_tokens = _stream_usage.get("input_tokens", 0)
                    _out_tokens = _stream_usage.get("output_tokens", 0)
                if _stream_usage:
                    _cache_read = int(
                        _stream_usage.get("cache_read_input_tokens", 0) or 0
                    )
                    _cache_create = int(
                        _stream_usage.get("cache_creation_input_tokens", 0) or 0
                    )
                if _usage:
                    _cache_read = _cache_read or getattr(
                        _usage, "cache_read_input_tokens", 0
                    )
                    _cache_create = _cache_create or getattr(
                        _usage, "cache_creation_input_tokens", 0
                    )
                if _in_tokens or _out_tokens:
                    self._budget.record_tokens(_in_tokens, _out_tokens)
                # 流式路径下 brain 不落 token_tracking（详见 brain.messages_create_stream
                # 注释），需在此显式落库以保留 cache_read/cache_create 命中统计。
                if _in_tokens or _out_tokens or _cache_read or _cache_create:
                    try:
                        from .token_tracking import record_usage as _tt_record_usage

                        _ep_info = self._brain.get_current_endpoint_info() or {}
                        _ep_name = _ep_info.get("name", "")
                        _cost = 0.0
                        for _ep in self._brain._llm_client.endpoints:
                            if _ep.name == _ep_name:
                                _cost = _ep.calculate_cost(
                                    input_tokens=_in_tokens,
                                    output_tokens=_out_tokens,
                                    cache_read_tokens=_cache_read,
                                )
                                break
                        _tt_record_usage(
                            model=current_model or "",
                            endpoint_name=_ep_name,
                            input_tokens=_in_tokens,
                            output_tokens=_out_tokens,
                            cache_creation_tokens=_cache_create,
                            cache_read_tokens=_cache_read,
                            estimated_cost=_cost,
                        )
                    except Exception as _tt_err:
                        logger.debug(
                            f"[ReAct-Stream] token_tracking record failed (non-fatal): {_tt_err}"
                        )
                _iter_trace: dict = {
                    "iteration": _iteration + 1,
                    "timestamp": datetime.now().isoformat(),
                    "decision_type": decision.type.value
                    if hasattr(decision.type, "value")
                    else str(decision.type),
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
                    f"[ReAct-Stream] Iter {_iteration + 1} — decision={_iter_trace['decision_type']}, "
                    f"tools={tool_names_log}, tokens_in={_in_tokens}, tokens_out={_out_tokens}"
                )

                # ==================== stop_reason=max_tokens detection (matching run()) ====================
                if decision.stop_reason == "max_tokens":
                    logger.warning(
                        f"[ReAct-Stream] Iter {_iteration + 1} — ⚠️ LLM output truncated (stop_reason=max_tokens). "
                        f"The response hit the max_tokens limit ({self._brain.max_tokens}). "
                        f"Tool calls may have incomplete JSON arguments."
                    )
                    _iter_trace["truncated"] = True

                    # Automatically raise max_tokens and retry (matching run())
                    if decision.type == DecisionType.TOOL_CALLS:
                        truncated_calls = [
                            tc
                            for tc in decision.tool_calls
                            if isinstance(tc.get("input"), dict) and PARSE_ERROR_KEY in tc["input"]
                        ]
                        _current_max = self._brain.max_tokens or 16384
                        _max_ceiling = min(_current_max * 3, 65536)
                        if truncated_calls and len(truncated_calls) == len(decision.tool_calls):
                            _new_max = min(_current_max * 2, _max_ceiling)
                            if _new_max > _current_max:
                                logger.warning(
                                    f"[ReAct-Stream] Iter {_iteration + 1} — All "
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
                                    f"[ReAct-Stream] Iter {_iteration + 1} — "
                                    f"{len(truncated_calls)}/{len(decision.tool_calls)} tool "
                                    f"calls truncated. Increasing max_tokens for next "
                                    f"iteration: {_current_max} → {_new_max}"
                                )
                                self._brain.max_tokens = _new_max

                # ==================== FINAL_ANSWER ====================
                if decision.type == DecisionType.FINAL_ANSWER:
                    # Automatically continue when FINAL_ANSWER is truncated by max_tokens (up to 2 times)
                    if (
                        decision.stop_reason == "max_tokens"
                        and getattr(state, "_text_continuation_count", 0) < 2
                    ):
                        state._text_continuation_count = getattr(state, "_text_continuation_count", 0) + 1
                        logger.info(
                            f"[ReAct-Stream] FINAL_ANSWER truncated by max_tokens, "
                            f"auto-continuation #{state._text_continuation_count}"
                        )
                        working_messages.append({
                            "role": "assistant",
                            "content": decision.assistant_content or [{"type": "text", "text": decision.text_content or ""}],
                            **({"reasoning_content": decision.thinking_content} if decision.thinking_content else {}),
                        })
                        working_messages.append({
                            "role": "user",
                            "content": "Your response was cut off. Please continue directly from where you left off — do not repeat yourself or apologize.",
                        })
                        react_trace.append(_iter_trace)
                        continue

                    consecutive_tool_rounds = 0

                    # Task-completion verification (matching run())
                    result = await self._handle_final_answer(
                        decision=decision,
                        working_messages=working_messages,
                        original_messages=messages,
                        tools_executed_in_task=tools_executed_in_task,
                        executed_tool_names=executed_tool_names,
                        delivery_receipts=delivery_receipts,
                        all_tool_results=all_tool_results,
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
                            react_trace,
                            conversation_id,
                            session_type,
                            "completed",
                            _trace_started_at,
                        )
                        try:
                            state.transition(TaskStatus.COMPLETED)
                        except ValueError:
                            state.status = TaskStatus.COMPLETED
                        logger.info(
                            f"[ReAct-Stream] === COMPLETED after {_iteration + 1} iterations ==="
                        )
                        if _streamed_text:
                            if result != _raw_streamed_text:
                                yield {"type": "text_replace", "content": result}
                        else:
                            chunk_size = 20
                            for i in range(0, len(result), chunk_size):
                                yield {"type": "text_delta", "content": result[i : i + chunk_size]}
                                await asyncio.sleep(0.01)
                        await broadcast_event("pet-status-update", {"status": "success"})
                        yield {"type": "done"}
                        return
                    else:
                        # Verification failed -> continue the loop; clear the streamed text already shown on the frontend
                        logger.info(
                            f"[ReAct-Stream] Iter {_iteration + 1} — VERIFY: incomplete, continuing loop"
                        )
                        if _streamed_text:
                            yield {"type": "text_replace", "content": ""}
                        yield {"type": "chain_text", "content": "Task not yet complete, continuing…"}
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

                    working_messages.append(
                        {
                            "role": "assistant",
                            "content": decision.assistant_content or [{"type": "text", "text": ""}],
                            "reasoning_content": decision.thinking_content or None,
                        }
                    )

                    # ---- ask_user interception ----
                    ask_user_calls = [
                        tc for tc in decision.tool_calls if tc.get("name") == "ask_user"
                    ]
                    other_tool_calls = [
                        tc for tc in decision.tool_calls if tc.get("name") != "ask_user"
                    ]

                    if ask_user_calls:
                        # Execute non-ask_user tools first
                        tool_results_for_msg: list[dict] = []
                        for tc in other_tool_calls:
                            t_name = self._tool_executor.canonicalize_tool_name(
                                tc.get("name", "unknown")
                            )
                            t_args = tc.get("input", tc.get("arguments", {}))
                            t_id = tc.get("id", str(uuid.uuid4()))
                            # Runtime mode guard — no tool_call events for blocked tools
                            _blocked_msg = _should_block_tool(
                                t_name, t_args, _allowed_tool_names, _effective_mode
                            )
                            if _blocked_msg:
                                logger.warning(
                                    f"[ModeGuard] Blocked '{t_name}' in {_effective_mode} mode"
                                )
                                yield {"type": "chain_text", "content": f"\n{_blocked_msg}\n"}
                                tool_results_for_msg.append(
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": t_id,
                                        "content": _blocked_msg,
                                        "is_error": True,
                                    }
                                )
                                continue
                            # chain_text: tool description
                            yield {
                                "type": "chain_text",
                                "content": self._describe_tool_call(t_name, t_args),
                            }
                            yield {
                                "type": "tool_call_start",
                                "tool": t_name,
                                "name": t_name,
                                "args": t_args,
                                "id": t_id,
                                "friendly_message": self._describe_tool_call(t_name, t_args),
                            }
                            await broadcast_event(
                                "pet-status-update",
                                {"status": "tool_execution", "tool_name": t_name},
                            )
                            # PolicyEngine check
                            from .policy import PolicyDecision, get_policy_engine

                            _pe = get_policy_engine()
                            _pr = _pe.assert_tool_allowed(
                                t_name, t_args if isinstance(t_args, dict) else {}
                            )
                            if _pr.decision == PolicyDecision.DENY:
                                r = f"WARNING: policy rejection: {_pr.reason}"
                                _tool_is_error = True
                            elif _pr.decision == PolicyDecision.CONFIRM:
                                _risk = _pr.metadata.get("risk_level", "HIGH")
                                _needs_sb = _pr.metadata.get("needs_sandbox", False)
                                _pe.store_ui_pending(
                                    t_id,
                                    t_name,
                                    t_args if isinstance(t_args, dict) else {},
                                    session_id=conversation_id or "",
                                    needs_sandbox=_needs_sb,
                                )
                                yield {
                                    "type": "security_confirm",
                                    "tool": t_name,
                                    "args": t_args if isinstance(t_args, dict) else {},
                                    "id": t_id,
                                    "reason": _pr.reason,
                                    "risk_level": _risk,
                                    "needs_sandbox": _needs_sb,
                                    "timeout_seconds": _pe._config.confirmation.timeout_seconds,
                                    "default_on_timeout": _pe._config.confirmation.default_on_timeout,
                                    "options": [
                                        "allow_once",
                                        "allow_session",
                                        "allow_always",
                                        "deny",
                                    ]
                                    + (["sandbox"] if _needs_sb else []),
                                }
                                r = (
                                    f"WARNING: user confirmation required: {_pr.reason}\n"
                                    "A confirmation request has been sent to the user; wait for them to decide via the UI before continuing."
                                    "Do not use the ask_user tool to ask again."
                                )
                                _tool_is_error = True
                            else:
                                _tool_is_error = False
                                try:
                                    r = await self._tool_executor.execute_tool_with_policy(
                                        tool_name=t_name,
                                        tool_input=t_args if isinstance(t_args, dict) else {},
                                        policy_result=_pr,
                                        session_id=conversation_id,
                                    )
                                    r = str(r) if r else ""
                                except Exception as exc:
                                    r = f"Tool error: {exc}"
                                    _tool_is_error = True
                            _ask_result_summary = self._summarize_tool_result(t_name, r)
                            yield {
                                "type": "tool_call_end",
                                "tool": t_name,
                                "result": r[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": t_id,
                                "is_error": _tool_is_error,
                                "result_summary": _ask_result_summary or "",
                            }
                            # chain_text: result summary
                            if _ask_result_summary:
                                yield {"type": "chain_text", "content": _ask_result_summary}
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": t_id,
                                    "content": r,
                                }
                            )

                        all_tool_results.extend(tool_results_for_msg)

                        # ask_user event
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
                                if (
                                    not isinstance(q, dict)
                                    or not q.get("id")
                                    or not q.get("prompt")
                                ):
                                    continue
                                pq: dict = {"id": str(q["id"]), "prompt": str(q["prompt"])}
                                q_options = q.get("options")
                                if q_options and isinstance(q_options, list):
                                    pq["options"] = [
                                        {
                                            "id": str(o.get("id", "")),
                                            "label": str(o.get("label", "")),
                                        }
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
                            react_trace,
                            conversation_id,
                            session_type,
                            "ask_user",
                            _trace_started_at,
                        )
                        self._last_exit_reason = "ask_user"
                        try:
                            state.transition(TaskStatus.WAITING_USER)
                        except ValueError:
                            state.status = TaskStatus.WAITING_USER
                        yield {"type": "done"}
                        return

                    # ---- Normal tool execution (supports three-way race between cancel_event / skip_event) ----
                    tool_results_for_msg: list[dict] = []
                    _non_denied_tool_names: list[str] = []
                    _stream_cancelled = False
                    _stream_skipped = False
                    cancel_event = state.cancel_event if state else asyncio.Event()
                    skip_event = state.skip_event if state else asyncio.Event()
                    for tc in decision.tool_calls:
                        # Check cancellation before each tool executes
                        if state and state.cancelled:
                            _stream_cancelled = True
                            break

                        tool_name = self._tool_executor.canonicalize_tool_name(
                            tc.get("name", "unknown")
                        )
                        tool_args = tc.get("input", tc.get("arguments", {}))
                        tool_id = tc.get("id", str(uuid.uuid4()))

                        # Same-name tool rate limit
                        _tool_call_counter[tool_name] = _tool_call_counter.get(tool_name, 0) + 1
                        if _tool_call_counter[tool_name] > _MAX_SAME_TOOL_PER_TASK:
                            logger.warning(
                                f"[RateLimit] Tool '{tool_name}' called "
                                f"{_tool_call_counter[tool_name]} times "
                                f"(limit={_MAX_SAME_TOOL_PER_TASK}), skipping"
                            )
                            _rl_msg = (
                                f"[System] Tool {tool_name} has already been called "
                                f"{_tool_call_counter[tool_name] - 1} times in this task, reaching the limit."
                                f"Please consolidate operations or move on to the next step."
                            )
                            yield {
                                "type": "tool_call_start",
                                "tool": tool_name,
                                "name": tool_name,
                                "args": tool_args,
                                "id": tool_id,
                                "friendly_message": self._describe_tool_call(tool_name, tool_args),
                            }
                            yield {
                                "type": "tool_call_end",
                                "tool": tool_name,
                                "result": _rl_msg[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id,
                                "is_error": False,
                                "result_summary": _rl_msg,
                            }
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": _rl_msg,
                                }
                            )
                            continue

                        # Runtime mode guard — blocked tools do NOT emit
                        # tool_call_start/end to avoid leaking events to the frontend
                        _blocked_msg = _should_block_tool(
                            tool_name, tool_args, _allowed_tool_names, _effective_mode
                        )
                        if _blocked_msg:
                            logger.warning(
                                f"[ModeGuard] Blocked '{tool_name}' in {_effective_mode} mode"
                            )
                            yield {"type": "chain_text", "content": f"\n{_blocked_msg}\n"}
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": _blocked_msg,
                                    "is_error": True,
                                }
                            )
                            continue

                        _tool_desc = self._describe_tool_call(tool_name, tool_args)
                        yield {"type": "chain_text", "content": _tool_desc}

                        yield {
                            "type": "tool_call_start",
                            "tool": tool_name,
                            "name": tool_name,
                            "args": tool_args,
                            "id": tool_id,
                            "friendly_message": _tool_desc,
                        }
                        await broadcast_event(
                            "pet-status-update",
                            {"status": "tool_execution", "tool_name": tool_name},
                        )

                        # PolicyEngine check (matching execute_batch)
                        from .policy import PolicyDecision, get_policy_engine

                        _pe = get_policy_engine()
                        _tool_args_dict = tool_args if isinstance(tool_args, dict) else {}
                        _pr = _pe.assert_tool_allowed(tool_name, _tool_args_dict)
                        if _pr.decision == PolicyDecision.DENY:
                            result_text = f"WARNING: policy rejection: {_pr.reason}"
                            _deny_summary = self._summarize_tool_result(tool_name, result_text)
                            yield {
                                "type": "tool_call_end",
                                "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id,
                                "is_error": True,
                                "result_summary": _deny_summary or "",
                            }
                            if _deny_summary:
                                yield {"type": "chain_text", "content": _deny_summary}
                            if _pe.readonly_mode and not _death_switch_notified:
                                yield {"type": "death_switch", "active": True, "reason": _pr.reason}
                                _death_switch_notified = True
                            if _pe.readonly_mode:
                                result_text = (
                                    f"{result_text}\n\n"
                                    "[DEATH SWITCH] Agent has entered read-only mode; all non-read operations will be rejected."
                                    "Stop trying to modify/write/execute immediately; use read-only tools only."
                                    "Wait for the user to manually exit read-only mode before continuing."
                                )
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result_text,
                                    "is_error": True,
                                }
                            )
                            continue

                        if _pr.decision == PolicyDecision.CONFIRM:
                            _risk = _pr.metadata.get("risk_level", "HIGH")
                            _needs_sb = _pr.metadata.get("needs_sandbox", False)
                            _pe.store_ui_pending(
                                tool_id,
                                tool_name,
                                _tool_args_dict,
                                session_id=conversation_id or "",
                                needs_sandbox=_needs_sb,
                            )
                            yield {
                                "type": "security_confirm",
                                "tool": tool_name,
                                "args": _tool_args_dict,
                                "id": tool_id,
                                "reason": _pr.reason,
                                "risk_level": _risk,
                                "needs_sandbox": _needs_sb,
                                "timeout_seconds": _pe._config.confirmation.timeout_seconds,
                                "default_on_timeout": _pe._config.confirmation.default_on_timeout,
                                "options": ["allow_once", "allow_session", "allow_always", "deny"]
                                + (["sandbox"] if _needs_sb else []),
                            }
                            result_text = (
                                f"WARNING: user confirmation required: {_pr.reason}\n"
                                "A confirmation request has been sent to the user; wait for them to decide via the UI before continuing."
                                "Do not use the ask_user tool to ask again."
                            )
                            yield {
                                "type": "tool_call_end",
                                "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id,
                                "is_error": True,
                                "result_summary": self._summarize_tool_result(tool_name, result_text) or "",
                            }
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result_text,
                                    "is_error": True,
                                }
                            )
                            continue

                        _non_denied_tool_names.append(tool_name)

                        # Race tool execution against cancel_event / skip_event (three-way)
                        # Note: do not clear_skip() here; let any already-arrived skip signal be consumed naturally by the race
                        try:
                            tool_exec_task = asyncio.create_task(
                                self._tool_executor.execute_tool_with_policy(
                                    tool_name=tool_name,
                                    tool_input=tool_args if isinstance(tool_args, dict) else {},
                                    policy_result=_pr,
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
                                result_text = f"[Tool {tool_name} interrupted by user]"
                                _stream_cancelled = True
                            elif skip_waiter in done_set and tool_exec_task not in done_set:
                                _skip_reason = state.skip_reason if state else "user requested skip"
                                if state:
                                    state.clear_skip()
                                result_text = f"[User skipped this step: {_skip_reason}]"
                                _stream_skipped = True
                                logger.info(
                                    f"[SkipStep-Stream] Tool {tool_name} skipped: {_skip_reason}"
                                )
                            elif tool_exec_task in done_set:
                                result_text = tool_exec_task.result()
                                result_text = str(result_text) if result_text else ""
                            else:
                                result_text = f"[Tool {tool_name} interrupted by user]"
                                _stream_cancelled = True
                        except Exception as exc:
                            result_text = f"Tool error: {exc}"

                        _tool_is_error = result_text.startswith("Tool error:")
                        # Emit agent_handoff events from session.context.handoff_events (set by orchestrator.delegate)
                        if (
                            session
                            and hasattr(session, "context")
                            and hasattr(session.context, "handoff_events")
                        ):
                            for h in session.context.handoff_events:
                                yield {
                                    "type": "agent_handoff",
                                    "from_agent": h.get("from_agent", ""),
                                    "to_agent": h.get("to_agent", ""),
                                    "reason": h.get("reason", ""),
                                }
                            session.context.handoff_events.clear()
                        _end_result_summary = self._summarize_tool_result(tool_name, result_text) or ""
                        # On skip, send a tool_call_skipped event to notify the frontend
                        if _stream_skipped:
                            yield {
                                "type": "tool_call_end",
                                "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id,
                                "skipped": True,
                                "is_error": False,
                                "result_summary": _end_result_summary,
                            }
                        else:
                            yield {
                                "type": "tool_call_end",
                                "tool": tool_name,
                                "result": result_text[:_SSE_RESULT_PREVIEW_CHARS],
                                "id": tool_id,
                                "is_error": _tool_is_error,
                                "result_summary": _end_result_summary,
                            }

                        if _stream_cancelled:
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result_text,
                                    "is_error": True,
                                }
                            )
                            break

                        if _stream_skipped:
                            tool_results_for_msg.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result_text,
                                }
                            )
                            _stream_skipped = False
                            continue

                        # === chain_text: briefly summarize the tool's return value ===
                        _result_summary = self._summarize_tool_result(tool_name, result_text)
                        if _result_summary:
                            yield {"type": "chain_text", "content": _result_summary}

                        # deliver_artifacts receipt collection (matching run())
                        # Aligned with run(): deliver_artifacts is direct delivery,
                        # org_accept_deliverable is 'relay delivery' (the parent accepts the child's
                        # file-bearing artifacts; receipts.status == "relayed"),
                        # both of which count as valid delivery evidence in TaskVerify's view.
                        if (
                            tool_name in ("deliver_artifacts", "org_accept_deliverable")
                            and result_text
                        ):
                            try:
                                _rt = result_text
                                _lm = "\n\n[Execution log]"
                                if _lm in _rt:
                                    _rt = _rt[: _rt.index(_lm)]
                                _receipts_data = json.loads(_rt)
                                if (
                                    isinstance(_receipts_data, dict)
                                    and "receipts" in _receipts_data
                                    and isinstance(_receipts_data["receipts"], list)
                                    and _receipts_data["receipts"]
                                ):
                                    delivery_receipts = _receipts_data["receipts"]
                                    self._last_delivery_receipts = delivery_receipts
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Plan event
                        if tool_name == "create_todo" and isinstance(tool_args, dict):
                            raw_steps = tool_args.get("steps", [])
                            plan_steps = []
                            for idx, s in enumerate(raw_steps):
                                if isinstance(s, dict):
                                    plan_steps.append(
                                        {
                                            "id": str(s.get("id", f"step_{idx + 1}")),
                                            "description": str(
                                                s.get("description", s.get("id", ""))
                                            ),
                                            "status": "pending",
                                        }
                                    )
                                else:
                                    plan_steps.append(
                                        {
                                            "id": f"step_{idx + 1}",
                                            "description": str(s),
                                            "status": "pending",
                                        }
                                    )
                            # Obtain the real plan_id from the backend so that backend/frontend IDs stay consistent
                            _sse_plan_id = str(uuid.uuid4())
                            try:
                                from ..tools.handlers.plan import get_active_plan_id

                                _real_id = get_active_plan_id(conversation_id)
                                if _real_id:
                                    _sse_plan_id = _real_id
                            except Exception:
                                pass
                            yield {
                                "type": "todo_created",
                                "plan": {
                                    "id": _sse_plan_id,
                                    "taskSummary": tool_args.get("task_summary", ""),
                                    "steps": plan_steps,
                                    "status": "in_progress",
                                },
                            }
                        elif tool_name == "create_plan_file" and isinstance(tool_args, dict):
                            pf_todos = tool_args.get("todos", [])
                            pf_steps = []
                            for idx, t in enumerate(pf_todos):
                                if isinstance(t, dict):
                                    pf_steps.append(
                                        {
                                            "id": str(t.get("id", f"step_{idx + 1}")),
                                            "description": str(t.get("content", t.get("id", ""))),
                                            "status": "pending",
                                        }
                                    )
                            if pf_steps:
                                _pf_plan_id = ""
                                try:
                                    from ..tools.handlers.plan import get_active_plan_id

                                    _pf_plan_id = get_active_plan_id(conversation_id) or ""
                                except Exception:
                                    pass
                                yield {
                                    "type": "todo_created",
                                    "plan": {
                                        "id": _pf_plan_id or str(uuid.uuid4()),
                                        "taskSummary": tool_args.get("name", ""),
                                        "steps": pf_steps,
                                        "status": "in_progress",
                                    },
                                }
                        elif tool_name == "update_todo_step" and isinstance(tool_args, dict):
                            step_id = tool_args.get("step_id", "")
                            yield {
                                "type": "todo_step_updated",
                                "stepId": step_id,
                                "status": tool_args.get("status", "completed"),
                            }
                        elif tool_name == "complete_todo":
                            yield {"type": "todo_completed"}

                        _tr_entry: dict = {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result_text,
                        }
                        if _tool_is_error:
                            _tr_entry["is_error"] = True
                        tool_results_for_msg.append(_tr_entry)

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

                        # SSE: notify the frontend to show the approval panel (via SSE rather than WS, so Tauri local mode works)
                        _pending = self._plan_exit_pending or {}
                        _pending_data = (
                            _pending.get(conversation_id, {}) if isinstance(_pending, dict) else {}
                        )
                        if _pending_data:
                            yield {
                                "type": "plan_ready_for_approval",
                                "data": {
                                    "conversation_id": conversation_id,
                                    "summary": _pending_data.get("summary", ""),
                                    "plan_id": _pending_data.get("plan_id", ""),
                                    "plan_file": _pending_data.get("plan_file", ""),
                                },
                            }

                        yield {"type": "text_delta", "content": _summary_text}
                        self._save_react_trace(
                            react_trace,
                            conversation_id,
                            session_type,
                            "plan_exit",
                            _trace_started_at,
                        )
                        yield {"type": "done"}
                        return

                    if decision.tool_calls:
                        all_tool_results.extend(tool_results_for_msg)

                        if _non_denied_tool_names:
                            if any(t not in _ADMIN_TOOL_NAMES for t in _non_denied_tool_names):
                                tools_executed_in_task = True
                            executed_tool_names.extend(_non_denied_tool_names)
                            state.record_tool_execution(_non_denied_tool_names)
                            self._budget.record_tool_calls(len(_non_denied_tool_names))

                        # Record tool success/failure state (iterate decision.tool_calls with aligned indexing;
                        # include policy-rejected tools, matching run())
                        for i, tc_rec in enumerate(decision.tool_calls):
                            _tc_name = tc_rec.get("name", "")
                            r_content = ""
                            if i < len(tool_results_for_msg):
                                r_content = str(tool_results_for_msg[i].get("content", ""))
                            is_error = any(
                                m in r_content
                                for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"]
                            )
                            self._record_tool_result(_tc_name, success=not is_error)

                    # Collect tool results into the trace (full content; no truncation)
                    _s_error_markers = ("❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:")
                    _iter_trace["tool_results"] = []
                    for tr in tool_results_for_msg:
                        _rc = str(tr.get("content", ""))
                        _is_err = tr.get("is_error", False) or any(
                            m in _rc for m in _s_error_markers
                        )
                        _iter_trace["tool_results"].append({
                            "tool_use_id": tr.get("tool_use_id", ""),
                            "result_content": _rc,
                            "is_error": _is_err,
                        })
                    react_trace.append(_iter_trace)

                    try:
                        state.transition(TaskStatus.OBSERVING)
                    except ValueError:
                        state.status = TaskStatus.OBSERVING

                    # --- Truncation detection (matching run()) ---
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
                            f"[ReAct-Stream] Iter {_iteration + 1} — Tool args truncated "
                            f"(count: {self._consecutive_truncation_count}), "
                            f"skipping rollback"
                        )
                    else:
                        self._consecutive_truncation_count = 0

                    # --- Rollback check (matching run()) -- never on truncation errors ---
                    should_rb, rb_reason = self._should_rollback(tool_results_for_msg)
                    if should_rb and not _has_truncation:
                        rollback_result = self._rollback(rb_reason)
                        if rollback_result:
                            working_messages, _ = rollback_result
                            logger.info("[ReAct-Stream][Rollback] rollback succeeded; will re-reason with a different approach")
                            continue

                    # Cancellation check (escalates to LLM-backed farewell handling)
                    if state.cancelled or _stream_cancelled:
                        # Add tool results to the context
                        working_messages.append({"role": "user", "content": tool_results_for_msg})
                        self._save_react_trace(
                            react_trace,
                            conversation_id,
                            session_type,
                            "cancelled",
                            _trace_started_at,
                        )
                        async for ev in self._stream_cancel_farewell(
                            working_messages, effective_prompt, current_model, state
                        ):
                            yield ev
                        yield {"type": "done"}
                        return

                    tool_results_for_msg = _apply_tool_result_budget(tool_results_for_msg)
                    working_messages.append(
                        {
                            "role": "user",
                            "content": tool_results_for_msg,
                        }
                    )

                    # >= 2 consecutive truncations: inject mandatory splitting guidance (matching run())
                    if _has_truncation and self._consecutive_truncation_count >= 2:
                        _split_guidance = (
                            "WARNING: your tool-call arguments were repeatedly truncated by the API for being too long (consecutively "
                            f"{self._consecutive_truncation_count} times). You must change strategy immediately:\n"
                            "1. Split large files into multiple write_file calls (no more than 2000 lines each)\n"
                            "2. Create a file skeleton first, then fill in section-by-section with edit_file\n"
                            "3. Reduce inline CSS/JS; use concise implementations\n"
                            "4. If the content is truly long, consider Markdown instead of HTML"
                        )
                        working_messages.append({"role": "user", "content": _split_guidance})
                        logger.warning(
                            f"[ReAct-Stream] Injected split guidance after "
                            f"{self._consecutive_truncation_count} consecutive truncations"
                        )

                    # === Unified handling of skip-reflection + user-inserted messages ===
                    if state:
                        _msg_count_before = len(working_messages)
                        await state.process_post_tool_signals(working_messages)
                        for _new_msg in working_messages[_msg_count_before:]:
                            _content = _new_msg.get("content", "")
                            if "[系统提示-用户跳过步骤]" in _content:
                                yield {"type": "chain_text", "content": "User skipped the current step"}
                            elif "[用户插入消息]" in _content:
                                _preview = (
                                    _content.split("]")[1].split("\n")[0].strip()
                                    if "]" in _content
                                    else _content[:60]
                                )
                                yield {
                                    "type": "chain_text",
                                    "content": f"User inserted message: {_preview[:60]}",
                                }

                    # --- Supervisor: record tool data (iterate decision.tool_calls with aligned indexing; matching run()) ---
                    for _si, _stc in enumerate(decision.tool_calls or []):
                        _stn = _stc.get("name", "")
                        _sr_content = ""
                        if _si < len(tool_results_for_msg):
                            _sr = tool_results_for_msg[_si]
                            _sr_content = (
                                str(_sr.get("content", "")) if isinstance(_sr, dict) else str(_sr)
                            )
                        _sr_err = any(
                            m in _sr_content
                            for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "⚠️ 策略拒绝:"]
                        )
                        self._supervisor.record_tool_call(
                            tool_name=_stn,
                            params=_stc.get("input", {}),
                            success=not _sr_err,
                            iteration=_iteration,
                        )
                    self._supervisor.record_response(decision.text_content or "")
                    if _in_tokens or _out_tokens:
                        self._supervisor.record_token_usage(_in_tokens + _out_tokens)

                    # --- Loop detection (Supervisor-based; matching run()) ---
                    consecutive_tool_rounds += 1
                    self._supervisor.record_consecutive_tool_rounds(consecutive_tool_rounds)

                    # stop_reason check
                    if decision.stop_reason == "end_turn":
                        cleaned_text = strip_thinking_tags(decision.text_content)
                        _, cleaned_text = parse_intent_tag(cleaned_text)
                        if cleaned_text and cleaned_text.strip():
                            logger.info(
                                f"[ReAct-Stream][LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds"
                            )
                            self._save_react_trace(
                                react_trace,
                                conversation_id,
                                session_type,
                                "completed_end_turn",
                                _trace_started_at,
                            )
                            if _streamed_text:
                                if cleaned_text != _raw_streamed_text:
                                    yield {"type": "text_replace", "content": cleaned_text}
                            else:
                                chunk_size = 20
                                for i in range(0, len(cleaned_text), chunk_size):
                                    yield {
                                        "type": "text_delta",
                                        "content": cleaned_text[i : i + chunk_size],
                                    }
                                    await asyncio.sleep(0.01)
                            yield {"type": "done"}
                            return

                    # Supervisor holistic evaluation
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
                        _iteration,
                        has_active_todo=_has_todo_s,
                        plan_current_step=_todo_step_s,
                    )

                    if intervention:
                        _supervisor_intervened = True
                        max_no_tool_retries = 0

                        if intervention.should_terminate:
                            cleaned = strip_thinking_tags(decision.text_content)
                            self._save_react_trace(
                                react_trace,
                                conversation_id,
                                session_type,
                                "loop_terminated",
                                _trace_started_at,
                            )
                            try:
                                state.transition(TaskStatus.FAILED)
                            except ValueError:
                                state.status = TaskStatus.FAILED
                            self._run_failure_analysis(
                                react_trace,
                                "loop_terminated",
                                task_description=task_description,
                                task_id=state.task_id,
                            )
                            msg = (
                                cleaned
                                or "WARNING: a tool-call deadlock was detected; the task has been auto-terminated. Please restate your request."
                            )
                            self._last_exit_reason = "loop_terminated"
                            yield {"type": "text_delta", "content": msg}
                            yield {"type": "done"}
                            return

                        if intervention.should_rollback:
                            rollback_result = self._rollback(intervention.message)
                            if rollback_result:
                                working_messages, _ = rollback_result

                        if intervention.should_inject_prompt and intervention.prompt_injection:
                            working_messages.append(
                                {
                                    "role": "user",
                                    "content": intervention.prompt_injection,
                                }
                            )
                            if intervention.throttled_tool_names:
                                _blocked = set(intervention.throttled_tool_names)
                                tools = [t for t in tools if t.get("name") not in _blocked]
                                logger.info(
                                    f"[Supervisor] NUDGE: removed throttled tools {_blocked}, "
                                    f"{len(tools)} tools remain "
                                    f"(iter={_iteration}, pattern={intervention.pattern.value})"
                                )
                            else:
                                tools = []
                                logger.info(
                                    f"[Supervisor] NUDGE: tools stripped to force text response "
                                    f"(iter={_iteration}, pattern={intervention.pattern.value})"
                                )
                            max_no_tool_retries = 0

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
                react_trace,
                "max_iterations",
                task_description=task_description,
                task_id=state.task_id,
            )
            if max_iterations < 30:
                hint = (
                    f"\n\n(Maximum iteration count reached {max_iterations}."
                    f"The current MAX_ITERATIONS={max_iterations} is set too low;"
                    f"we recommend raising it to 100-300 in settings to support complex tasks)"
                )
            else:
                hint = "\n\n(Maximum iteration count reached)"
            self._last_exit_reason = "max_iterations"
            yield {"type": "text_delta", "content": hint}
            yield {"type": "done"}

        except Exception as e:
            logger.error(f"reason_stream error: {e}", exc_info=True)
            self._last_working_messages = working_messages
            self._save_react_trace(
                react_trace,
                conversation_id,
                session_type,
                f"error: {str(e)[:100]}",
                _trace_started_at,
            )
            yield {"type": "error", "message": str(e)[:500]}
            await broadcast_event("pet-status-update", {"status": "error"})
            yield {"type": "done"}

        finally:
            # Clear the per-conversation endpoint override
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
        Unified streaming interface: wrap reason_stream as a standardized async generator.

        All streaming events are consumed via async for; callers need not worry about internal loop details.
        Offers the same feature set as run() (retry, rollback, cancel, etc.) and additionally supports:
        - Token-budget warning injection
        - Observability metrics
        - Standardized event format

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

    # ==================== Reasoning-chain narration helpers ====================

    @staticmethod
    def _describe_tool_call(tool_name: str, tool_args: dict) -> str:
        """Generate a human-readable narrative description for a tool call."""
        args = tool_args if isinstance(tool_args, dict) else {}
        match tool_name:
            case "read_file":
                path = args.get("path") or args.get("file") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "file"
                return f"Reading {fname}..."
            case "write_file":
                path = args.get("path") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "file"
                return f"Writing {fname}..."
            case "edit_file":
                path = args.get("path") or ""
                fname = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "file"
                return f"Editing {fname}..."
            case "grep" | "search" | "ripgrep" | "search_files":
                pattern = str(args.get("pattern") or args.get("query") or "")[:50]
                return f'Searching "{pattern}"...'
            case "web_search":
                query = str(args.get("query") or "")[:50]
                return f'Searching the web for "{query}"...'
            case "execute_code" | "run_code" | "run_command":
                cmd = str(args.get("command") or args.get("code") or "")[:60]
                return f"Running command: {cmd}..." if cmd else "Running code..."
            case "browser_navigate":
                url = str(args.get("url") or "")[:60]
                return f"Visiting {url}..."
            case "browser_screenshot":
                return "Taking a page screenshot..."
            case "create_todo":
                summary = str(args.get("task_summary") or "")[:40]
                return f"Planning: {summary}..."
            case "update_todo_step":
                idx = args.get("step_index", "")
                status = args.get("status", "")
                return f"Updating plan step {idx} -> {status}"
            case "switch_persona":
                preset = args.get("preset_name", "")
                return f"Switching persona: {preset}..."
            case "get_persona_profile":
                return "Getting current persona configuration..."
            case "ask_user":
                q = str(args.get("question") or "")[:40]
                return f'Asking the user: "{q}"...'
            case "list_files" | "list_dir":
                path = str(args.get("path") or args.get("directory") or ".")
                return f"Listing directory {path}..."
            case "deliver_artifacts":
                return "Delivering files..."
            case _:
                params = ", ".join(f"{k}" for k in list(args.keys())[:3])
                return f"Calling {tool_name}({params})..."

    @staticmethod
    def _summarize_tool_result(tool_name: str, result_text: str) -> str:
        """Generate a brief narrative summary for a tool result."""
        if not result_text:
            return ""
        r = result_text.strip()
        is_error = any(
            m in r[:200]
            for m in ["❌", "⚠️ 工具执行错误", "错误类型:", "Tool error:", "⚠️ 策略拒绝:"]
        )
        if is_error:
            # Extract the first line of the error message
            first_line = r.split("\n")[0][:120]
            return f"Error: {first_line}"
        r_len = len(r)
        match tool_name:
            case "read_file":
                lines = r.count("\n") + 1
                return f"Read ({lines} lines, {r_len} characters)"
            case "grep" | "search" | "ripgrep" | "search_files":
                matches = r.count("\n") + 1 if r else 0
                return f"Found {matches} results" if matches > 0 else "No matches"
            case "web_search":
                return f"Search complete ({r_len} characters)"
            case "execute_code" | "run_code" | "run_command":
                lines = r.count("\n") + 1
                preview = r[:80].replace("\n", " ")
                return f"Execution complete: {preview}{'...' if r_len > 80 else ''}"
            case "write_file" | "edit_file":
                return (
                    "Write succeeded"
                    if "成功" in r or "ok" in r.lower() or r_len < 100
                    else f"Done ({r_len} characters)"
                )
            case "browser_screenshot":
                return "Screenshot captured"
            case "desktop_screenshot":
                return "Desktop screenshot saved"
            case "deliver_artifacts":
                try:
                    import json as _json

                    _d = _json.loads(r)
                    _n = len(_d.get("receipts", []))
                    return f"Delivered {_n} file(s)" if _n else ""
                except Exception:
                    return ""
            case "switch_persona":
                return "Switch complete"
            case _:
                if r_len < 100:
                    return r[:100]
                return f"Done ({r_len} characters)"

    # ==================== ReAct reasoning-chain persistence ====================

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
        Save the full ReAct reasoning chain to a file.

        Also cached in self._last_react_trace for agent_handler to read (reasoning-chain feature).
        If working_messages is provided, it is also cached for token-stat reads.

        Path: data/react_traces/{date}/trace_{conversation_id}_{timestamp}.json
        """
        # Reasoning chain: cache the trace for external reads (update even when empty to clear stale data)
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

            # Aggregate statistics
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

            # Clean up trace files older than 7 days
            self._cleanup_old_traces(Path("data/react_traces"), max_age_days=7)

        except Exception as e:
            logger.warning(f"[ReAct] Failed to save trace: {e}")

    def _cleanup_old_traces(self, base_dir: Path, max_age_days: int = 7) -> None:
        """Clean up trace date directories older than the given number of days"""
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

    # ==================== Cancellation-farewell utilities ====================

    def _reset_structural_cooldown_after_farewell(self):
        """Clear structural cooldown after a failed farewell call to avoid poisoning subsequent normal requests."""
        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            if not llm_client:
                return
            providers = getattr(llm_client, "_providers", {})
            for name, provider in providers.items():
                if not provider.is_healthy and provider.error_category == "structural":
                    provider.reset_cooldown()
                    logger.info(f"[CancelFarewell] Reset structural cooldown for endpoint {name}")
        except Exception as exc:
            logger.debug(f"[CancelFarewell] Failed to reset cooldown: {exc}")

    @staticmethod
    def _yield_missing_tool_results(working_messages: list[dict]) -> None:
        """Patch *working_messages* in-place so every ``tool_use`` block in the
        last assistant message has a matching ``tool_result`` in a subsequent
        user message.

        When an exception (cancel / timeout / model-switch) fires after the
        assistant emits ``tool_use`` blocks but before all tool executions
        complete, some ``tool_result`` entries will be absent.  The next LLM
        API call would then fail with HTTP 400.  This helper fills the gaps
        with synthetic ``[cancelled]`` results.
        """
        if not working_messages:
            return

        last_asst_idx: int | None = None
        for i in range(len(working_messages) - 1, -1, -1):
            msg = working_messages[i]
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_use" for b in content
                ):
                    last_asst_idx = i
                break

        if last_asst_idx is None:
            return

        tool_use_ids: set[str] = set()
        for block in working_messages[last_asst_idx].get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                tool_use_ids.add(block["id"])
        if not tool_use_ids:
            return

        answered_ids: set[str] = set()
        existing_result_msg: dict | None = None
        for msg in working_messages[last_asst_idx + 1 :]:
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tid = block.get("tool_use_id")
                            if tid:
                                answered_ids.add(tid)
                                if existing_result_msg is None:
                                    existing_result_msg = msg

        missing_ids = tool_use_ids - answered_ids
        if not missing_ids:
            return

        synthetic = [
            {
                "type": "tool_result",
                "tool_use_id": mid,
                "content": "[cancelled]",
                "is_error": True,
            }
            for mid in missing_ids
        ]

        if existing_result_msg is not None:
            existing_result_msg["content"].extend(synthetic)
        else:
            working_messages.append({"role": "user", "content": synthetic})

        logger.debug(
            "[ToolResultSafetyNet] Injected %d synthetic tool_result(s) for IDs: %s",
            len(synthetic),
            ", ".join(missing_ids),
        )

    @staticmethod
    def _sanitize_messages_for_farewell(messages: list[dict]) -> list[dict]:
        """
        Clean up working_messages so it can be safely sent to the LLM's farewell call.

        Problem: when an assistant message contains tool_calls but lacks the corresponding tool result,
        the LLM API returns 400: 'tool_calls must be followed by tool messages'.
        This can happen at the tail (last round incomplete on interrupt) or in the middle (residue from rollback).

        Strategy: scan everything, collect all tool_call_id and their tool_result matches,
        then remove any unclosed assistant(tool_calls) and orphan tool results.
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
                tc_ids = [tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")]
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
            result = [{"role": "user", "content": "(conversation context unavailable)"}]

        return result

    async def _cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        state: TaskState | None = None,
    ) -> str:
        """Non-streaming cancellation farewell: return default text immediately; dispatch LLM farewell asynchronously in the background."""
        self._yield_missing_tool_results(working_messages)

        cancel_reason = (state.cancel_reason if state else "") or "user requested stop"
        logger.info(
            f"[ReAct][CancelFarewell] entering farewell flow: cancel_reason={cancel_reason!r}, "
            f"model={current_model}, msg_count={len(working_messages)}"
        )

        default_farewell = "✅ Got it, current task stopped."

        asyncio.create_task(
            self._background_cancel_farewell(
                list(working_messages), system_prompt, current_model, cancel_reason
            )
        )

        return default_farewell

    # ==================== Cancellation farewell (streaming) ====================

    async def _stream_cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        state: TaskState | None = None,
    ):
        """Streaming cancellation farewell: return default text immediately; dispatch LLM farewell asynchronously in the background.

        Yields:
            {"type": "user_insert", ...} and {"type": "text_delta", ...} events
        """
        self._yield_missing_tool_results(working_messages)

        cancel_reason = (state.cancel_reason if state else "") or "user requested stop"
        logger.info(
            f"[ReAct-Stream][CancelFarewell] entering farewell flow: cancel_reason={cancel_reason!r}, "
            f"model={current_model}, msg_count={len(working_messages)}"
        )

        user_text = ""
        if cancel_reason.startswith("User sent stop command: "):
            user_text = cancel_reason[len("User sent stop command: ") :]
        elif cancel_reason.startswith("User sent skip command: "):
            user_text = cancel_reason[len("User sent skip command: ") :]
        if user_text:
            logger.info(f"[ReAct-Stream][CancelFarewell] relaying user command text: {user_text!r}")
            yield {"type": "user_insert", "content": user_text}

        default_farewell = "✅ Got it, current task stopped."
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
        """Run the LLM farewell call in the background and persist the result to the context (non-blocking for the user)."""
        try:
            self._yield_missing_tool_results(working_messages)
            cancel_msg = (
                f"[System notice] User sent stop command '{cancel_reason}';"
                "Please stop the current operation immediately and briefly tell the user it has stopped along with current progress (1-2 sentences)."
                "Do not call any tools."
            )
            farewell_messages = self._sanitize_messages_for_farewell(working_messages)
            farewell_messages.append({"role": "user", "content": cancel_msg})

            _tt = set_tracking_context(
                TokenTrackingContext(
                    operation_type="farewell",
                    channel="api",
                )
            )
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
                            f"[ReAct-Stream][BgFarewell] LLM farewell complete: "
                            f"{block.text.strip()[:100]}"
                        )
                        break
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("[ReAct-Stream][BgFarewell] LLM farewell timed out (5s)")
            except Exception as e:
                logger.warning(f"[ReAct-Stream][BgFarewell] LLM farewell failed: {e}")
                self._reset_structural_cooldown_after_farewell()
            finally:
                reset_tracking_context(_tt)
        except Exception as e:
            logger.warning(f"[ReAct-Stream][BgFarewell] background farewell exception: {e}")

    # ==================== Streaming reasoning ====================

    _HEARTBEAT_INTERVAL = 15  # seconds: heartbeat interval when there are no events

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
        """Streaming reasoning iterator: yields text/thinking deltas immediately; yields a Decision once the stream ends.

        Modeled after Claude Code's (claude.ts) for-await event-loop pattern:
        - Each incoming LLM token emits a high-level event via StreamAccumulator
        - Once the stream ends, a Decision is built from the accumulated state

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
            self._state.get_task_for_session(conversation_id) if conversation_id else None
        ) or self._state.current_task
        cancel_event = state.cancel_event if state else asyncio.Event()

        use_thinking = None
        if thinking_mode == "on":
            use_thinking = True
        elif thinking_mode == "off":
            use_thinking = False

        # on_before_llm_call: lets plugins inject context into the last user message
        # Inject on the user-message side (rather than system prompt) to protect the Anthropic prompt cache
        if self._plugin_hooks:
            try:
                hook_results = await self._plugin_hooks.dispatch(
                    "on_before_llm_call", messages=messages, tools=tools
                )
                extra_parts = [r for r in hook_results if isinstance(r, str) and r.strip()]
                if extra_parts and messages:
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "user":
                            content = messages[i].get("content", "")
                            if isinstance(content, str):
                                messages[i]["content"] = (
                                    content + "\n\n[Plugin Context]\n" + "\n".join(extra_parts)
                                )
                            break
            except Exception as _hook_err:
                logger.debug(f"on_before_llm_call hook error (ignored): {_hook_err}")

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
                    cancel_reason = state.cancel_reason if state else "user requested stop"
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

            # Stream ended -> build the Decision
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

    # ==================== Heartbeat keep-alive (non-streaming path) ====================

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
        Wrap _reason() and yield heartbeat events every HEARTBEAT_INTERVAL seconds while waiting for the LLM
        to prevent frontend SSE idle timeout.

        Also listens on cancel_event; when the user cancels, the LLM call is aborted immediately and UserCancelledError is raised.

        Yields:
            {"type": "heartbeat"} or {"type": "decision", "decision": Decision}
        """
        queue: asyncio.Queue = asyncio.Queue()

        # Get the cancel_event for the current session (avoiding cross-session cancellation)
        state = (
            self._state.get_task_for_session(conversation_id) if conversation_id else None
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
            """Listen on cancel_event; when fired, notify the main loop via the queue"""
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
                    cancel_reason = state.cancel_reason if state else "user requested stop"
                    raise UserCancelledError(
                        reason=cancel_reason,
                        source="llm_call_stream",
                    )
                elif typ == "error":
                    raise data  # propagate _reason's exception
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

    # ==================== Reasoning phase ====================

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
        Reasoning phase: call the LLM and return a structured Decision.
        """
        # Decide the use_thinking parameter based on thinking_mode
        use_thinking = None  # None = let Brain use default logic
        if thinking_mode == "on":
            use_thinking = True
        elif thinking_mode == "off":
            use_thinking = False
        # "auto" or None: use_thinking=None -> Brain uses its own default logic

        tracer = get_tracer()
        with tracer.llm_span(model=current_model) as span:
            _tt = set_tracking_context(
                TokenTrackingContext(
                    session_id=conversation_id or "",
                    operation_type="chat_react_iteration",
                    channel="api",
                    iteration=iteration,
                    agent_profile_id=agent_profile_id,
                )
            )
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

            # Record token usage
            if hasattr(response, "usage"):
                span.set_attribute("input_tokens", getattr(response.usage, "input_tokens", 0))
                span.set_attribute("output_tokens", getattr(response.usage, "output_tokens", 0))

            decision = self._parse_decision(response)
            span.set_attribute("decision_type", decision.type.value)
            span.set_attribute("tool_count", len(decision.tool_calls))
            return decision

    def _parse_decision(self, response: Any) -> Decision:
        """Parse the LLM response into a Decision"""
        tool_calls = []
        text_content = ""
        thinking_content = ""
        assistant_content = []

        for block in response.content:
            if block.type == "thinking":
                thinking_text = block.thinking if hasattr(block, "thinking") else str(block)
                thinking_content += (
                    thinking_text if isinstance(thinking_text, str) else str(thinking_text)
                )
                assistant_content.append(
                    {
                        "type": "thinking",
                        "thinking": thinking_text,
                    }
                )
            elif block.type == "text":
                raw_text = block.text
                # brain.py wraps OpenAI-compatible reasoning_content as <thinking> tags
                # embedded in a TextBlock; Qwen3/MiniMax may emit <think> tags.
                # Route them correctly into thinking_content to avoid raw tags leaking to the frontend,
                # while assistant_content keeps the raw text (message history needs the tags for next-round extraction).
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
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        # Defensive layer: if the provider layer failed to extract tool calls embedded in thinking content,
        # do one final check here (MiniMax-M2.5 is known to embed <minimax:tool_call> inside thinking blocks)
        if not tool_calls and thinking_content:
            try:
                from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls

                if has_text_tool_calls(thinking_content):
                    _, embedded_tool_calls = parse_text_tool_calls(thinking_content)
                    if embedded_tool_calls:
                        for tc in embedded_tool_calls:
                            tool_calls.append(
                                {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.input,
                                }
                            )
                            assistant_content.append(
                                {
                                    "type": "tool_use",
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.input,
                                }
                            )
                        logger.warning(
                            f"[_parse_decision] Recovered {len(embedded_tool_calls)} tool calls "
                            f"from thinking content (provider-level extraction missed)"
                        )
            except Exception as e:
                logger.debug(f"[_parse_decision] Thinking tool-call check failed: {e}")

        # Defensive layer: extract tool calls embedded in text_content (Python dot-style, etc.).
        # Some models (e.g. qwen3-coder, qwen3.5) do not use native function calling
        # and instead emit .web_search(query="...")-style calls in text.
        if not tool_calls and text_content:
            try:
                from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls

                if has_text_tool_calls(text_content):
                    _clean, embedded_tool_calls = parse_text_tool_calls(text_content)
                    if embedded_tool_calls:
                        text_content = _clean
                        for tc in embedded_tool_calls:
                            tool_calls.append(
                                {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.input,
                                }
                            )
                            assistant_content.append(
                                {
                                    "type": "tool_use",
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.input,
                                }
                            )
                        logger.warning(
                            f"[_parse_decision] Recovered {len(embedded_tool_calls)} tool calls "
                            f"from text content: {[tc.name for tc in embedded_tool_calls]}"
                        )
            except Exception as e:
                logger.debug(f"[_parse_decision] Text tool-call check failed: {e}")

        # Defensive layer: strip bare tool names at the end of text_content.
        # Some models emit junk like 'user input\nbrowser_open' in content;
        # such bare tool names are neither valid tool calls (no args/format) nor meaningful replies.
        # Only triggers when text_content is short (<200 chars) to avoid damaging legitimate long text.
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
        """When the LLM repeatedly fails to return visible text, build a fallback summary from the tool-execution records."""
        parts: list[str] = []

        if delivery_receipts:
            for r in delivery_receipts:
                desc = r.get("description") or r.get("summary") or r.get("title") or ""
                if desc:
                    parts.append(f"• {desc}")
            if parts:
                return "Completed the following operations:\n" + "\n".join(parts)

        if executed_tool_names:
            unique = list(dict.fromkeys(executed_tool_names))
            tool_summary = "、".join(unique[:10])
            if len(unique) > 10:
                tool_summary += f", {len(unique)} items in total"
            return f"Task execution finished (tools used: {tool_summary}), but the model produced no text summary. Please re-ask if you need details."

        return None

    # ==================== Final-answer handling ====================

    async def _handle_final_answer(
        self,
        *,
        decision: Decision,
        working_messages: list[dict],
        original_messages: list[dict],
        tools_executed_in_task: bool,
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
        all_tool_results: list[dict] | None = None,
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
        Handle a plain-text response (no tool calls).

        Returns:
            str: final answer
            tuple: (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries) -- need to continue looping
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
                    tool_results=all_tool_results,
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
                        working_messages.append(
                            {
                                "role": "assistant",
                                "content": [{"type": "text", "text": decision.text_content}],
                                "reasoning_content": decision.thinking_content or None,
                            }
                        )
                        working_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[System] WARNING: for multiple turns in a row you have only described what you will do,"
                                    "but never actually called any tool to execute it. The system log confirms that no files were produced."
                                    "A textual description is not the same as actual execution."
                                    "Call a tool like run_shell or write_file immediately to perform the actual operation,"
                                    "and stop outputting any more descriptive text."
                                ),
                            }
                        )
                        return (
                            working_messages,
                            no_tool_call_count,
                            verify_incomplete_count,
                            no_confirmation_text_count,
                            max_no_tool_retries,
                        )
                    self._last_exit_reason = "verify_incomplete"
                    return cleaned_text

                # Continue looping
                working_messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": decision.text_content}],
                        "reasoning_content": decision.thinking_content or None,
                    }
                )

                if has_todo_pending:
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System notice] The current Plan still has pending steps."
                                "Continue executing the next pending step immediately."
                            ),
                        }
                    )
                elif is_in_progress_promise:
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System] WARNING: your previous reply only described operations you would perform,"
                                "but the system log confirms that no tool was called (tool_calls=0)."
                                "A textual description is not the same as actual execution."
                                "Invoke the required tools immediately to finish the task; do not just output a text description."
                            ),
                        }
                    )
                else:
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System notice] Based on re-verification, the user's request may still have unfinished parts."
                                "If there are indeed remaining steps, continue invoking tools;"
                                "if everything is done, give the user a summary reply that includes the results."
                            ),
                        }
                    )
                return (
                    working_messages,
                    no_tool_call_count,
                    verify_incomplete_count,
                    no_confirmation_text_count,
                    max_no_tool_retries,
                )
            else:
                # No visible text
                no_confirmation_text_count += 1
                if no_confirmation_text_count <= max_confirmation_text_retries:
                    if no_confirmation_text_count == 1:
                        retry_prompt = (
                            "[System] You have executed tools, but you just produced no user-visible text acknowledgement."
                            "Produce a final reply based on the tool_result evidence already produced."
                        )
                    else:
                        retry_prompt = (
                            "[System] WARNING: you have failed to produce visible text multiple times in a row."
                            "Produce a one-or-two-sentence summary of what you accomplished immediately; do not call any tools, do not output thinking."
                        )
                    working_messages.append(
                        {
                            "role": "user",
                            "content": retry_prompt,
                        }
                    )
                    return (
                        working_messages,
                        no_tool_call_count,
                        verify_incomplete_count,
                        no_confirmation_text_count,
                        max_no_tool_retries,
                    )

                # All retries exhausted; try building a fallback summary from the tool-execution records
                fallback = self._build_fallback_summary(executed_tool_names, delivery_receipts)
                if fallback:
                    logger.warning(
                        "[ForceToolCall] LLM returned empty confirmation; using fallback summary from tool history"
                    )
                    return fallback

                # When the thinking content is non-empty, extract usable info from it
                if decision.thinking_content:
                    thinking_text = decision.thinking_content.strip()
                    if len(thinking_text) > 20:
                        logger.warning(
                            "[ForceToolCall] LLM returned empty visible text but has thinking content; "
                            "extracting summary from thinking"
                        )
                        preview = thinking_text[:500]
                        return f"(The following is an internal reasoning summary from the model; the original reply produced no visible text)\n\n{preview}"

                return (
                    "WARNING: the LLM returned unexpectedly: tools ran but repeatedly produced no visible text; the task has been aborted."
                    "Retry, or switch to a more stable endpoint/model before continuing."
                )

        # No 'substantive' tool was executed -- parse the intent-declaration marker
        intent, stripped_text = parse_intent_tag(decision.text_content or "")
        logger.info(
            f"[IntentTag] intent={intent or 'NONE'}, "
            f"has_tool_calls=False, tools_executed_in_task=False, "
            f'text_preview="{(stripped_text or "")[:80].replace(chr(10), " ")}"'
        )

        # Management-type tools (e.g. create_todo) have executed and a text reply exists -> the task is done;
        # do not ForceToolCall-retry, or 'create plan' would turn into 'execute plan'.
        if (
            executed_tool_names
            and all(t in _ADMIN_TOOL_NAMES for t in executed_tool_names)
            and stripped_text
            and len(stripped_text.strip()) > 10
        ):
            logger.info(
                "[IntentTag] Admin-only tools executed with substantial reply — "
                "accepting as completed (skip ForceToolCall)"
            )
            return clean_llm_response(stripped_text)

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
            working_messages.append(
                {
                    "role": "user",
                    "content": "[System] Your previous reply was empty. Please answer the user's question directly.",
                }
            )
            return (
                working_messages,
                no_tool_call_count,
                verify_incomplete_count,
                no_confirmation_text_count,
                max_no_tool_retries,
            )

        if intent == "REPLY" and stripped_text and len(stripped_text.strip()) > 10:
            logger.info(
                "[IntentTag] REPLY intent with substantial text, "
                "accepting as valid response (no ForceToolCall)"
            )
            return clean_llm_response(stripped_text)

        # No intent tag but text is long enough to be a genuine analysis / knowledge
        # response.  Accept as implicit REPLY **only if** the text does not look like
        # an action-claim hallucination (e.g. "已帮你保存/删除/发送…" without any
        # actual tool calls).
        _IMPLICIT_REPLY_THRESHOLD = 200
        _ACTION_CLAIM_RE = _get_action_claim_re()
        _txt = (stripped_text or "").strip()
        if (
            intent is None
            and _txt
            and len(_txt) > _IMPLICIT_REPLY_THRESHOLD
            and not _ACTION_CLAIM_RE.search(_txt)
        ):
            logger.info(
                f"[IntentTag] No intent tag but substantial text "
                f"({len(_txt)} chars > {_IMPLICIT_REPLY_THRESHOLD}), "
                f"no action-claim detected — accepting as implicit REPLY"
            )
            return clean_llm_response(stripped_text)

        no_tool_call_count += 1

        if no_tool_call_count <= max_no_tool_retries:
            if stripped_text:
                working_messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": stripped_text}],
                        "reasoning_content": decision.thinking_content or None,
                    }
                )
            if intent == "REPLY":
                logger.warning(
                    f"[IntentTag] REPLY intent but text too short — "
                    f"ForceToolCall retry ({no_tool_call_count}/{max_no_tool_retries})"
                )
                retry_msg = "[System] Your reply is too short; please provide a more detailed answer."
            elif intent == "ACTION":
                logger.warning(
                    "[IntentTag] ACTION intent declared but no tool calls — "
                    "hallucination detected, forcing retry"
                )
                retry_msg = (
                    "[System] WARNING: you declared an [ACTION] intent but did not call any tool."
                    "Invoke the required tools immediately to fulfill the user's request; do not merely describe what you would do."
                )
            else:
                logger.warning(
                    f"[IntentTag] No intent tag, short text with action claims, tool_calls=0 — "
                    f"ForceToolCall retry "
                    f"({no_tool_call_count}/{max_no_tool_retries})"
                )
                retry_msg = (
                    "[System] WARNING: your previous reply did not invoke any tool (system log confirms tool_calls=0)."
                    "A textual description is not the same as actual execution. Invoke tools immediately to fulfill the user's request."
                )
            working_messages.append({"role": "user", "content": retry_msg})
            return (
                working_messages,
                no_tool_call_count,
                verify_incomplete_count,
                no_confirmation_text_count,
                max_no_tool_retries,
            )

        # Follow-up attempts exhausted
        cleaned_text = clean_llm_response(stripped_text)
        return cleaned_text or (
            "WARNING: the LLM returned unexpectedly: no usable output produced. Task aborted. Retry, or switch endpoints/models and try again."
        )

    # ==================== Loop detection ====================

    # ==================== Model switching ====================

    def _check_model_switch(
        self,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> tuple[str, list[dict]] | None:
        """Check whether a model switch is needed. Returns (new_model, new_messages) or None."""
        if not task_monitor or not task_monitor.should_switch_model:
            return None

        new_model = task_monitor.fallback_model
        self._switch_llm_endpoint(new_model, reason="task_monitor timeout fallback")
        task_monitor.switch_model(
            new_model,
            "switch after task timeout",
            reset_context=True,
        )

        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            current = llm_client.get_current_model() if llm_client else None
            new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append(
            {
                "role": "user",
                "content": (
                    "[System notice] A model switch occurred: previous tool_use/tool_result history has been cleared."
                    "Please handle the user's request from scratch."
                ),
            }
        )

        # Note: _check_model_switch does not perform state transitions because it doesn't use continue;
        # after it runs, the main loop naturally moves into the REASONING transition logic.
        state.reset_for_model_switch()
        return new_model, new_messages

    # Maximum model-switch count (prevents deadlock)
    MAX_MODEL_SWITCHES = 2

    # Global retry cap across model switches: once reached, terminate immediately and inform the user
    MAX_TOTAL_LLM_RETRIES = 3

    @staticmethod
    def _strip_heavy_content(messages: list[dict]) -> tuple[list[dict], bool]:
        """Strip heavy multimedia content (video / large data URLs) from messages, replacing with a textual description.

        Returns:
            (processed message list, whether anything was stripped)
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
                        new_parts.append(
                            {
                                "type": "text",
                                "text": "[Video content removed: the video file is too large, exceeding the API data-uri limit. Please send a smaller video.]",
                            }
                        )
                        stripped = True
                        continue

                elif part_type == "video":
                    source = part.get("source", {})
                    data = source.get("data", "")
                    if len(data) > DATA_URL_SIZE_THRESHOLD:
                        new_parts.append(
                            {
                                "type": "text",
                                "text": "[Video content removed: the video file is too large, exceeding the API data-uri limit. Please send a smaller video.]",
                            }
                        )
                        stripped = True
                        continue

                elif part_type == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if len(url) > DATA_URL_SIZE_THRESHOLD:
                        new_parts.append(
                            {
                                "type": "text",
                                "text": "[Image content removed: file too large, exceeds API limit.]",
                            }
                        )
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
            "[Tool-returned content removed: content triggered platform safety review and cannot be sent to the model."
            "Please ignore this tool's result and answer the user based on existing information.]"
        )
        stripped = False
        result = list(messages)

        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            content = msg.get("content")
            if msg.get("role") != "user" or not isinstance(content, list):
                continue

            has_tool_results = any(
                isinstance(item, dict) and item.get("type") == "tool_result" for item in content
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
        """Truncate oversized text messages to prevent context overflow.

        When a single message's text content exceeds the estimated max_single_tokens,
        keep halves at the start and end, truncate the middle, and insert a notice.
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
                        + "\n\n[... Content too long; truncated to fit the model's context window ...]\n\n"
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
                        text = str(part.get("text", part.get("content", "")))
                    elif isinstance(part, str):
                        text = part

                    if text:
                        est = ContextManager.static_estimate_tokens(text)
                        if est > max_single_tokens:
                            half = target_chars // 2
                            text = text[:half] + "\n\n[... Content too long; truncated ...]\n\n" + text[-half:]
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
        """Force-truncate conversation history to fit the context window.

        Keep the system prompt (first message) and the most recent messages, dropping from the middle
        of the earlier messages until the estimated token count drops below target_tokens.
        Returns True when truncation actually occurred.
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
            msg_tokens = ContextManager.static_estimate_tokens(str(msg.get("content", "")))
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
                f"[Notice] Due to the model's context-window limit, {dropped} earlier"
                " conversation messages have been dropped automatically. Continue answering based on the remaining context."
            ),
        }

        new_messages = system_msgs + added_back + [truncation_notice] + recent
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

    async def _handle_llm_error(
        self,
        error: Exception,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> str | tuple | None:
        """
        Handle LLM-call errors.

        Returns:
            "retry" - retry
            (new_model, new_messages) - switch model
            None - re-raise
        """
        from ..llm.types import AllEndpointsFailedError

        if not task_monitor:
            return None

        # -- global retry counter (across model switches) --
        # Regardless of error type, terminate and inform the user once total retries reach the cap.
        total_retries = getattr(state, "_total_llm_retries", 0) + 1
        state._total_llm_retries = total_retries

        if total_retries > self.MAX_TOTAL_LLM_RETRIES:
            logger.error(
                f"[ReAct] Global retry limit reached ({total_retries}/{self.MAX_TOTAL_LLM_RETRIES}). "
                f"Aborting and notifying user. Last error: {str(error)[:200]}"
            )
            return None

        # -- Plan A+B: fast circuit-break on structural errors --
        if isinstance(error, AllEndpointsFailedError) and error.is_structural:
            already_stripped = getattr(state, "_structural_content_stripped", False)

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

                # Plan C: context overflow -- when stripping media doesn't help, try truncating oversized text
                error_lower = str(error).lower()
                _ctx_overflow_patterns = [
                    "context length",
                    "context size",
                    "too many tokens",
                    "token limit",
                    "context_length_exceeded",
                    "context window",
                    "max_tokens",
                    "input too long",
                    "payload too large",
                    "request entity too large",
                    "larger than allowed",
                    "(413)",
                ]
                is_ctx_overflow = any(p in error_lower for p in _ctx_overflow_patterns) or (
                    "maximum" in error_lower and "length" in error_lower
                )
                if not is_ctx_overflow:
                    is_ctx_overflow = "exceeded" in error_lower and (
                        "context" in error_lower or "token" in error_lower
                    )
                if not is_ctx_overflow:
                    is_ctx_overflow = "payload" in error_lower and "larger" in error_lower
                if is_ctx_overflow:
                    # Layer 2: Reactive compact (the third tier of the three-layer compression strategy)
                    try:
                        compacted = await self._context_manager.reactive_compact(
                            working_messages,
                            system_prompt=getattr(state, "_system_prompt", ""),
                        )
                        if compacted is not working_messages:
                            working_messages.clear()
                            working_messages.extend(compacted)
                        llm_client = getattr(self._brain, "_llm_client", None)
                        if llm_client:
                            llm_client.reset_all_cooldowns(include_structural=True)
                        return "retry"
                    except Exception:
                        pass

                    trunc_msgs, did_trunc = self._truncate_oversized_messages(working_messages)
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
                            llm_client.reset_all_cooldowns(include_structural=True)
                        return "retry"

                    # Plan C2: single-message truncation didn't help (overflow is accumulated across many small messages)
                    # Force hard truncation at 50% of the current context budget
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
                            llm_client = getattr(self._brain, "_llm_client", None)
                            if llm_client:
                                llm_client.reset_all_cooldowns(include_structural=True)
                            return "retry"

                # Plan D: content safety review -- tool result triggered platform content filtering
                _content_safety_patterns = [
                    "data_inspection",
                    "inappropriate content",
                ]
                is_content_safety = any(p in error_lower for p in _content_safety_patterns)
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
                            llm_client.reset_all_cooldowns(include_structural=True)
                        return "retry"

            logger.error(
                f"[ReAct] Structural API error, cannot recover "
                f"(content already stripped={already_stripped}). "
                f"Aborting. Error: {str(error)[:200]}"
            )
            return None

        # -- regular errors: TaskMonitor retry chain --
        should_retry = task_monitor.record_error(str(error))

        if should_retry:
            logger.info(
                f"[LLM] Will retry (attempt {task_monitor.retry_count}, "
                f"global {total_retries}/{self.MAX_TOTAL_LLM_RETRIES})"
            )
            return "retry"

        # --- circuit-break: terminate once max model-switches is exceeded ---
        switch_count = getattr(state, "_model_switch_count", 0) + 1
        state._model_switch_count = switch_count
        if switch_count > self.MAX_MODEL_SWITCHES:
            logger.error(
                f"[ReAct] Exceeded max model switches ({self.MAX_MODEL_SWITCHES}), "
                f"aborting. Last error: {str(error)[:200]}"
            )
            return None

        # --- check whether a fallback model is available ---
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

        # Reset the target endpoint's cooldown before switching: all endpoints just failed,
        # so the fallback endpoint is inevitably in cooldown; without a reset, switch_model would refuse the switch
        llm_client = getattr(self._brain, "_llm_client", None)
        if llm_client and resolved:
            llm_client.reset_endpoint_cooldown(resolved)

        switched = self._switch_llm_endpoint(new_model, reason=f"LLM error fallback: {error}")
        if not switched:
            logger.warning(
                f"[ModelSwitch] _switch_llm_endpoint failed for '{new_model}', "
                f"proceeding with model switch anyway (endpoint selection will use fallback strategy)"
            )
        task_monitor.switch_model(new_model, "switch after LLM call failed", reset_context=True)

        try:
            if llm_client:
                current = llm_client.get_current_model()
                new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append(
            {
                "role": "user",
                "content": ("[System notice] A model switch occurred: previous history has been cleared. Please handle the user's request from scratch."),
            }
        )

        state.transition(TaskStatus.MODEL_SWITCHING)
        state.reset_for_model_switch()
        return new_model, new_messages

    def _switch_llm_endpoint(self, model_or_endpoint: str, reason: str = "") -> bool:
        """Perform the model switch"""
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
        """Resolve the endpoint name"""
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

    # ==================== Helper methods ====================

    @staticmethod
    def _is_human_user_message(msg: dict) -> bool:
        """Determine whether a message is from a human user (excludes tool_result)"""
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            part_types = {
                part.get("type") for part in content if isinstance(part, dict) and part.get("type")
            }
            return "tool_result" not in part_types
        return False

    @staticmethod
    def _is_in_progress_promise(text: str) -> bool:
        """Detect whether a response is an 'in-progress promise' -- the model claims it is working but did not actually call any tool.

        Typical signs: the response is short, contains progress phrases like "generating" / "one moment",
        but has no actual execution results or complete content.
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
        """Detect whether a model reply is a confirmation-style reply (asking the user to confirm before proceeding).

        Typical scenarios: confirming the recognized text after speech-to-text, or restating the execution plan and waiting for confirmation.
        Such replies should not trigger ForceToolCall retries -- the model is deliberately soliciting user input.
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
    def _effective_force_retries(base_retries: int, conversation_id: str | None) -> int:
        """Compute the effective ForceToolCall retry count.

        No longer bumped automatically when a plan is active -- Plan progression is driven by Supervisor self-checks and
        todo_reminder; ForceToolCall respects only the configured value.
        """
        return max(0, int(base_retries))

    @staticmethod
    def _has_active_todo_pending(conversation_id: str | None) -> bool:
        """Check whether there is an active Plan with pending steps"""
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
