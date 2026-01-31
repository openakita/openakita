"""
任务调度器

核心调度器:
- 管理任务生命周期
- 触发任务执行
- 任务持久化
"""

import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Any

from .task import ScheduledTask, TriggerType, TaskStatus, TaskExecution
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
        storage_path: Optional[Path] = None,
        executor: Optional[TaskExecutorFunc] = None,
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
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running_tasks: set[str] = set()
        self._semaphore: Optional[asyncio.Semaphore] = None
        
        # 加载任务
        self._load_tasks()
    
    async def start(self) -> None:
        """启动调度器"""
        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # 更新所有任务的下一次运行时间
        for task in self._tasks.values():
            if task.is_active:
                self._update_next_run(task)
        
        # 启动调度循环
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        
        logger.info(f"TaskScheduler started with {len(self._tasks)} tasks")
    
    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
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
    
    async def remove_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id in self._tasks:
            task = self._tasks[task_id]
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
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务"""
        return self._tasks.get(task_id)
    
    def list_tasks(
        self,
        user_id: Optional[str] = None,
        enabled_only: bool = False,
    ) -> list[ScheduledTask]:
        """列出任务"""
        tasks = list(self._tasks.values())
        
        if user_id:
            tasks = [t for t in tasks if t.user_id == user_id]
        if enabled_only:
            tasks = [t for t in tasks if t.enabled]
        
        return sorted(tasks, key=lambda t: t.next_run or datetime.max)
    
    async def trigger_now(self, task_id: str) -> Optional[TaskExecution]:
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
                            # 异步执行任务
                            asyncio.create_task(self._run_task(task))
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(1)
    
    async def _run_task(self, task: ScheduledTask) -> None:
        """执行任务（带并发控制）"""
        async with self._semaphore:
            self._running_tasks.add(task.id)
            try:
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
                success, result = await self.executor(task)
                execution.finish(success, result=result)
            else:
                # 没有执行器，模拟执行
                execution.finish(True, result="No executor configured")
            
            # 更新任务状态
            trigger = self._triggers.get(task.id)
            next_run = trigger.get_next_run_time(datetime.now()) if trigger else None
            task.mark_completed(next_run)
            
            logger.info(f"Task {task.id} completed successfully")
            
        except Exception as e:
            error_msg = str(e)
            execution.finish(False, error=error_msg)
            task.mark_failed(error_msg)
            logger.error(f"Task {task.id} failed: {error_msg}")
        
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
    
    # ==================== 持久化 ====================
    
    def _load_tasks(self) -> None:
        """加载任务"""
        tasks_file = self.storage_path / "tasks.json"
        
        if not tasks_file.exists():
            return
        
        try:
            with open(tasks_file, "r", encoding="utf-8") as f:
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
    
    def _save_tasks(self) -> None:
        """保存任务"""
        tasks_file = self.storage_path / "tasks.json"
        
        try:
            data = [task.to_dict() for task in self._tasks.values()]
            
            with open(tasks_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error(f"Failed to save tasks: {e}")
    
    def _save_executions(self) -> None:
        """保存执行记录"""
        executions_file = self.storage_path / "executions.json"
        
        try:
            # 只保留最近 1000 条记录
            recent = self._executions[-1000:]
            data = [e.to_dict() for e in recent]
            
            with open(executions_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
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
                "once": len([t for t in self._tasks.values() if t.trigger_type == TriggerType.ONCE]),
                "interval": len([t for t in self._tasks.values() if t.trigger_type == TriggerType.INTERVAL]),
                "cron": len([t for t in self._tasks.values() if t.trigger_type == TriggerType.CRON]),
            },
            "next_runs": [
                {"id": t.id, "name": t.name, "next_run": t.next_run.isoformat() if t.next_run else None}
                for t in sorted(active_tasks, key=lambda x: x.next_run or datetime.max)[:5]
            ],
        }
    
    def get_executions(
        self,
        task_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[TaskExecution]:
        """获取执行记录"""
        executions = self._executions
        
        if task_id:
            executions = [e for e in executions if e.task_id == task_id]
        
        return executions[-limit:]
