"""
Task scheduler

Core scheduler:
- Manage task lifecycle
- Trigger task execution
- Persist tasks
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.atomic_io import safe_json_write, safe_write
from .task import ScheduledTask, TaskDurability, TaskExecution, TaskStatus, TriggerType
from .triggers import Trigger

logger = logging.getLogger(__name__)

# Executor type definition
TaskExecutorFunc = Callable[[ScheduledTask], Awaitable[tuple[bool, str]]]


class TaskScheduler:
    """
    Task scheduler

    Responsibilities:
    - Load and save tasks
    - Compute next run time
    - Trigger task execution
    - Handle execution results
    """

    def __init__(
        self,
        storage_path: Path | None = None,
        executor: TaskExecutorFunc | None = None,
        timezone: str = "Asia/Shanghai",
        max_concurrent: int = 5,
        check_interval_seconds: int = 2,  # Optimization: reduced from 10s to 2s for better reminder precision
        advance_seconds: int = 20,  # Seconds to run ahead of schedule, to compensate for Agent init and LLM call latency
    ):
        """
        Args:
            storage_path: task storage directory
            executor: task executor function
            timezone: timezone
            max_concurrent: maximum concurrent executions
            check_interval_seconds: check interval in seconds
        """
        self.storage_path = Path(storage_path) if storage_path else Path("data/scheduler")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.executor = executor
        self.timezone = timezone
        self.max_concurrent = max_concurrent
        self.check_interval = check_interval_seconds
        self.advance_seconds = advance_seconds  # Seconds to run ahead of schedule

        self._plugin_hooks = None

        # Task storage {task_id: ScheduledTask}
        self._tasks: dict[str, ScheduledTask] = {}

        # Trigger cache {task_id: Trigger}
        self._triggers: dict[str, Trigger] = {}

        # Execution records
        self._executions: list[TaskExecution] = []
        self._seen_execution_ids: set[str] = set()

        # Running state
        self._running = False
        self._scheduler_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()
        self._semaphore: asyncio.Semaphore | None = None

        # Concurrency guard lock: covers all write paths for _tasks/_triggers
        self._lock = asyncio.Lock()

        # Callback: fired when a task is auto-disabled after repeated failures
        self.on_task_auto_disabled: Callable[[ScheduledTask], Awaitable[None]] | None = None

        # Callback: summary notification when there are missed tasks at startup
        self.on_missed_tasks_summary: Callable[[list[ScheduledTask]], Awaitable[None]] | None = None

        # Load tasks
        self._load_tasks()
        self._load_executions()

    async def start(self) -> None:
        """Start the scheduler"""
        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        self._trim_executions_file()

        # Update next run time for tasks
        # Note: only tasks whose next_run is empty or has seriously overdue are recalculated,
        # to avoid immediate execution after a program restart
        now = datetime.now()
        missed_tasks: list[ScheduledTask] = []

        async with self._lock:
            for task in self._tasks.values():
                if task.is_active:
                    if task.next_run is None:
                        self._update_next_run(task)
                    elif task.next_run < now:
                        missed_tasks.append(task)
                        self._recalculate_missed_run(task, now)

            self._save_tasks()

        # Start the scheduler loop
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(f"TaskScheduler started with {len(self._tasks)} tasks")

        # Async-notify missed tasks
        if missed_tasks and self.on_missed_tasks_summary:
            asyncio.ensure_future(self._notify_missed_tasks(missed_tasks))

    async def _notify_missed_tasks(self, missed: list[ScheduledTask]) -> None:
        """Safely invoke the missed-tasks summary notification"""
        try:
            await self.on_missed_tasks_summary(missed)
        except Exception as e:
            logger.debug(f"on_missed_tasks_summary callback error: {e}")

    async def stop(self, graceful_timeout: float = 30.0) -> None:
        """Stop the scheduler, gracefully waiting for running tasks to complete"""
        self._running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler_task

        if self._running_tasks:
            running_ids = list(self._running_tasks)
            logger.info(
                f"Waiting for {len(running_ids)} running tasks to finish "
                f"(timeout={graceful_timeout}s): {running_ids}"
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + graceful_timeout
            while self._running_tasks and loop.time() < deadline:
                await asyncio.sleep(0.5)

            still_running = list(self._running_tasks)
            if still_running:
                logger.warning(
                    f"Force-stopping: {len(still_running)} tasks still running "
                    f"after {graceful_timeout}s timeout, resetting to SCHEDULED: {still_running}"
                )
                async with self._lock:
                    for tid in still_running:
                        task = self._tasks.get(tid)
                        if task and task.status == TaskStatus.RUNNING:
                            task.force_reset_to_scheduled(
                                reason=f"scheduler stop (timeout={graceful_timeout}s)"
                            )
                    self._running_tasks.clear()

        async with self._lock:
            # T1: Remove all SESSION tasks on stop
            session_ids = [
                tid for tid, t in self._tasks.items() if t.durability == TaskDurability.SESSION
            ]
            for tid in session_ids:
                self._tasks.pop(tid, None)
                self._triggers.pop(tid, None)
            if session_ids:
                logger.info(f"Cleared {len(session_ids)} SESSION task(s) on stop")

            self._save_tasks()

        logger.info("TaskScheduler stopped")

    # ==================== Task management ====================

    MAX_TASKS = 200  # Upper limit for user tasks, to prevent unbounded creation

    async def add_task(self, task: ScheduledTask) -> str:
        """
        Add a task

        Returns:
            Task ID

        Raises:
            ValueError: duplicate task ID or upper limit reached
        """
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task with id {task.id!r} already exists")

            user_tasks = [t for t in self._tasks.values() if t.deletable]
            if len(user_tasks) >= self.MAX_TASKS:
                raise ValueError(
                    f"Task count limit reached ({self.MAX_TASKS}); cancel unneeded tasks before creating new ones"
                )

            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)

            task.next_run = trigger.get_next_run_time()
            task.status = TaskStatus.SCHEDULED

            self._tasks[task.id] = task
            self._triggers[task.id] = trigger

            self._save_tasks()

        logger.info(f"Added task: {task.id} ({task.name}), next run: {task.next_run}")
        return task.id

    async def remove_task(self, task_id: str, force: bool = False) -> str:
        """
        Remove a task

        Args:
            task_id: task ID
            force: force delete (even for system tasks)

        Returns:
            "ok" on success, "not_found" if missing, "system_task" if it is a non-deletable system task
        """
        async with self._lock:
            if task_id not in self._tasks:
                return "not_found"

            task = self._tasks[task_id]

            if not task.deletable and not force:
                logger.warning(
                    f"Task {task_id} is a system task and cannot be deleted. Use disable instead."
                )
                return "system_task"

            task.cancel()

            del self._tasks[task_id]
            self._triggers.pop(task_id, None)

            self._save_tasks()

        logger.info(f"Removed task: {task_id}")
        return "ok"

    _UPDATABLE_FIELDS: set[str] = {
        "name",
        "description",
        "prompt",
        "reminder_message",
        "task_type",
        "trigger_type",
        "trigger_config",
        "channel_id",
        "chat_id",
        "user_id",
        "agent_profile_id",
        "metadata",
        "script_path",
        "action",
    }

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """Update a task (only whitelisted fields are allowed)"""
        async with self._lock:
            if task_id not in self._tasks:
                return False

            task = self._tasks[task_id]

            rejected = set(updates.keys()) - self._UPDATABLE_FIELDS
            if rejected:
                logger.warning(f"update_task({task_id}): rejected non-updatable fields: {rejected}")

            for key, value in updates.items():
                if key in self._UPDATABLE_FIELDS and hasattr(task, key):
                    setattr(task, key, value)

            task.updated_at = datetime.now()

            if "trigger_config" in updates or "trigger_type" in updates:
                trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
                self._triggers[task_id] = trigger
                task.next_run = trigger.get_next_run_time(task.last_run)

            self._save_tasks()

        logger.info(f"Updated task: {task_id}")
        return True

    async def enable_task(self, task_id: str) -> bool:
        """Enable a task"""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            task.fail_count = 0  # Bug-7: reset fail count to give the task a fresh start
            task.enable()
            self._update_next_run(task)
            self._save_tasks()
        return True

    async def disable_task(self, task_id: str) -> bool:
        """Disable a task"""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            task.disable()
            self._save_tasks()
        return True

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a task"""
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        user_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[ScheduledTask]:
        """List tasks"""
        tasks = list(self._tasks.values())

        if user_id:
            tasks = [t for t in tasks if t.user_id == user_id]
        if enabled_only:
            tasks = [t for t in tasks if t.enabled]

        return sorted(tasks, key=lambda t: t.next_run or datetime.max)

    async def save(self) -> None:
        """Public save interface (acquires the lock and saves, for callers that need to batch-update externally)"""
        async with self._lock:
            self._save_tasks()

    async def trigger_now(self, task_id: str) -> TaskExecution | None:
        """
        Trigger the task immediately (uses semaphore concurrency control, checks task state)

        Returns:
            Execution record, or None (task missing / unavailable / already running)
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        if not task.enabled:
            logger.warning(f"trigger_now: task {task_id} is disabled, skipping")
            return None

        if task_id in self._running_tasks:
            logger.warning(f"trigger_now: task {task_id} is already running, skipping")
            return None

        self._running_tasks.add(task_id)
        try:
            if self._semaphore:
                async with self._semaphore:
                    return await self._execute_task(task)
            else:
                return await self._execute_task(task)
        finally:
            self._running_tasks.discard(task_id)

    # ==================== Scheduler loop ====================

    @staticmethod
    def _deterministic_jitter(task_id: str, max_jitter_seconds: int = 10) -> float:
        """Deterministic jitter based on task_id, to prevent a thundering herd from multiple tasks firing simultaneously"""
        return (hash(task_id) % (max_jitter_seconds * 1000)) / 1000.0

    async def _scheduler_loop(self) -> None:
        """Scheduler loop"""
        while self._running:
            try:
                now = datetime.now()

                for task_id, task in list(self._tasks.items()):
                    if not task.is_active:
                        continue

                    if task_id in self._running_tasks:
                        continue

                    if task.next_run:
                        jitter = self._deterministic_jitter(task_id)
                        trigger_time = task.next_run - timedelta(
                            seconds=self.advance_seconds - jitter
                        )
                        if now >= trigger_time:
                            self._running_tasks.add(task_id)
                            asyncio.create_task(self._run_task_safe(task))

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(1)

    async def _run_task_safe(self, task: ScheduledTask) -> None:
        """
        Safely execute a task

        Note: _running_tasks has already been added in the scheduler loop;
        here we just need to run and clean up.
        """
        try:
            async with self._semaphore:
                await self._execute_task(task)
        finally:
            self._running_tasks.discard(task.id)

    async def _execute_task(self, task: ScheduledTask) -> TaskExecution:
        """Execute a task"""
        execution = TaskExecution.create(task.id)

        logger.info(f"Executing task: {task.id} ({task.name})")
        task.mark_running()

        if self._plugin_hooks:
            try:
                await self._plugin_hooks.dispatch("on_schedule", task=task, execution=execution)
            except Exception as e:
                logger.debug(f"on_schedule hook error: {e}")

        try:
            if self.executor:
                success, result_or_error = await self.executor(task)
                if success:
                    execution.finish(True, result=result_or_error)
                else:
                    execution.finish(False, error=result_or_error)
            else:
                execution.finish(True, result="No executor configured")

            if execution.status == "success":
                trigger = self._triggers.get(task.id)
                next_run = trigger.get_next_run_time(datetime.now()) if trigger else None
                task.mark_completed(next_run)
                logger.info(f"Task {task.id} completed successfully")
            else:
                self._handle_task_failure(task, execution.error or "Unknown error")

        except asyncio.CancelledError:
            execution.finish(False, error="Task was cancelled")
            task.mark_failed("Task was cancelled")
            self._advance_next_run(task)
            logger.warning(f"Task {task.id} was cancelled")

        except Exception as e:
            error_msg = str(e)
            execution.finish(False, error=error_msg)
            task.mark_failed(error_msg)
            self._advance_next_run(task)
            logger.error(f"Task {task.id} failed: {error_msg}", exc_info=True)

        async with self._lock:
            self._executions.append(execution)
            self._save_tasks()
            self._append_execution(execution)

        return execution

    def _handle_task_failure(self, task: ScheduledTask, error_msg: str) -> None:
        """Handle task failure: mark failure state and advance next_run"""
        was_enabled = task.enabled
        task.mark_failed(error_msg)
        self._advance_next_run(task)
        logger.warning(f"Task {task.id} reported failure: {error_msg}")

        # Detect whether it was just auto-disabled (mark_failed disables internally when fail_count>=5)
        if was_enabled and not task.enabled and self.on_task_auto_disabled:
            asyncio.ensure_future(self._notify_auto_disabled(task))

    async def _notify_auto_disabled(self, task: ScheduledTask) -> None:
        """Safely invoke the on_task_auto_disabled callback"""
        try:
            await self.on_task_auto_disabled(task)
        except Exception as e:
            logger.debug(f"on_task_auto_disabled callback error for {task.id}: {e}")

    def _advance_next_run(self, task: ScheduledTask) -> None:
        """Ensure next_run skips current advance window to prevent rapid retries within same trigger window"""
        trigger = self._triggers.get(task.id)
        if not trigger:
            return
        min_next = datetime.now() + timedelta(seconds=self.advance_seconds + 5)
        next_run = trigger.get_next_run_time(min_next)
        if next_run:
            task.next_run = next_run

    def _update_next_run(self, task: ScheduledTask) -> None:
        """Update task's next run time"""
        trigger = self._triggers.get(task.id)
        if not trigger:
            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
            self._triggers[task.id] = trigger

        task.next_run = trigger.get_next_run_time(task.last_run)

    def _recalculate_missed_run(self, task: ScheduledTask, now: datetime) -> None:
        """
        Recalculate next run time for tasks that missed their scheduled execution time

        Difference from _update_next_run:
        - Will not schedule for immediate execution (even if last_run is None)
        - Used to recover tasks after program restart
        - Records missed metadata for subsequent summary notification
        """
        trigger = self._triggers.get(task.id)
        if not trigger:
            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
            self._triggers[task.id] = trigger

        missed_at = task.next_run

        if task.trigger_type == TriggerType.ONCE:
            logger.info(f"One-time task {task.id} missed (was due at {missed_at})")
            task.status = TaskStatus.MISSED
            task.enabled = False
            task.metadata["missed_at"] = missed_at.isoformat() if missed_at else now.isoformat()
            return

        # For interval and cron tasks, record missed and advance to next occurrence
        task.metadata["last_missed_at"] = missed_at.isoformat() if missed_at else now.isoformat()
        missed_count = task.metadata.get("missed_count", 0)
        task.metadata["missed_count"] = missed_count + 1

        next_run = trigger.get_next_run_time(now)

        min_next_run = now + timedelta(seconds=60)
        if next_run and next_run < min_next_run:
            next_run = trigger.get_next_run_time(min_next_run)

        task.next_run = next_run
        logger.info(
            f"Recalculated next_run for task {task.id}: {next_run} "
            f"(missed at {missed_at}, total missed: {missed_count + 1})"
        )

    # ==================== Persistence ====================

    def _try_recover_json(self, target: Path) -> bool:
        """
        Attempt to recover from .bak or .tmp file when target is missing/corrupted.
        Returns whether recovery was attempted (success or failure both count as attempted).
        """
        bak = target.with_suffix(target.suffix + ".bak")
        tmp = target.with_suffix(target.suffix + ".tmp")

        # Do not recover if target file exists
        if target.exists():
            return False

        if bak.exists():
            with contextlib.suppress(Exception):
                os.replace(str(bak), str(target))
                logger.warning(f"Recovered {target.name} from backup")
                return True

        if tmp.exists():
            with contextlib.suppress(Exception):
                os.replace(str(tmp), str(target))
                logger.warning(f"Recovered {target.name} from temp file")
                return True

        return False

    def _load_tasks(self) -> None:
        """Load tasks"""
        tasks_file = self.storage_path / "tasks.json"

        # If file does not exist, attempt recovery (rename is non-atomic on Windows, may be lost during crash window)
        if not tasks_file.exists():
            self._try_recover_json(tasks_file)
        if not tasks_file.exists():
            return

        try:
            with open(tasks_file, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                logger.error(
                    f"tasks.json contains {type(data).__name__} instead of list, "
                    f"skipping load (file may be corrupt)"
                )
                return

            skipped_session = 0
            for item in data:
                try:
                    if not isinstance(item, dict):
                        logger.warning(f"Skipping non-dict task entry: {type(item).__name__}")
                        continue
                    task = ScheduledTask.from_dict(item)
                    # T1: SESSION tasks should not survive restart
                    if task.durability == TaskDurability.SESSION:
                        skipped_session += 1
                        continue
                    self._tasks[task.id] = task

                    trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
                    self._triggers[task.id] = trigger

                except Exception as e:
                    task_id = item.get("id", "?") if isinstance(item, dict) else "?"
                    logger.warning(f"Failed to load task {task_id}: {e}")
            if skipped_session:
                logger.info(f"Skipped {skipped_session} SESSION-durability task(s) on load")

            logger.info(f"Loaded {len(self._tasks)} tasks from storage")

        except Exception as e:
            logger.error(f"Failed to load tasks: {e}")

    def _load_executions(self) -> None:
        """Load execution records, supporting both legacy JSON array and new JSONL formats."""
        executions_file = self.storage_path / "executions.json"

        if not executions_file.exists():
            self._try_recover_json(executions_file)
        if not executions_file.exists():
            return

        try:
            loaded = []
            with open(executions_file, encoding="utf-8") as f:
                first_char = f.read(1)
                if not first_char:
                    return
                f.seek(0)

                if first_char == "[":
                    data = json.load(f)
                    for item in data or []:
                        with contextlib.suppress(Exception):
                            loaded.append(TaskExecution.from_dict(item))
                    self._executions = loaded[-1000:]
                    self._migrate_to_jsonl(executions_file)
                else:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            loaded.append(TaskExecution.from_dict(json.loads(line)))
                        except Exception:
                            logger.debug(f"Skipping corrupt execution line {line_num}")
                    self._executions = loaded[-1000:]

            self._seen_execution_ids = {e.id for e in self._executions}
            logger.info(f"Loaded {len(self._executions)} executions from storage")
        except Exception as e:
            logger.warning(f"Failed to load executions: {e}")

    def _migrate_to_jsonl(self, executions_file: Path) -> None:
        """One-time migration from legacy JSON array format to JSONL."""
        try:
            lines = []
            for e in self._executions:
                lines.append(json.dumps(e.to_dict(), ensure_ascii=False, default=str))
            content = "\n".join(lines) + "\n" if lines else ""
            safe_write(executions_file, content, backup=True, fsync=True)
            logger.info(f"Migrated executions.json to JSONL format ({len(lines)} records)")
        except Exception as e:
            logger.warning(f"Failed to migrate executions to JSONL: {e}")

    def _save_tasks(self) -> None:
        """Save tasks (SESSION durability tasks are excluded from persistence)."""
        tasks_file = self.storage_path / "tasks.json"

        try:
            data = [
                task.to_dict()
                for task in self._tasks.values()
                if task.durability != TaskDurability.SESSION
            ]
            safe_json_write(tasks_file, data, fsync=True)

        except Exception as e:
            logger.error(f"Failed to save tasks: {e}")

    def _append_execution(self, execution: TaskExecution) -> None:
        """Append single execution record to JSONL file (idempotent: skip already recorded ids)."""
        if execution.id in self._seen_execution_ids:
            logger.debug(f"Skipping duplicate execution append: {execution.id}")
            return
        from ..utils.atomic_io import append_jsonl

        executions_file = self.storage_path / "executions.json"
        try:
            append_jsonl(executions_file, execution.to_dict(), fsync=True)
            self._seen_execution_ids.add(execution.id)
        except Exception as e:
            logger.error(f"Failed to append execution: {e}")

    def _trim_executions_file(self) -> None:
        """Trim JSONL file at startup to prevent unbounded growth. Keep last 1000 lines."""
        executions_file = self.storage_path / "executions.json"
        if not executions_file.exists():
            return
        try:
            with open(executions_file, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= 2000:
                return
            recent = lines[-1000:]
            safe_write(executions_file, "".join(recent), backup=True, fsync=True)
            self._executions = self._executions[-1000:]
            self._seen_execution_ids = {e.id for e in self._executions}
            logger.info(f"Trimmed executions file: {len(lines)} -> {len(recent)} lines")
        except Exception as e:
            logger.warning(f"Failed to trim executions file: {e}")

    # ==================== Statistics ====================

    def get_stats(self) -> dict:
        """Get scheduler statistics"""
        active_tasks = [t for t in self._tasks.values() if t.is_active]

        return {
            "running": self._running,
            "total_tasks": len(self._tasks),
            "active_tasks": len(active_tasks),
            "running_tasks": len(self._running_tasks),
            "total_executions": len(self._executions),
            "by_type": {
                "once": len(
                    [t for t in self._tasks.values() if t.trigger_type == TriggerType.ONCE]
                ),
                "interval": len(
                    [t for t in self._tasks.values() if t.trigger_type == TriggerType.INTERVAL]
                ),
                "cron": len(
                    [t for t in self._tasks.values() if t.trigger_type == TriggerType.CRON]
                ),
            },
            "next_runs": [
                {
                    "id": t.id,
                    "name": t.name,
                    "next_run": t.next_run.isoformat() if t.next_run else None,
                }
                for t in sorted(active_tasks, key=lambda x: x.next_run or datetime.max)[:5]
            ],
        }

    def get_executions(
        self,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[TaskExecution]:
        """Get execution records"""
        executions = self._executions

        if task_id:
            executions = [e for e in executions if e.task_id == task_id]

        return executions[-limit:]
