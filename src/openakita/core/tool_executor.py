"""
Tool execution engine.

Tool execution logic extracted from agent.py; responsible for:
- Single tool execution (execute_tool)
- Batch tool execution (execute_batch)
- Parallel/serial strategy
- Handler mutex management (browser/desktop/mcp)
- Structured error handling (ToolError)
- Plan mode checks
- Generic truncation guard (auto-truncate large results + overflow file)
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .permission import PermissionDecision

from ..config import settings
from ..tools.errors import ToolError, classify_error
from ..tools.handlers import SystemHandlerRegistry
from ..tools.input_normalizer import normalize_tool_input
from ..tracing.tracer import get_tracer
from .agent_state import TaskState

logger = logging.getLogger(__name__)


class ToolSkipped(Exception):
    """User proactively skipped the current tool execution (not an error, only interrupts a single step)."""

    def __init__(self, reason: str = "User requested skip"):
        self.reason = reason
        super().__init__(reason)


# ========== Generic truncation guard constants ==========
MAX_TOOL_RESULT_CHARS = 16000  # Generic truncation threshold (~8000 tokens)
OVERFLOW_MARKER = "[OUTPUT_TRUNCATED]"  # Truncation marker; content already containing this is not truncated again
_OVERFLOW_DIR = Path("data/tool_overflow")
_OVERFLOW_MAX_FILES = 50  # Maximum number of files kept in the overflow directory


def save_overflow(tool_name: str, content: str) -> str:
    """Save large output to an overflow file and return the file path.

    Shared by tool_executor and individual handlers.
    """
    try:
        _OVERFLOW_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{tool_name}_{ts}.txt"
        filepath = _OVERFLOW_DIR / filename
        filepath.write_text(content, encoding="utf-8")
        _cleanup_overflow_files(_OVERFLOW_DIR, _OVERFLOW_MAX_FILES)
        logger.info(f"[Overflow] Saved {len(content)} chars to {filepath}")
        return str(filepath)
    except Exception as exc:
        logger.warning(f"[Overflow] Failed to save overflow file: {exc}")
        return "(failed to save overflow file)"


def smart_truncate(
    content: str,
    limit: int,
    *,
    label: str = "content",
    save_full: bool = True,
    head_ratio: float = 0.65,
) -> tuple[str, bool]:
    """Smart truncation: keep head and tail + overflow file + truncation marker.

    Args:
        content: Original text.
        limit: Maximum number of characters to keep.
        label: Prefix for the overflow file name.
        save_full: Whether to save the full content to an overflow file (set False for validation-style calls).
        head_ratio: Proportion of characters to keep at the head.

    Returns:
        (result_text, was_truncated)
    """
    if not content or len(content) <= limit:
        return content, False

    head = int(limit * head_ratio)
    tail = limit - head - 120
    if tail < 0:
        tail = 0

    overflow_ref = ""
    if save_full:
        path = save_overflow(label, content)
        overflow_ref = f", full content: {path}, use read_file to view"

    marker = f"\n[truncated, original {len(content)} chars{overflow_ref}]\n"

    if tail > 0:
        return content[:head] + marker + content[-tail:], True
    return content[:head] + marker, True


def _cleanup_overflow_files(directory: Path, max_files: int) -> None:
    """Clean up the overflow directory, keeping only the most recent max_files files."""
    try:
        files = sorted(directory.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        if len(files) > max_files:
            for f in files[: len(files) - max_files]:
                f.unlink(missing_ok=True)
    except Exception:
        pass


class ToolExecutor:
    """
    Tool execution engine.

    Manages serial/parallel tool execution, handler mutexes,
    structured error handling, and Plan mode checks.
    """

    _TOOL_ALIASES: dict[str, str] = {
        "create_todo_plan": "create_todo",
        "create-todo": "create_todo",
        "get-todo-status": "get_todo_status",
        "update-todo-step": "update_todo_step",
        "complete-todo": "complete_todo",
        "exit-plan-mode": "exit_plan_mode",
        "create-plan-file": "create_plan_file",
        "schedule-task": "schedule_task",
        "schedule_task_create": "schedule_task",
        "list-scheduled-tasks": "list_scheduled_tasks",
    }

    def __init__(
        self,
        handler_registry: SystemHandlerRegistry,
        max_parallel: int = 1,
    ) -> None:
        self._handler_registry = handler_registry
        self._agent_ref: Any = None  # set by Agent after construction
        self._plugin_hooks: Any = None  # HookRegistry, set by Agent after construction

        # Parallelism control
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._max_parallel = max_parallel

        # Mutex locks for stateful tools (browser/desktop/mcp, etc. cannot run concurrently)
        self._handler_locks: dict[str, asyncio.Lock] = {}
        for handler_name in ("browser", "desktop", "mcp"):
            self._handler_locks[handler_name] = asyncio.Lock()

        # Security: pending confirmations — tool calls that returned CONFIRM
        # and are awaiting user decision via ask_user.
        # When the agent retries after ask_user, we auto-mark as confirmed.
        self._pending_confirms: dict[
            str, dict
        ] = {}  # cache_key → {tool_name, params, metadata, ts}

        # Current mode for permission checks (set by ReasoningEngine before tool loop)
        self._current_mode: str = "agent"

        # Extra permission rules injected by AgentFactory (profile rules)
        self._extra_permission_rules: list | None = None

    # Concurrency-safe tools: their read-only operations can execute in parallel
    _CONCURRENCY_SAFE_TOOLS: set[str] = {
        "read_file",
        "list_files",
        "search_files",
        "web_fetch",
        "get_time",
        "read_resource",
        "list_resources",
    }

    # Hard timeout (seconds) for long-running tools to prevent a stuck tool from dragging down the whole agent loop.
    # A value of 0 means no hard timeout (tool-owned progress monitoring handles it, e.g. Orchestrator idle-timeout).
    _TOOL_HARD_TIMEOUT: int = 120

    _LONG_RUNNING_TOOLS: dict[str, int] = {
        "org_request_meeting": 600,
        "org_broadcast": 300,
        "delegate_to_agent": 0,
        "delegate_parallel": 0,
        "spawn_agent": 0,
        "browser_navigate": 300,
        "browser_use": 300,
        "run_shell": 300,
    }

    def get_handler_name(self, tool_name: str) -> str | None:
        """Get the handler name for a given tool."""
        try:
            return self._handler_registry.get_handler_name_for_tool(tool_name)
        except Exception:
            return None

    def _canonicalize_tool_name(self, tool_name: str) -> str:
        canonical = self._TOOL_ALIASES.get(tool_name)
        if canonical is None and "-" in tool_name:
            canonical = self._TOOL_ALIASES.get(tool_name.replace("-", "_"))
        if canonical:
            logger.info(f"[ToolExecutor] Alias corrected: '{tool_name}' -> '{canonical}'")
            return canonical
        return tool_name

    def canonicalize_tool_name(self, tool_name: str) -> str:
        return self._canonicalize_tool_name(tool_name)

    def _suggest_similar_tool(self, tool_name: str) -> str:
        """Generate an error message with similar-name suggestions for an unknown tool."""
        all_tools = self._handler_registry.list_tools()
        candidates: list[tuple[float, str]] = []
        name_lower = tool_name.lower()
        for t in all_tools:
            t_lower = t.lower()
            # substring match scores highest
            if name_lower in t_lower or t_lower in name_lower:
                candidates.append((0.9, t))
                continue
            # token overlap (split on _ and compare)
            tokens_a = set(name_lower.split("_"))
            tokens_b = set(t_lower.split("_"))
            overlap = tokens_a & tokens_b
            if overlap:
                score = len(overlap) / max(len(tokens_a | tokens_b), 1)
                candidates.append((score, t))
        candidates.sort(key=lambda x: -x[0])
        top = [name for _, name in candidates[:5]]
        msg = f"❌ Unknown tool: {tool_name}."
        if top:
            msg += f" Did you mean: {', '.join(top)}?"
        else:
            msg += " Please check that the tool name is correct."
        return msg

    def _is_concurrency_safe(self, tool_name: str, tool_input: dict) -> bool:
        """Determine whether a tool is concurrency-safe for the given input.

        Asks the handler-level callback first (which can make fine-grained decisions based on tool_input);
        falls back to the static ``_CONCURRENCY_SAFE_TOOLS`` set when the callback returns None.
        """
        override = self._handler_registry.check_concurrency_safe(tool_name, tool_input)
        if override is not None:
            return override
        if tool_name in self._CONCURRENCY_SAFE_TOOLS:
            return True
        handler_name = self.get_handler_name(tool_name)
        if handler_name in self._handler_locks:
            return False
        return False

    def _partition_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Partition tool calls into concurrency-safe batches and serial batches.

        Consecutive concurrency-safe tools are batched and run in parallel; non-safe tools run serially on their own.
        Each tool_call is tagged with _idx for order restoration.
        """
        batches: list[dict] = []
        current_safe: list[dict] = []

        for i, tc in enumerate(tool_calls):
            tc_with_idx = {**tc, "_idx": i}
            name = tc.get("name", "")
            inp = tc.get("input", {})

            if self._is_concurrency_safe(name, inp):
                current_safe.append(tc_with_idx)
            else:
                if current_safe:
                    batches.append({"calls": current_safe, "concurrent": True})
                    current_safe = []
                batches.append({"calls": [tc_with_idx], "concurrent": False})

        if current_safe:
            batches.append({"calls": current_safe, "concurrent": True})

        return batches

    async def _execute_with_cancel(
        self,
        coro,
        state: TaskState | None,
        tool_name: str,
    ) -> str:
        """
        Run the tool coroutine while racing against cancel_event / skip_event / hard timeout.

        - cancel_event fires → return an interruption error (terminates the whole task).
        - skip_event fires → raise ToolSkipped (skips only the current tool).
        - Hard timeout → return a timeout error.
        - hard_timeout=0 means no hard timeout is set.
        """
        tool_task = asyncio.ensure_future(coro)

        cancel_future: asyncio.Future | None = None
        if state and hasattr(state, "cancel_event") and state.cancel_event:
            cancel_future = asyncio.ensure_future(state.cancel_event.wait())

        skip_future: asyncio.Future | None = None
        if state and hasattr(state, "skip_event") and state.skip_event:
            skip_future = asyncio.ensure_future(state.skip_event.wait())

        hard_timeout = self._LONG_RUNNING_TOOLS.get(tool_name, self._TOOL_HARD_TIMEOUT)

        timeout_task: asyncio.Future | None = None
        if hard_timeout > 0:
            timeout_task = asyncio.ensure_future(asyncio.sleep(hard_timeout))

        wait_set: set[asyncio.Future] = {tool_task}
        if timeout_task is not None:
            wait_set.add(timeout_task)
        if cancel_future:
            wait_set.add(cancel_future)
        if skip_future:
            wait_set.add(skip_future)

        try:
            done, pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if tool_task in done:
                return tool_task.result()

            # skip_event checked before cancel (skip only interrupts the current step, not the task)
            if skip_future and skip_future in done:
                tool_task.cancel()
                try:
                    await tool_task
                except (asyncio.CancelledError, Exception):
                    pass
                skip_reason = getattr(state, "skip_reason", "") or "User requested skip"
                if state and hasattr(state, "clear_skip"):
                    state.clear_skip()
                logger.info(f"[ToolExecutor] Tool '{tool_name}' skipped: {skip_reason}")
                raise ToolSkipped(skip_reason)

            reason = ""
            if cancel_future and cancel_future in done:
                reason = "User requested task cancellation"
                logger.warning(f"[ToolExecutor] Tool '{tool_name}' cancelled by user")
            else:
                reason = f"Tool execution timed out ({hard_timeout}s)"
                logger.error(f"[ToolExecutor] Tool '{tool_name}' timed out after {hard_timeout}s")

            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass

            return f"⚠️ Tool execution interrupted: {reason}. Tool '{tool_name}' has been stopped."

        finally:
            for t in [tool_task, timeout_task]:
                if t is not None and not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            for f in [cancel_future, skip_future]:
                if f and not f.done():
                    f.cancel()
                    try:
                        await f
                    except (asyncio.CancelledError, Exception):
                        pass

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        session_id: str | None = None,
    ) -> str:
        """
        Execute a single tool call.

        Executes via handler_registry by preference and, on exception,
        returns a structured ToolError.

        Args:
            tool_name: Name of the tool.
            tool_input: Tool input parameters.
            session_id: Current session ID (used for Plan checks).

        Returns:
            Tool execution result string.
        """
        tool_name = self._canonicalize_tool_name(tool_name)
        if isinstance(tool_input, dict):
            tool_input = normalize_tool_input(tool_name, tool_input)

        todo_block = self._check_todo_required(tool_name, session_id)
        if todo_block:
            return todo_block

        perm_block = self._check_permission_deny_msg(tool_name, tool_input)
        if perm_block:
            return perm_block

        return await self._execute_tool_impl(tool_name, tool_input)

    async def _dispatch_hook(self, hook_name: str, **kwargs) -> None:
        """Fire a plugin hook if a HookRegistry is attached. Never raises."""
        hooks = self._plugin_hooks
        if hooks is None:
            return
        try:
            await hooks.dispatch(hook_name, **kwargs)
        except Exception as e:
            logger.debug(f"[ToolExecutor] {hook_name} hook error (ignored): {e}")

    async def _execute_tool_impl(
        self,
        tool_name: str,
        tool_input: dict,
    ) -> str:
        """Execute a tool after todo / permission gates have been handled."""
        logger.info(f"Executing tool: {tool_name} with {tool_input}")

        # ★ Intercept tool calls with JSON parse failures (arguments truncated by the API).
        # convert_tool_calls_from_openai() injects __parse_error__ when JSON parsing fails.
        from ..llm.converters.tools import PARSE_ERROR_KEY

        if isinstance(tool_input, dict) and PARSE_ERROR_KEY in tool_input:
            err_msg = tool_input[PARSE_ERROR_KEY]
            logger.warning(
                f"[ToolExecutor] Skipping tool '{tool_name}' due to parse error: {err_msg[:200]}"
            )
            return err_msg

        await self._dispatch_hook(
            "on_before_tool_use", tool_name=tool_name, tool_input=tool_input
        )

        # Import the log buffer
        from ..logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()
        logs_before = log_buffer.get_logs(count=500)
        logs_before_count = len(logs_before)

        tracer = get_tracer()
        with tracer.tool_span(tool_name=tool_name, input_data=tool_input) as span:
            try:
                # Execute via handler_registry
                if self._handler_registry.has_tool(tool_name):
                    result = await self._handler_registry.execute_by_tool(tool_name, tool_input)
                else:
                    span.set_attribute("error", f"unknown_tool: {tool_name}")
                    suggestion = self._suggest_similar_tool(tool_name)
                    await self._dispatch_hook(
                        "on_after_tool_use",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_result=suggestion,
                        error="unknown_tool",
                    )
                    return suggestion

                # Collect new logs produced during execution (WARNING/ERROR/CRITICAL)
                all_logs = log_buffer.get_logs(count=500)
                new_logs = [
                    log
                    for log in all_logs[logs_before_count:]
                    if log["level"] in ("WARNING", "ERROR", "CRITICAL")
                ]

                # If there are warning/error logs, append them to the result
                if new_logs:
                    result += "\n\n[Execution log]:\n"
                    for log in new_logs[-10:]:
                        result += f"[{log['level']}] {log['module']}: {log['message']}\n"

                # ★ Generic truncation guard: safety net when the tool itself did not truncate
                result = self._guard_truncate(tool_name, result)

                span.set_attribute("result_length", len(result))

                await self._dispatch_hook(
                    "on_after_tool_use",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=result,
                )
                return result

            except ToolError as e:
                logger.warning(f"Tool error ({e.error_type.value}): {tool_name} - {e.message}")
                span.set_attribute("error_type", e.error_type.value)
                span.set_attribute("error_message", e.message)
                error_result = e.to_tool_result()
                await self._dispatch_hook(
                    "on_after_tool_use",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=error_result,
                    error=str(e),
                )
                return error_result

            except ToolSkipped:
                raise

            except Exception as e:
                tool_error = classify_error(e, tool_name=tool_name)
                logger.error(f"Tool execution error: {e}", exc_info=True)
                span.set_attribute("error_type", tool_error.error_type.value)
                span.set_attribute("error_message", str(e))
                error_result = tool_error.to_tool_result()
                await self._dispatch_hook(
                    "on_after_tool_use",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=error_result,
                    error=str(e),
                )
                return error_result

    async def execute_tool_with_policy(
        self,
        tool_name: str,
        tool_input: dict,
        policy_result: Any,
        *,
        session_id: str | None = None,
    ) -> str:
        """Execute an already policy-checked tool, applying sandbox/checkpoint hooks.

        Permission check is assumed to be done by the caller (execute_batch or
        ReasoningEngine).  Only todo-required gate remains here.
        """
        tool_name = self._canonicalize_tool_name(tool_name)
        if isinstance(tool_input, dict):
            tool_input = normalize_tool_input(tool_name, tool_input)

        todo_block = self._check_todo_required(tool_name, session_id)
        if todo_block:
            return todo_block

        if getattr(policy_result, "metadata", {}).get("needs_checkpoint"):
            try:
                from .checkpoint import get_checkpoint_manager

                path = tool_input.get("path", "") or tool_input.get("file_path", "")
                if path:
                    get_checkpoint_manager().create_checkpoint(
                        file_paths=[path],
                        tool_name=tool_name,
                        description=f"Auto-snapshot before {tool_name}",
                    )
            except Exception as e:
                logger.debug(f"[Checkpoint] Failed: {e}")

        if tool_name in ("run_shell", "run_powershell") and getattr(
            policy_result, "metadata", {}
        ).get("needs_sandbox"):
            from .sandbox import get_sandbox_executor

            sandbox = get_sandbox_executor()
            command = tool_input.get("command", "")
            cwd = tool_input.get("cwd")
            timeout = tool_input.get("timeout", 60)
            sb_result = await sandbox.execute(command, cwd=cwd, timeout=float(timeout))
            sandbox_output = (
                f"[Sandbox execution backend={sb_result.backend}]\nExit code: {sb_result.returncode}\n"
            )
            if sb_result.stdout:
                sandbox_output += f"stdout:\n{sb_result.stdout}\n"
            if sb_result.stderr:
                sandbox_output += f"stderr:\n{sb_result.stderr}\n"
            return sandbox_output

        return await self._execute_tool_impl(tool_name, tool_input)

    async def execute_batch(
        self,
        tool_calls: list[dict],
        *,
        state: TaskState | None = None,
        task_monitor: Any = None,
        allow_interrupt_checks: bool = True,
        capture_delivery_receipts: bool = False,
    ) -> tuple[list[dict], list[str], list | None]:
        """
        Execute a batch of tool calls and return tool_results.

        Parallelism strategy:
        - Serial by default (max_parallel=1, or when interrupt checks are enabled).
        - Parallel execution is allowed when max_parallel>1.
        - browser/desktop/mcp handlers use mutex locks by default.

        Args:
            tool_calls: List of tool calls [{id, name, input}, ...].
            state: Task state (used for cancellation checks).
            task_monitor: Task monitor.
            allow_interrupt_checks: Whether interrupt checks are allowed.
            capture_delivery_receipts: Whether to capture delivery receipts.

        Returns:
            (tool_results, executed_tool_names, delivery_receipts)
        """
        executed_tool_names: list[str] = []
        delivery_receipts: list | None = None

        if not tool_calls:
            return [], executed_tool_names, delivery_receipts

        # Parallelism strategy decision
        allow_parallel_with_interrupts = bool(
            getattr(settings, "allow_parallel_tools_with_interrupt_checks", False)
        )
        parallel_enabled = self._max_parallel > 1 and (
            (not allow_interrupt_checks) or allow_parallel_with_interrupts
        )

        session_id = state.session_id if state else None

        async def _run_one(tc: dict, idx: int) -> tuple[int, dict, str | None, list | None]:
            tool_name = self._canonicalize_tool_name(tc.get("name", ""))
            tool_input = tc.get("input") or {}
            tool_use_id = tc.get("id", "")

            if isinstance(tool_input, dict):
                tool_input = normalize_tool_input(tool_name, tool_input)

            # Check for cancellation
            if state and state.cancelled:
                return (
                    idx,
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "[Task stopped by user]",
                        "is_error": True,
                    },
                    None,
                    None,
                )

            # Unified permission check (mode + policy + fail-closed)
            perm_decision = self.check_permission(tool_name, tool_input)

            if perm_decision.behavior == "deny":
                return (
                    idx,
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"⚠️ Policy denied: {perm_decision.reason}",
                        "is_error": True,
                    },
                    None,
                    None,
                )

            if perm_decision.behavior == "confirm":
                from .policy import get_policy_engine

                policy_engine = get_policy_engine()
                confirm_key = policy_engine._confirm_cache_key(tool_name, tool_input)
                if confirm_key in self._pending_confirms:
                    policy_engine.mark_confirmed(tool_name, tool_input)
                    del self._pending_confirms[confirm_key]
                    logger.info(f"[Security] Auto-allowed retry of confirmed tool: {tool_name}")
                else:
                    self._pending_confirms[confirm_key] = {
                        "tool_name": tool_name,
                        "params": tool_input,
                        "metadata": perm_decision.metadata,
                        "ts": time.time(),
                    }
                    risk = perm_decision.metadata.get("risk_level", "")
                    sandbox_hint = ""
                    if perm_decision.metadata.get("needs_sandbox"):
                        sandbox_hint = "\nNote: this command will run in a sandbox to protect system security."

                    return (
                        idx,
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": (
                                f"⚠️ User confirmation required: {perm_decision.reason}"
                                f"{sandbox_hint}\n"
                                "A confirmation request has been sent to the user. Wait for the user's decision via the UI before proceeding. "
                                "Do not use the ask_user tool to repeat the question."
                            ),
                            "is_error": True,
                            "_security_confirm": {
                                "tool_name": tool_name,
                                "params": tool_input,
                                "risk_level": risk,
                                "needs_sandbox": perm_decision.metadata.get("needs_sandbox", False),
                            },
                        },
                        None,
                        None,
                    )

            # Auto-promote deferred tools (formerly blind-call guard).
            #
            # 旧逻辑：直接报错强制 LLM 用 tool_search 跨轮重试，
            #         在小白消费者场景导致首轮必失败、token 浪费。
            # 新逻辑：发现 LLM 直接调用 deferred 工具时，**当轮**自动 promote：
            #   1) 加入 _discovered_tools，下一轮拿到完整 schema
            #   2) 立即清除当前 tool_def 的 _deferred 标记
            #   3) Fall-through 继续执行，handler 一般无需完整 schema 即可工作
            # 失败回退：handler 报参数错时由 LLM 在下一轮自行修正
            #         （此时已具备完整 schema），不会陷入死循环。
            _agent = self._agent_ref
            if _agent and hasattr(_agent, "_discovered_tools"):
                _all_tools = getattr(_agent, "_tools", [])
                _tool_def = next((t for t in _all_tools if t.get("name") == tool_name), None)
                if _tool_def and _tool_def.get("_deferred"):
                    try:
                        _agent._discovered_tools.add(tool_name)
                        _tool_def.pop("_deferred", None)
                        logger.info(
                            f"[ToolExec] Auto-promoted deferred tool '{tool_name}' "
                            f"on direct call (discovered={len(_agent._discovered_tools)})"
                        )
                    except Exception as _promote_err:
                        logger.debug(
                            f"[ToolExec] Auto-promote failed for '{tool_name}': {_promote_err}"
                        )

            # Build a minimal policy_result-like object for execute_tool_with_policy
            policy_result = perm_decision

            handler_name = self.get_handler_name(tool_name)
            handler_lock = self._handler_locks.get(handler_name) if handler_name else None

            t0 = time.time()
            success = True
            result_str = ""
            receipts: list | None = None

            use_parallel_safe_monitor = (
                parallel_enabled
                and task_monitor is not None
                and hasattr(task_monitor, "record_tool_call")
            )
            if (not parallel_enabled) and task_monitor:
                task_monitor.begin_tool_call(tool_name, tool_input)

            try:
                async with self._semaphore:
                    if handler_lock:
                        async with handler_lock:
                            result = await self._execute_with_cancel(
                                self.execute_tool_with_policy(
                                    tool_name,
                                    tool_input,
                                    policy_result,
                                    session_id=session_id,
                                ),
                                state,
                                tool_name,
                            )
                    else:
                        result = await self._execute_with_cancel(
                            self.execute_tool_with_policy(
                                tool_name,
                                tool_input,
                                policy_result,
                                session_id=session_id,
                            ),
                            state,
                            tool_name,
                        )

                result_str = str(result) if result is not None else "Operation completed"

                # execute_tool internally catches all exceptions and returns a string, so none reach here.
                # For the PARSE_ERROR_KEY (arguments truncation) path, we need to fix up the success
                # flag here so that tool_result's is_error is correctly propagated to reasoning_engine.
                from ..llm.converters.tools import PARSE_ERROR_KEY

                if isinstance(tool_input, dict) and PARSE_ERROR_KEY in tool_input:
                    success = False

                if success and isinstance(result_str, str) and result_str.lstrip().startswith("{"):
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(result_str.lstrip())
                        if isinstance(payload, dict) and payload.get("error") is True:
                            success = False
                    except Exception:
                        pass

                # Print tool result to the terminal (for debugging/observation)
                _preview = (
                    result_str if len(result_str) <= 800 else result_str[:800] + "\n... (truncated)"
                )
                try:
                    logger.info(f"[Tool] {tool_name} → {_preview}")
                except (UnicodeEncodeError, OSError):
                    logger.info(f"[Tool] {tool_name} → (result logged, {len(result_str)} chars)")

                # Capture delivery receipts: deliver_artifacts delivers directly; org_accept_deliverable
                # acts as "relayed delivery" — the parent node accepts a deliverable from a subordinate
                # node that already includes files, with receipts status "relayed". Both are considered
                # valid delivery evidence so TaskVerify no longer misclassifies relay scenarios as INCOMPLETE.
                if (
                    capture_delivery_receipts
                    and tool_name in ("deliver_artifacts", "org_accept_deliverable")
                    and result_str
                ):
                    try:
                        import json as _json

                        # execute_one may append "[Execution log]" warning text after the JSON,
                        # which must be stripped before JSON can be parsed correctly.
                        json_str = result_str
                        log_marker = "\n\n[Execution log]"
                        if log_marker in json_str:
                            json_str = json_str[: json_str.index(log_marker)]

                        parsed = _json.loads(json_str)
                        rs = parsed.get("receipts") if isinstance(parsed, dict) else None
                        if isinstance(rs, list) and rs:
                            receipts = rs
                    except Exception:
                        pass

            except ToolSkipped as e:
                skip_reason = e.reason or "User requested skip"
                result_str = f"[User skipped this step: {skip_reason}]"
                logger.info(f"[SkipStep] Tool {tool_name} skipped: {skip_reason}")
                elapsed = time.time() - t0
                if use_parallel_safe_monitor and task_monitor:
                    task_monitor.record_tool_call(tool_name, tool_input, elapsed, True)
                elif (not parallel_enabled) and task_monitor:
                    task_monitor.end_tool_call(result_str, success=True)
                return (
                    idx,
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    },
                    tool_name,
                    None,
                )

            except Exception as e:
                success = False
                tool_error = classify_error(e, tool_name=tool_name)
                result_str = tool_error.to_tool_result()
                logger.error(f"Tool batch execution error: {tool_name}: {e}")
                logger.info(f"[Tool] {tool_name} ❌ Error: {result_str}")

            elapsed = time.time() - t0

            # Record to task_monitor
            if use_parallel_safe_monitor and task_monitor:
                task_monitor.record_tool_call(tool_name, tool_input, elapsed, success)
            elif (not parallel_enabled) and task_monitor:
                task_monitor.end_tool_call(result_str, success)

            tool_result = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_str,
            }
            if not success:
                tool_result["is_error"] = True

            return idx, tool_result, tool_name if success else None, receipts

        # Execute: use the partitioning strategy (concurrency-safe tools run in parallel, others serially)
        if parallel_enabled and len(tool_calls) > 1:
            batches = self._partition_tool_calls(tool_calls)
            results = []
            for batch in batches:
                if state and state.cancelled:
                    break
                if batch["concurrent"] and len(batch["calls"]) > 1:
                    tasks = [_run_one(tc, tc["_idx"]) for tc in batch["calls"]]
                    batch_results = await asyncio.gather(*tasks)
                    results.extend(batch_results)
                else:
                    for tc in batch["calls"]:
                        if state and state.cancelled:
                            break
                        result = await _run_one(tc, tc["_idx"])
                        results.append(result)
            results = sorted(results, key=lambda x: x[0])
        else:
            # Serial execution
            results = []
            for i, tc in enumerate(tool_calls):
                result = await _run_one(tc, i)
                results.append(result)

                # In serial mode, check for interruption and cancellation
                if state and state.cancelled:
                    # Generate cancellation results for the remaining tools
                    for j in range(i + 1, len(tool_calls)):
                        remaining_tc = tool_calls[j]
                        results.append(
                            (
                                j,
                                {
                                    "type": "tool_result",
                                    "tool_use_id": remaining_tc.get("id", ""),
                                    "content": "[Task stopped by user]",
                                    "is_error": True,
                                },
                                None,
                                None,
                            )
                        )
                    break

        # Collate results
        tool_results = []
        for _, tool_result, name, receipts_item in results:
            tool_results.append(tool_result)
            if name:
                executed_tool_names.append(name)
            if receipts_item:
                delivery_receipts = receipts_item

        return tool_results, executed_tool_names, delivery_receipts

    @staticmethod
    def _guard_truncate(tool_name: str, result: str) -> str:
        """Generic truncation guard: fallback when the tool itself does not truncate and the result is too long.

        - Results already containing OVERFLOW_MARKER are skipped (the tool handled it itself).
        - When over the limit, save the full output to the overflow file, truncate, and append a pagination hint.
        """
        if not result or len(result) <= MAX_TOOL_RESULT_CHARS:
            return result
        if OVERFLOW_MARKER in result:
            return result  # Already handled by the tool itself

        overflow_path = save_overflow(tool_name, result)
        total_chars = len(result)
        truncated = result[:MAX_TOOL_RESULT_CHARS]
        hint = (
            f"\n\n{OVERFLOW_MARKER} Tool '{tool_name}' produced {total_chars} characters of output, "
            f"truncated to the first {MAX_TOOL_RESULT_CHARS} characters.\n"
            f"Full output saved to: {overflow_path}\n"
            f'Use read_file(path="{overflow_path}", offset=1, limit=300) to view the complete content.'
        )
        logger.info(
            f"[Guard] Truncated {tool_name} output: {total_chars} → {MAX_TOOL_RESULT_CHARS} chars, "
            f"overflow saved to {overflow_path}"
        )
        return truncated + hint

    def _check_todo_required(self, tool_name: str, session_id: str | None) -> str | None:
        """
        Check whether a Todo must be created first (applies only to todo tracking in Agent mode).

        If the current session is marked as requiring a Todo (compound task) but no Todo has
        been created yet, refuse to execute other tools.

        This check is skipped in Plan/Ask modes (controlled by the mode prompt and tool filtering).

        Returns:
            A block-message string, or None (execution allowed).
        """
        if self._current_mode in ("plan", "ask"):
            return None

        if tool_name in (
            "create_todo",
            "create_plan_file",
            "exit_plan_mode",
            "get_todo_status",
            "ask_user",
        ):
            return None

        try:
            from ..tools.handlers.plan import has_active_todo, is_todo_required

            if session_id and is_todo_required(session_id) and not has_active_todo(session_id):
                return (
                    "⚠️ **This is a multi-step task; please create a Todo first!**\n\n"
                    "Please call the `create_todo` tool to create a task plan before performing concrete operations.\n\n"
                    "Example:\n"
                    "```\n"
                    "create_todo(\n"
                    "  task_summary='Write a script to get and display the time',\n"
                    "  steps=[\n"
                    "    {id: 'step1', description: 'Create Python script', tool: 'write_file'},\n"
                    "    {id: 'step2', description: 'Run the script', tool: 'run_shell'},\n"
                    "    {id: 'step3', description: 'Read the result', tool: 'read_file'}\n"
                    "  ]\n"
                    ")\n"
                    "```"
                )
        except Exception:
            pass

        return None

    def check_permission(self, tool_name: str, tool_input: dict) -> "PermissionDecision":
        """Unified permission check — mode rules + PolicyEngine + fail-closed.

        This is the single choke-point for all permission decisions.
        Callers should inspect `decision.behavior` ("allow" / "deny" / "confirm").
        """
        from .permission import PermissionDecision, check_permission

        self._prune_stale_confirms()

        try:
            decision = check_permission(
                tool_name,
                tool_input,
                mode=self._current_mode,
                extra_rules=self._extra_permission_rules,
            )
        except Exception as e:
            logger.error(f"[Permission] Unexpected error in check_permission: {e}")
            decision = PermissionDecision(
                behavior="deny",
                reason="Permission check raised an exception; operation blocked.",
                reason_detail=str(e),
            )

        # Step 3: per-tool check_permissions callback (PM3 extension point)
        if decision.behavior == "allow":
            tool_perm_check = self._handler_registry.get_permission_check(tool_name)
            if tool_perm_check is not None:
                try:
                    tool_decision = tool_perm_check(tool_name, tool_input)
                    if (
                        tool_decision is not None
                        and getattr(tool_decision, "behavior", "allow") != "allow"
                    ):
                        decision = tool_decision
                except Exception as e:
                    logger.warning(
                        f"[Permission] per-tool check_permissions error for {tool_name}: {e}"
                    )

        if decision.behavior != "allow":
            logger.warning(
                f"[Permission] {decision.behavior.upper()} {tool_name} "
                f"in {self._current_mode} mode: {decision.reason_detail}"
            )

        # Audit log for every decision
        try:
            from .audit_logger import get_audit_logger

            get_audit_logger().log(
                tool_name=tool_name,
                decision=decision.behavior,
                reason=decision.reason,
                policy=decision.policy_name,
                params_preview=str(tool_input)[:200],
                metadata=decision.metadata,
            )
        except Exception:
            pass

        return decision

    def clear_confirm_cache(self) -> None:
        """Clear all pending confirm entries (called on /api/chat/clear)."""
        count = len(self._pending_confirms)
        self._pending_confirms.clear()
        if count:
            logger.debug(f"[Permission] Cleared {count} pending confirm(s)")

    def _prune_stale_confirms(self) -> None:
        """Remove pending confirms older than 5 minutes."""
        if not self._pending_confirms:
            return
        now = time.time()
        stale = [k for k, v in self._pending_confirms.items() if now - v.get("ts", 0) > 300]
        for k in stale:
            del self._pending_confirms[k]

    def _check_permission_deny_msg(self, tool_name: str, tool_input: dict) -> str | None:
        """Convenience wrapper: returns a deny message string or None for allow.

        For CONFIRM decisions in standalone (non-batch) context, returns a
        message asking the user to confirm via ask_user.
        """
        decision = self.check_permission(tool_name, tool_input)
        if decision.behavior == "allow":
            return None
        if decision.behavior == "confirm":
            return (
                f"⚠️ User confirmation required: {decision.reason}\n"
                "A confirmation request has been sent to the user. Wait for the user's decision via the UI before proceeding. "
                "Do not use the ask_user tool to repeat the question."
            )
        return decision.reason
