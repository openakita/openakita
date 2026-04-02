"""
任务调度器

核心调度器:
- 管理任务生命周期
- 触发任务执行
- 任务持久化
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.atomic_io import safe_json_write
from .task import ScheduledTask, TaskExecution, TaskStatus, TriggerType
from .triggers import Trigger

logger = logging.getLogger(__name__)

# 执行器类型定义
TaskExecutorFunc = Callable[[ScheduledTask], Awaitable[tuple[bool, str]]]


class TaskScheduler:
    """
    任务调度器

    职责:
    - 加载和保存任务
    - 计算下一次运行时间
    - 触发任务执行
    - 处理执行结果
    """

    def __init__(
        self,
        storage_path: Path | None = None,
        executor: TaskExecutorFunc | None = None,
        timezone: str = "Asia/Shanghai",
        max_concurrent: int = 5,
        check_interval_seconds: int = 2,  # 优化：从 10 秒改为 2 秒，提高提醒精度
        advance_seconds: int = 20,  # 提前执行秒数，补偿 Agent 初始化和 LLM 调用延迟
    ):
        """
        Args:
            storage_path: 任务存储目录
            executor: 任务执行器函数
            timezone: 时区
            max_concurrent: 最大并发执行数
            check_interval_seconds: 检查间隔（秒）
        """
        self.storage_path = Path(storage_path) if storage_path else Path("data/scheduler")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.executor = executor
        self.timezone = timezone
        self.max_concurrent = max_concurrent
        self.check_interval = check_interval_seconds
        self.advance_seconds = advance_seconds  # 提前执行秒数

        self._plugin_hooks = None

        # 任务存储 {task_id: ScheduledTask}
        self._tasks: dict[str, ScheduledTask] = {}

        # 触发器缓存 {task_id: Trigger}
        self._triggers: dict[str, Trigger] = {}

        # 执行记录
        self._executions: list[TaskExecution] = []

        # 运行状态
        self._running = False
        self._scheduler_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()
        self._semaphore: asyncio.Semaphore | None = None

        # 并发保护锁：覆盖 _tasks/_triggers 所有写路径
        self._lock = asyncio.Lock()

        # 回调：任务因连续失败被自动禁用时触发
        self.on_task_auto_disabled: (
            Callable[[ScheduledTask], Awaitable[None]] | None
        ) = None

        # 回调：启动时有 missed 任务汇总通知
        self.on_missed_tasks_summary: (
            Callable[[list[ScheduledTask]], Awaitable[None]] | None
        ) = None

        # 加载任务
        self._load_tasks()
        self._load_executions()

    async def start(self) -> None:
        """启动调度器"""
        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        # 更新任务的下一次运行时间
        # 注意：只有 next_run 为空或已严重过期的任务才重新计算
        # 避免程序重启导致任务立即执行
        now = datetime.now()
        missed_tasks: list[ScheduledTask] = []

        for task in self._tasks.values():
            if task.is_active:
                if task.next_run is None:
                    self._update_next_run(task)
                elif task.next_run < now:
                    missed_tasks.append(task)
                    self._recalculate_missed_run(task, now)

        self._save_tasks()

        # 启动调度循环
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(f"TaskScheduler started with {len(self._tasks)} tasks")

        # 异步通知 missed 任务
        if missed_tasks and self.on_missed_tasks_summary:
            asyncio.ensure_future(self._notify_missed_tasks(missed_tasks))

    async def _notify_missed_tasks(self, missed: list[ScheduledTask]) -> None:
        """安全调用 missed 任务汇总通知"""
        try:
            await self.on_missed_tasks_summary(missed)
        except Exception as e:
            logger.debug(f"on_missed_tasks_summary callback error: {e}")

    async def stop(self, graceful_timeout: float = 30.0) -> None:
        """停止调度器，优雅等待运行中的任务完成"""
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
            deadline = asyncio.get_event_loop().time() + graceful_timeout
            while self._running_tasks and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.5)

            still_running = list(self._running_tasks)
            if still_running:
                logger.warning(
                    f"Force-stopping: {len(still_running)} tasks still running "
                    f"after {graceful_timeout}s timeout, resetting to SCHEDULED: {still_running}"
                )
                for tid in still_running:
                    task = self._tasks.get(tid)
                    if task and task.status == TaskStatus.RUNNING:
                        task.status = TaskStatus.SCHEDULED
                        task.updated_at = datetime.now()
                self._running_tasks.clear()

        self._save_tasks()

        logger.info("TaskScheduler stopped")

    # ==================== 任务管理 ====================

    MAX_TASKS = 200  # 用户任务数上限，防止无限创建

    async def add_task(self, task: ScheduledTask) -> str:
        """
        添加任务

        Returns:
            任务 ID

        Raises:
            ValueError: 任务 ID 重复或达到上限
        """
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task with id {task.id!r} already exists")

            user_tasks = [t for t in self._tasks.values() if t.deletable]
            if len(user_tasks) >= self.MAX_TASKS:
                raise ValueError(
                    f"已达到任务数量上限（{self.MAX_TASKS}），"
                    f"请先取消不需要的任务再创建新任务"
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
        删除任务

        Args:
            task_id: 任务 ID
            force: 强制删除（即使是系统任务）

        Returns:
            "ok" 成功, "not_found" 不存在, "system_task" 系统任务不可删
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
        "name", "description", "prompt", "reminder_message",
        "task_type", "trigger_type", "trigger_config",
        "channel_id", "chat_id", "user_id", "agent_profile_id",
        "metadata", "script_path", "action",
    }

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """更新任务（仅允许白名单字段）"""
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
        """启用任务"""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            task.fail_count = 0  # Bug-7: 重置失败计数，给任务重新来过的机会
            task.enable()
            self._update_next_run(task)
            self._save_tasks()
        return True

    async def disable_task(self, task_id: str) -> bool:
        """禁用任务"""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            task.disable()
            self._save_tasks()
        return True

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        user_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[ScheduledTask]:
        """列出任务"""
        tasks = list(self._tasks.values())

        if user_id:
            tasks = [t for t in tasks if t.user_id == user_id]
        if enabled_only:
            tasks = [t for t in tasks if t.enabled]

        return sorted(tasks, key=lambda t: t.next_run or datetime.max)

    async def save(self) -> None:
        """公共保存接口（获取锁后保存，供外部需要批量修改后调用）"""
        async with self._lock:
            self._save_tasks()

    async def trigger_now(self, task_id: str) -> TaskExecution | None:
        """
        立即触发任务（走 semaphore 并发控制，检查任务状态）

        Returns:
            执行记录, 或 None（任务不存在/不可用/已在运行）
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

    # ==================== 调度循环 ====================

    @staticmethod
    def _deterministic_jitter(task_id: str, max_jitter_seconds: int = 10) -> float:
        """基于 task_id 的确定性抖动，防止多任务同时触发雷群"""
        return (hash(task_id) % (max_jitter_seconds * 1000)) / 1000.0

    async def _scheduler_loop(self) -> None:
        """调度循环"""
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
        安全地执行任务

        注意：_running_tasks 已经在调度循环中添加了，这里只需要执行和清理
        """
        try:
            async with self._semaphore:
                await self._execute_task(task)
        finally:
            self._running_tasks.discard(task.id)

    async def _execute_task(self, task: ScheduledTask) -> TaskExecution:
        """执行任务"""
        execution = TaskExecution.create(task.id)

        logger.info(f"Executing task: {task.id} ({task.name})")
        task.mark_running()

        if self._plugin_hooks:
            try:
                await self._plugin_hooks.dispatch(
                    "on_schedule", task=task, execution=execution
                )
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

        self._executions.append(execution)
        self._save_tasks()
        self._save_executions()

        return execution

    def _handle_task_failure(self, task: ScheduledTask, error_msg: str) -> None:
        """处理任务失败：标记失败状态并推进 next_run"""
        was_enabled = task.enabled
        task.mark_failed(error_msg)
        self._advance_next_run(task)
        logger.warning(f"Task {task.id} reported failure: {error_msg}")

        # 检测是否刚被自动禁用（mark_failed 内部会在 fail_count>=5 时禁用）
        if was_enabled and not task.enabled and self.on_task_auto_disabled:
            asyncio.ensure_future(self._notify_auto_disabled(task))

    async def _notify_auto_disabled(self, task: ScheduledTask) -> None:
        """安全调用 on_task_auto_disabled 回调"""
        try:
            await self.on_task_auto_disabled(task)
        except Exception as e:
            logger.debug(f"on_task_auto_disabled callback error for {task.id}: {e}")

    def _advance_next_run(self, task: ScheduledTask) -> None:
        """确保 next_run 跳过当前 advance 窗口，防止同一触发窗口内快速重试"""
        trigger = self._triggers.get(task.id)
        if not trigger:
            return
        min_next = datetime.now() + timedelta(seconds=self.advance_seconds + 5)
        next_run = trigger.get_next_run_time(min_next)
        if next_run:
            task.next_run = next_run

    def _update_next_run(self, task: ScheduledTask) -> None:
        """更新任务的下一次运行时间"""
        trigger = self._triggers.get(task.id)
        if not trigger:
            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
            self._triggers[task.id] = trigger

        task.next_run = trigger.get_next_run_time(task.last_run)

    def _recalculate_missed_run(self, task: ScheduledTask, now: datetime) -> None:
        """
        重新计算错过执行时间的任务的下一次运行时间

        与 _update_next_run 的区别：
        - 不会设置为立即执行（即使 last_run 为 None）
        - 用于程序重启后恢复任务
        - 记录 missed 元数据供后续汇总通知
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

        # 对于间隔任务和 cron 任务，记录 missed 并推进到下一次
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

    # ==================== 持久化 ====================

    def _try_recover_json(self, target: Path) -> bool:
        """
        当 target 缺失/损坏时，尝试从 .bak 或 .tmp 恢复。
        返回是否执行了恢复动作（成功与否都算尝试过）。
        """
        bak = target.with_suffix(target.suffix + ".bak")
        tmp = target.with_suffix(target.suffix + ".tmp")

        # 目标文件存在则不恢复
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
        """加载任务"""
        tasks_file = self.storage_path / "tasks.json"

        # 若文件不存在，尝试恢复（Windows 上 rename 非原子，可能在崩溃窗口丢失）
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

            for item in data:
                try:
                    if not isinstance(item, dict):
                        logger.warning(f"Skipping non-dict task entry: {type(item).__name__}")
                        continue
                    task = ScheduledTask.from_dict(item)
                    self._tasks[task.id] = task

                    trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
                    self._triggers[task.id] = trigger

                except Exception as e:
                    task_id = item.get("id", "?") if isinstance(item, dict) else "?"
                    logger.warning(f"Failed to load task {task_id}: {e}")

            logger.info(f"Loaded {len(self._tasks)} tasks from storage")

        except Exception as e:
            logger.error(f"Failed to load tasks: {e}")

    def _load_executions(self) -> None:
        """加载执行记录（可选）"""
        executions_file = self.storage_path / "executions.json"

        if not executions_file.exists():
            self._try_recover_json(executions_file)
        if not executions_file.exists():
            return

        try:
            with open(executions_file, encoding="utf-8") as f:
                data = json.load(f)
            loaded = []
            for item in data or []:
                with contextlib.suppress(Exception):
                    loaded.append(TaskExecution.from_dict(item))
            # 只保留最近 1000 条
            self._executions = loaded[-1000:]
            logger.info(f"Loaded {len(self._executions)} executions from storage")
        except Exception as e:
            logger.warning(f"Failed to load executions: {e}")

    def _save_tasks(self) -> None:
        """保存任务"""
        tasks_file = self.storage_path / "tasks.json"

        try:
            data = [task.to_dict() for task in self._tasks.values()]
            safe_json_write(tasks_file, data, fsync=True)

        except Exception as e:
            logger.error(f"Failed to save tasks: {e}")

    def _save_executions(self) -> None:
        """保存执行记录"""
        executions_file = self.storage_path / "executions.json"

        try:
            # 只保留最近 1000 条记录
            recent = self._executions[-1000:]
            data = [e.to_dict() for e in recent]
            # 同步裁剪内存，避免长期运行无限增长
            self._executions = recent
            safe_json_write(executions_file, data, fsync=True)

        except Exception as e:
            logger.error(f"Failed to save executions: {e}")

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        """获取调度器统计"""
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
        """获取执行记录"""
        executions = self._executions

        if task_id:
            executions = [e for e in executions if e.task_id == task_id]

        return executions[-limit:]
