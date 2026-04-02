"""
异步工具执行器 - 依赖分析 + 并行执行 + 线程池包装

扩展现有 ToolExecutor，提供：
- 工具调用依赖关系分析
- 基于依赖的分组执行（组内并行，组间串行）
- 线程池包装同步工具为异步调用
- 信号量控制的并行执行能力
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..config import settings

if TYPE_CHECKING:
    from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class AsyncToolExecutor:
    """异步工具执行器 - 扩展现有 ToolExecutor"""

    def __init__(self, sync_executor: "ToolExecutor"):
        self._sync_executor = sync_executor
        max_workers = getattr(settings, "tool_max_parallel", 4)
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._dependency_pattern = re.compile(
            getattr(settings, "dependency_pattern", r"\$\{(\w+)\.result\}")
        )
        logger.info(
            f"[AsyncToolExecutor] Initialized: max_workers={max_workers}, "
            f"pattern={self._dependency_pattern.pattern}"
        )

    def analyze_dependencies(self, tool_calls: list[dict]) -> dict[str, list[int]]:
        """分析工具调用间的依赖关系，返回 {tool_name: [indices]}"""
        dependencies: dict[str, list[int]] = {}

        for idx, call in enumerate(tool_calls):
            tool_input = call.get("input") or {}
            input_str = str(tool_input)

            matches = self._dependency_pattern.findall(input_str)
            if matches:
                logger.debug(f"[AsyncToolExecutor] Tool {call.get('name')} depends on: {matches}")
            for dep_tool in matches:
                tool_names = {c.get("name", ""): i for i, c in enumerate(tool_calls)}
                if dep_tool in tool_names:
                    if dep_tool not in dependencies:
                        dependencies[dep_tool] = []
                    dependencies[dep_tool].append(idx)

        if dependencies:
            logger.info(f"[AsyncToolExecutor] Found dependencies: {dependencies}")
        return dependencies

    def group_by_dependency(self, tool_calls: list[dict]) -> list[list[dict]]:
        """将工具调用分组：组内可并行，组间串行"""
        if not tool_calls:
            return []

        executed: set[int] = set()
        groups: list[list[dict]] = []
        remaining = list(range(len(tool_calls)))

        while remaining:
            ready = []
            for idx in remaining:
                tool_input = str(tool_calls[idx].get("input", {}))
                matches = self._dependency_pattern.findall(tool_input)

                tool_names_map = {call.get("name", ""): i for i, call in enumerate(tool_calls)}
                valid_matches = [m for m in matches if m in tool_names_map]

                if all(tool_names_map.get(dep) in executed for dep in valid_matches):
                    ready.append(idx)

            if not ready:
                groups.append([tool_calls[i] for i in remaining])
                break

            groups.append([tool_calls[i] for i in ready])
            executed.update(ready)
            remaining = [i for i in remaining if i not in ready]

        logger.info(
            f"[AsyncToolExecutor] Grouped {len(tool_calls)} tools into {len(groups)} groups"
        )
        for i, g in enumerate(groups):
            logger.debug(f"[AsyncToolExecutor] Group {i}: {[c.get('name') for c in g]}")
        return groups

    async def execute_parallel(
        self,
        tool_calls: list[dict],
        semaphore: int | None = None,
    ) -> list[dict]:
        """带信号量控制的并行执行"""
        max_concurrent = semaphore or getattr(settings, "tool_max_parallel", 4)
        sem = asyncio.Semaphore(max_concurrent)

        logger.info(
            f"[AsyncToolExecutor] Executing {len(tool_calls)} tools in parallel (max={max_concurrent})"
        )

        async def _execute_with_semaphore(call: dict) -> dict:
            async with sem:
                return await self._sync_executor.execute_tool(
                    call.get("name", ""),
                    call.get("input", {}),
                )

        tasks = [_execute_with_semaphore(call) for call in tool_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_calls[i].get("id", ""),
                        "content": f"[执行错误] {str(result)}",
                        "is_error": True,
                    }
                )
            else:
                processed_results.append(result)

        return processed_results

    def wrap_sync_tool(self, sync_func: callable) -> callable:
        """将同步工具包装为异步调用（线程池）"""
        loop = asyncio.get_event_loop()

        async def wrapper(*args, **kwargs):
            return await loop.run_in_executor(self._thread_pool, lambda: sync_func(*args, **kwargs))

        return wrapper

    async def execute_batch_async(
        self,
        tool_calls: list[dict],
        state: Any = None,
        task_monitor: Any = None,
        allow_interrupt_checks: bool = True,
        capture_delivery_receipts: bool = False,
    ) -> tuple[list[dict], list[str], list | None]:
        """异步批量执行（依赖感知 + 并行）"""
        if not getattr(settings, "async_tool_enabled", True):
            logger.info("[AsyncToolExecutor] Disabled, falling back to sync execution")
            return await self._sync_executor.execute_batch(
                tool_calls,
                state=state,
                task_monitor=task_monitor,
                allow_interrupt_checks=allow_interrupt_checks,
                capture_delivery_receipts=capture_delivery_receipts,
            )

        logger.info(
            f"[AsyncToolExecutor] Starting async batch execution for {len(tool_calls)} tools"
        )
        groups = self.group_by_dependency(tool_calls)
        all_results = []
        executed_names: list[str] = []

        for group in groups:
            group_results = await self.execute_parallel(group)
            all_results.extend(group_results)
            executed_names.extend([c.get("name", "") for c in group])

        logger.info(
            f"[AsyncToolExecutor] Completed: {len(all_results)} results, executed: {executed_names}"
        )
        return all_results, executed_names, None

    def shutdown(self) -> None:
        """关闭线程池"""
        self._thread_pool.shutdown(wait=True)
