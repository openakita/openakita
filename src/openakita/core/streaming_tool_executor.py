"""
流式工具执行器

参考 Claude Code 的 StreamingToolExecutor 设计:
- 模型流式输出时，tool_use 块一到达就排队执行
- 只读并发安全工具可并行，非安全工具独占
- getCompletedResults() 在流式过程中返回已完成结果
- getRemainingResults() 等待全部完成
- Bash 错误触发 sibling abort
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SAFE_TOOLS = 5


@dataclass
class PendingToolCall:
    """排队中的工具调用"""

    tool_use_id: str
    tool_name: str
    tool_input: dict
    is_concurrency_safe: bool = False
    task: asyncio.Task | None = None
    result: str | None = None
    error: str | None = None
    completed: bool = False


class StreamingToolExecutor:
    """流式工具执行器。

    在模型流式输出过程中，每当一个 tool_use 块完整到达，
    立即开始执行。并发安全的工具可以并行执行。

    Usage:
        executor = StreamingToolExecutor(execute_fn, is_safe_fn)
        # During streaming:
        executor.add_tool(tool_use_id, tool_name, tool_input)
        # Get completed results without waiting:
        for result in executor.get_completed_results():
            yield result
        # After streaming ends, wait for remaining:
        remaining = await executor.get_remaining_results()
    """

    def __init__(
        self,
        execute_fn: Callable[..., Coroutine],
        is_concurrency_safe_fn: Callable[[str, dict], bool] | None = None,
    ) -> None:
        """
        Args:
            execute_fn: async (tool_name, tool_input) -> str
            is_concurrency_safe_fn: (tool_name, tool_input) -> bool
        """
        self._execute_fn = execute_fn
        self._is_safe_fn = is_concurrency_safe_fn or (lambda name, inp: False)
        self._queue: list[PendingToolCall] = []
        self._completed: list[PendingToolCall] = []
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_SAFE_TOOLS)
        self._abort_event = asyncio.Event()

    def add_tool(self, tool_use_id: str, tool_name: str, tool_input: dict) -> None:
        """添加一个工具调用到执行队列。"""
        is_safe = self._is_safe_fn(tool_name, tool_input)
        pending = PendingToolCall(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            is_concurrency_safe=is_safe,
        )
        self._queue.append(pending)
        self._schedule(pending)

    def _schedule(self, pending: PendingToolCall) -> None:
        """调度工具执行任务。"""
        pending.task = asyncio.create_task(self._run_tool(pending))

    async def _run_tool(self, pending: PendingToolCall) -> None:
        """执行单个工具。"""
        if self._abort_event.is_set():
            pending.error = "Aborted by sibling error"
            pending.completed = True
            self._completed.append(pending)
            return

        if pending.is_concurrency_safe:
            async with self._semaphore:
                await self._execute_one(pending)
        else:
            await self._execute_one(pending)

    async def _execute_one(self, pending: PendingToolCall) -> None:
        """执行单个工具并记录结果。"""
        try:
            result = await self._execute_fn(pending.tool_name, pending.tool_input)
            pending.result = str(result) if result is not None else ""
        except Exception as e:
            pending.error = str(e)
            if self._is_bash_error(pending.tool_name, e):
                logger.warning(
                    "Bash error in %s, aborting siblings: %s",
                    pending.tool_name, e,
                )
                self._abort_event.set()
        finally:
            pending.completed = True
            self._completed.append(pending)

    def get_completed_results(self) -> list[dict]:
        """获取已完成的工具结果（不阻塞）。

        Returns:
            按原始顺序排列的已完成结果列表
        """
        results = []
        newly_completed = []

        for pending in self._queue:
            if pending.completed and pending not in newly_completed:
                newly_completed.append(pending)
                results.append(self._to_result_dict(pending))

        return results

    async def get_remaining_results(self, timeout: float = 300.0) -> list[dict]:
        """等待所有工具执行完成并返回结果。

        Args:
            timeout: 总超时秒数

        Returns:
            所有工具结果的列表（按原始顺序）
        """
        tasks = [p.task for p in self._queue if p.task and not p.completed]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except TimeoutError:
                logger.warning("StreamingToolExecutor: timeout waiting for %d tools", len(tasks))

        return [self._to_result_dict(p) for p in self._queue]

    @property
    def pending_count(self) -> int:
        return sum(1 for p in self._queue if not p.completed)

    @property
    def completed_count(self) -> int:
        return sum(1 for p in self._queue if p.completed)

    @staticmethod
    def _to_result_dict(pending: PendingToolCall) -> dict:
        """将 PendingToolCall 转为结果 dict。"""
        return {
            "tool_use_id": pending.tool_use_id,
            "tool_name": pending.tool_name,
            "content": pending.result or pending.error or "",
            "is_error": pending.error is not None,
        }

    @staticmethod
    def _is_bash_error(tool_name: str, error: Exception) -> bool:
        """判断是否为 bash 执行错误（触发 sibling abort）。"""
        return tool_name in ("run_shell", "bash", "execute_command")
