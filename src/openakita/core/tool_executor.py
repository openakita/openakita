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
import uuid
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
    """用户主动跳过当前工具执行（非错误，仅中断单步）。"""

    def __init__(self, reason: str = "用户请求跳过"):
        self.reason = reason
        super().__init__(reason)


# ========== 通用截断守卫常量 ==========
DEFAULT_TOOL_RESULT_MAX_CHARS = 32000
MAX_TOOL_RESULT_CHARS = DEFAULT_TOOL_RESULT_MAX_CHARS  # backward-compatible export
OVERFLOW_MARKER = "[OUTPUT_TRUNCATED]"  # 截断标记，已含此标记的不二次截断
_OVERFLOW_DIR = Path("data/tool_overflow")
_OVERFLOW_MAX_FILES = 200  # fallback; runtime value comes from settings


def _get_tool_result_max_chars() -> int:
    try:
        return max(1000, int(getattr(settings, "tool_result_max_chars", DEFAULT_TOOL_RESULT_MAX_CHARS)))
    except (TypeError, ValueError):
        return DEFAULT_TOOL_RESULT_MAX_CHARS


def _get_tool_overflow_max_files() -> int:
    try:
        return max(10, int(getattr(settings, "tool_overflow_max_files", _OVERFLOW_MAX_FILES)))
    except (TypeError, ValueError):
        return _OVERFLOW_MAX_FILES


def _get_read_file_default_limit() -> int:
    try:
        return max(1, int(getattr(settings, "read_file_default_limit", 2000)))
    except (TypeError, ValueError):
        return 2000


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
        max_files = _get_tool_overflow_max_files()
        _cleanup_overflow_files(_OVERFLOW_DIR, max_files)
        logger.info(f"[Overflow] Saved {len(content)} chars to {filepath}")
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
        "browser-open": "browser_open",
        "browser-navigate": "browser_navigate",
        "browser-click": "browser_click",
        "browser-type": "browser_type",
        "browser-input": "browser_type",
        "browser-input-text": "browser_type",
        "browser-fill": "browser_type",
        "browser_fill": "browser_type",
        "browser-get-content": "browser_get_content",
        "browser-screenshot": "browser_screenshot",
        "browser-execute-js": "browser_execute_js",
    }

    def __init__(
        self,
        handler_registry: SystemHandlerRegistry,
        max_parallel: int = 1,
    ) -> None:
        self._handler_registry = handler_registry
        self._agent_ref: Any = None  # set by Agent after construction
        self._plugin_hooks: Any = None  # HookRegistry, set by Agent after construction
        # C10: PluginManager set by Agent.late_wire — needed for
        # mutates_params authorization in on_before_tool_use audit.
        self._plugin_manager: Any = None

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
        self._pending_confirms: dict[
            str, dict
        ] = {}  # cache_key → {tool_name, params, metadata, ts}

        # Current mode for permission checks (set by ReasoningEngine before tool loop)
        self._current_mode: str = "agent"

        # Extra permission rules injected by AgentFactory (profile rules)
        self._extra_permission_rules: list | None = None

    # 并发安全工具: 这些工具的只读操作可以并行执行
    _CONCURRENCY_SAFE_TOOLS: set[str] = {
        "read_file",
        "list_files",
        "search_files",
        "web_fetch",
        "get_time",
        "read_resource",
        "list_resources",
    }

    # 默认不对工具施加硬超时。长任务由用户停止/跳过、工具自身进度监控、
    # 或用户显式配置的 timeout 控制，避免短硬限制打断真实任务。
    _LONG_RUNNING_TOOLS: frozenset[str] = frozenset(
        {
            "org_request_meeting",
            "org_broadcast",
            "delegate_to_agent",
            "delegate_parallel",
            "spawn_agent",
            "browser_navigate",
            "browser_use",
            "run_shell",
            "run_powershell",
        }
    )

    def get_handler_name(self, tool_name: str) -> str | None:
        """获取工具对应的 handler 名称"""
        try:
            return self._handler_registry.get_handler_name_for_tool(tool_name)
        except Exception:
            return None

    def _canonicalize_tool_name(self, tool_name: str) -> str:
        normalized = (tool_name or "").strip()
        canonical = self._TOOL_ALIASES.get(normalized)
        if canonical is None:
            lowered = normalized.lower()
            canonical = self._TOOL_ALIASES.get(lowered)
        if canonical is None and "-" in normalized:
            canonical = self._TOOL_ALIASES.get(normalized.replace("-", "_"))
        if canonical is None and "-" in normalized:
            candidate = normalized.lower().replace("-", "_")
            try:
                if self._handler_registry.has_tool(candidate) is True:
                    canonical = candidate
            except Exception:
                pass
        if canonical:
            logger.info(f"[ToolExecutor] Alias corrected: '{normalized}' -> '{canonical}'")
            return canonical
        return normalized

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

        优先询问 handler 级回调（可根据 tool_input 细粒度判断），
        回调返回 None 时回退到静态 ``_CONCURRENCY_SAFE_TOOLS`` 集合。
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
        """将工具调用分区为并发安全批次和串行批次。

        连续的并发安全工具合批并行，非安全工具独立串行。
        每个 tool_call 标记 _idx 用于排序恢复。
        """
        batches: list[dict] = []
        current_safe: list[dict] = []

        for i, tc in enumerate(tool_calls):
            tc_with_idx = {**tc, "_idx": i}
            name = tc.get("name", "")
            inp = tc.get("input", tc.get("arguments", {}))

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

    def _hard_timeout_for_tool(self, tool_name: str) -> int:
        """Return the user-configured hard timeout for a tool.

        0 means no executor-level hard timeout. This keeps long user tasks from
        being cut off by built-in short limits; users can still configure a
        timeout when they want that safety net.
        """
        setting_name = (
            "long_running_tool_timeout_seconds"
            if tool_name in self._LONG_RUNNING_TOOLS
            else "tool_hard_timeout_seconds"
        )
        try:
            return max(0, int(getattr(settings, setting_name, 0)))
        except (TypeError, ValueError):
            return 0

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

        hard_timeout = self._hard_timeout_for_tool(tool_name)

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

        grounding_block = self._check_current_turn_grounding(tool_name, tool_input)
        if grounding_block:
            return grounding_block

        return await self._execute_tool_impl(tool_name, tool_input)

    async def _dispatch_hook(self, hook_name: str, **kwargs) -> None:
        """Fire a plugin hook if a HookRegistry is attached. Never raises.

        ``on_before_tool_use`` 走 :meth:`_dispatch_before_tool_use_hook` 走专门
        的 mutates_params 审计 + revert 路径（C10 / R2-12）。其他 hook 透传。
        """
        hooks = self._plugin_hooks
        if hooks is None:
            return
        if hook_name == "on_before_tool_use":
            await self._dispatch_before_tool_use_hook(**kwargs)
            return
        try:
            await hooks.dispatch(hook_name, **kwargs)
        except Exception as e:
            logger.debug(f"[ToolExecutor] {hook_name} hook error (ignored): {e}")

    async def _dispatch_before_tool_use_hook(
        self, *, tool_name: str, tool_input: Any
    ) -> None:
        """C10：on_before_tool_use 专用 dispatch + R2-12 强制审计。

        步骤：
        1. 用 ``ParamMutationAuditor.snapshot`` deep-copy 一份 ``tool_input``。
        2. 派发 hook（HookRegistry 现状为并行 ``asyncio.gather``）。
        3. diff before vs after：无差异 → 跳过审计静默返回。
        4. 收集 ``on_before_tool_use`` 注册的 plugin_id 列表作为 attribution
           候选。
        5. 任一候选 plugin 在 ``manifest.mutates_params`` 列出该 tool → ``allowed``，
           否则 ``revert``。
        6. 写 jsonl 审计；revert 时把 ``tool_input`` 原地恢复为 snapshot。

        所有异常都吃掉只 WARN——hook / 审计绝不能阻止 tool 执行。
        """
        hooks = self._plugin_hooks
        if hooks is None:
            return

        from .policy_v2.param_mutation_audit import get_default_auditor

        auditor = get_default_auditor()
        before_snapshot = auditor.snapshot(tool_input)

        try:
            await hooks.dispatch(
                "on_before_tool_use", tool_name=tool_name, tool_input=tool_input
            )
        except Exception as e:
            logger.debug(
                f"[ToolExecutor] on_before_tool_use hook error (ignored): {e}"
            )

        if not isinstance(tool_input, dict):
            # Hook 不会原地改非 dict，diff 没意义
            return

        candidate_plugin_ids: list[str] = []
        try:
            for cb in hooks.get_hooks("on_before_tool_use"):
                pid = getattr(cb, "__plugin_id__", "") or ""
                if pid and pid not in candidate_plugin_ids:
                    candidate_plugin_ids.append(pid)
        except Exception as exc:
            logger.debug(
                "[ToolExecutor] failed to enumerate on_before_tool_use callbacks: %s",
                exc,
            )

        plugin_manager = getattr(self, "_plugin_manager", None)
        if plugin_manager is not None and hasattr(
            plugin_manager, "plugin_allows_param_mutation"
        ):
            is_authorized = plugin_manager.plugin_allows_param_mutation
        else:
            def is_authorized(_plugin_id: str, _tool: str) -> bool:
                return False

        outcome = auditor.evaluate(
            tool_name=tool_name,
            before=before_snapshot,
            after=tool_input,
            candidate_plugin_ids=candidate_plugin_ids,
            is_plugin_authorized=is_authorized,
        )
        if not outcome.has_changes:
            return

        if not outcome.allowed:
            # Revert：把 tool_input 原地恢复——不能 reassign，因为外层调用
            # 持有同一个 dict 引用。snapshot_failed 时 before_snapshot 是
            # sentinel（不是 dict），无法恢复——只能 fail closed 清空
            # tool_input，让下游 handler 因缺参直接拒绝执行（远优于带着
            # 未审计的 mutation 继续）。
            if outcome.snapshot_failed:
                try:
                    tool_input.clear()
                except Exception as exc:
                    logger.error(
                        "[ToolExecutor] snapshot failed for tool=%s and "
                        "tool_input.clear() also failed: %s — mutation "
                        "remains; downstream handler MUST treat input as "
                        "untrusted",
                        tool_name,
                        exc,
                    )
                logger.error(
                    "[ToolExecutor] on_before_tool_use snapshot failed for "
                    "tool=%s; cleared tool_input as fail-closed (audit "
                    "record snapshot_failed=True)",
                    tool_name,
                )
            else:
                try:
                    tool_input.clear()
                    tool_input.update(before_snapshot)
                except Exception as exc:
                    logger.warning(
                        "[ToolExecutor] failed to revert on_before_tool_use "
                        "mutation for tool=%s: %s",
                        tool_name,
                        exc,
                    )

        auditor.write(
            tool_name=tool_name,
            outcome=outcome,
            before=before_snapshot,
            after=tool_input if outcome.allowed else before_snapshot,
        )

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
                f"[ToolExecutor] Skipping tool '{tool_name}' due to parse error: {err_msg[:200]}"
            )
            return err_msg

        await self._dispatch_hook(
            "on_before_tool_use", tool_name=tool_name, tool_input=tool_input
        )

        # 导入日志缓存
        from ..logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()
        logs_before = log_buffer.get_logs(count=500)
        logs_before_count = len(logs_before)

        tracer = get_tracer()
        started_at = time.monotonic()
        with tracer.tool_span(tool_name=tool_name, input_data=tool_input) as span:
            try:
                # 通过 handler_registry 执行
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

                # 获取执行期间产生的新日志（WARNING/ERROR/CRITICAL）
                all_logs = log_buffer.get_logs(count=500)
                new_logs = [
                    log
                    for log in all_logs[logs_before_count:]
                    if log["level"] in ("WARNING", "ERROR", "CRITICAL")
                ]

                # 如果有警告/错误日志，附加到结果
                if new_logs:
                    log_text = "\n\n[执行日志]:\n" + "".join(
                        f"[{log['level']}] {log['module']}: {log['message']}\n"
                        for log in new_logs[-10:]
                    )
                    if isinstance(result, list):
                        result.append({"type": "text", "text": log_text})
                    else:
                        result += log_text

                # ★ 通用截断守卫：工具自身未做截断时的安全网
                if isinstance(result, str):
                    result = self._guard_truncate(tool_name, result)
                self._observe_current_turn_tool_result(tool_name, tool_input, result)

                span.set_attribute("result_length", len(str(result)))

                await self._dispatch_hook(
                    "on_after_tool_use",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=result,
                )
                self._record_experience(
                    tool_name,
                    tool_input,
                    str(result),
                    success=True,
                    duration_ms=(time.monotonic() - started_at) * 1000,
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
                self._record_experience(
                    tool_name,
                    tool_input,
                    error_result,
                    success=False,
                    duration_ms=(time.monotonic() - started_at) * 1000,
                    error_type=e.error_type.value,
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
                self._record_experience(
                    tool_name,
                    tool_input,
                    error_result,
                    success=False,
                    duration_ms=(time.monotonic() - started_at) * 1000,
                    error_type=tool_error.error_type.value,
                )
                return error_result

    def _record_experience(
        self,
        tool_name: str,
        tool_input: dict,
        result: str,
        *,
        success: bool,
        duration_ms: float,
        error_type: str = "",
    ) -> None:
        try:
            # run_skill_script resolves its effective environment inside the
            # skills handler; the agent-level spec here can be misleading.
            if tool_name == "run_skill_script":
                return

            from ..experience import get_tool_experience_tracker
            from ..runtime_envs import describe_execution_env

            agent = getattr(self, "_agent_ref", None)
            spec = getattr(agent, "_execution_env_spec", None)
            env_info = describe_execution_env(spec)
            get_tool_experience_tracker().record(
                tool_name=tool_name,
                agent_profile_id=getattr(agent, "_agent_profile_id", "default"),
                env_scope=str(env_info.get("scope") or ""),
                deps_hash=str(env_info.get("deps_hash") or ""),
                success=success,
                duration_ms=duration_ms,
                error_type=error_type,
                output=result,
                input_summary=tool_input,
            )
        except Exception:
            logger.debug("Failed to record tool experience for %s", tool_name, exc_info=True)

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

        grounding_block = self._check_current_turn_grounding(tool_name, tool_input)
        if grounding_block:
            return grounding_block

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
                f"[沙箱执行 backend={sb_result.backend}]\nExit code: {sb_result.returncode}\n"
            )
            if sb_result.stdout:
                sandbox_output += f"stdout:\n{sb_result.stdout}\n"
            if sb_result.stderr:
                sandbox_output += f"stderr:\n{sb_result.stderr}\n"
            return self._guard_truncate(tool_name, sandbox_output)

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

        # C9c-1: emit `tool_intent_preview` SSE per tool call BEFORE we run
        # any of them. UI gets to show "about to call X" with sanitized
        # params + the inferred ApprovalClass, all in one batch up-front.
        # Best-effort: never blocks, never raises (catches all + logs DEBUG).
        self._emit_tool_intent_previews(tool_calls, session_id)

        async def _run_one(tc: dict, idx: int) -> tuple[int, dict, str | None, list | None]:
            tool_name = self._canonicalize_tool_name(tc.get("name", ""))
            tool_input = tc.get("input", tc.get("arguments", {})) or {}
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
                # C12 §14.3 unattended branch: when PolicyEngineV2 step 11
                # returned DEFER (defer_to_owner / defer_to_inbox / ask_owner),
                # ``metadata.is_unattended_path`` is True. We must NOT block
                # the loop waiting for user (no human attached) and must NOT
                # lie to the LLM ("已通知用户" — §2.1 bug). Instead:
                #   1. Persist a PendingApproval entry (atomic + SSE event)
                #   2. Return a tool_result that tells LLM **the truth**:
                #      task is paused awaiting owner approval
                #   3. Mark the result with ``_deferred_approval_id`` so the
                #      Ralph loop in agent.py / scheduler can raise
                #      ``DeferredApprovalRequired`` and halt the task
                #      cleanly (instead of letting LLM re-try / use ask_user)
                if perm_decision.metadata.get("is_unattended_path"):
                    pending_marker = await self._defer_unattended_confirm(
                        tool_use_id=tool_use_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        perm_decision=perm_decision,
                        session_id=session_id or "",
                        task_id=getattr(state, "task_id", None) if state else None,
                    )
                    return (idx, pending_marker, None, None)

                # C8b-3：dedup key 从 hash(tool, command, path) 改为 tool_use_id。
                #
                # 旧实现命中 dedup 后调 ``mark_confirmed`` 写 v1 _session_allowlist——
                # 这是"用户没在 UI 真的确认 → tool_executor 自作主张允许"的安全
                # 漏洞。新行为：tool_use_id 是 LLM 单次 tool block 的唯一 id；
                # 正常 LLM retry 会生成新 id（命中不到本分支，会重新走 confirm
                # 流程让用户再决策一次）。本 dedup 只防御"reasoning_engine 因
                # bug 重复消费同一 tool_use 块"的极端场景：直接返回 idle 错误，
                # **不**触发 _security_confirm metadata（避免 reasoning_engine
                # 重发 SSE 给 UI 弹两次卡片）。
                #
                # 真正的"session 内同一工具免 confirm"语义由 SessionAllowlistManager
                # 在 PolicyEngineV2 step 9 提供——用户点 "allow_session" 后第二次
                # 调用走 ALLOW 直接绕过本 confirm 分支。
                if tool_use_id and tool_use_id in self._pending_confirms:
                    logger.info(
                        "[Security] tool_use_id %s already pending—suppress dup "
                        "confirm SSE (tool=%s)",
                        tool_use_id[:8],
                        tool_name,
                    )
                    return (
                        idx,
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": "⚠️ 该工具调用的确认已在等待中，请勿重复触发。",
                            "is_error": True,
                        },
                        None,
                        None,
                    )

                if tool_use_id:
                    self._pending_confirms[tool_use_id] = {
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
                            "已向用户发送确认请求，请等待用户通过界面做出决定后再继续。"
                            "不要使用 ask_user 工具重复询问。"
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

                result_content = result if result is not None else "操作已完成"
                result_str = str(result_content)

                # execute_tool 内部捕获所有异常并返回字符串，不会抛到这里。
                # 对于 PARSE_ERROR_KEY（参数截断）路径，需要在此修正 success
                # 标志，使 tool_result 的 is_error 正确传播到 reasoning_engine。
                from ..llm.converters.tools import PARSE_ERROR_KEY

                if isinstance(tool_input, dict) and PARSE_ERROR_KEY in tool_input:
                    success = False

                if isinstance(result_str, str) and result_str.startswith("⚠️ 工具执行被中断:"):
                    success = False
                if isinstance(result_str, str) and result_str.lstrip().startswith("❌"):
                    success = False

                if success and isinstance(result_str, str) and result_str.lstrip().startswith("{"):
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(result_str.lstrip())
                        if isinstance(payload, dict) and payload.get("error") is True:
                            success = False
                    except Exception:
                        pass

                # 终端输出工具返回结果（便于调试与观察）
                _preview = (
                    result_str if len(result_str) <= 800 else result_str[:800] + "\n... (已截断)"
                )
                try:
                    logger.info(f"[Tool] {tool_name} → {_preview}")
                except (UnicodeEncodeError, OSError):
                    logger.info(f"[Tool] {tool_name} → (result logged, {len(result_str)} chars)")

                # 捕获交付回执：deliver_artifacts 直接交付；
                # org_submit_deliverable 是子节点向上级提交附件；
                # org_accept_deliverable 是父节点验收下级已带文件的交付物。
                # 三者都算 TaskVerify 眼里的有效交付证据。
                if (
                    capture_delivery_receipts
                    and tool_name in (
                        "deliver_artifacts",
                        "org_submit_deliverable",
                        "org_accept_deliverable",
                    )
                    and result_str
                ):
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
                        if isinstance(rs, list) and rs:
                            receipts = rs
                    except Exception:
                        pass

            except ToolSkipped as e:
                skip_reason = e.reason or "用户请求跳过"
                result_str = f"[用户跳过了此步骤: {skip_reason}]"
                logger.info(f"[SkipStep] Tool {tool_name} skipped: {skip_reason}")
                if use_parallel_safe_monitor and task_monitor:
                    task_monitor.record_tool_call(
                        tool_name,
                        tool_input,
                        result_str,
                        success=True,
                        duration_ms=int((time.time() - t0) * 1000),
                    )
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
                result_content = result_str
                logger.error(f"Tool batch execution error: {tool_name}: {e}")
                logger.info(f"[Tool] {tool_name} ❌ 错误: {result_str}")

            elapsed = time.time() - t0

            # 记录到 task_monitor
            if use_parallel_safe_monitor and task_monitor:
                task_monitor.record_tool_call(
                    tool_name,
                    tool_input,
                    result_str,
                    success=success,
                    duration_ms=int(elapsed * 1000),
                )
            elif (not parallel_enabled) and task_monitor:
                task_monitor.end_tool_call(result_str, success)

            tool_result = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_content,
                "receipt_id": f"tool_{uuid.uuid4().hex[:12]}",
                "tool_name": tool_name,
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
                        results.append(
                            (
                                j,
                                {
                                    "type": "tool_result",
                                    "tool_use_id": remaining_tc.get("id", ""),
                                    "content": "[任务已被用户停止]",
                                    "is_error": True,
                                },
                                None,
                                None,
                            )
                        )
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
        max_chars = _get_tool_result_max_chars()
        if not result or len(result) <= max_chars:
            return result
        if OVERFLOW_MARKER in result:
            return result  # 工具自己已处理

        overflow_path = save_overflow(tool_name, result)
        total_chars = len(result)
        truncated = result[:max_chars]
        read_limit = _get_read_file_default_limit()
        hint = (
            f"\n\n{OVERFLOW_MARKER} 工具 '{tool_name}' 输出共 {total_chars} 字符，"
            f"已截断到前 {max_chars} 字符。\n"
            f"完整输出已保存到: {overflow_path}\n"
            f'使用 read_file(path="{overflow_path}", offset=1, limit={read_limit}) 查看完整内容。'
        )
        logger.info(
            f"[Guard] Truncated {tool_name} output: {total_chars} → {max_chars} chars, "
            f"overflow saved to {overflow_path}"
        )
        return truncated + hint

    def _check_current_turn_grounding(self, tool_name: str, tool_input: dict) -> str | None:
        """Prevent latest-turn objects from being confused with historical ones."""
        agent = self._agent_ref
        current_turn = getattr(agent, "_current_turn_input", None) if agent is not None else None
        if current_turn is None or not hasattr(current_turn, "validate_tool_call"):
            return None
        try:
            return current_turn.validate_tool_call(tool_name, tool_input or {})
        except Exception as exc:
            logger.debug("[CurrentTurn] grounding check skipped: %s", exc)
            return None

    def _observe_current_turn_tool_result(
        self,
        tool_name: str,
        tool_input: dict,
        result: Any,
    ) -> None:
        """Update current-turn grounding state after stateful tool calls."""
        agent = self._agent_ref
        current_turn = getattr(agent, "_current_turn_input", None) if agent is not None else None
        if current_turn is None or not hasattr(current_turn, "observe_tool_result"):
            return
        try:
            current_turn.observe_tool_result(tool_name, tool_input or {}, result)
        except Exception as exc:
            logger.debug("[CurrentTurn] result observation skipped: %s", exc)

    def _check_todo_required(self, tool_name: str, session_id: str | None) -> str | None:
        """
        检查是否需要先创建 Todo（仅 Agent 模式下的 todo 跟踪）。

        Todo 是任务管理提示，不应成为工具执行的硬门槛。旧逻辑会在复合任务未
        创建 Todo 时拒绝 read_file/run_shell/browser 等工具，导致模型反复收到
        “请先创建 Todo”的长提示，反而阻碍用户任务推进。

        现在统一放行，由 prompt 和 Todo 工具自身引导模型在合适时主动建计划。

        Returns:
            始终返回 None（允许执行）。
        """
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
                reason="权限检查异常，已阻止操作。",
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

    # ---- C9c-1: tool_intent_preview SSE ----

    _PREVIEW_PARAM_MAX_CHARS = 200  # truncate per-value to avoid SSE bloat
    _PREVIEW_REDACT_KEYS = frozenset(
        {
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "private_key",
            "credential",
            "credentials",
            "auth",
            "authorization",
        }
    )

    def _sanitize_preview_params(self, raw: Any) -> Any:
        """Best-effort, recursive trim + redact for SSE preview.

        - Redacts well-known secret keys (case-insensitive substring).
        - Truncates strings to ``_PREVIEW_PARAM_MAX_CHARS`` with ellipsis.
        - Walks dict/list; leaves other types untouched (json.dumps will
          raise upstream if non-serializable, which is the right loud signal).
        """
        if isinstance(raw, dict):
            out: dict[str, Any] = {}
            for k, v in raw.items():
                key_lc = str(k).lower()
                if any(red in key_lc for red in self._PREVIEW_REDACT_KEYS):
                    out[k] = "***REDACTED***"
                else:
                    out[k] = self._sanitize_preview_params(v)
            return out
        if isinstance(raw, list):
            return [self._sanitize_preview_params(v) for v in raw]
        if isinstance(raw, str) and len(raw) > self._PREVIEW_PARAM_MAX_CHARS:
            return raw[: self._PREVIEW_PARAM_MAX_CHARS] + "...[truncated]"
        return raw

    def _emit_tool_intent_previews(
        self,
        tool_calls: list[dict],
        session_id: str | None,
    ) -> None:
        """Emit one ``tool_intent_preview`` SSE event per tool call.

        Schema:
            { event: "tool_intent_preview", data: {
                tool_use_id, tool_name, params (sanitized),
                approval_class (predicted via the same registry the
                production engine uses; "unknown" for tools without an
                explicit declaration), session_id, batch_size, batch_idx,
                ts
            }}

        ApprovalClass source: ``default_handler_registry.get_tool_class``
        — the exact same lookup that ``policy_v2.global_engine`` plumbs
        into the production ``ApprovalClassifier``'s ``explicit_lookup``.
        Using this lookup directly (instead of building a fresh
        ``ApprovalClassifier()`` per batch) keeps the preview honest
        with the actual decision the engine will produce later in the
        same call, AND skips the per-batch classifier construction +
        LRU cache that ``ApprovalClassifier()`` allocates internally.
        Tools that route through skills / MCP / plugin lookups still
        show "unknown" in the preview — that's accurate; the production
        engine will resolve them too, but only at decision time.

        Failure-mode: any exception is swallowed at DEBUG level; the SSE
        bridge is best-effort and must never block tool execution.
        """
        if not tool_calls:
            return

        try:
            from ..api.routes.websocket import fire_event
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ToolExec] tool_intent_preview skipped (no WS): %s", exc)
            return

        # Same registry the engine uses (handlers/__init__.py).
        try:
            from ..tools.handlers import default_handler_registry as _registry
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ToolExec] handler registry unavailable: %s", exc)
            _registry = None

        ts = time.time()
        total = len(tool_calls)
        for idx, tc in enumerate(tool_calls):
            try:
                tool_name = self._canonicalize_tool_name(tc.get("name", ""))
                tool_input = tc.get("input", tc.get("arguments", {})) or {}
                tool_use_id = tc.get("id", "")
                approval_class_str = "unknown"
                if _registry is not None:
                    try:
                        ac = _registry.get_tool_class(tool_name)
                        if ac is not None:
                            approval_class_str = (
                                ac.value if hasattr(ac, "value") else str(ac)
                            )
                    except Exception:
                        # Unknown / dynamic tool → preview just says "unknown".
                        # UI renders that with a neutral badge; engine still
                        # makes the real call at decision time.
                        approval_class_str = "unknown"

                payload = {
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "params": self._sanitize_preview_params(tool_input),
                    "approval_class": approval_class_str,
                    "session_id": session_id,
                    "batch_size": total,
                    "batch_idx": idx,
                    "ts": ts,
                }
                fire_event("tool_intent_preview", payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[ToolExec] tool_intent_preview emit failed for %r: %s",
                    tc.get("name"),
                    exc,
                )

    async def _defer_unattended_confirm(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict,
        perm_decision: "PermissionDecision",
        session_id: str,
        task_id: str | None,
    ) -> dict:
        """C12 §14.5: persist a pending_approval and return an honest tool_result.

        Called from ``execute_batch._run_one`` when a CONFIRM decision is
        coupled with ``metadata.is_unattended_path == True`` (i.e. the engine
        routed through ``_handle_unattended`` and decided owner approval is
        required).

        Behavior:
        - Atomic write to ``data/scheduler/pending_approvals.json``
        - Emit ``pending_approval_created`` SSE event (via Store hook)
        - Return a tool_result containing both LLM-readable text (so the LLM
          stops trying) AND a ``_deferred_approval_id`` field that the
          surrounding Ralph loop / scheduler picks up to halt the task

        Failure mode (Store unavailable / disk full):
        - Catch + log + return a deny-shaped tool_result with reason. Never
          fall back to the §2.1 "lying" path.
        """
        from .pending_approvals import get_pending_approvals_store
        from .policy_v2.context import get_current_context

        ctx = get_current_context()
        unattended_strategy = (
            ctx.unattended_strategy if ctx is not None else "defer_to_owner"
        ) or "defer_to_owner"
        approval_class = perm_decision.metadata.get("approval_class")
        decision_chain = list(perm_decision.decision_chain or [])
        # Capture the user_message so resume can write a ReplayAuthorization
        # that engine step 7 matches by equality (see api/routes/pending_approvals.py).
        captured_msg = (ctx.user_message if ctx is not None else "") or ""

        try:
            store = get_pending_approvals_store()
            entry = store.create(
                task_id=task_id,
                session_id=session_id or (ctx.session_id if ctx else "unknown"),
                tool_name=tool_name,
                params=tool_input if isinstance(tool_input, dict) else {},
                approval_class=str(approval_class) if approval_class else None,
                decision_chain=decision_chain,
                decision_meta=dict(perm_decision.metadata),
                reason=perm_decision.reason or "owner approval required",
                unattended_strategy=unattended_strategy,
                user_message=captured_msg,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[ToolExec] Failed to persist pending_approval for %s/%s: %s — "
                "denying tool call (fail-closed, do NOT lie to LLM)",
                tool_name,
                tool_use_id[:8] if tool_use_id else "no_id",
                exc,
                exc_info=True,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": (
                    "⚠️ 无人值守审批写入失败，工具调用已拒绝以保护安全。"
                    f"详情: {exc}"
                ),
                "is_error": True,
            }

        logger.info(
            "[ToolExec] Created pending_approval %s for %s "
            "(strategy=%s, session=%s, task=%s)",
            entry.id,
            tool_name,
            unattended_strategy,
            session_id or "?",
            task_id or "ad-hoc",
        )
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": (
                f"⏸️ 工具调用 '{tool_name}' 需要 owner 批准 (策略={unattended_strategy})。"
                f"\n已创建待审批记录 {entry.id}，本任务暂停等待人工决定。"
                "\n不要继续尝试此工具或绕路重试，等待 owner 通过 IM 卡片 / "
                "PendingApprovalsView 做决策。"
            ),
            "is_error": True,
            "_deferred_approval_id": entry.id,
            "_deferred_approval_strategy": unattended_strategy,
        }

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
                "已向用户发送确认请求，请等待用户通过界面做出决定后再继续。"
                "不要使用 ask_user 工具重复询问。"
            )
        return decision.reason
