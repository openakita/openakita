"""
Ralph Wiggum Loop Engine

References:
- https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum
- https://claytonfarr.github.io/ralph-playbook/

Core principles:
- Never stop until the task is complete
- Persist state via files
- Fresh context on each iteration
- Force self-correction through backpressure (test verification)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """Task definition."""

    id: str
    description: str
    session_id: str | None = None  # Associated session ID for isolating tasks across sessions
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 10
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error: str | None = None
    result: Any = None
    subtasks: list["Task"] = field(default_factory=list)

    def mark_in_progress(self) -> None:
        """Mark as in progress."""
        self.status = TaskStatus.IN_PROGRESS
        self.attempts += 1

    def mark_completed(self, result: Any = None) -> None:
        """Mark as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Mark as failed."""
        self.error = error
        if self.attempts >= self.max_attempts:
            self.status = TaskStatus.FAILED
        else:
            self.status = TaskStatus.PENDING  # Retriable

    @property
    def is_complete(self) -> bool:
        """Whether the task is complete."""
        return self.status == TaskStatus.COMPLETED

    @property
    def can_retry(self) -> bool:
        """Whether the task can be retried."""
        return (
            self.status in (TaskStatus.PENDING, TaskStatus.FAILED)
            and self.attempts < self.max_attempts
        )


@dataclass
class TaskResult:
    """Task execution result."""

    success: bool
    data: Any = None
    error: str | None = None
    iterations: int = 0
    duration_seconds: float = 0


class StopHook:
    """
    Stop Hook - Intercept exit attempts

    When the Agent tries to exit but the task is not yet complete, intercept and continue.
    """

    def __init__(self, task: Task):
        self.task = task
        self.intercepted_count = 0

    def should_stop(self) -> bool:
        """Check whether execution should stop."""
        if self.task.is_complete:
            return True

        if not self.task.can_retry:
            logger.warning(f"Task {self.task.id} cannot retry anymore")
            return True

        return False

    def intercept(self) -> bool:
        """
        Intercept an exit attempt.

        Returns:
            True if intercepted (should continue), False if should stop.
        """
        if self.should_stop():
            return False

        self.intercepted_count += 1
        logger.info(
            f"Stop hook intercepted exit attempt #{self.intercepted_count} for task {self.task.id}"
        )
        return True


class RalphLoop:
    """
    Ralph Wiggum Loop Engine

    Core loop logic:
    while not task.is_complete and iteration < max_iterations:
        1. Load state from MEMORY.md
        2. Execute one iteration
        3. Check the result
        4. If failed, analyze the cause and adjust strategy
        5. Save progress to MEMORY.md
        6. Continue to the next iteration
    """

    def __init__(
        self,
        max_iterations: int = 100,
        memory_path: Path | None = None,
        on_iteration: Callable[[int, Task], None] | None = None,
        on_error: Callable[[str, Task], None] | None = None,
    ):
        self.max_iterations = max_iterations
        self.memory_path = memory_path or settings.memory_path
        self.on_iteration = on_iteration
        self.on_error = on_error

        self._current_task: Task | None = None
        self._iteration = 0
        self._stop_hook: StopHook | None = None

    async def run(
        self,
        task: Task,
        execute_fn: Callable[[Task], Any],
    ) -> TaskResult:
        """
        Run the Ralph loop.

        Args:
            task: The task to execute.
            execute_fn: Execution function that receives a Task and returns a result or raises.

        Returns:
            TaskResult
        """
        self._current_task = task
        self._iteration = 0
        self._stop_hook = StopHook(task)

        start_time = datetime.now()

        logger.info(f"Ralph loop starting for task: {task.id}")
        logger.info(f"Max iterations: {self.max_iterations}")

        while self._iteration < self.max_iterations:
            self._iteration += 1

            # Check whether to stop
            if self._stop_hook.should_stop():
                break

            # Load progress
            await self._load_progress()

            # Notify iteration start
            if self.on_iteration:
                self.on_iteration(self._iteration, task)

            logger.info(f"Iteration {self._iteration}/{self.max_iterations}")

            # Mark task as in progress
            task.mark_in_progress()

            try:
                # Execute the task
                result = await execute_fn(task)

                # Execution succeeded
                task.mark_completed(result)
                logger.info(f"Task {task.id} completed successfully")

                # Save progress
                await self._save_progress()

                duration = (datetime.now() - start_time).total_seconds()
                return TaskResult(
                    success=True,
                    data=result,
                    iterations=self._iteration,
                    duration_seconds=duration,
                )

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Iteration {self._iteration} failed: {error_msg}")

                # Mark as failed
                task.mark_failed(error_msg)

                # Notify error
                if self.on_error:
                    self.on_error(error_msg, task)

                # Save progress
                await self._save_progress()

                # Attempt to intercept exit
                if not self._stop_hook.intercept():
                    break

                # Analyze error and adapt strategy
                await self._analyze_and_adapt(error_msg)

        # Loop ended but task not complete
        duration = (datetime.now() - start_time).total_seconds()

        if task.is_complete:
            return TaskResult(
                success=True,
                data=task.result,
                iterations=self._iteration,
                duration_seconds=duration,
            )
        else:
            return TaskResult(
                success=False,
                error=task.error or "Max iterations reached",
                iterations=self._iteration,
                duration_seconds=duration,
            )

    async def _load_progress(self) -> None:
        """Load progress from MEMORY.md (runs in a thread pool to avoid blocking the event loop)."""
        import asyncio

        await asyncio.to_thread(self._load_progress_sync)

    def _load_progress_sync(self) -> None:
        """Synchronously load progress."""
        try:
            if self.memory_path.exists():
                self.memory_path.read_text(encoding="utf-8")
                logger.debug("Progress loaded from MEMORY.md")
        except Exception as e:
            logger.warning(f"Failed to load progress: {e}")

    async def _save_progress(self) -> None:
        """Save progress to MEMORY.md (runs in a thread pool to avoid blocking the event loop)."""
        import asyncio

        if not self._current_task:
            return
        await asyncio.to_thread(self._save_progress_sync)

    def _save_progress_sync(self) -> None:
        """Synchronously save progress."""
        if not self._current_task:
            return

        try:
            content = ""
            if self.memory_path.exists():
                content = self.memory_path.read_text(encoding="utf-8")

            task = self._current_task
            session_line = f"- **Session**: {task.session_id}\n" if task.session_id else ""
            task_info = f"""### Active Task

- **ID**: {task.id}
{session_line}- **Description**: {task.description}
- **Status**: {task.status.value}
- **Attempts**: {task.attempts}
- **Last Updated**: {datetime.now().isoformat()}
"""

            if "### Active Task" in content:
                start = content.find("### Active Task")
                end = content.find("###", start + 1)
                if end == -1:
                    end = content.find("\n## ", start + 1)
                if end == -1:
                    end = len(content)
                content = content[:start] + task_info + content[end:]
            else:
                insert_pos = content.find("## Current Task Progress")
                if insert_pos != -1:
                    insert_pos = content.find("\n", insert_pos) + 1
                    content = content[:insert_pos] + "\n" + task_info + content[insert_pos:]

            from openakita.memory.types import MEMORY_MD_MAX_CHARS, truncate_memory_md

            if len(content) > MEMORY_MD_MAX_CHARS:
                logger.warning(
                    f"MEMORY.md exceeds limit after progress save "
                    f"({len(content)} > {MEMORY_MD_MAX_CHARS}), truncating"
                )
                content = truncate_memory_md(content, MEMORY_MD_MAX_CHARS)

            self.memory_path.write_text(content, encoding="utf-8")
            logger.debug("Progress saved to MEMORY.md")

        except Exception as e:
            logger.warning(f"Failed to save progress: {e}")

    async def _analyze_and_adapt(self, error: str) -> None:
        """
        Analyze the error and adapt strategy.

        This is the core of the Ralph pattern:
        - Analyze the failure cause
        - Search for solutions
        - Adjust strategy
        """
        logger.info("Analyzing error and adapting strategy...")

        # TODO: Implement smarter error analysis
        # 1. Use Brain to analyze the error
        # 2. Search GitHub for solutions
        # 3. Install new capabilities if needed
        # 4. Update execution strategy

        # For now, simply wait and retry
        import asyncio

        await asyncio.sleep(1)

    @property
    def iteration(self) -> int:
        """Current iteration count."""
        return self._iteration

    @property
    def current_task(self) -> Task | None:
        """Current task."""
        return self._current_task
