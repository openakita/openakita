"""
消息网关

统一消息入口/出口:
- 消息路由
- 会话管理集成
- 媒体预处理
- Agent 调用
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable, Any

from .types import UnifiedMessage, OutgoingMessage, MessageContent
from .base import ChannelAdapter
from ..sessions import SessionManager, Session

logger = logging.getLogger(__name__)

# Agent 处理函数类型
AgentHandler = Callable[[Session, str], Awaitable[str]]


class MessageGateway:
    """
    统一消息网关
    
    职责:
    - 管理多个通道适配器
    - 将收到的消息路由到会话
    - 调用 Agent 处理
    - 将回复发送回通道
    """
    
    def __init__(
        self,
        session_manager: SessionManager,
        agent_handler: Optional[AgentHandler] = None,
    ):
        """
        Args:
            session_manager: 会话管理器
            agent_handler: Agent 处理函数 (session, message) -> response
        """
        self.session_manager = session_manager
        self.agent_handler = agent_handler
        
        # 注册的适配器 {channel_name: adapter}
        self._adapters: dict[str, ChannelAdapter] = {}
        
        # 消息处理队列
        self._message_queue: asyncio.Queue[UnifiedMessage] = asyncio.Queue()
        
        # 处理任务
        self._processing_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 中间件
        self._pre_process_hooks: list[Callable[[UnifiedMessage], Awaitable[UnifiedMessage]]] = []
        self._post_process_hooks: list[Callable[[UnifiedMessage, str], Awaitable[str]]] = []
    
    async def start(self) -> None:
        """启动网关"""
        self._running = True
        
        # 启动所有适配器
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
                logger.info(f"Started adapter: {name}")
            except Exception as e:
                logger.error(f"Failed to start adapter {name}: {e}")
        
        # 启动消息处理循环
        self._processing_task = asyncio.create_task(self._process_loop())
        
        logger.info(f"MessageGateway started with {len(self._adapters)} adapters")
    
    async def stop(self) -> None:
        """停止网关"""
        self._running = False
        
        # 停止处理循环
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有适配器
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
                logger.info(f"Stopped adapter: {name}")
            except Exception as e:
                logger.error(f"Failed to stop adapter {name}: {e}")
        
        logger.info("MessageGateway stopped")
    
    # ==================== 适配器管理 ====================
    
    async def register_adapter(self, adapter: ChannelAdapter) -> None:
        """
        注册适配器
        
        Args:
            adapter: 通道适配器
        """
        name = adapter.channel_name
        
        if name in self._adapters:
            logger.warning(f"Adapter {name} already registered, replacing")
            await self._adapters[name].stop()
        
        # 设置消息回调
        adapter.on_message(self._on_message)
        
        self._adapters[name] = adapter
        logger.info(f"Registered adapter: {name}")
        
        # 如果网关已运行，启动适配器
        if self._running:
            await adapter.start()
    
    def get_adapter(self, channel: str) -> Optional[ChannelAdapter]:
        """获取适配器"""
        return self._adapters.get(channel)
    
    def list_adapters(self) -> list[str]:
        """列出所有适配器"""
        return list(self._adapters.keys())
    
    # ==================== 消息处理 ====================
    
    async def _on_message(self, message: UnifiedMessage) -> None:
        """
        消息回调（由适配器调用）
        """
        await self._message_queue.put(message)
    
    async def _process_loop(self) -> None:
        """消息处理循环"""
        while self._running:
            try:
                # 从队列获取消息
                message = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0
                )
                
                # 处理消息
                await self._handle_message(message)
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")
    
    async def _handle_message(self, message: UnifiedMessage) -> None:
        """
        处理单条消息
        """
        try:
            # 1. 预处理钩子
            for hook in self._pre_process_hooks:
                message = await hook(message)
            
            # 2. 获取或创建会话
            session = self.session_manager.get_session(
                channel=message.channel,
                chat_id=message.chat_id,
                user_id=message.user_id,
            )
            
            # 3. 记录消息到会话
            session.add_message(
                role="user",
                content=message.plain_text,
                message_id=message.id,
                channel_message_id=message.channel_message_id,
            )
            
            # 4. 调用 Agent 处理
            response_text = await self._call_agent(session, message)
            
            # 5. 后处理钩子
            for hook in self._post_process_hooks:
                response_text = await hook(message, response_text)
            
            # 6. 记录响应到会话
            session.add_message(
                role="assistant",
                content=response_text,
            )
            
            # 7. 发送响应
            await self._send_response(message, response_text)
            
        except Exception as e:
            logger.error(f"Error handling message {message.id}: {e}")
            # 发送错误提示
            await self._send_error(message, str(e))
    
    async def _call_agent(self, session: Session, message: UnifiedMessage) -> str:
        """
        调用 Agent 处理消息
        """
        if not self.agent_handler:
            return "Agent handler not configured"
        
        try:
            # 获取纯文本输入
            input_text = message.plain_text
            
            # 调用 Agent
            response = await self.agent_handler(session, input_text)
            return response
            
        except Exception as e:
            logger.error(f"Agent error: {e}")
            return f"处理出错: {str(e)}"
    
    async def _send_response(self, original: UnifiedMessage, response: str) -> None:
        """
        发送响应
        """
        adapter = self._adapters.get(original.channel)
        if not adapter:
            logger.error(f"No adapter for channel: {original.channel}")
            return
        
        # 构建响应消息（不使用 parse_mode，避免特殊字符导致解析失败）
        outgoing = OutgoingMessage.text(
            chat_id=original.chat_id,
            text=response,
            reply_to=original.channel_message_id,
            thread_id=original.thread_id,
        )
        
        try:
            await adapter.send_message(outgoing)
        except Exception as e:
            logger.error(f"Failed to send response: {e}")
            # 发送失败时尝试发送纯文本错误提示
            try:
                await adapter.send_text(
                    chat_id=original.chat_id,
                    text=f"消息发送失败，请稍后重试。",
                )
            except:
                pass
    
    async def _send_error(self, original: UnifiedMessage, error: str) -> None:
        """
        发送错误提示
        """
        adapter = self._adapters.get(original.channel)
        if not adapter:
            return
        
        try:
            await adapter.send_text(
                chat_id=original.chat_id,
                text=f"❌ 处理出错: {error}",
                reply_to=original.channel_message_id,
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")
    
    # ==================== 主动发送 ====================
    
    async def send(
        self,
        channel: str,
        chat_id: str,
        text: str,
        **kwargs,
    ) -> Optional[str]:
        """
        主动发送消息
        
        Args:
            channel: 目标通道
            chat_id: 目标聊天
            text: 消息文本
        
        Returns:
            消息 ID 或 None
        """
        adapter = self._adapters.get(channel)
        if not adapter:
            logger.error(f"No adapter for channel: {channel}")
            return None
        
        try:
            return await adapter.send_text(chat_id, text, **kwargs)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    async def send_to_session(
        self,
        session: Session,
        text: str,
        **kwargs,
    ) -> Optional[str]:
        """
        发送消息到会话
        """
        return await self.send(
            channel=session.channel,
            chat_id=session.chat_id,
            text=text,
            **kwargs,
        )
    
    async def broadcast(
        self,
        text: str,
        channels: Optional[list[str]] = None,
        user_ids: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """
        广播消息
        
        Args:
            text: 消息文本
            channels: 目标通道列表（None 表示所有）
            user_ids: 目标用户列表（None 表示所有）
        
        Returns:
            {channel: sent_count}
        """
        results = {}
        
        # 获取目标会话
        sessions = self.session_manager.list_sessions()
        
        for session in sessions:
            # 过滤通道
            if channels and session.channel not in channels:
                continue
            
            # 过滤用户
            if user_ids and session.user_id not in user_ids:
                continue
            
            try:
                await self.send_to_session(session, text)
                results[session.channel] = results.get(session.channel, 0) + 1
            except Exception as e:
                logger.error(f"Broadcast error to {session.id}: {e}")
        
        return results
    
    # ==================== 中间件 ====================
    
    def add_pre_process_hook(
        self,
        hook: Callable[[UnifiedMessage], Awaitable[UnifiedMessage]],
    ) -> None:
        """
        添加预处理钩子
        
        在消息处理前调用，可以修改消息
        """
        self._pre_process_hooks.append(hook)
    
    def add_post_process_hook(
        self,
        hook: Callable[[UnifiedMessage, str], Awaitable[str]],
    ) -> None:
        """
        添加后处理钩子
        
        在 Agent 响应后调用，可以修改响应
        """
        self._post_process_hooks.append(hook)
    
    # ==================== 统计 ====================
    
    def get_stats(self) -> dict:
        """获取网关统计"""
        return {
            "running": self._running,
            "adapters": {
                name: adapter.is_running
                for name, adapter in self._adapters.items()
            },
            "queue_size": self._message_queue.qsize(),
            "sessions": self.session_manager.get_session_count(),
        }
