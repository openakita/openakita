"""
工具执行引擎

从 agent.py 提取的工具执行逻辑，负责:
- 单工具执行 (execute_tool)
- 批量工具执行 (execute_batch)
- 并行/串行策略
- Handler 互斥锁管理 (browser/desktop/mcp)
- 结构化错误处理 (ToolError)
- Plan 模式检查
- 通用截断守卫 (大结果自动截断 + 溢出文件)
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from ..tools.errors import ToolError, classify_error
from ..tools.handlers import SystemHandlerRegistry
from ..tools.input_normalizer import normalize_tool_input
from ..tracing.tracer import get_tracer
from .agent_state import TaskState

logger = logging.getLogger(__name__)


class ToolSkipped(Exception):
    """用户主动跳过当前工具执行（非错误，仅中断单步）。"""
    def __init__(self, reason: str = "用户请求跳过"):
        self.reason = reason
        super().__init__(reason)

# ========== 通用截断守卫常量 ==========
MAX_TOOL_RESULT_CHARS = 16000  # 通用截断阈值 (~8000 tokens)
OVERFLOW_MARKER = "[OUTPUT_TRUNCATED]"  # 截断标记，已含此标记的不二次截断
_OVERFLOW_DIR = Path("data/tool_overflow")
_OVERFLOW_MAX_FILES = 50  # 溢出目录保留的最大文件数


def save_overflow(tool_name: str, content: str) -> str:
    """将大输出保存到溢出文件，返回文件路径。

    供 tool_executor 和各 handler 共用。
    """
    try:
        _OVERFLOW_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{tool_name}_{ts}.txt"
        filepath = _OVERFLOW_DIR / filename
        filepath.write_text(content, encoding="utf-8")
        _cleanup_overflow_files(_OVERFLOW_DIR, _OVERFLOW_MAX_FILES)
        logger.info(
            f"[Overflow] Saved {len(content)} chars to {filepath}"
        )
        return str(filepath)
    except Exception as exc:
        logger.warning(f"[Overflow] Failed to save overflow file: {exc}")
        return "(溢出文件保存失败)"


def smart_truncate(
    content: str,
    limit: int,
    *,
    label: str = "content",
    save_full: bool = True,
    head_ratio: float = 0.65,
) -> tuple[str, bool]:
    """智能截断：首尾保留 + 溢出文件 + 截断标记。

    Args:
        content: 原始文本
        limit: 截断字符上限
        label: 溢出文件名前缀
        save_full: 是否保存完整内容到溢出文件（验证类调用设为 False）
        head_ratio: 保留头部的比例

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
        overflow_ref = f", 完整内容: {path}, 可用 read_file 查看"

    marker = f"\n[已截断, 原文{len(content)}字{overflow_ref}]\n"

    if tail > 0:
        return content[:head] + marker + content[-tail:], True
    return content[:head] + marker, True


def _cleanup_overflow_files(directory: Path, max_files: int) -> None:
    """清理溢出目录，只保留最近 max_files 个文件。"""
    try:
        files = sorted(directory.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        if len(files) > max_files:
            for f in files[: len(files) - max_files]:
                f.unlink(missing_ok=True)
    except Exception:
        pass


class ToolExecutor:
    """
    工具执行引擎。

    管理工具的串行/并行执行、Handler 互斥锁、
    结构化错误处理和 Plan 模式检查。
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

        # 并行控制
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._max_parallel = max_parallel

        # 状态型工具互斥锁（browser/desktop/mcp 等不能并发执行）
        self._handler_locks: dict[str, asyncio.Lock] = {}
        for handler_name in ("browser", "desktop", "mcp"):
            self._handler_locks[handler_name] = asyncio.Lock()

        # Security: pending confirmations — tool calls that returned CONFIRM
        # and are awaiting user decision via ask_user.
        # When the agent retries after ask_user, we auto-mark as confirmed.
        self._pending_confirms: dict[str, dict] = {}  # cache_key → {tool_name, params, metadata, ts}

        # Current mode for permission checks (set by ReasoningEngine before tool loop)
        self._current_mode: str = "agent"

        # Extra permission rules injected by AgentFactory (profile rules)
        self._extra_permission_rules: list | None = None

    # 并发安全工具: 这些工具的只读操作可以并行执行
    _CONCURRENCY_SAFE_TOOLS: set[str] = {
        "read_file", "list_files", "search_files", "web_fetch",
        "get_time", "read_resource", "list_resources",
    }

    # 长时间运行工具的硬超时（秒），防止工具卡死拖垮整个 agent 循环
    # 值为 0 表示不设硬超时（由工具自身的进度监控负责，如 Orchestrator 的 idle-timeout）
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
        """获取工具对应的 handler 名称"""
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
        """为未知工具名生成带相似推荐的错误信息。"""
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
        msg = f"❌ 未知工具: {tool_name}。"
        if top:
            msg += f" 你是否想使用: {', '.join(top)}？"
        else:
            msg += " 请检查工具名称是否正确。"
        return msg

    def _is_concurrency_safe(self, tool_name: str, tool_input: dict) -> bool:
        """判断工具在给定输入下是否并发安全。

        参考 Claude Code 的 isConcurrencySafe(input) 设计:
        按工具名 + 输入内容判断，而非全局开关。
        """
        if tool_name in self._CONCURRENCY_SAFE_TOOLS:
            return True
        handler_name = self.get_handler_name(tool_name)
        if handler_name in self._handler_locks:
            return False
        return False

    def _partition_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """将工具调用分区为并发安全批次和串行批次。

        连续的并发安全工具合批并行，非安全工具独立串行。
        每个 tool_call 标记 _idx 用于排序恢复。
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
        执行工具协程，同时监听 cancel_event / skip_event / 硬超时 三路竞速。

        - cancel_event 触发 → 返回中断错误（终止整个任务）
        - skip_event 触发 → 抛出 ToolSkipped（仅跳过当前工具）
        - 硬超时 → 返回超时错误
        - hard_timeout=0 表示不设硬超时
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

            # skip_event 先于 cancel 检查（skip 只中断当前步骤，不终止任务）
            if skip_future and skip_future in done:
                tool_task.cancel()
                try:
                    await tool_task
                except (asyncio.CancelledError, Exception):
                    pass
                skip_reason = getattr(state, "skip_reason", "") or "用户请求跳过"
                if state and hasattr(state, "clear_skip"):
                    state.clear_skip()
                logger.info(f"[ToolExecutor] Tool '{tool_name}' skipped: {skip_reason}")
                raise ToolSkipped(skip_reason)

            reason = ""
            if cancel_future and cancel_future in done:
                reason = "用户请求取消任务"
                logger.warning(f"[ToolExecutor] Tool '{tool_name}' cancelled by user")
            else:
                reason = f"工具执行超时 ({hard_timeout}s)"
                logger.error(f"[ToolExecutor] Tool '{tool_name}' timed out after {hard_timeout}s")

            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass

            return f"⚠️ 工具执行被中断: {reason}。工具 '{tool_name}' 已停止。"

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
        执行单个工具调用。

        优先使用 handler_registry 执行，
        捕获异常后返回结构化 ToolError。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数
            session_id: 当前会话 ID（用于 Plan 检查）

        Returns:
            工具执行结果字符串
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

    async def _execute_tool_impl(
        self,
        tool_name: str,
        tool_input: dict,
    ) -> str:
        """Execute a tool after todo / permission gates have been handled."""
        logger.info(f"Executing tool: {tool_name} with {tool_input}")

        # ★ 拦截 JSON 解析失败的工具调用（参数被 API 截断）
        # convert_tool_calls_from_openai() 在 JSON 解析失败时会注入 __parse_error__
        from ..llm.converters.tools import PARSE_ERROR_KEY

        if isinstance(tool_input, dict) and PARSE_ERROR_KEY in tool_input:
            err_msg = tool_input[PARSE_ERROR_KEY]
            logger.warning(
                f"[ToolExecutor] Skipping tool '{tool_name}' due to parse error: "
                f"{err_msg[:200]}"
            )
            return err_msg

        # 导入日志缓存
        from ..logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()
        logs_before = log_buffer.get_logs(count=500)
        logs_before_count = len(logs_before)

        tracer = get_tracer()
        with tracer.tool_span(tool_name=tool_name, input_data=tool_input) as span:
            try:
                # 通过 handler_registry 执行
                if self._handler_registry.has_tool(tool_name):
                    result = await self._handler_registry.execute_by_tool(tool_name, tool_input)
                else:
                    span.set_attribute("error", f"unknown_tool: {tool_name}")
                    suggestion = self._suggest_similar_tool(tool_name)
                    return suggestion

                # 获取执行期间产生的新日志（WARNING/ERROR/CRITICAL）
                all_logs = log_buffer.get_logs(count=500)
                new_logs = [
                    log
                    for log in all_logs[logs_before_count:]
                    if log["level"] in ("WARNING", "ERROR", "CRITICAL")
                ]

                # 如果有警告/错误日志，附加到结果
                if new_logs:
                    result += "\n\n[执行日志]:\n"
                    for log in new_logs[-10:]:
                        result += f"[{log['level']}] {log['module']}: {log['message']}\n"

                # ★ 通用截断守卫：工具自身未做截断时的安全网
                result = self._guard_truncate(tool_name, result)

                span.set_attribute("result_length", len(result))
                return result

            except ToolError as e:
                # 结构化工具错误，直接序列化返回给 LLM
                logger.warning(f"Tool error ({e.error_type.value}): {tool_name} - {e.message}")
                span.set_attribute("error_type", e.error_type.value)
                span.set_attribute("error_message", e.message)
                return e.to_tool_result()

            except ToolSkipped:
                raise

            except Exception as e:
                # 将通用异常分类为结构化 ToolError
                tool_error = classify_error(e, tool_name=tool_name)
                logger.error(f"Tool execution error: {e}", exc_info=True)
                span.set_attribute("error_type", tool_error.error_type.value)
                span.set_attribute("error_message", str(e))
                return tool_error.to_tool_result()

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

        if (
            tool_name in ("run_shell", "run_powershell")
            and getattr(policy_result, "metadata", {}).get("needs_sandbox")
        ):
            from .sandbox import get_sandbox_executor

            sandbox = get_sandbox_executor()
            command = tool_input.get("command", "")
            cwd = tool_input.get("cwd")
            timeout = tool_input.get("timeout", 60)
            sb_result = await sandbox.execute(command, cwd=cwd, timeout=float(timeout))
            sandbox_output = (
                f"[沙箱执行 backend={sb_result.backend}]\n"
                f"Exit code: {sb_result.returncode}\n"
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
        执行一批工具调用，返回 tool_results。

        并行策略：
        - 默认串行（max_parallel=1 或启用中断检查时）
        - 当 max_parallel>1 时允许并行执行
        - browser/desktop/mcp handler 默认互斥锁

        Args:
            tool_calls: 工具调用列表 [{id, name, input}, ...]
            state: 任务状态（用于取消检查）
            task_monitor: 任务监控器
            allow_interrupt_checks: 是否允许中断检查
            capture_delivery_receipts: 是否捕获交付回执

        Returns:
            (tool_results, executed_tool_names, delivery_receipts)
        """
        executed_tool_names: list[str] = []
        delivery_receipts: list | None = None

        if not tool_calls:
            return [], executed_tool_names, delivery_receipts

        # 并行策略决策
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

            # 检查取消
            if state and state.cancelled:
                return (
                    idx,
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "[任务已被用户停止]",
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
                        "content": f"⚠️ 策略拒绝: {perm_decision.reason}",
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
                    logger.info(
                        f"[Security] Auto-allowed retry of confirmed tool: {tool_name}"
                    )
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
                        sandbox_hint = "\n注意: 此命令将在沙箱中执行以保护系统安全。"

                    return (
                        idx,
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": (
                                f"⚠️ 需要用户确认: {perm_decision.reason}"
                                f"{sandbox_hint}\n"
                                "请使用 ask_user 工具询问用户是否允许此操作，"
                                "得到用户同意后再重新调用此工具。"
                            ),
                            "is_error": True,
                            "_security_confirm": {
                                "tool_name": tool_name,
                                "params": tool_input,
                                "risk_level": risk,
                                "needs_sandbox": perm_decision.metadata.get(
                                    "needs_sandbox", False
                                ),
                            },
                        },
                        None,
                        None,
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

                result_str = str(result) if result is not None else "操作已完成"

                # execute_tool 内部捕获所有异常并返回字符串，不会抛到这里。
                # 对于 PARSE_ERROR_KEY（参数截断）路径，需要在此修正 success
                # 标志，使 tool_result 的 is_error 正确传播到 reasoning_engine。
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

                # 终端输出工具返回结果（便于调试与观察）
                _preview = result_str if len(result_str) <= 800 else result_str[:800] + "\n... (已截断)"
                try:
                    logger.info(f"[Tool] {tool_name} → {_preview}")
                except (UnicodeEncodeError, OSError):
                    logger.info(f"[Tool] {tool_name} → (result logged, {len(result_str)} chars)")

                # 捕获交付回执
                if capture_delivery_receipts and tool_name == "deliver_artifacts" and result_str:
                    try:
                        import json as _json

                        # execute_one 可能在 JSON 后追加 "[执行日志]" 警告文本，
                        # 需要先剥离才能正确解析 JSON
                        json_str = result_str
                        log_marker = "\n\n[执行日志]"
                        if log_marker in json_str:
                            json_str = json_str[: json_str.index(log_marker)]

                        parsed = _json.loads(json_str)
                        rs = parsed.get("receipts") if isinstance(parsed, dict) else None
                        if isinstance(rs, list):
                            receipts = rs
                    except Exception:
                        pass

            except ToolSkipped as e:
                skip_reason = e.reason or "用户请求跳过"
                result_str = f"[用户跳过了此步骤: {skip_reason}]"
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
                logger.info(f"[Tool] {tool_name} ❌ 错误: {result_str}")

            elapsed = time.time() - t0

            # 记录到 task_monitor
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

        # 执行: 使用分区策略（并发安全工具可并行，其他串行）
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
            # 串行执行
            results = []
            for i, tc in enumerate(tool_calls):
                result = await _run_one(tc, i)
                results.append(result)

                # 串行模式下检查中断和取消
                if state and state.cancelled:
                    # 为剩余工具生成取消结果
                    for j in range(i + 1, len(tool_calls)):
                        remaining_tc = tool_calls[j]
                        results.append((
                            j,
                            {
                                "type": "tool_result",
                                "tool_use_id": remaining_tc.get("id", ""),
                                "content": "[任务已被用户停止]",
                                "is_error": True,
                            },
                            None,
                            None,
                        ))
                    break

        # 整理结果
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
        """通用截断守卫：如果工具自身未截断且结果超长，在此兜底。

        - 已含 OVERFLOW_MARKER 的跳过（工具自行处理过了）
        - 超限时保存完整输出到溢出文件，截断并附加分页提示
        """
        if not result or len(result) <= MAX_TOOL_RESULT_CHARS:
            return result
        if OVERFLOW_MARKER in result:
            return result  # 工具自己已处理

        overflow_path = save_overflow(tool_name, result)
        total_chars = len(result)
        truncated = result[:MAX_TOOL_RESULT_CHARS]
        hint = (
            f"\n\n{OVERFLOW_MARKER} 工具 '{tool_name}' 输出共 {total_chars} 字符，"
            f"已截断到前 {MAX_TOOL_RESULT_CHARS} 字符。\n"
            f"完整输出已保存到: {overflow_path}\n"
            f'使用 read_file(path="{overflow_path}", offset=1, limit=300) 查看完整内容。'
        )
        logger.info(
            f"[Guard] Truncated {tool_name} output: {total_chars} → {MAX_TOOL_RESULT_CHARS} chars, "
            f"overflow saved to {overflow_path}"
        )
        return truncated + hint

    def _check_todo_required(self, tool_name: str, session_id: str | None) -> str | None:
        """
        检查是否需要先创建 Todo（仅 Agent 模式下的 todo 跟踪）。

        如果当前 session 被标记为需要 Todo（compound 任务），
        但还没有创建 Todo，则拒绝执行其他工具。

        Plan/Ask 模式下跳过此检查（由模式提示词和工具过滤控制）。

        Returns:
            阻止消息字符串，或 None（允许执行）
        """
        if self._current_mode in ("plan", "ask"):
            return None

        if tool_name in ("create_todo", "create_plan_file", "exit_plan_mode",
                         "get_todo_status", "ask_user"):
            return None

        try:
            from ..tools.handlers.plan import has_active_todo, is_todo_required

            if session_id and is_todo_required(session_id) and not has_active_todo(session_id):
                return (
                    "⚠️ **这是一个多步骤任务，建议先创建 Todo！**\n\n"
                    "请先调用 `create_todo` 工具创建任务计划，然后再执行具体操作。\n\n"
                    "示例：\n"
                    "```\n"
                    "create_todo(\n"
                    "  task_summary='写脚本获取时间并显示',\n"
                    "  steps=[\n"
                    "    {id: 'step1', description: '创建Python脚本', tool: 'write_file'},\n"
                    "    {id: 'step2', description: '执行脚本', tool: 'run_shell'},\n"
                    "    {id: 'step3', description: '读取结果', tool: 'read_file'}\n"
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
                tool_name, tool_input,
                mode=self._current_mode,
                extra_rules=self._extra_permission_rules,
            )
        except Exception as e:
            logger.error(f"[Permission] Unexpected error in check_permission: {e}")
            decision = PermissionDecision(
                behavior="deny",
                reason="权限检查异常，已阻止操作。",
                reason_detail=str(e),
            )

        # Step 3: per-tool check_permissions callback (PM3 extension point)
        if decision.behavior == "allow":
            tool_perm_check = self._handler_registry.get_permission_check(tool_name)
            if tool_perm_check is not None:
                try:
                    tool_decision = tool_perm_check(tool_name, tool_input)
                    if tool_decision is not None and getattr(tool_decision, "behavior", "allow") != "allow":
                        decision = tool_decision
                except Exception as e:
                    logger.warning(f"[Permission] per-tool check_permissions error for {tool_name}: {e}")

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
                f"⚠️ 需要用户确认: {decision.reason}\n"
                "请使用 ask_user 工具询问用户是否允许此操作，"
                "得到用户同意后再重新调用此工具。"
            )
        return decision.reason
