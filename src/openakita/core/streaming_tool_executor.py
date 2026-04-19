"""
Streaming Tool Executor

Inspired by Claude Code's StreamingToolExecutor design:
- When the model streams output, tool_use blocks are queued for execution as they arrive
- Read-only concurrency-safe tools run in parallel; non-safe tools run exclusively
- getCompletedResults() returns finished results during streaming
- getRemainingResults() waits for all results to complete
- Bash errors trigger sibling abort
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
    """Pending tool call"""

    tool_use_id: str
    tool_name: str
    tool_input: dict
    is_concurrency_safe: bool = False
    task: asyncio.Task | None = None
    result: str | None = None
    error: str | None = None
    completed: bool = False


class StreamingToolExecutor:
    """Streaming tool executor.

    During model streaming output, each complete tool_use block is
    executed immediately. Concurrency-safe tools run in parallel.

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
        """Add a tool call to the execution queue."""
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
        """Schedule a tool execution task."""
        pending.task = asyncio.create_task(self._run_tool(pending))

    async def _run_tool(self, pending: PendingToolCall) -> None:
        """Execute a single tool."""
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
        """Execute a single tool and record the result."""
        try:
            result = await self._execute_fn(pending.tool_name, pending.tool_input)
            pending.result = str(result) if result is not None else ""
        except Exception as e:
            pending.error = str(e)
            if self._is_bash_error(pending.tool_name, e):
                logger.warning(
                    "Bash error in %s, aborting siblings: %s",
                    pending.tool_name,
                    e,
                )
                self._abort_event.set()
        finally:
            pending.completed = True
            self._completed.append(pending)

    def get_completed_results(self) -> list[dict]:
        """Get completed tool results (non-blocking).

        Returns:
            List of completed results in original order
        """
        results = []
        newly_completed = []

        for pending in self._queue:
            if pending.completed and pending not in newly_completed:
                newly_completed.append(pending)
                results.append(self._to_result_dict(pending))

        return results

    async def get_remaining_results(self, timeout: float = 300.0) -> list[dict]:
        """Wait for all tools to finish and return results.

        Args:
            timeout: Total timeout in seconds

        Returns:
            List of all tool results (in original order)
        """
        tasks = [p.task for p in self._queue if p.task and not p.completed]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
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
        """Convert PendingToolCall to a result dict."""
        return {
            "tool_use_id": pending.tool_use_id,
            "tool_name": pending.tool_name,
            "content": pending.result or pending.error or "",
            "is_error": pending.error is not None,
        }

    @staticmethod
    def _is_bash_error(tool_name: str, error: Exception) -> bool:
        """Check if the error is a bash execution error (triggers sibling abort)."""
        return tool_name in ("run_shell", "bash", "execute_command")
