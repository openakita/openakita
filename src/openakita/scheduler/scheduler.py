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
        for task in self._tasks.values():
            if task.is_active:
                if task.next_run is None:
                    # 没有 next_run，需要计算
                    self._update_next_run(task)
                elif task.next_run < now:
                    # next_run 已过期，重新计算（但不设为立即执行）
                    self._recalculate_missed_run(task, now)
                # 如果 next_run 在未来，保持不变

        # 启动调度循环
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(f"TaskScheduler started with {len(self._tasks)} tasks")

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler_task

        # 等待正在执行的任务
        if self._running_tasks:
            logger.info(f"Waiting for {len(self._running_tasks)} running tasks...")
            # 给运行中的任务一些时间完成
            await asyncio.sleep(2)

        # 保存任务
        self._save_tasks()

        logger.info("TaskScheduler stopped")

    # ==================== 任务管理 ====================

    async def add_task(self, task: ScheduledTask) -> str:
        """
        添加任务

        Returns:
            任务 ID
        """
        # 创建触发器
        trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)

        # 计算下一次运行时间
        task.next_run = trigger.get_next_run_time()
        task.status = TaskStatus.SCHEDULED

        # 保存
        self._tasks[task.id] = task
        self._triggers[task.id] = trigger

        self._save_tasks()

        logger.info(f"Added task: {task.id} ({task.name}), next run: {task.next_run}")
        return task.id

    async def remove_task(self, task_id: str, force: bool = False) -> bool:
        """
        删除任务

        Args:
            task_id: 任务 ID
            force: 强制删除（即使是系统任务）

        Returns:
            是否删除成功
        """
        if task_id in self._tasks:
            task = self._tasks[task_id]

            # 检查是否允许删除
            if not task.deletable and not force:
                logger.warning(
                    f"Task {task_id} is a system task and cannot be deleted. Use disable instead."
                )
                return False

            task.cancel()

            del self._tasks[task_id]
            if task_id in self._triggers:
                del self._triggers[task_id]

            self._save_tasks()
            logger.info(f"Removed task: {task_id}")
            return True
        return False

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """更新任务"""
        if task_id not in self._tasks:
            return False

        task = self._tasks[task_id]

        # 更新字段
        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)

        task.updated_at = datetime.now()

        # 如果触发配置变更，重新创建触发器
        if "trigger_config" in updates or "trigger_type" in updates:
            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
            self._triggers[task_id] = trigger
            task.next_run = trigger.get_next_run_time(task.last_run)

        self._save_tasks()
        logger.info(f"Updated task: {task_id}")
        return True

    async def enable_task(self, task_id: str) -> bool:
        """启用任务"""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.enable()
            self._update_next_run(task)
            self._save_tasks()
            return True
        return False

    async def disable_task(self, task_id: str) -> bool:
        """禁用任务"""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.disable()
            self._save_tasks()
            return True
        return False

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

    async def trigger_now(self, task_id: str) -> TaskExecution | None:
        """
        立即触发任务

        Returns:
            执行记录
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        return await self._execute_task(task)

    # ==================== 调度循环 ====================

    async def _scheduler_loop(self) -> None:
        """调度循环"""
        while self._running:
            try:
                now = datetime.now()

                # 检查需要执行的任务
                for task_id, task in list(self._tasks.items()):
                    if not task.is_active:
                        continue

                    if task_id in self._running_tasks:
                        continue  # 正在执行

                    # 提前 advance_seconds 秒执行，补偿 Agent 初始化和 LLM 调用延迟
                    if task.next_run:
                        trigger_time = task.next_run - timedelta(seconds=self.advance_seconds)
                        if now >= trigger_time:
                            # 重要：先标记为运行中，防止重复触发！
                            # 必须在 create_task 之前执行
                            self._running_tasks.add(task_id)
                            # 异步执行任务
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

        try:
            if self.executor:
                success, result_or_error = await self.executor(task)
                if success:
                    execution.finish(True, result=result_or_error)
                else:
                    execution.finish(False, error=result_or_error)
            else:
                # 没有执行器，模拟执行
                execution.finish(True, result="No executor configured")

            # 根据执行结果更新任务状态
            if execution.status == "success":
                trigger = self._triggers.get(task.id)
                next_run = trigger.get_next_run_time(datetime.now()) if trigger else None
                task.mark_completed(next_run)
                logger.info(f"Task {task.id} completed successfully")
            else:
                # 失败：标记失败并尽量推进下一次运行时间（避免 next_run 停留在过去）
                error_msg = execution.error or "Unknown error"
                task.mark_failed(error_msg)
                trigger = self._triggers.get(task.id)
                next_run = trigger.get_next_run_time(datetime.now()) if trigger else None
                if next_run:
                    task.next_run = next_run
                logger.warning(f"Task {task.id} reported failure: {error_msg}")

        except Exception as e:
            error_msg = str(e)
            execution.finish(False, error=error_msg)
            task.mark_failed(error_msg)
            logger.error(f"Task {task.id} failed: {error_msg}", exc_info=True)

        # 保存执行记录
        self._executions.append(execution)
        self._save_tasks()
        self._save_executions()

        return execution

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

        Args:
            task: 任务
            now: 当前时间
        """
        trigger = self._triggers.get(task.id)
        if not trigger:
            trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
            self._triggers[task.id] = trigger

        # 对于一次性任务，如果已经过期，标记为完成
        if task.trigger_type == TriggerType.ONCE:
            logger.info(f"One-time task {task.id} missed, marking as completed")
            task.status = TaskStatus.COMPLETED
            task.enabled = False
            return

        # 对于间隔任务和 cron 任务，计算下一次运行时间
        # 使用当前时间作为基准（而不是 last_run），避免立即执行
        next_run = trigger.get_next_run_time(now)

        # 确保 next_run 在未来（至少 1 分钟后），避免启动时立即执行
        min_next_run = now + timedelta(seconds=60)
        if next_run and next_run < min_next_run:
            next_run = trigger.get_next_run_time(min_next_run)

        task.next_run = next_run
        logger.info(f"Recalculated next_run for task {task.id}: {next_run}")

    # ==================== 持久化 ====================

    def _atomic_write_json(self, target: Path, data: object) -> None:
        """
        原子写入 JSON 文件（Windows 友好）
        - 写入临时文件
        - fsync 落盘
        - 使用 os.replace 原子替换目标文件
        - 保留 .bak 备份（尽力而为，不阻塞主流程）
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        bak = target.with_suffix(target.suffix + ".bak")

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # 尽力备份旧文件
        if target.exists():
            with contextlib.suppress(Exception):
                if bak.exists():
                    bak.unlink()
                # 用 replace，允许覆盖旧 bak
                os.replace(str(target), str(bak))

        os.replace(str(tmp), str(target))

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

            for item in data:
                try:
                    task = ScheduledTask.from_dict(item)
                    self._tasks[task.id] = task

                    # 创建触发器
                    trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
                    self._triggers[task.id] = trigger

                except Exception as e:
                    logger.warning(f"Failed to load task: {e}")

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
            self._atomic_write_json(tasks_file, data)

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
            self._atomic_write_json(executions_file, data)

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
