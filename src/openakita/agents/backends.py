"""
Teammate/Swarm multi-agent backends

Inspired by Claude Code's AgentTool + Swarm design:
- InProcessBackend: In-process concurrency (asyncio.Task)
- SubprocessBackend: Independent process execution
- Leader-Teammate model: team lead assigns tasks

Coexists with the existing AgentOrchestrator, incrementally enhanced.
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
    """Task assigned to a teammate"""

    task_id: str
    description: str
    agent_id: str = ""
    isolation: str = "none"  # 'none' | 'worktree'
    max_turns: int = 20
    enable_thinking: bool = False


@dataclass
class TeammateResult:
    """Teammate execution result"""

    task_id: str
    agent_id: str
    success: bool
    output: str = ""
    error: str = ""
    tokens_used: int = 0
    worktree_path: str = ""
    worktree_branch: str = ""


class AgentBackend(ABC):
    """Agent execution backend base class"""

    @abstractmethod
    async def run_teammate(
        self,
        task: TeammateTask,
        create_agent_fn: Callable,
    ) -> TeammateResult:
        """Execute a teammate task."""
        pass

    @abstractmethod
    async def wait_all(self, timeout: float = 300) -> list[TeammateResult]:
        """Wait for all running teammates to complete."""
        pass


class InProcessBackend(AgentBackend):
    """In-process concurrency backend (asyncio.Task).

    Teammates run in parallel as asyncio.Tasks within the same process.
    They share memory but avoid state pollution through context isolation.
    """

    def __init__(self) -> None:
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, TeammateResult] = {}

    async def run_teammate(
        self,
        task: TeammateTask,
        create_agent_fn: Callable,
    ) -> TeammateResult:
        """Start a teammate task."""

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
        """Wait for all teammates to complete."""
        if not self._running_tasks:
            return list(self._results.values())

        tasks = list(self._running_tasks.values())
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("InProcessBackend: timeout waiting for %d tasks", len(tasks))

        return list(self._results.values())


class TeamManager:
    """Team manager.

    Manages parallel execution of multiple teammates. Supports:
    - Task dispatch
    - Progress tracking
    - Result aggregation
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
        """Dispatch and execute multiple tasks.

        Launches all tasks in parallel, waits for all to complete.
        """
        self._tasks = tasks

        # Launch all tasks concurrently
        coros = [self._backend.run_teammate(task, create_agent_fn) for task in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        self._results = []
        for r in results:
            if isinstance(r, TeammateResult):
                self._results.append(r)
            elif isinstance(r, Exception):
                self._results.append(
                    TeammateResult(
                        task_id="unknown",
                        agent_id="unknown",
                        success=False,
                        error=str(r),
                    )
                )

        return self._results

    @property
    def pending_count(self) -> int:
        return len(self._tasks) - len(self._results)
