"""
智能调度器 - 根据任务类型自动选择最优执行策略

提供：
- 自动检测任务是 I/O 密集型还是 CPU 密集型
- 根据任务类型自动选择 asyncio 或线程池执行
- 根据运行时指标动态调整线程池大小
- 混合执行能力，支持批量任务的高效调度
- 降级模式：检测失败时使用默认执行器
"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..config import settings

if TYPE_CHECKING:
    from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """任务类型枚举"""

    IO_BOUND = "io_bound"
    CPU_BOUND = "cpu_bound"
    MIXED = "mixed"


class SmartScheduler:
    """智能调度器 - 根据任务类型选择执行策略"""

    IO_TOOLS = {
        "read_file",
        "write_file",
        "list_directory",
        "glob",
        "grep",
        "browser_navigate",
        "browser_click",
        "http_request",
        "fetch_url",
        "search_memory",
        "list_recent_tasks",
        "list_mcp_servers",
        "mcp",
    }

    CPU_TOOLS = {
        "run_shell",
        "execute_code",
        "analyze_code",
        "run_tests",
        "compile",
        "build",
        "search_and_replace",
    }

    def __init__(self, tool_executor: "ToolExecutor | None" = None):
        self._tool_executor = tool_executor
        max_workers = getattr(settings, "agent_thread_pool_size", 4)
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._dynamic_adjustment = getattr(settings, "smart_scheduler_enabled", True)
        self._io_timeout = getattr(settings, "io_task_timeout", 120)
        self._cpu_timeout = getattr(settings, "cpu_task_timeout", 300)
        self._metrics: dict[str, Any] = {"avg_latency_ms": 0, "total_tasks": 0}
        logger.info(
            f"[SmartScheduler] Initialized: max_workers={max_workers}, "
            f"dynamic_adjustment={self._dynamic_adjustment}, "
            f"io_timeout={self._io_timeout}, cpu_timeout={self._cpu_timeout}"
        )

    def detect_task_type(self, tool_name: str, tool_input: dict | None = None) -> TaskType:
        """根据工具类型自动检测任务类型"""
        if tool_name in self.IO_TOOLS:
            logger.debug(f"[SmartScheduler] {tool_name} detected as IO_BOUND")
            return TaskType.IO_BOUND
        elif tool_name in self.CPU_TOOLS:
            logger.debug(f"[SmartScheduler] {tool_name} detected as CPU_BOUND")
            return TaskType.CPU_BOUND
        else:
            logger.debug(f"[SmartScheduler] {tool_name} detected as IO_BOUND (default)")
            return TaskType.IO_BOUND

    async def execute(
        self,
        func: Callable,
        *args: Any,
        task_type: TaskType | None = None,
        tool_name: str | None = None,
        tool_input: dict | None = None,
        **kwargs: Any,
    ) -> Any:
        """智能执行：根据任务类型选择最优策略"""
        if not getattr(settings, "smart_scheduler_enabled", True):
            return await self._default_execute(func, *args, **kwargs)

        if task_type is None and tool_name:
            task_type = self.detect_task_type(tool_name, tool_input)

        if task_type == TaskType.IO_BOUND:
            return await self._execute_io_bound(func, *args, **kwargs)
        elif task_type == TaskType.CPU_BOUND:
            return await self._execute_cpu_bound(func, *args, **kwargs)
        else:
            return await self._default_execute(func, *args, **kwargs)

    async def _execute_io_bound(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """I/O 密集型：使用 asyncio（不阻塞线程）"""
        timeout = kwargs.pop("timeout", self._io_timeout)

        try:
            if asyncio.iscoroutinefunction(func):
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            else:
                loop = asyncio.get_event_loop()
                return await asyncio.wait_for(
                    loop.run_in_executor(self._thread_pool, lambda: func(*args, **kwargs)),
                    timeout=timeout,
                )
        except TimeoutError:
            logger.warning(f"I/O task timeout after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"I/O execution error: {e}")
            raise

    async def _execute_cpu_bound(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """CPU 密集型：使用线程池（释放 GIL）"""
        timeout = kwargs.pop("timeout", self._cpu_timeout)
        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    self._thread_pool,
                    lambda: func(*args, **kwargs),
                ),
                timeout=timeout,
            )
            return result
        except TimeoutError:
            logger.warning(f"CPU task timeout after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"CPU execution error: {e}")
            raise

    async def _default_execute(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """默认执行器：保持原有行为"""
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(self._thread_pool, lambda: func(*args, **kwargs))

    async def execute_tool_smart(
        self,
        tool_name: str,
        tool_input: dict,
        **kwargs: Any,
    ) -> dict:
        """智能工具执行"""
        task_type = self.detect_task_type(tool_name, tool_input)

        if self._tool_executor:
            return await self.execute(
                self._tool_executor.execute_tool,
                tool_name,
                tool_input,
                task_type=task_type,
                tool_name=tool_name,
                tool_input=tool_input,
                **kwargs,
            )

        raise RuntimeError("No tool executor configured")

    async def execute_batch_smart(
        self,
        tool_calls: list[dict],
        **kwargs: Any,
    ) -> tuple[list[dict], list[str], list | None]:
        """智能批量工具执行"""
        if not self._tool_executor:
            raise RuntimeError("No tool executor configured")

        if not getattr(settings, "smart_scheduler_enabled", True):
            return await self._tool_executor.execute_batch(tool_calls, **kwargs)

        from .async_tool_executor import AsyncToolExecutor

        async_executor = AsyncToolExecutor(self._tool_executor)

        groups = async_executor.group_by_dependency(tool_calls)
        all_results = []
        executed_names: list[str] = []

        for group in groups:
            task_type = self.detect_task_type(group[0].get("name", ""), group[0].get("input", {}))

            if task_type == TaskType.CPU_BOUND:
                results = []
                for call in group:
                    result = await self.execute_tool_smart(
                        call.get("name", ""), call.get("input", {})
                    )
                    results.append(result)
                all_results.extend(results)
            else:
                results = await async_executor.execute_parallel(group)
                all_results.extend(results)

            executed_names.extend([c.get("name", "") for c in group])

        return all_results, executed_names, None

    async def execute_batch(
        self,
        tasks: list[tuple[Callable, dict]],
        task_type: TaskType = TaskType.IO_BOUND,
    ) -> list[Any]:
        """批量任务的高效调度"""
        if task_type == TaskType.IO_BOUND:

            async def run_task(task: tuple[Callable, dict]) -> Any:
                func, kw = task
                return await self._execute_io_bound(func, **kw)

            return await asyncio.gather(*[run_task(t) for t in tasks])
        else:
            sem = asyncio.Semaphore(getattr(settings, "tool_max_parallel", 4))

            async def run_task_limited(task: tuple[Callable, dict]) -> Any:
                async with sem:
                    func, kw = task
                    return await self._execute_cpu_bound(func, **kw)

            return await asyncio.gather(*[run_task_limited(t) for t in tasks])

    def adjust_thread_pool(self, metrics: dict | None = None) -> None:
        """根据运行时指标动态调整线程池大小"""
        if not self._dynamic_adjustment:
            return

        if metrics:
            self._metrics.update(metrics)

        avg_latency = self._metrics.get("avg_latency_ms", 0)
        current_size = self._thread_pool._max_workers

        if avg_latency > 1000 and current_size > 2:
            new_size = current_size - 1
            self._thread_pool._max_workers = new_size
            logger.info(f"Reducing thread pool to {new_size} due to high latency")
        elif avg_latency < 100 and current_size < 16:
            new_size = current_size + 1
            self._thread_pool._max_workers = new_size
            logger.info(f"Increasing thread pool to {new_size} due to low latency")

    def record_task_completion(self, latency_ms: float) -> None:
        """记录任务完成，更新指标"""
        total = self._metrics.get("total_tasks", 0)
        avg = self._metrics.get("avg_latency_ms", 0)

        self._metrics["total_tasks"] = total + 1
        self._metrics["avg_latency_ms"] = (avg * total + latency_ms) / (total + 1)

        if self._metrics["total_tasks"] % 10 == 0:
            self.adjust_thread_pool()

    def shutdown(self) -> None:
        """关闭线程池"""
        self._thread_pool.shutdown(wait=True)
