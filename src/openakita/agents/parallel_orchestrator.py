"""
多 Agent 并行编排器 - 扩展现有 AgentOrchestrator

提供：
- 同时运行多个独立的 Agent 处理不同子任务
- 为同步 Agent 提供线程池并行执行能力
- 支持收集异步执行结果并统一处理超时
- 依赖感知的调度器，处理有依赖关系的 Agent 链
- 混合执行模式：部分 Agent 串行、部分 Agent 并行
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..config import settings

if TYPE_CHECKING:
    from ..agents.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


class ParallelAgentOrchestrator:
    """多 Agent 并行编排器 - 扩展现有编排器"""

    def __init__(self, base_orchestrator: "AgentOrchestrator"):
        self._base = base_orchestrator
        max_workers = getattr(settings, "agent_thread_pool_size", 4)
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        logger.info(f"[ParallelAgentOrchestrator] Initialized: max_workers={max_workers}")

    async def execute_parallel_agents(
        self,
        agent_requests: list[dict],
        timeout: float = 300.0,
    ) -> list[dict]:
        """并行执行多个独立的 Agent 任务"""
        if not getattr(settings, "multi_agent_parallel_enabled", True):
            logger.info("[ParallelAgentOrchestrator] Disabled, falling back to serial execution")
            return await self._execute_serial(agent_requests)

        logger.info(
            f"[ParallelAgentOrchestrator] Starting parallel execution for "
            f"{len(agent_requests)} agents (timeout={timeout}s)"
        )

        async def _run_agent(req: dict) -> dict:
            agent_id = req.get("agent_id", "unknown")

            try:
                result = await asyncio.wait_for(
                    self._base.delegate(
                        session=req.get("session"),
                        from_agent=req.get("from_agent", "unknown"),
                        to_agent=req.get("to_agent", agent_id),
                        message=req.get("message", ""),
                        depth=req.get("depth", 0),
                        reason=req.get("reason", ""),
                    ),
                    timeout=timeout,
                )
                return {
                    "agent_id": agent_id,
                    "result": result,
                    "status": "completed",
                }
            except TimeoutError:
                logger.warning(f"Agent {agent_id} timeout after {timeout}s")
                return {
                    "agent_id": agent_id,
                    "result": None,
                    "status": "timeout",
                    "error": f"Timeout after {timeout}s",
                }
            except Exception as e:
                logger.error(f"Agent {agent_id} error: {e}")
                return {
                    "agent_id": agent_id,
                    "result": None,
                    "status": "error",
                    "error": str(e),
                }

        tasks = [_run_agent(req) for req in agent_requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append(
                    {
                        "agent_id": agent_requests[i].get("agent_id", "unknown"),
                        "result": None,
                        "status": "error",
                        "error": str(result),
                    }
                )
            else:
                processed.append(result)

        return processed

    async def _execute_serial(self, agent_requests: list[dict]) -> list[dict]:
        """串行执行（回退模式）"""
        results = []
        for req in agent_requests:
            try:
                result = await self._base.delegate(
                    session=req.get("session"),
                    from_agent=req.get("from_agent", "unknown"),
                    to_agent=req.get("to_agent", req.get("agent_id", "")),
                    message=req.get("message", ""),
                    depth=req.get("depth", 0),
                    reason=req.get("reason", ""),
                )
                results.append(
                    {
                        "agent_id": req.get("agent_id", "unknown"),
                        "result": result,
                        "status": "completed",
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "agent_id": req.get("agent_id", "unknown"),
                        "result": None,
                        "status": "error",
                        "error": str(e),
                    }
                )
        return results

    async def execute_mixed(
        self,
        serial_tasks: list[dict],
        parallel_tasks: list[dict],
    ) -> list[dict]:
        """混合执行：串行任务 + 并行任务"""
        results = []

        for task in serial_tasks:
            try:
                result = await self._base.delegate(
                    session=task.get("session"),
                    from_agent=task.get("from_agent", "unknown"),
                    to_agent=task.get("to_agent", task.get("agent_id", "")),
                    message=task.get("message", ""),
                    depth=task.get("depth", 0),
                    reason=task.get("reason", ""),
                )
                results.append(
                    {
                        "agent_id": task.get("agent_id", "unknown"),
                        "result": result,
                        "status": "completed",
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "agent_id": task.get("agent_id", "unknown"),
                        "result": None,
                        "status": "error",
                        "error": str(e),
                    }
                )

        if parallel_tasks:
            parallel_results = await self.execute_parallel_agents(parallel_tasks)
            results.extend(parallel_results)

        return results

    def schedule_by_dependency(self, agent_chain: list[dict]) -> list[list[dict]]:
        """依赖感知的调度：根据 Agent 依赖关系分组"""
        scheduled: list[list[dict]] = []
        remaining = list(range(len(agent_chain)))
        completed: set[int] = set()

        while remaining:
            ready = []
            for idx in remaining:
                deps = agent_chain[idx].get("depends_on", [])
                if all(d in completed for d in deps):
                    ready.append(idx)

            if not ready:
                ready = remaining

            scheduled.append([agent_chain[i] for i in ready])
            completed.update(ready)
            remaining = [i for i in remaining if i not in ready]

        return scheduled

    async def execute_with_dependency(
        self,
        agent_chain: list[dict],
        timeout: float = 300.0,
    ) -> list[dict]:
        """执行有依赖关系的 Agent 链"""
        scheduled = self.schedule_by_dependency(agent_chain)
        all_results: list[dict] = []

        for group in scheduled:
            group_results = await self.execute_parallel_agents(group, timeout=timeout)
            all_results.extend(group_results)

        return all_results

    def wrap_sync_agent(self, agent_instance: Any) -> Any:
        """将同步 Agent 实例包装为异步（线程池）"""
        loop = asyncio.get_event_loop()

        async def async_run(message: str) -> Any:
            return await loop.run_in_executor(
                self._thread_pool, lambda: agent_instance.run(message)
            )

        return async_run

    def shutdown(self) -> None:
        """关闭线程池"""
        self._thread_pool.shutdown(wait=True)
