"""
Priority TaskQueue for multi-agent task management.

Provides async priority-based task scheduling with cancellation support.
"""

import asyncio
import heapq
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Task priority levels. Lower value = higher priority."""

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass(order=True)
class QueuedTask:
    """A task in the priority queue."""

    priority: int
    created_at: float = field(compare=True)
    task_id: str = field(default_factory=lambda: f"qt_{uuid.uuid4().hex[:10]}", compare=False)
    agent_profile_id: str = field(default="default", compare=False)
    session_key: str = field(default="", compare=False)
    payload: dict = field(default_factory=dict, compare=False)
    cancelled: bool = field(default=False, compare=False)
    status: str = field(default="queued", compare=False)
    parent_task_id: str = field(default="", compare=False)
    blocked_by: list[str] = field(default_factory=list, compare=False)
    locked_by: str = field(default="", compare=False)
    locked_at: float = field(default=0.0, compare=False)
    lease_expires_at: float = field(default=0.0, compare=False)
    failure_reason: str = field(default="", compare=False)


class TaskQueue:
    """
    Async priority task queue with cancellation and metrics.

    Usage:
        queue = TaskQueue(max_concurrent=3)
        await queue.start()
        task_id = await queue.enqueue("session_key", "agent_id", payload, Priority.NORMAL)
        result = await queue.wait_for(task_id)
        await queue.stop()
    """

    def __init__(self, max_concurrent: int = 5):
        self._heap: list[QueuedTask] = []
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._results: dict[str, asyncio.Future] = {}
        self._active: dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent
        self._handler: Callable[[QueuedTask], Awaitable[Any]] | None = None
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._total_enqueued = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        self._task_index: dict[str, QueuedTask] = {}

    def set_handler(self, handler: Callable[[QueuedTask], Awaitable[Any]]) -> None:
        """Set the function that processes each task."""
        self._handler = handler

    @staticmethod
    def _audit_task(event: str, task: QueuedTask, reason: str = "") -> None:
        try:
            from openakita.core.audit_logger import get_audit_logger

            get_audit_logger().log_event(
                "task_transition",
                {
                    "transition": event,
                    "task_id": task.task_id,
                    "session_key": task.session_key,
                    "agent_profile_id": task.agent_profile_id,
                    "status": task.status,
                    "reason": reason,
                },
            )
        except Exception:
            pass

    async def start(self) -> None:
        """Start the queue worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("[TaskQueue] Started")

    async def stop(self) -> None:
        """Stop the queue worker, cancel active tasks, and resolve pending futures."""
        self._running = False
        self._not_empty.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass

        for _task_id, task in self._active.items():
            if not task.done():
                task.cancel()
        self._active.clear()

        # 清理堆中未执行的 Future，防止泄漏
        for qt in self._heap:
            fut = self._results.pop(qt.task_id, None)
            if fut and not fut.done():
                fut.cancel()
        self._heap.clear()

        # 清理任何残留的 Future
        for _tid, fut in list(self._results.items()):
            if not fut.done():
                fut.cancel()
        self._results.clear()

        logger.info("[TaskQueue] Stopped")

    async def enqueue(
        self,
        session_key: str,
        agent_profile_id: str,
        payload: dict,
        priority: Priority = Priority.NORMAL,
    ) -> str:
        """Add a task to the queue. Returns the task_id."""
        task = QueuedTask(
            priority=priority.value,
            created_at=time.time(),
            agent_profile_id=agent_profile_id,
            session_key=session_key,
            payload=payload,
        )
        async with self._lock:
            heapq.heappush(self._heap, task)
            self._task_index[task.task_id] = task
            self._results[task.task_id] = asyncio.get_running_loop().create_future()
            self._total_enqueued += 1
        self._not_empty.set()
        self._audit_task("queued", task)
        logger.debug(f"[TaskQueue] Enqueued {task.task_id} (priority={priority.name})")
        return task.task_id

    def mark_done(self, task_id: str, *, reason: str = "") -> bool:
        task = self._task_index.get(task_id)
        if not task:
            return False
        unfinished_children = [
            child.task_id
            for child in self._task_index.values()
            if child.parent_task_id == task_id and child.status not in ("done", "failed", "cancelled")
        ]
        if unfinished_children:
            task.blocked_by = unfinished_children
            return False
        task.status = "done"
        self._audit_task("done", task, reason)
        return True

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or active task."""
        async with self._lock:
            for t in self._heap:
                if t.task_id == task_id and not t.cancelled:
                    t.cancelled = True
                    t.status = "cancelled"
                    t.failure_reason = "cancelled"
                    self._total_cancelled += 1
                    fut = self._results.get(task_id)
                    if fut and not fut.done():
                        fut.cancel()
                    self._audit_task("cancelled", t, "queued_cancelled")
                    return True
        # Check active tasks
        active = self._active.get(task_id)
        if active and not active.done():
            active.cancel()
            if task := self._task_index.get(task_id):
                task.status = "cancelled"
                task.failure_reason = "cancelled"
                self._audit_task("cancelled", task, "active_cancelled")
            self._total_cancelled += 1
            return True
        return False

    async def wait_for(self, task_id: str, timeout: float = 120.0) -> Any:
        """Wait for a task result."""
        fut = self._results.get(task_id)
        if fut is None:
            raise KeyError(f"Unknown task: {task_id}")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._results.pop(task_id, None)

    async def _worker_loop(self) -> None:
        """Main worker loop: picks tasks from queue and executes them."""
        while self._running:
            async with self._lock:
                task = heapq.heappop(self._heap) if self._heap else None

            if task is None:
                self._not_empty.clear()
                await self._not_empty.wait()
                if not self._running:
                    break
                continue  # Go back and check heap under lock

            if task.cancelled:
                task.status = "cancelled"
                fut = self._results.pop(task.task_id, None)
                if fut and not fut.done():
                    fut.cancel()
                continue

            # Wait for concurrency slot
            while len(self._active) >= self._max_concurrent and self._active:
                tasks = list(self._active.values())
                if not tasks:
                    break
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finished_ids = [tid for tid, t in self._active.items() if t.done()]
                for tid in finished_ids:
                    self._active.pop(tid, None)

            self._active[task.task_id] = asyncio.create_task(self._execute_task(task))

    async def _execute_task(self, task: QueuedTask) -> None:
        """Execute a single task and resolve its future."""
        fut = self._results.get(task.task_id)
        try:
            if self._handler is None:
                raise RuntimeError("No handler set")
            task.status = "running"
            task.locked_by = "task_queue_worker"
            task.locked_at = time.time()
            task.lease_expires_at = task.locked_at + 300
            self._audit_task("running", task)
            result = await self._handler(task)
            if fut and not fut.done():
                fut.set_result(result)
            task.status = "done"
            self._audit_task("done", task)
            self._total_completed += 1
        except asyncio.CancelledError:
            if fut and not fut.done():
                fut.cancel()
            task.status = "cancelled"
            task.failure_reason = "cancelled"
            self._audit_task("cancelled", task)
            self._total_cancelled += 1
        except Exception as e:
            if fut and not fut.done():
                fut.set_exception(e)
            task.status = "failed"
            task.failure_reason = str(e)
            self._audit_task("failed", task, str(e))
            self._total_failed += 1
            logger.error(f"[TaskQueue] Task {task.task_id} failed: {e}")

    def get_stats(self) -> dict:
        """Get queue statistics."""
        return {
            "pending": len(self._heap),
            "active": len(self._active),
            "total_enqueued": self._total_enqueued,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_cancelled": self._total_cancelled,
            "max_concurrent": self._max_concurrent,
            "stale_leases": sum(
                1
                for task in self._task_index.values()
                if task.status == "running" and task.lease_expires_at and task.lease_expires_at < time.time()
            ),
        }
