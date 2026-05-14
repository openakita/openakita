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

from ..utils.atomic_io import safe_json_write, safe_write
from ._naming import quarantine_invalid_task_name, validate_task_name
from .task import ScheduledTask, TaskDurability, TaskExecution, TaskStatus, TriggerType
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
        self._seen_execution_ids: set[str] = set()

        # 运行状态
        self._running = False
        self._scheduler_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()
        self._semaphore: asyncio.Semaphore | None = None

        # 并发保护锁：覆盖 _tasks/_triggers 所有写路径
        self._lock = asyncio.Lock()

        # 回调：任务因连续失败被自动禁用时触发
        self.on_task_auto_disabled: Callable[[ScheduledTask], Awaitable[None]] | None = None

        # 回调：启动时有 missed 任务汇总通知
        self.on_missed_tasks_summary: Callable[[list[ScheduledTask]], Awaitable[None]] | None = None

        # 加载任务
        self._load_tasks()
        self._load_executions()

    async def start(self) -> None:
        """启动调度器（幂等：重复 start 立即返回，避免新建第二条 _scheduler_loop 与 _semaphore）。"""
        if self._running:
            logger.debug("TaskScheduler.start() called while already running — no-op")
            return

        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        self._trim_executions_file()

        # 更新任务的下一次运行时间
        # 注意：只有 next_run 为空或已严重过期的任务才重新计算
        # 避免程序重启导致任务立即执行
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

    # ==================== 任务管理 ====================

    MAX_TASKS = 200  # 用户任务数上限，防止无限创建

    async def add_task(self, task: ScheduledTask) -> str:
        """
        添加任务

        Returns:
            任务 ID

        Raises:
            ValueError: 任务 ID 重复、达到上限或 name 不合规
        """
        # Fix-15：内核侧统一校验 name —— 防 MCP/programmatic/系统种子任务绕开
        # API 层的 _validate_task_name 把路径穿越/控制字符塞进 storage。
        ok, reason = validate_task_name(task.name)
        if not ok:
            raise ValueError(f"Invalid task name: {reason}")

        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task with id {task.id!r} already exists")

            user_tasks = [t for t in self._tasks.values() if t.deletable]
            if len(user_tasks) >= self.MAX_TASKS:
                raise ValueError(
                    f"已达到任务数量上限（{self.MAX_TASKS}），请先取消不需要的任务再创建新任务"
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

    async def trigger_now(
        self,
        task_id: str,
        *,
        execution: TaskExecution | None = None,
        _skip_running_check: bool = False,
    ) -> TaskExecution | None:
        """
        立即触发任务（走 semaphore 并发控制，检查任务状态）

        Args:
            task_id: 目标任务 id
            execution: 可选，调用方预先分配的 TaskExecution；
                用于 trigger_in_background 场景下让 API 返回的 execution_id
                与实际落库的 execution 对齐。若为 None 则由 _execute_task 自行创建。
            _skip_running_check: 内部参数。当调用方已经同步把 task_id 放入
                ``_running_tasks`` 做排他占位（trigger_in_background 的用法），
                置为 True 以跳过重复的 "already running" 护栏——否则此处会把
                调用方自己提前放入的标记当作"另一个正在执行的副本"直接返回
                None，导致任务永远不被执行。

        Returns:
            执行记录, 或 None（任务不存在/不可用/已在运行）
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        if not task.enabled:
            logger.warning(f"trigger_now: task {task_id} is disabled, skipping")
            return None

        if not _skip_running_check:
            if task_id in self._running_tasks:
                logger.warning(f"trigger_now: task {task_id} is already running, skipping")
                return None
            self._running_tasks.add(task_id)

        try:
            if self._semaphore:
                async with self._semaphore:
                    return await self._execute_task(task, execution=execution)
            else:
                return await self._execute_task(task, execution=execution)
        finally:
            # discard 幂等：无论是 trigger_now 自己 add 的还是 trigger_in_background
            # 预先 add 的，执行完后都释放占位，允许后续再次触发。
            self._running_tasks.discard(task_id)

    def trigger_in_background(self, task_id: str) -> str | None:
        """
        在后台触发任务（不等待执行完成），用于 API 路由避免请求超时。

        立即返回真实 execution_id（或 None 表示任务不存在 / 已在运行 / 已禁用）；
        实际执行通过 asyncio.create_task 异步进行。预创建的 TaskExecution 会随后被
        _execute_task 使用并最终落到 executions 存储中，因此调用方可用返回的 id
        查询真实结果。

        Returns:
            真实的 execution_id（形如 "exec_<12hex>"）或 None
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        if not task.enabled:
            logger.warning(f"trigger_in_background: task {task_id} is disabled, skipping")
            return None
        if task_id in self._running_tasks:
            logger.warning(
                f"trigger_in_background: task {task_id} is already running, skipping"
            )
            return None

        # 同步占位 _running_tasks，避免快速连按在 create_task 调度到之前
        # 两次 trigger_in_background 都通过 running 检查导致重复触发。
        # 由于 trigger_now 开头也有 "already running" 护栏，这里必须把
        # _skip_running_check=True 透传下去，否则 _runner 里的 trigger_now
        # 会把我们刚放进去的占位当作"另一个副本"直接返回 None，任务永远不跑。
        self._running_tasks.add(task_id)

        # 预创建 TaskExecution，让返回值与最终落库的记录共享同一个 id。
        execution = TaskExecution.create(task.id)
        execution_id = execution.id

        async def _runner() -> None:
            try:
                await self.trigger_now(
                    task_id,
                    execution=execution,
                    _skip_running_check=True,
                )
            except Exception as e:
                logger.error(f"trigger_in_background runner error for {task_id}: {e}")
            finally:
                # trigger_now 正常路径已 discard；这里兜底处理 trigger_now
                # 因 task 消失 / 被禁用等在进入 try/finally 之前 return 的情况，
                # 保证我们提前 add 的占位最终一定被释放。
                self._running_tasks.discard(task_id)

        asyncio.create_task(_runner())
        return execution_id

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

    async def _execute_task(
        self,
        task: ScheduledTask,
        *,
        execution: TaskExecution | None = None,
    ) -> TaskExecution:
        """执行任务

        Args:
            task: 要执行的任务
            execution: 可选，外部预创建的 TaskExecution（trigger_in_background 用以
                让 API 返回的 execution_id 与最终持久化的记录对齐）。
                若为 None 则创建一个新的 TaskExecution。
        """
        if execution is None:
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

            # C12 §14.5: a `[awaiting_approval] pending_id=…` marker from the
            # executor signals the task hit a PendingApproval and is paused —
            # NOT a normal failure. Move task to AWAITING_APPROVAL and skip
            # the failure-counter / auto-disable / advance_next_run flow.
            # When owner resolves to "allow", a separate API call (resume_pending)
            # transitions back to SCHEDULED and re-runs the task with a 30s
            # ReplayAuthorization injected (see C12-R3-5).
            _err_or_res = (
                execution.error if execution.status != "success" else (execution.result or "")
            )
            _is_deferred = isinstance(_err_or_res, str) and _err_or_res.startswith(
                "[awaiting_approval]"
            )

            if execution.status == "success":
                # Fix: 使用"实际预定时间"而非 datetime.now() 作为基准。
                # 由于调度循环的 advance_seconds=20 让任务比预定时间提前 ~20s 触发，
                # 任务在「真正预定时刻」之前完成时，若直接用 now 调
                # trigger.get_next_run_time(now)，CronTrigger 内部会
                # `start = now + 1 分钟 → replace(s/ms=0)`，对于 cron `0 9 * * *`
                # 这种整点表达式，得到的"下一次"仍然落在今天 09:00 这个槽位，
                # 于是 _scheduler_loop 下一轮立刻再触发 → 同一任务连跑两次。
                # 把基线推到 `now + advance_seconds + 5s` 之外，可保证 cron 必须
                # 跳到下一个真正的匹配槽位。等价于失败/取消路径用的 _advance_next_run。
                trigger = self._triggers.get(task.id)
                if trigger:
                    min_next = datetime.now() + timedelta(seconds=self.advance_seconds + 5)
                    next_run = trigger.get_next_run_time(min_next)
                else:
                    next_run = None
                task.mark_completed(next_run)
                # 成功跑完一次后，重置 metadata 里历史累计的 missed_count，
                # 否则前端会一直显示 "missed=27/29/36" 这种早期版本遗留的体感数字，
                # 让用户误以为系统在不停漏跑。保留 missed_count_cleared_at 时间戳，
                # 便于排查历史 missed 行为。
                if isinstance(task.metadata, dict) and task.metadata.get("missed_count"):
                    task.metadata["missed_count"] = 0
                    task.metadata["missed_count_cleared_at"] = datetime.now().isoformat()
                logger.info(f"Task {task.id} completed successfully")
            elif _is_deferred:
                # C12 §14.5: pause the task; do not increment fail_count,
                # do not advance next_run. ``mark_awaiting_approval`` keeps
                # the state machine honest (with logging if transition illegal).
                task.mark_awaiting_approval(marker=_err_or_res)
                logger.info(
                    "Task %s paused awaiting owner approval: %s",
                    task.id,
                    _err_or_res,
                )
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
        new_missed_count = missed_count + 1

        # Fix-7：missed_count 上限保护（默认 100）。无限累积只会让 UI
        # SchedulerView 出现"missed=27"这种"不健康"的数字，但实际上调度器
        # 已经按 trigger.get_next_run_time(now) 跳过历史，再多统计也无意义。
        # 超过上限时强制清零并写一条审计字段，避免历史包袱误导用户。
        _MISSED_COUNT_HARD_CAP = 100
        if new_missed_count >= _MISSED_COUNT_HARD_CAP:
            task.metadata["missed_count_reset_at"] = now.isoformat()
            task.metadata["missed_count_last_overflow"] = new_missed_count
            new_missed_count = 0
            logger.warning(
                f"Task {task.id} missed_count reached cap "
                f"({_MISSED_COUNT_HARD_CAP}); resetting to 0 and stamping "
                f"metadata.missed_count_last_overflow"
            )
        task.metadata["missed_count"] = new_missed_count

        next_run = trigger.get_next_run_time(now)

        min_next_run = now + timedelta(seconds=60)
        if next_run and next_run < min_next_run:
            next_run = trigger.get_next_run_time(min_next_run)

        task.next_run = next_run
        logger.info(
            f"Recalculated next_run for task {task.id}: {next_run} "
            f"(missed at {missed_at}, total missed: {new_missed_count})"
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

            skipped_session = 0
            quarantined_names: list[tuple[str, str]] = []
            for item in data:
                try:
                    if not isinstance(item, dict):
                        logger.warning(f"Skipping non-dict task entry: {type(item).__name__}")
                        continue
                    task = ScheduledTask.from_dict(item)
                    if task.durability == TaskDurability.SESSION:
                        skipped_session += 1
                        continue

                    # Fix-15：历史 task 可能在 storage 里残留路径穿越/控制字符
                    # 形态的 name —— 启动时一次性 quarantine 重命名，
                    # 既保留审计痕迹（可在 UI 看到 `__quarantine__/` 前缀），
                    # 又把这些非法字符隔离出后续日志/文件命名链路。
                    new_name = quarantine_invalid_task_name(task.name)
                    if new_name is not None:
                        quarantined_names.append((task.name, new_name))
                        task.name = new_name

                    self._tasks[task.id] = task

                    trigger = Trigger.from_config(task.trigger_type.value, task.trigger_config)
                    self._triggers[task.id] = trigger

                except Exception as e:
                    task_id = item.get("id", "?") if isinstance(item, dict) else "?"
                    logger.warning(f"Failed to load task {task_id}: {e}")
            if skipped_session:
                logger.info(f"Skipped {skipped_session} SESSION-durability task(s) on load")

            if quarantined_names:
                for orig, new in quarantined_names:
                    logger.warning(
                        "[Scheduler] Quarantined invalid task name on load: %r → %r",
                        orig,
                        new,
                    )
                with contextlib.suppress(Exception):
                    self._save_tasks()

            logger.info(f"Loaded {len(self._tasks)} tasks from storage")

        except Exception as e:
            logger.error(f"Failed to load tasks: {e}")

    def _load_executions(self) -> None:
        """加载执行记录，同时支持旧 JSON 数组和新 JSONL 格式。"""
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
        """一次性将旧 JSON 数组格式迁移为 JSONL。"""
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
        """保存tasks (SESSION durability tasks are excluded from persistence)."""
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
        """追加单条执行记录到 JSONL 文件（幂等：跳过已记录的 id）。"""
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
        """启动时裁剪 JSONL 文件，防止无限增长。保留最近 1000 行。"""
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
