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
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..utils.atomic_io import safe_json_write, safe_write
from ._naming import quarantine_invalid_task_name, validate_task_name
from .locks import (
    HEARTBEAT_INTERVAL_SECONDS,
    ExecLock,
    OrphanLock,
    acquire_exec_lock,
    default_lock_dir,
    heartbeat_exec_lock,
    release_exec_lock,
    reset_current_scheduled_task_id,
    scan_orphaned_locks,
    set_current_scheduled_task_id,
    unlink_orphan,
)
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

        # C17 Phase A.1：scheduler 单任务执行锁目录。``locks.acquire_exec_lock``
        # 写入 ``exec_<task_id>.json``；startup rescan 用 ``scan_orphaned_locks``
        # 清理崩溃残留。
        self.lock_dir = default_lock_dir(self.storage_path)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

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
        """启动调度器（幂等：重复 start 立即返回，避免新建第二条 _scheduler_loop 与 _semaphore）。

        C17 Phase A.3: startup rescan 必须先于 ``_scheduler_loop`` 启动：

        1. 扫描 ``data/scheduler/locks/`` 里所有 stale exec lock，删文件 +
           对应 task 强制 reset 回 SCHEDULED。
        2. 用 ``_reconcile_awaiting_approval()`` 把 ``awaiting_approval``
           的 task 与 ``pending_approvals.json`` 对账：pending 缺失 / 过期
           → 标 fail；存活 → 保留。
        3. 已有的 ``missed_tasks`` 处理保留（但加 stagger / cap，借鉴
           openclaw ``planStartupCatchup``）。
        """
        if self._running:
            logger.debug("TaskScheduler.start() called while already running — no-op")
            return

        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        self._trim_executions_file()

        # ── C17 Phase A.3: rescan + reconcile，必须先于 scheduler_loop ──
        orphans = self._rescan_orphaned_runs()
        self._reconcile_awaiting_approval()

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

            # C17 Phase A.3：startup catch-up stagger + cap，避免重启后
            # 同一秒触发 N 个任务给 LLM 发雷群。借鉴 openclaw
            # ``planStartupCatchup``：超过 MAX_MISSED_PER_RESTART 的任务
            # 推迟到分散的将来时间点。
            self._stagger_missed_tasks(missed_tasks, now)

            self._save_tasks()

        # 启动调度循环
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(
            "TaskScheduler started with %d tasks (orphan_locks=%d, missed=%d)",
            len(self._tasks),
            len(orphans),
            len(missed_tasks),
        )

        # 异步通知 missed 任务
        if missed_tasks and self.on_missed_tasks_summary:
            asyncio.ensure_future(self._notify_missed_tasks(missed_tasks))

    async def _notify_missed_tasks(self, missed: list[ScheduledTask]) -> None:
        """安全调用 missed 任务汇总通知"""
        try:
            await self.on_missed_tasks_summary(missed)
        except Exception as e:
            logger.debug(f"on_missed_tasks_summary callback error: {e}")

    # ---- C17 Phase A.3: startup rescan + reconcile -----------------------

    # missed 任务恢复时一次性可以"立刻就触发"的上限；超过部分按
    # STAGGER_INTERVAL_S 推迟到将来不同时刻。借鉴 openclaw
    # ``planStartupCatchup`` 防雷群策略。
    MAX_MISSED_PER_RESTART: int = 10
    STAGGER_INTERVAL_S: int = 30

    def _rescan_orphaned_runs(self) -> list[OrphanLock]:
        """C17 Phase A.3：清理上次进程崩溃留下的执行锁。

        - 用 :func:`scan_orphaned_locks` 找出 stale lock 文件。
        - 对应 task 若仍处于 ``RUNNING``，``force_reset_to_scheduled`` 回到
          可调度状态（保留 audit trail）。
        - 把 audit 记录追加到 ``data/scheduler/recovery.jsonl``（plain JSONL，
          C17 文档明确：恢复审计与策略 hash 链是两套系统，不交叉）。
        """
        orphans = scan_orphaned_locks(self.lock_dir)
        if not orphans:
            return []

        recovery_log = self.storage_path / "recovery.jsonl"
        try:
            recovery_log.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        for orphan in orphans:
            task = self._tasks.get(orphan.task_id)
            reset_reason = f"startup_rescan:{orphan.reason}"
            if task is not None and task.status == TaskStatus.RUNNING:
                try:
                    task.force_reset_to_scheduled(reason=reset_reason)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[scheduler] reset(%s) on orphan lock failed: %s",
                        orphan.task_id,
                        exc,
                    )

            try:
                unlink_orphan(orphan)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler] unlink_orphan(%s) failed (will retry next start): %s",
                    orphan.task_id,
                    exc,
                )

            try:
                with open(recovery_log, "a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": datetime.now().isoformat(),
                                "kind": "orphan_lock",
                                "task_id": orphan.task_id,
                                "reason": orphan.reason,
                                "pid": orphan.pid,
                                "hostname": orphan.hostname,
                                "acquired_at": orphan.acquired_at,
                                "heartbeat_at": orphan.heartbeat_at,
                                "lease_until": orphan.lease_until,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as exc:
                logger.debug("[scheduler] recovery log append failed: %s", exc)

            logger.warning(
                "[scheduler] recovered orphan lock task=%s reason=%s pid=%s",
                orphan.task_id,
                orphan.reason,
                orphan.pid,
            )
        return orphans

    def _reconcile_awaiting_approval(self) -> None:
        """C17 Phase A.3：对账 ``awaiting_approval`` task 与 pending_approvals.json。

        - 仍有未过期 ``PendingApproval`` → 保留 awaiting 状态。
        - 找不到 / 已过期 → 标 fail，写 recovery 审计，让 task 不再永远卡住。

        ``PendingApprovalsStore`` 是独立模块；这里 lazy import 避免循环。
        """
        awaiting_ids = [
            tid for tid, t in self._tasks.items()
            if t.status == TaskStatus.AWAITING_APPROVAL
        ]
        if not awaiting_ids:
            return

        try:
            from ..core.pending_approvals import get_pending_approvals_store
            store = get_pending_approvals_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[scheduler] reconcile_awaiting_approval skipped: "
                "PendingApprovalsStore unavailable (%s); leaving tasks as-is",
                exc,
            )
            return

        recovery_log = self.storage_path / "recovery.jsonl"
        now = datetime.now()
        for tid in awaiting_ids:
            task = self._tasks.get(tid)
            if task is None:
                continue
            entries: list[Any] = []
            try:
                entries = [
                    e for e in store.list_pending() if getattr(e, "task_id", None) == tid
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler] reconcile: list_pending(%s) failed: %s",
                    tid,
                    exc,
                )
            keep = False
            for e in entries:
                expires_at = getattr(e, "expires_at", None)
                if not expires_at:
                    keep = True
                    break
                try:
                    if isinstance(expires_at, (int, float)) and expires_at > time.time():
                        keep = True
                        break
                    if isinstance(expires_at, str):
                        if datetime.fromisoformat(expires_at) > now:
                            keep = True
                            break
                except Exception:
                    continue
            if keep:
                continue

            reason = (
                "approval_orphaned" if not entries else "approval_expired"
            )
            try:
                # mark_failed expects status==RUNNING; force-transition first
                # so we don't tear up the state machine on the corrupted row.
                task.status = TaskStatus.RUNNING
                task.mark_failed(error=reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[scheduler] reconcile: failed to fail-out task %s: %s",
                    tid,
                    exc,
                )
            try:
                with open(recovery_log, "a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": now.isoformat(),
                                "kind": "awaiting_reconcile",
                                "task_id": tid,
                                "reason": reason,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as exc:
                logger.debug("[scheduler] recovery log append failed: %s", exc)

            logger.warning(
                "[scheduler] reconciled awaiting_approval task %s → failed (reason=%s)",
                tid,
                reason,
            )

    def _stagger_missed_tasks(
        self, missed_tasks: list[ScheduledTask], now: datetime
    ) -> None:
        """Spread missed tasks beyond MAX_MISSED_PER_RESTART out in time.

        借鉴 openclaw ``planStartupCatchup``：第 N (N >= MAX) 个 missed 任务
        的 ``next_run`` 推到 now + (N - MAX + 1) * STAGGER_INTERVAL_S，
        避免重启瞬间把队列里所有 missed 都立刻拉起。
        """
        if len(missed_tasks) <= self.MAX_MISSED_PER_RESTART:
            return
        for idx, task in enumerate(missed_tasks[self.MAX_MISSED_PER_RESTART :], start=1):
            try:
                task.next_run = now + timedelta(seconds=idx * self.STAGGER_INTERVAL_S)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler] stagger(%s) failed: %s", task.id, exc
                )
        logger.info(
            "[scheduler] staggered %d missed tasks (cap=%d, interval=%ds)",
            len(missed_tasks) - self.MAX_MISSED_PER_RESTART,
            self.MAX_MISSED_PER_RESTART,
            self.STAGGER_INTERVAL_S,
        )

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

        C17 Phase A.2 关键时序：

        1. ``mark_running`` 后立刻 ``_save_tasks`` —— SIGKILL 在
           mark_running 和 persist 之间会让 tasks.json 看不到 running
           状态，启动 rescan 也救不回来。
        2. 对周期任务（cron/interval）**先**调 ``_update_next_run``
           推进 next_run，借鉴 hermes-agent 的 ``advance_next_run``
           前置 —— 即使本次执行崩溃，下一次 next_run 也不会因为还卡
           在过去而被立刻重抓（at-most-once-per-window）。
        3. 拿 :func:`scheduler.locks.acquire_exec_lock` 写 exec lock 文件 +
           PID + lease。同进程已经 ``_running_tasks`` 占位，跨进程的
           另一实例会在此被挡住。
        4. 启动 heartbeat ``asyncio.Task``，每
           ``HEARTBEAT_INTERVAL_SECONDS`` 刷新 lock 文件 ``heartbeat_at``。
        5. 通过 :func:`scheduler.locks.set_current_scheduled_task_id`
           把 task_id 推到 ``ContextVar`` —— Phase A.4 让
           ``tool_executor._defer_unattended_confirm`` 拿到正确的
           ``pending_approval.task_id``，不再都是 ``None``。
        """
        if execution is None:
            execution = TaskExecution.create(task.id)

        logger.info(f"Executing task: {task.id} ({task.name})")
        task.mark_running()

        # C17 Phase A.2 §1：mark_running 后立刻 persist。避免 SIGKILL
        # 在状态机变更与持久化之间留下 silent gap。
        async with self._lock:
            self._save_tasks()

        # C17 Phase A.2 §2：周期任务先把 next_run 推进到下一窗口，
        # at-most-once-per-window 语义。一次性任务跳过（mark_completed
        # 会处理一次性任务的终态）。
        if task.trigger_type in (TriggerType.CRON, TriggerType.INTERVAL):
            try:
                self._update_next_run(task)
                async with self._lock:
                    self._save_tasks()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[scheduler] advance_next_run(%s) failed before exec: %s",
                    task.id,
                    exc,
                )

        # C17 Phase A.2 §3：cross-process exec lock。
        expected_runtime = task.metadata.get("timeout_seconds") if isinstance(task.metadata, dict) else None
        if not isinstance(expected_runtime, (int, float)) or expected_runtime <= 0:
            expected_runtime = 300
        exec_lock: ExecLock | None = acquire_exec_lock(
            task.id,
            lock_dir=self.lock_dir,
            expected_runtime_s=expected_runtime,
            execution_id=execution.id,
        )
        if exec_lock is None:
            logger.warning(
                "[scheduler] %s: another process holds the exec lease; skip this run",
                task.id,
            )
            # Roll back the RUNNING transition so the next tick can retry
            # cleanly (force_reset is the audited reset path).
            try:
                task.force_reset_to_scheduled(reason="exec_lock_busy")
                async with self._lock:
                    self._save_tasks()
            except Exception:
                pass
            execution.finish(False, error="exec_lock_busy")
            async with self._lock:
                self._executions.append(execution)
                self._append_execution(execution)
            return execution

        # C17 Phase A.2 §4：heartbeat task。
        hb_stop = asyncio.Event()

        async def _heartbeat_loop() -> None:
            interval = HEARTBEAT_INTERVAL_SECONDS
            try:
                while not hb_stop.is_set():
                    try:
                        await asyncio.wait_for(hb_stop.wait(), timeout=interval)
                    except TimeoutError:
                        pass
                    if hb_stop.is_set():
                        return
                    still_ours = await asyncio.to_thread(
                        heartbeat_exec_lock,
                        exec_lock,
                        expected_runtime,
                    )
                    if not still_ours:
                        logger.warning(
                            "[scheduler] %s exec lock taken over by another "
                            "process mid-run; stopping heartbeat",
                            task.id,
                        )
                        return
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler] heartbeat loop(%s) raised %s",
                    task.id,
                    exc,
                )

        hb_task = asyncio.create_task(_heartbeat_loop(), name=f"sched-hb-{task.id}")

        # C17 Phase A.4：把 task_id 推到 ContextVar，供
        # tool_executor._defer_unattended_confirm 在没有 state.task_id 时回退。
        ctx_token = set_current_scheduled_task_id(task.id)

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

        finally:
            # C17 Phase A.2 §4：heartbeat task + exec lock 兜底清理。
            # 即便上面任一分支抛了未捕获异常，也要保证 lock 文件被删除、
            # ContextVar 被复位，否则下次进程启动会把这次留下的 lock
            # 当 orphan 走 rescan，task 状态徒增噪音。
            hb_stop.set()
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            try:
                release_exec_lock(exec_lock)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[scheduler] release_exec_lock(%s) raised %s", task.id, exc
                )
            reset_current_scheduled_task_id(ctx_token)

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
