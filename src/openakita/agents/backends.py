"""
Teammate/Swarm 多 Agent 后端

参考 Claude Code 的 AgentTool + Swarm 设计:
- InProcessBackend: 进程内并发 (asyncio.Task)
- SubprocessBackend: 独立进程执行
- Leader-Teammate 模型: team lead 分配任务

与现有 AgentOrchestrator 共存，逐步增强。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TeammateTask:
    """分配给 Teammate 的任务"""

    task_id: str
    description: str
    agent_id: str = ""
    isolation: str = "none"  # 'none' | 'worktree'
    max_turns: int = 20
    enable_thinking: bool = False


@dataclass
class TeammateResult:
    """Teammate 执行结果"""

    task_id: str
    agent_id: str
    success: bool
    output: str = ""
    error: str = ""
    tokens_used: int = 0
    worktree_path: str = ""
    worktree_branch: str = ""


class AgentBackend(ABC):
    """Agent 执行后端基类"""

    @abstractmethod
    async def run_teammate(
        self,
        task: TeammateTask,
        create_agent_fn: Callable,
    ) -> TeammateResult:
        """执行一个 Teammate 任务。"""
        pass

    @abstractmethod
    async def wait_all(self, timeout: float = 300) -> list[TeammateResult]:
        """等待所有正在执行的 Teammate 完成。"""
        pass


class InProcessBackend(AgentBackend):
    """进程内并发后端 (asyncio.Task)。

    Teammate 在同一进程内以 asyncio.Task 方式并行执行。
    共享内存但通过上下文隔离避免状态污染。
    """

    def __init__(self) -> None:
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, TeammateResult] = {}

    async def run_teammate(
        self,
        task: TeammateTask,
        create_agent_fn: Callable,
    ) -> TeammateResult:
        """启动 Teammate 任务。"""
        async def _execute():
            try:
                agent = await create_agent_fn(task.agent_id, task)
                output = await agent.run(task.description)
                return TeammateResult(
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    success=True,
                    output=str(output),
                )
            except Exception as e:
                logger.error("Teammate %s failed: %s", task.agent_id, e)
                return TeammateResult(
                    task_id=task.task_id,
                    agent_id=task.agent_id,
                    success=False,
                    error=str(e),
                )

        t = asyncio.create_task(_execute())
        self._running_tasks[task.task_id] = t

        result = await t
        self._results[task.task_id] = result
        del self._running_tasks[task.task_id]
        return result

    async def wait_all(self, timeout: float = 300) -> list[TeammateResult]:
        """等待所有 Teammate 完成。"""
        if not self._running_tasks:
            return list(self._results.values())

        tasks = list(self._running_tasks.values())
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("InProcessBackend: timeout waiting for %d tasks", len(tasks))

        return list(self._results.values())


class TeamManager:
    """团队管理器。

    管理多个 Teammate 的并行执行，支持:
    - 任务分配
    - 进度追踪
    - 结果聚合
    """

    def __init__(self, backend: AgentBackend | None = None) -> None:
        self._backend = backend or InProcessBackend()
        self._tasks: list[TeammateTask] = []
        self._results: list[TeammateResult] = []

    async def dispatch(
        self,
        tasks: list[TeammateTask],
        create_agent_fn: Callable,
    ) -> list[TeammateResult]:
        """分配并执行多个任务。

        并行启动所有任务，等待全部完成。
        """
        self._tasks = tasks

        # Launch all tasks concurrently
        coros = [
            self._backend.run_teammate(task, create_agent_fn)
            for task in tasks
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        self._results = []
        for r in results:
            if isinstance(r, TeammateResult):
                self._results.append(r)
            elif isinstance(r, Exception):
                self._results.append(TeammateResult(
                    task_id="unknown",
                    agent_id="unknown",
                    success=False,
                    error=str(r),
                ))

        return self._results

    @property
    def pending_count(self) -> int:
        return len(self._tasks) - len(self._results)
