"""
WorkerAgent - 工作进程

在独立进程中运行，负责:
- 接收 Master 分发的任务
- 使用内置 Agent 执行任务
- 返回结果给 Master
- 定期发送心跳

设计说明:
- 每个 WorkerAgent 是一个独立进程
- 使用 ZMQ DEALER 与 Master 通信
- 内置完整的 Agent 实例用于任务执行
- Session 历史通过消息传递，不本地保存
- 记忆系统使用共享文件存储
"""

import os
import asyncio
import logging
import signal
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from .bus import WorkerBus, BusConfig
from .messages import (
    AgentMessage,
    AgentInfo,
    AgentStatus,
    AgentType,
    MessageType,
    CommandType,
    EventType,
    TaskPayload,
    TaskResult,
    create_register_command,
    create_unregister_command,
)

logger = logging.getLogger(__name__)


class WorkerAgent:
    """
    工作 Agent
    
    在独立进程中运行，执行 Master 分发的任务
    
    特点:
    - 无状态：不保存 Session 历史（通过消息传递）
    - 共享记忆：使用共享文件存储
    - 心跳机制：定期向 Master 报告状态
    """
    
    def __init__(
        self,
        agent_id: str,
        router_address: str = "tcp://127.0.0.1:5555",
        pub_address: str = "tcp://127.0.0.1:5556",
        heartbeat_interval: int = 5,
        capabilities: Optional[List[str]] = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            agent_id: Worker 唯一标识
            router_address: Master ROUTER 地址
            pub_address: Master PUB 地址
            heartbeat_interval: 心跳间隔（秒）
            capabilities: 能力列表
            data_dir: 数据目录（用于共享记忆）
        """
        self.agent_id = agent_id
        self.heartbeat_interval = heartbeat_interval
        self.capabilities = capabilities or ["chat", "execute"]
        self.data_dir = data_dir
        
        # 总线配置
        bus_config = BusConfig(
            router_address=router_address,
            pub_address=pub_address,
        )
        
        # 通信总线
        self.bus = WorkerBus(worker_id=agent_id, config=bus_config)
        
        # Agent 信息
        self.agent_info = AgentInfo(
            agent_id=agent_id,
            agent_type=AgentType.WORKER.value,
            process_id=os.getpid(),
            status=AgentStatus.STARTING.value,
            capabilities=self.capabilities,
        )
        
        # 内置 Agent（用于执行任务）
        self._agent = None
        
        # 运行状态
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._current_task: Optional[TaskPayload] = None
        
        # 信号处理
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, stopping...")
            self._running = False
        
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
    
    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self._running
    
    # ==================== 生命周期 ====================
    
    async def start(self) -> None:
        """启动 Worker"""
        if self._running:
            return
        
        logger.info(f"Starting WorkerAgent (id={self.agent_id})")
        
        # 初始化内置 Agent
        await self._init_agent()
        
        # 启动通信总线
        await self.bus.start()
        
        # 注册消息处理器
        self._register_handlers()
        
        # 向 Master 注册
        await self._register()
        
        # 启动心跳
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        self._running = True
        self.agent_info.set_status(AgentStatus.IDLE)
        
        logger.info(f"WorkerAgent {self.agent_id} started")
    
    async def stop(self) -> None:
        """停止 Worker"""
        if not self._running:
            return
        
        logger.info(f"Stopping WorkerAgent {self.agent_id}...")
        self._running = False
        
        # 更新状态
        self.agent_info.set_status(AgentStatus.STOPPING)
        
        # 停止心跳
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # 向 Master 注销
        await self._unregister()
        
        # 停止通信总线
        await self.bus.stop()
        
        # 关闭内置 Agent
        if self._agent:
            await self._agent.shutdown()
        
        logger.info(f"WorkerAgent {self.agent_id} stopped")
    
    async def _init_agent(self) -> None:
        """初始化内置 Agent"""
        from ..core.agent import Agent
        
        self._agent = Agent()
        # 不启动 scheduler，避免重复
        await self._agent.initialize(start_scheduler=False)
        
        logger.info(f"Worker {self.agent_id}: internal agent initialized")
    
    def _register_handlers(self) -> None:
        """注册消息处理器"""
        self.bus.register_command_handler(
            CommandType.ASSIGN_TASK, self._handle_task
        )
        self.bus.register_command_handler(
            CommandType.SHUTDOWN, self._handle_shutdown
        )
        self.bus.register_command_handler(
            CommandType.GET_STATUS, self._handle_get_status
        )
        self.bus.register_command_handler(
            CommandType.CANCEL_TASK, self._handle_cancel_task
        )
    
    # ==================== 注册/注销 ====================
    
    async def _register(self) -> None:
        """向 Master 注册"""
        message = create_register_command(self.agent_info)
        
        try:
            await self.bus._send_to_master(message)
            logger.info(f"Worker {self.agent_id}: registration sent")
        except Exception as e:
            logger.error(f"Worker {self.agent_id}: registration failed: {e}")
            raise
    
    async def _unregister(self) -> None:
        """向 Master 注销"""
        message = create_unregister_command(self.agent_id)
        
        try:
            await self.bus._send_to_master(message)
            logger.info(f"Worker {self.agent_id}: unregistration sent")
        except Exception as e:
            logger.error(f"Worker {self.agent_id}: unregistration failed: {e}")
    
    # ==================== 心跳 ====================
    
    async def _heartbeat_loop(self) -> None:
        """心跳循环"""
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                
                # 更新心跳时间
                self.agent_info.update_heartbeat()
                
                # 发送心跳
                await self.bus.send_heartbeat(self.agent_id, self.agent_info)
                
                logger.debug(f"Worker {self.agent_id}: heartbeat sent")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {self.agent_id}: heartbeat error: {e}")
    
    # ==================== 任务处理 ====================
    
    async def _handle_task(self, message: AgentMessage) -> Optional[AgentMessage]:
        """
        处理任务
        
        这是 Worker 的核心方法，接收 Master 分发的任务并执行
        """
        task = TaskPayload.from_dict(message.payload)
        task_id = task.task_id
        
        logger.info(f"Worker {self.agent_id}: received task {task_id}")
        
        # 更新状态
        self._current_task = task
        self.agent_info.set_task(task_id, task.description)
        
        # 执行任务
        start_time = datetime.now()
        result = await self._execute_task(task)
        duration = (datetime.now() - start_time).total_seconds()
        
        # 清除任务状态
        self._current_task = None
        self.agent_info.clear_task(success=result.success)
        
        # 更新统计
        result.duration_seconds = duration
        
        logger.info(
            f"Worker {self.agent_id}: task {task_id} completed "
            f"(success={result.success}, duration={duration:.2f}s)"
        )
        
        # 发送结果给 Master
        await self._send_task_result(result)
        
        return None  # 结果通过单独的消息发送
    
    async def _execute_task(self, task: TaskPayload) -> TaskResult:
        """
        执行任务
        
        根据任务类型调用相应的处理方法
        """
        try:
            if task.task_type == "chat":
                return await self._execute_chat_task(task)
            elif task.task_type == "execute":
                return await self._execute_execute_task(task)
            else:
                return TaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"Unknown task type: {task.task_type}",
                )
        except Exception as e:
            logger.error(f"Task execution error: {e}", exc_info=True)
            return TaskResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
            )
    
    async def _execute_chat_task(self, task: TaskPayload) -> TaskResult:
        """执行对话任务"""
        session_messages = task.context.get("session_messages", [])
        session_id = task.session_id or "worker"
        
        try:
            if session_messages:
                # 使用 session 上下文
                # 注意：这里不传递 session 和 gateway，因为它们不能跨进程
                response = await self._agent.chat_with_session(
                    message=task.content,
                    session_messages=session_messages,
                    session_id=session_id,
                )
            else:
                # 使用简单对话模式
                response = await self._agent.chat(task.content, session_id=session_id)
            
            return TaskResult(
                task_id=task.task_id,
                success=True,
                result=response,
            )
            
        except Exception as e:
            logger.error(f"Chat task error: {e}", exc_info=True)
            return TaskResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
            )
    
    async def _execute_execute_task(self, task: TaskPayload) -> TaskResult:
        """执行执行类任务（使用 Ralph 循环）"""
        try:
            result = await self._agent.execute_task_from_message(task.content)
            
            return TaskResult(
                task_id=task.task_id,
                success=result.success,
                result=result.data if result.success else None,
                error=result.error if not result.success else None,
                iterations=result.iterations,
            )
            
        except Exception as e:
            logger.error(f"Execute task error: {e}", exc_info=True)
            return TaskResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
            )
    
    async def _send_task_result(self, result: TaskResult) -> None:
        """发送任务结果给 Master"""
        message = AgentMessage.command(
            sender_id=self.agent_id,
            target_id="master",
            command_type=CommandType.TASK_RESULT,
            payload=result.to_dict(),
        )
        
        try:
            await self.bus._send_to_master(message)
            logger.debug(f"Worker {self.agent_id}: task result sent")
        except Exception as e:
            logger.error(f"Failed to send task result: {e}")
    
    # ==================== 其他命令处理 ====================
    
    async def _handle_shutdown(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理关闭命令"""
        logger.info(f"Worker {self.agent_id}: received shutdown command")
        self._running = False
        
        return AgentMessage.response(
            sender_id=self.agent_id,
            target_id=message.sender_id,
            correlation_id=message.msg_id,
            payload={"success": True},
        )
    
    async def _handle_get_status(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理状态查询"""
        return AgentMessage.response(
            sender_id=self.agent_id,
            target_id=message.sender_id,
            correlation_id=message.msg_id,
            payload=self.agent_info.to_dict(),
        )
    
    async def _handle_cancel_task(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理取消任务命令"""
        task_id = message.payload.get("task_id")
        
        if self._current_task and self._current_task.task_id == task_id:
            # TODO: 实现任务取消逻辑
            logger.warning(f"Task cancellation not fully implemented: {task_id}")
        
        return AgentMessage.response(
            sender_id=self.agent_id,
            target_id=message.sender_id,
            correlation_id=message.msg_id,
            payload={"success": True, "task_id": task_id},
        )
    
    # ==================== 统计 ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "agent_id": self.agent_id,
            "status": self.agent_info.status,
            "tasks_completed": self.agent_info.tasks_completed,
            "tasks_failed": self.agent_info.tasks_failed,
            "current_task": self._current_task.task_id if self._current_task else None,
            "uptime": self._calculate_uptime(),
        }
    
    def _calculate_uptime(self) -> str:
        """计算运行时长"""
        created = datetime.fromisoformat(self.agent_info.created_at)
        uptime = datetime.now() - created
        
        seconds = uptime.total_seconds()
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.0f}m"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"


# ==================== 便捷函数 ====================

async def run_worker(
    agent_id: str,
    router_address: str = "tcp://127.0.0.1:5555",
    pub_address: str = "tcp://127.0.0.1:5556",
    heartbeat_interval: int = 5,
    capabilities: Optional[List[str]] = None,
) -> None:
    """
    运行 Worker（便捷函数）
    
    可以在脚本中直接调用：
    
        asyncio.run(run_worker("worker-001"))
    """
    worker = WorkerAgent(
        agent_id=agent_id,
        router_address=router_address,
        pub_address=pub_address,
        heartbeat_interval=heartbeat_interval,
        capabilities=capabilities,
    )
    
    await worker.start()
    
    try:
        while worker.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await worker.stop()
