"""
Agent 通信总线

基于 ZeroMQ 的进程间通信层，支持:
- ROUTER/DEALER: 双向命令/响应通信
- PUB/SUB: 事件广播
- 异步消息处理
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

try:
    import zmq
    import zmq.asyncio

    HAS_ZMQ = True
except ImportError:
    HAS_ZMQ = False
    zmq = None  # type: ignore[assignment]
    from openakita.tools._import_helper import import_or_hint as _zmq_hint
    _ZMQ_HINT = _zmq_hint("zmq")  # 预生成提示信息

import contextlib

from .messages import (
    AgentMessage,
    CommandType,
    EventType,
    MessageType,
)

logger = logging.getLogger(__name__)


@dataclass
class BusConfig:
    """总线配置"""

    router_address: str = "tcp://127.0.0.1:5555"  # ROUTER 地址（命令/响应）
    pub_address: str = "tcp://127.0.0.1:5556"  # PUB 地址（事件广播）
    recv_timeout_ms: int = 1000  # 接收超时（毫秒）
    send_timeout_ms: int = 5000  # 发送超时（毫秒）
    high_water_mark: int = 1000  # 高水位标记


# 消息处理器类型
MessageHandler = Callable[[AgentMessage], Awaitable[AgentMessage | None]]


class AgentBus:
    """
    Agent 通信总线

    主进程端（Master）使用 ROUTER + PUB
    工作进程端（Worker）使用 DEALER + SUB

    通信模式:
    - 命令/响应: Master ROUTER <-> Worker DEALER
    - 事件广播: Master PUB -> Worker SUB
    """

    def __init__(
        self,
        config: BusConfig | None = None,
        is_master: bool = True,
    ):
        """
        Args:
            config: 总线配置
            is_master: 是否是主进程端
        """
        if not HAS_ZMQ:
            raise ImportError(_ZMQ_HINT)

        self.config = config or BusConfig()
        self.is_master = is_master

        # ZMQ 上下文（使用异步版本）
        self._context: zmq.asyncio.Context | None = None

        # Socket
        self._router: zmq.asyncio.Socket | None = None  # Master 端
        self._pub: zmq.asyncio.Socket | None = None  # Master 端
        self._dealer: zmq.asyncio.Socket | None = None  # Worker 端
        self._sub: zmq.asyncio.Socket | None = None  # Worker 端

        # 消息处理器
        self._command_handlers: dict[str, MessageHandler] = {}
        self._event_handlers: dict[str, MessageHandler] = {}
        self._default_handler: MessageHandler | None = None

        # 等待响应的请求 {correlation_id: asyncio.Future}
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._pending_lock = threading.Lock()

        # 运行状态
        self._running = False
        self._recv_task: asyncio.Task | None = None

        # 统计
        self._stats = {
            "messages_sent": 0,
            "messages_received": 0,
            "errors": 0,
        }

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动总线"""
        if self._running:
            return

        # 创建 ZMQ 上下文
        self._context = zmq.asyncio.Context()

        if self.is_master:
            await self._start_master()
        else:
            await self._start_worker()

        self._running = True

        # 启动接收循环
        self._recv_task = asyncio.create_task(self._receive_loop())

        logger.info(f"AgentBus started (is_master={self.is_master})")

    async def _start_master(self) -> None:
        """启动 Master 端"""
        # ROUTER socket（接收 Worker 消息，发送命令）
        self._router = self._context.socket(zmq.ROUTER)
        self._router.setsockopt(zmq.RCVTIMEO, self.config.recv_timeout_ms)
        self._router.setsockopt(zmq.SNDTIMEO, self.config.send_timeout_ms)
        self._router.setsockopt(zmq.SNDHWM, self.config.high_water_mark)
        self._router.setsockopt(zmq.RCVHWM, self.config.high_water_mark)
        self._router.bind(self.config.router_address)
        logger.info(f"ROUTER bound to {self.config.router_address}")

        # PUB socket（广播事件）
        self._pub = self._context.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, self.config.high_water_mark)
        self._pub.bind(self.config.pub_address)
        logger.info(f"PUB bound to {self.config.pub_address}")

    async def _start_worker(self) -> None:
        """启动 Worker 端"""
        # DEALER socket（发送消息给 Master，接收命令）
        self._dealer = self._context.socket(zmq.DEALER)
        self._dealer.setsockopt(zmq.RCVTIMEO, self.config.recv_timeout_ms)
        self._dealer.setsockopt(zmq.SNDTIMEO, self.config.send_timeout_ms)
        self._dealer.connect(self.config.router_address)
        logger.info(f"DEALER connected to {self.config.router_address}")

        # SUB socket（接收广播事件）
        self._sub = self._context.socket(zmq.SUB)
        self._sub.setsockopt(zmq.SUBSCRIBE, b"")  # 订阅所有消息
        self._sub.setsockopt(zmq.RCVTIMEO, self.config.recv_timeout_ms)
        self._sub.connect(self.config.pub_address)
        logger.info(f"SUB connected to {self.config.pub_address}")

    async def stop(self) -> None:
        """停止总线"""
        self._running = False

        # 取消接收任务
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task

        # 关闭所有 socket
        for socket in [self._router, self._pub, self._dealer, self._sub]:
            if socket:
                socket.close()

        # 销毁上下文
        if self._context:
            self._context.term()

        # 取消所有等待中的请求
        with self._pending_lock:
            for future in self._pending_requests.values():
                if not future.done():
                    future.cancel()
            self._pending_requests.clear()

        logger.info("AgentBus stopped")

    # ==================== 消息发送 ====================

    async def send_command(
        self,
        target_id: str,
        command_type: CommandType,
        payload: dict[str, Any],
        sender_id: str = "master",
        wait_response: bool = True,
        timeout: float = 30.0,
    ) -> AgentMessage | None:
        """
        发送命令消息

        Args:
            target_id: 目标 Agent ID
            command_type: 命令类型
            payload: 消息负载
            sender_id: 发送者 ID
            wait_response: 是否等待响应
            timeout: 等待超时（秒）

        Returns:
            响应消息（如果 wait_response=True）
        """
        message = AgentMessage.command(
            sender_id=sender_id,
            target_id=target_id,
            command_type=command_type,
            payload=payload,
        )

        if wait_response:
            return await self._send_and_wait(message, target_id, timeout)
        else:
            await self._send_to_worker(message, target_id)
            return None

    async def send_response(
        self,
        target_id: str,
        correlation_id: str,
        payload: dict[str, Any],
        sender_id: str,
    ) -> None:
        """
        发送响应消息

        Args:
            target_id: 目标 Agent ID
            correlation_id: 关联的请求 ID
            payload: 响应负载
            sender_id: 发送者 ID
        """
        message = AgentMessage.response(
            sender_id=sender_id,
            target_id=target_id,
            correlation_id=correlation_id,
            payload=payload,
        )

        if self.is_master:
            await self._send_to_worker(message, target_id)
        else:
            await self._send_to_master(message)

    async def broadcast_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        sender_id: str = "master",
    ) -> None:
        """
        广播事件

        仅 Master 可以广播

        Args:
            event_type: 事件类型
            payload: 事件负载
            sender_id: 发送者 ID
        """
        if not self.is_master:
            logger.warning("Only master can broadcast events")
            return

        message = AgentMessage.event(
            sender_id=sender_id,
            event_type=event_type,
            payload=payload,
        )

        try:
            await self._pub.send(message.to_bytes())
            self._stats["messages_sent"] += 1
            logger.debug(f"Broadcast event: {event_type.value}")
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Failed to broadcast event: {e}")

    async def send_heartbeat(
        self,
        sender_id: str,
        agent_info: Any,  # AgentInfo
    ) -> None:
        """
        发送心跳消息

        仅 Worker 使用

        Args:
            sender_id: 发送者 ID
            agent_info: Agent 信息
        """
        if self.is_master:
            return

        message = AgentMessage.heartbeat(sender_id, agent_info)
        await self._send_to_master(message)

    # ==================== 内部发送方法 ====================

    async def _send_to_worker(self, message: AgentMessage, worker_id: str) -> None:
        """发送消息给指定 Worker（Master 端）"""
        if not self._router:
            raise RuntimeError("Router socket not initialized")

        try:
            # ROUTER socket 需要先发送 identity，再发送消息
            # identity 是 Worker 连接时设置的
            await self._router.send_multipart(
                [
                    worker_id.encode("utf-8"),  # Worker identity
                    message.to_bytes(),
                ]
            )
            self._stats["messages_sent"] += 1
            logger.debug(f"Sent to worker {worker_id}: {message.msg_type}")
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Failed to send to worker {worker_id}: {e}")
            raise

    async def _send_to_master(self, message: AgentMessage) -> None:
        """发送消息给 Master（Worker 端）"""
        if not self._dealer:
            raise RuntimeError("Dealer socket not initialized")

        try:
            await self._dealer.send(message.to_bytes())
            self._stats["messages_sent"] += 1
            logger.debug(f"Sent to master: {message.msg_type}")
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Failed to send to master: {e}")
            raise

    async def _send_and_wait(
        self,
        message: AgentMessage,
        target_id: str,
        timeout: float,
    ) -> AgentMessage | None:
        """发送消息并等待响应"""
        # 创建 Future
        future = asyncio.get_event_loop().create_future()
        correlation_id = message.msg_id

        with self._pending_lock:
            self._pending_requests[correlation_id] = future

        try:
            # 发送消息
            await self._send_to_worker(message, target_id)

            # 等待响应
            response = await asyncio.wait_for(future, timeout=timeout)
            return response

        except TimeoutError:
            logger.warning(f"Request timeout: {correlation_id}")
            return None
        finally:
            with self._pending_lock:
                self._pending_requests.pop(correlation_id, None)

    # ==================== 消息接收 ====================

    async def _receive_loop(self) -> None:
        """消息接收循环"""
        while self._running:
            try:
                if self.is_master:
                    await self._receive_master()
                else:
                    await self._receive_worker()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Receive loop error: {e}")
                await asyncio.sleep(0.1)

    async def _receive_master(self) -> None:
        """Master 端接收消息"""
        if not self._router:
            return

        try:
            # ROUTER 接收格式: [identity, message]
            frames = await self._router.recv_multipart(flags=zmq.NOBLOCK)

            if len(frames) >= 2:
                worker_id = frames[0].decode("utf-8")
                message = AgentMessage.from_bytes(frames[1])

                self._stats["messages_received"] += 1
                await self._handle_message(message, worker_id)

        except zmq.Again:
            # 没有消息，让出控制权
            await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Master receive error: {e}")

    async def _receive_worker(self) -> None:
        """Worker 端接收消息"""
        # 使用 poll 同时检查多个 socket
        poller = zmq.asyncio.Poller()

        if self._dealer:
            poller.register(self._dealer, zmq.POLLIN)
        if self._sub:
            poller.register(self._sub, zmq.POLLIN)

        try:
            socks = dict(await poller.poll(timeout=100))  # 100ms 超时

            # 检查 DEALER（来自 Master 的命令）
            if self._dealer in socks:
                data = await self._dealer.recv(flags=zmq.NOBLOCK)
                message = AgentMessage.from_bytes(data)
                self._stats["messages_received"] += 1
                await self._handle_message(message, "master")

            # 检查 SUB（广播事件）
            if self._sub in socks:
                data = await self._sub.recv(flags=zmq.NOBLOCK)
                message = AgentMessage.from_bytes(data)
                self._stats["messages_received"] += 1
                await self._handle_message(message, "master")

        except zmq.Again:
            pass
        except Exception as e:
            logger.error(f"Worker receive error: {e}")

    async def _handle_message(self, message: AgentMessage, sender_identity: str) -> None:
        """处理收到的消息"""
        logger.debug(f"Received message: {message.msg_type} from {sender_identity}")

        # 检查是否是响应消息
        if message.msg_type == MessageType.RESPONSE.value:
            await self._handle_response(message)
            return

        # 查找处理器
        handler = None

        if message.msg_type == MessageType.COMMAND.value and message.command_type:
            handler = self._command_handlers.get(message.command_type)
        elif message.msg_type == MessageType.EVENT.value and message.event_type:
            handler = self._event_handlers.get(message.event_type)
        elif message.msg_type == MessageType.HEARTBEAT.value:
            handler = self._command_handlers.get("heartbeat")

        if not handler:
            handler = self._default_handler

        if handler:
            try:
                response = await handler(message)

                # 如果处理器返回响应，自动发送
                if response and message.msg_type == MessageType.COMMAND.value:
                    if self.is_master:
                        await self._send_to_worker(response, sender_identity)
                    else:
                        await self._send_to_master(response)

            except Exception as e:
                logger.error(f"Handler error for {message.msg_type}: {e}")
        else:
            logger.warning(f"No handler for message type: {message.msg_type}")

    async def _handle_response(self, message: AgentMessage) -> None:
        """处理响应消息"""
        correlation_id = message.correlation_id
        if not correlation_id:
            logger.warning("Response without correlation_id")
            return

        with self._pending_lock:
            future = self._pending_requests.get(correlation_id)

        if future and not future.done():
            future.set_result(message)
        else:
            logger.warning(f"No pending request for correlation_id: {correlation_id}")

    # ==================== 处理器注册 ====================

    def on_command(self, command_type: CommandType) -> Callable:
        """
        命令处理器装饰器

        Usage:
            @bus.on_command(CommandType.CHAT_REQUEST)
            async def handle_chat(message: AgentMessage) -> AgentMessage:
                ...
        """

        def decorator(handler: MessageHandler) -> MessageHandler:
            self._command_handlers[command_type.value] = handler
            return handler

        return decorator

    def on_event(self, event_type: EventType) -> Callable:
        """
        事件处理器装饰器

        Usage:
            @bus.on_event(EventType.AGENT_REGISTERED)
            async def handle_registered(message: AgentMessage) -> None:
                ...
        """

        def decorator(handler: MessageHandler) -> MessageHandler:
            self._event_handlers[event_type.value] = handler
            return handler

        return decorator

    def on_heartbeat(self, handler: MessageHandler) -> None:
        """注册心跳处理器"""
        self._command_handlers["heartbeat"] = handler

    def set_default_handler(self, handler: MessageHandler) -> None:
        """设置默认处理器"""
        self._default_handler = handler

    def register_command_handler(
        self,
        command_type: CommandType,
        handler: MessageHandler,
    ) -> None:
        """注册命令处理器"""
        self._command_handlers[command_type.value] = handler

    def register_event_handler(
        self,
        event_type: EventType,
        handler: MessageHandler,
    ) -> None:
        """注册事件处理器"""
        self._event_handlers[event_type.value] = handler

    # ==================== 统计 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            **self._stats,
            "pending_requests": len(self._pending_requests),
            "is_master": self.is_master,
            "running": self._running,
        }


class WorkerBus(AgentBus):
    """
    Worker 端通信总线

    便捷类，自动设置 is_master=False 和 identity
    """

    def __init__(
        self,
        worker_id: str,
        config: BusConfig | None = None,
    ):
        """
        Args:
            worker_id: Worker 的唯一标识
            config: 总线配置
        """
        super().__init__(config=config, is_master=False)
        self.worker_id = worker_id

    async def _start_worker(self) -> None:
        """启动 Worker 端（设置 identity）"""
        # DEALER socket
        self._dealer = self._context.socket(zmq.DEALER)
        # 设置 identity，让 Master 可以识别这个 Worker
        self._dealer.setsockopt(zmq.IDENTITY, self.worker_id.encode("utf-8"))
        self._dealer.setsockopt(zmq.RCVTIMEO, self.config.recv_timeout_ms)
        self._dealer.setsockopt(zmq.SNDTIMEO, self.config.send_timeout_ms)
        self._dealer.connect(self.config.router_address)
        logger.info(f"DEALER connected to {self.config.router_address} as {self.worker_id}")

        # SUB socket
        self._sub = self._context.socket(zmq.SUB)
        self._sub.setsockopt(zmq.SUBSCRIBE, b"")
        self._sub.setsockopt(zmq.RCVTIMEO, self.config.recv_timeout_ms)
        self._sub.connect(self.config.pub_address)
        logger.info(f"SUB connected to {self.config.pub_address}")
