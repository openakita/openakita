"""
MasterAgent - 主协调器

多 Agent 系统的核心，负责:
- 任务分发和路由
- Worker 生命周期管理
- 简单任务直接处理
- 健康监控和故障恢复
- 与 Session/记忆系统集成
"""

import os
import asyncio
import logging
import uuid
import multiprocessing
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable, Awaitable

from .registry import AgentRegistry
from .bus import AgentBus, BusConfig
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
    create_chat_response,
)

logger = logging.getLogger(__name__)


class MasterAgent:
    """
    主协调器
    
    职责:
    1. **任务协调**: 接收请求，决定自己处理还是分发给 Worker
    2. **简单任务处理**: 直接处理简单对话、查询
    3. **监督管理**: 监控 Worker 健康状态，自动重启故障 Agent
    4. **动态扩缩**: 根据负载创建/销毁 Worker
    
    与现有系统集成:
    - SessionManager: 在主进程，提供会话历史
    - MemoryManager: 共享文件存储，Worker 按需加载
    - CLI/Gateway: 通过 MasterAgent 路由请求
    """
    
    # 默认配置
    DEFAULT_MIN_WORKERS = 1
    DEFAULT_MAX_WORKERS = 5
    DEFAULT_HEARTBEAT_INTERVAL = 5
    DEFAULT_HEALTH_CHECK_INTERVAL = 10
    DEFAULT_SIMPLE_TASK_THRESHOLD = 50  # 简单任务的消息长度阈值
    
    def __init__(
        self,
        agent_id: str = "master",
        bus_config: Optional[BusConfig] = None,
        min_workers: int = DEFAULT_MIN_WORKERS,
        max_workers: int = DEFAULT_MAX_WORKERS,
        heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL,
        health_check_interval: int = DEFAULT_HEALTH_CHECK_INTERVAL,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            agent_id: Master Agent ID
            bus_config: 总线配置
            min_workers: 最小 Worker 数量
            max_workers: 最大 Worker 数量
            heartbeat_interval: Worker 心跳间隔（秒）
            health_check_interval: 健康检查间隔（秒）
            data_dir: 数据目录
        """
        self.agent_id = agent_id
        self.bus_config = bus_config or BusConfig()
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.heartbeat_interval = heartbeat_interval
        self.health_check_interval = health_check_interval
        
        # 数据目录
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            from ..config import settings
            self.data_dir = settings.project_root / "data"
        
        # 核心组件
        self.registry = AgentRegistry(
            heartbeat_timeout=heartbeat_interval * 3,
            storage_path=self.data_dir / "orchestration" / "registry.json",
            on_status_change=self._on_agent_status_change,
        )
        self.bus = AgentBus(config=self.bus_config, is_master=True)
        
        # 内置 Agent（用于直接处理简单任务）
        self._local_agent = None
        self._local_agent_lock = asyncio.Lock()
        
        # Worker 进程管理 {agent_id: Process}
        self._worker_processes: Dict[str, multiprocessing.Process] = {}
        
        # 任务队列 {task_id: TaskPayload}
        self._pending_tasks: Dict[str, TaskPayload] = {}
        self._task_futures: Dict[str, asyncio.Future] = {}
        
        # 运行状态
        self._running = False
        self._health_check_task: Optional[asyncio.Task] = None
        self._auto_scale_task: Optional[asyncio.Task] = None
        
        # 统计
        self._stats = {
            "tasks_total": 0,
            "tasks_local": 0,      # 本地处理的任务
            "tasks_distributed": 0, # 分发给 Worker 的任务
            "tasks_success": 0,
            "tasks_failed": 0,
        }
        
        # 注册 Master 自己
        master_info = AgentInfo(
            agent_id=self.agent_id,
            agent_type=AgentType.MASTER.value,
            process_id=os.getpid(),
            status=AgentStatus.IDLE.value,
            capabilities=["coordinate", "chat", "execute"],
        )
        self.registry.register(master_info)
    
    # ==================== 生命周期 ====================
    
    async def start(self) -> None:
        """启动 MasterAgent"""
        if self._running:
            return
        
        logger.info(f"Starting MasterAgent (id={self.agent_id})")
        
        # 启动通信总线
        await self.bus.start()
        
        # 注册消息处理器
        self._register_handlers()
        
        # 初始化本地 Agent（用于处理简单任务）
        await self._init_local_agent()
        
        # 启动最小数量的 Worker
        for i in range(self.min_workers):
            await self.spawn_worker()
        
        # 启动健康检查
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        # 启动自动扩缩（可选）
        # self._auto_scale_task = asyncio.create_task(self._auto_scale_loop())
        
        self._running = True
        logger.info(f"MasterAgent started with {self.registry.count()} agents")
    
    async def stop(self) -> None:
        """停止 MasterAgent"""
        if not self._running:
            return
        
        logger.info("Stopping MasterAgent...")
        self._running = False
        
        # 停止后台任务
        for task in [self._health_check_task, self._auto_scale_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # 停止所有 Worker
        await self._stop_all_workers()
        
        # 停止通信总线
        await self.bus.stop()
        
        # 关闭本地 Agent
        if self._local_agent:
            await self._local_agent.shutdown()
        
        logger.info("MasterAgent stopped")
    
    async def _init_local_agent(self) -> None:
        """初始化本地 Agent"""
        from ..core.agent import Agent
        
        self._local_agent = Agent()
        # MasterAgent 是主进程，启动 scheduler
        # Worker 进程不启动 scheduler，避免重复
        await self._local_agent.initialize(start_scheduler=True)
        logger.info("Local agent initialized (with scheduler)")
    
    def _register_handlers(self) -> None:
        """注册消息处理器"""
        # 心跳处理
        self.bus.on_heartbeat(self._handle_heartbeat)
        
        # 命令处理
        self.bus.register_command_handler(
            CommandType.REGISTER, self._handle_register
        )
        self.bus.register_command_handler(
            CommandType.UNREGISTER, self._handle_unregister
        )
        self.bus.register_command_handler(
            CommandType.TASK_RESULT, self._handle_task_result
        )
        self.bus.register_command_handler(
            CommandType.CHAT_RESPONSE, self._handle_chat_response
        )
    
    # ==================== 请求处理 ====================
    
    async def handle_request(
        self,
        session_id: str,
        message: str,
        session_messages: Optional[List[Dict]] = None,
        session: Any = None,
        gateway: Any = None,
    ) -> str:
        """
        处理请求
        
        这是主入口，CLI 和 IM Gateway 都通过此方法处理请求。
        
        Args:
            session_id: 会话 ID
            message: 用户消息
            session_messages: 会话历史（用于 IM 通道）
            session: Session 对象（用于 IM 通道）
            gateway: MessageGateway（用于 IM 通道）
        
        Returns:
            Agent 响应
        """
        self._stats["tasks_total"] += 1
        
        # 决定处理方式
        if self._should_handle_locally(message, session_messages):
            # 本地处理
            return await self._handle_locally(
                session_id, message, session_messages, session, gateway
            )
        else:
            # 分发给 Worker
            return await self._distribute_task(
                session_id, message, session_messages, session, gateway
            )
    
    def _should_handle_locally(
        self,
        message: str,
        session_messages: Optional[List[Dict]] = None,
    ) -> bool:
        """
        决定是否本地处理
        
        本地处理条件:
        1. 没有可用的 Worker
        2. 简单任务（消息短、无复杂上下文）
        3. Worker 全部繁忙且消息简单
        """
        # 查找空闲 Worker
        idle_worker = self.registry.find_idle_agent(exclude_ids=[self.agent_id])
        
        # 没有空闲 Worker
        if not idle_worker:
            # 检查是否有任何活跃 Worker
            workers = self.registry.list_by_type(AgentType.WORKER)
            active_workers = [w for w in workers if w.status != AgentStatus.DEAD.value]
            
            if not active_workers:
                # 没有 Worker，本地处理
                logger.debug("No workers available, handling locally")
                return True
            
            # 有 Worker 但都在忙，检查消息复杂度
            if len(message) < self.DEFAULT_SIMPLE_TASK_THRESHOLD:
                logger.debug("All workers busy, simple message, handling locally")
                return True
            
            # 复杂任务，等待 Worker
            return False
        
        # 有空闲 Worker
        # 简单消息仍可本地处理（减少通信开销）
        if len(message) < 30 and not session_messages:
            logger.debug("Simple message, handling locally")
            return True
        
        return False
    
    async def _handle_locally(
        self,
        session_id: str,
        message: str,
        session_messages: Optional[List[Dict]] = None,
        session: Any = None,
        gateway: Any = None,
    ) -> str:
        """本地处理请求"""
        self._stats["tasks_local"] += 1
        logger.info(f"Handling locally: {message[:50]}...")
        
        async with self._local_agent_lock:
            try:
                if session_messages is not None:
                    # IM 通道：使用 session 上下文
                    response = await self._local_agent.chat_with_session(
                        message=message,
                        session_messages=session_messages,
                        session_id=session_id,
                        session=session,
                        gateway=gateway,
                    )
                else:
                    # CLI：使用全局上下文
                    response = await self._local_agent.chat(message, session_id=session_id)
                
                self._stats["tasks_success"] += 1
                return response
                
            except Exception as e:
                self._stats["tasks_failed"] += 1
                logger.error(f"Local handling error: {e}", exc_info=True)
                return f"处理出错: {str(e)}"
    
    async def _distribute_task(
        self,
        session_id: str,
        message: str,
        session_messages: Optional[List[Dict]] = None,
        session: Any = None,
        gateway: Any = None,
    ) -> str:
        """分发任务给 Worker"""
        self._stats["tasks_distributed"] += 1
        
        # 查找空闲 Worker
        worker = self.registry.find_idle_agent(exclude_ids=[self.agent_id])
        
        if not worker:
            # 所有 Worker 都在忙，等待或本地处理
            logger.warning("No idle worker, falling back to local handling")
            return await self._handle_locally(
                session_id, message, session_messages, session, gateway
            )
        
        # 创建任务
        task_id = str(uuid.uuid4())[:8]
        task = TaskPayload(
            task_id=task_id,
            task_type="chat",
            description=f"处理用户消息: {message[:50]}...",
            content=message,
            session_id=session_id,
            context={
                "session_messages": session_messages or [],
                # 注意：session 和 gateway 不能序列化，需要在结果返回时重新关联
                "has_session": session is not None,
                "has_gateway": gateway is not None,
            },
        )
        
        # 记录任务
        self._pending_tasks[task_id] = task
        
        # 创建 Future 等待结果
        future = asyncio.get_event_loop().create_future()
        self._task_futures[task_id] = future
        
        # 标记 Worker 为 BUSY
        self.registry.set_agent_task(worker.agent_id, task_id, task.description)
        
        logger.info(f"Distributing task {task_id} to worker {worker.agent_id}")
        
        try:
            # 发送任务
            response = await self.bus.send_command(
                target_id=worker.agent_id,
                command_type=CommandType.ASSIGN_TASK,
                payload=task.to_dict(),
                wait_response=False,  # 异步等待结果
            )
            
            # 等待任务完成
            result = await asyncio.wait_for(future, timeout=task.timeout_seconds)
            
            self._stats["tasks_success"] += 1
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"Task {task_id} timeout")
            self._stats["tasks_failed"] += 1
            
            # 清理
            self.registry.clear_agent_task(worker.agent_id, success=False)
            self._pending_tasks.pop(task_id, None)
            self._task_futures.pop(task_id, None)
            
            return "任务处理超时，请稍后重试"
            
        except Exception as e:
            logger.error(f"Task distribution error: {e}", exc_info=True)
            self._stats["tasks_failed"] += 1
            
            # 清理
            self.registry.clear_agent_task(worker.agent_id, success=False)
            self._pending_tasks.pop(task_id, None)
            self._task_futures.pop(task_id, None)
            
            return f"任务处理出错: {str(e)}"
    
    # ==================== 消息处理器 ====================
    
    async def _handle_heartbeat(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理心跳"""
        agent_info = AgentInfo.from_dict(message.payload)
        self.registry.heartbeat(agent_info.agent_id, agent_info)
        return None  # 心跳不需要响应
    
    async def _handle_register(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理注册请求"""
        agent_info = AgentInfo.from_dict(message.payload)
        success = self.registry.register(agent_info)
        
        # 广播事件
        if success:
            await self.bus.broadcast_event(
                EventType.AGENT_REGISTERED,
                {"agent_id": agent_info.agent_id, "type": agent_info.agent_type},
            )
        
        return AgentMessage.response(
            sender_id=self.agent_id,
            target_id=message.sender_id,
            correlation_id=message.msg_id,
            payload={"success": success},
        )
    
    async def _handle_unregister(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理注销请求"""
        agent_id = message.payload.get("agent_id")
        success = self.registry.unregister(agent_id)
        
        if success:
            await self.bus.broadcast_event(
                EventType.AGENT_UNREGISTERED,
                {"agent_id": agent_id},
            )
        
        return AgentMessage.response(
            sender_id=self.agent_id,
            target_id=message.sender_id,
            correlation_id=message.msg_id,
            payload={"success": success},
        )
    
    async def _handle_task_result(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理任务结果"""
        result = TaskResult.from_dict(message.payload)
        task_id = result.task_id
        
        logger.info(f"Task {task_id} result: success={result.success}")
        
        # 清理任务状态
        worker_id = message.sender_id
        self.registry.clear_agent_task(worker_id, success=result.success)
        self._pending_tasks.pop(task_id, None)
        
        # 完成 Future
        future = self._task_futures.pop(task_id, None)
        if future and not future.done():
            if result.success:
                future.set_result(result.result or "任务完成")
            else:
                future.set_result(result.error or "任务失败")
        
        return None  # 不需要响应
    
    async def _handle_chat_response(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理对话响应"""
        # 类似 task_result，但专门用于对话
        return await self._handle_task_result(message)
    
    # ==================== Worker 管理 ====================
    
    async def spawn_worker(
        self,
        agent_type: str = "worker",
        capabilities: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        创建新的 Worker 进程
        
        Args:
            agent_type: Worker 类型
            capabilities: 能力列表
        
        Returns:
            Worker ID 或 None
        """
        # 检查是否超过最大数量
        current_workers = len([
            w for w in self.registry.list_by_type(AgentType.WORKER)
            if w.status != AgentStatus.DEAD.value
        ])
        
        if current_workers >= self.max_workers:
            logger.warning(f"Max workers ({self.max_workers}) reached")
            return None
        
        # 生成 Worker ID
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        
        # 创建 Worker 进程
        process = multiprocessing.Process(
            target=_worker_process_entry,
            args=(
                worker_id,
                self.bus_config.router_address,
                self.bus_config.pub_address,
                self.heartbeat_interval,
                capabilities or ["chat", "execute"],
                str(self.data_dir),
            ),
            daemon=True,
        )
        
        process.start()
        self._worker_processes[worker_id] = process
        
        logger.info(f"Spawned worker {worker_id} (pid={process.pid})")
        
        # 等待 Worker 注册（最多 10 秒）
        for _ in range(100):
            await asyncio.sleep(0.1)
            agent_info = self.registry.get(worker_id)
            if agent_info and agent_info.status == AgentStatus.IDLE.value:
                logger.info(f"Worker {worker_id} registered successfully")
                return worker_id
        
        logger.warning(f"Worker {worker_id} registration timeout")
        return worker_id
    
    async def terminate_worker(self, worker_id: str, graceful: bool = True) -> bool:
        """
        终止 Worker
        
        Args:
            worker_id: Worker ID
            graceful: 是否优雅关闭
        
        Returns:
            是否成功
        """
        if worker_id not in self._worker_processes:
            logger.warning(f"Worker {worker_id} not found in process list")
            return False
        
        if graceful:
            # 发送关闭命令
            try:
                await self.bus.send_command(
                    target_id=worker_id,
                    command_type=CommandType.SHUTDOWN,
                    payload={},
                    wait_response=False,
                )
                
                # 等待进程退出
                process = self._worker_processes[worker_id]
                process.join(timeout=5)
                
                if process.is_alive():
                    logger.warning(f"Worker {worker_id} didn't exit gracefully, killing")
                    process.terminate()
                    process.join(timeout=2)
                    
            except Exception as e:
                logger.error(f"Error shutting down worker {worker_id}: {e}")
        else:
            # 强制终止
            process = self._worker_processes[worker_id]
            process.terminate()
            process.join(timeout=2)
        
        # 清理
        del self._worker_processes[worker_id]
        self.registry.unregister(worker_id)
        
        logger.info(f"Worker {worker_id} terminated")
        return True
    
    async def _stop_all_workers(self) -> None:
        """停止所有 Worker"""
        worker_ids = list(self._worker_processes.keys())
        
        for worker_id in worker_ids:
            await self.terminate_worker(worker_id, graceful=True)
    
    # ==================== 监控 ====================
    
    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while self._running:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                # 检查心跳超时
                dead_agents = self.registry.check_heartbeats()
                
                for agent_id in dead_agents:
                    # 处理死亡的 Worker
                    if agent_id in self._worker_processes:
                        await self._handle_dead_worker(agent_id)
                
                # 清理长时间死亡的 Agent
                self.registry.cleanup_dead_agents(max_age_hours=1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _handle_dead_worker(self, worker_id: str) -> None:
        """处理死亡的 Worker"""
        logger.warning(f"Worker {worker_id} is dead, handling...")
        
        # 检查进程状态
        process = self._worker_processes.get(worker_id)
        if process:
            if not process.is_alive():
                logger.info(f"Worker {worker_id} process already terminated")
            else:
                logger.warning(f"Worker {worker_id} process still alive but not responding")
                process.terminate()
            
            del self._worker_processes[worker_id]
        
        # 获取该 Worker 的未完成任务
        agent_info = self.registry.get(worker_id)
        if agent_info and agent_info.current_task:
            task_id = agent_info.current_task
            logger.warning(f"Reassigning task {task_id} from dead worker")
            
            # 标记任务失败
            future = self._task_futures.pop(task_id, None)
            if future and not future.done():
                future.set_result("Worker 故障，请重试")
            
            self._pending_tasks.pop(task_id, None)
        
        # 注销死亡的 Worker
        self.registry.unregister(worker_id)
        
        # 检查是否需要创建新 Worker
        current_workers = len([
            w for w in self.registry.list_by_type(AgentType.WORKER)
            if w.status != AgentStatus.DEAD.value
        ])
        
        if current_workers < self.min_workers:
            logger.info("Spawning replacement worker...")
            await self.spawn_worker()
    
    def _on_agent_status_change(
        self,
        agent_id: str,
        old_status: AgentStatus,
        new_status: AgentStatus,
    ) -> None:
        """Agent 状态变更回调"""
        logger.debug(f"Agent {agent_id} status: {old_status.value} -> {new_status.value}")
    
    # ==================== 统计和监控 ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self._stats,
            "registry": self.registry.get_dashboard_data(),
            "bus": self.bus.get_stats(),
            "worker_processes": len(self._worker_processes),
            "pending_tasks": len(self._pending_tasks),
        }
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取仪表盘数据"""
        return self.registry.get_dashboard_data()


# ==================== Worker 进程入口 ====================

def _worker_process_entry(
    worker_id: str,
    router_address: str,
    pub_address: str,
    heartbeat_interval: int,
    capabilities: List[str],
    data_dir: str,
) -> None:
    """
    Worker 进程入口函数
    
    在独立进程中运行，创建 WorkerAgent 实例
    """
    import asyncio
    
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s - {worker_id} - %(levelname)s - %(message)s",
    )
    
    async def run_worker():
        from .worker import WorkerAgent
        
        worker = WorkerAgent(
            agent_id=worker_id,
            router_address=router_address,
            pub_address=pub_address,
            heartbeat_interval=heartbeat_interval,
            capabilities=capabilities,
            data_dir=Path(data_dir),
        )
        
        await worker.start()
        
        try:
            # 保持运行直到收到停止信号
            while worker.is_running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            await worker.stop()
    
    asyncio.run(run_worker())
