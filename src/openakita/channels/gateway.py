"""
消息网关

统一消息入口/出口:
- 消息路由
- 会话管理集成
- 媒体预处理（图片、语音）
- Agent 调用
"""

import asyncio
import logging
import base64
import httpx
from pathlib import Path
from typing import Optional, Callable, Awaitable, Any

from .types import UnifiedMessage, OutgoingMessage, MessageContent, MediaFile
from .base import ChannelAdapter
from ..sessions import SessionManager, Session
from ..config import settings

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
            # 1. 发送"正在输入"状态
            await self._send_typing(message)
            
            # 2. 预处理钩子
            for hook in self._pre_process_hooks:
                message = await hook(message)
            
            # 3. 媒体预处理（下载图片、语音转文字）
            await self._preprocess_media(message)
            
            # 4. 获取或创建会话
            session = self.session_manager.get_session(
                channel=message.channel,
                chat_id=message.chat_id,
                user_id=message.user_id,
            )
            
            # 5. 记录消息到会话
            session.add_message(
                role="user",
                content=message.plain_text,
                message_id=message.id,
                channel_message_id=message.channel_message_id,
            )
            
            # 6. 调用 Agent 处理（期间持续发送 typing 状态）
            response_text = await self._call_agent_with_typing(session, message)
            
            # 7. 后处理钩子
            for hook in self._post_process_hooks:
                response_text = await hook(message, response_text)
            
            # 8. 记录响应到会话
            session.add_message(
                role="assistant",
                content=response_text,
            )
            
            # 9. 发送响应
            await self._send_response(message, response_text)
            
        except Exception as e:
            logger.error(f"Error handling message {message.id}: {e}")
            # 发送错误提示
            await self._send_error(message, str(e))
    
    async def _preprocess_media(self, message: UnifiedMessage) -> None:
        """
        预处理媒体文件（下载语音、图片到本地）
        
        注意：语音转文字由 Agent 自主实现，这里只负责下载
        """
        adapter = self._adapters.get(message.channel)
        if not adapter:
            return
        
        # 处理语音消息 - 下载到本地
        for voice in message.content.voices:
            try:
                if not voice.local_path:
                    local_path = await adapter.download_media(voice)
                    voice.local_path = str(local_path)
                    logger.info(f"Voice downloaded: {voice.local_path}")
            except Exception as e:
                logger.error(f"Failed to download voice: {e}")
        
        # 处理图片消息 - 下载到本地
        for img in message.content.images:
            try:
                if not img.local_path:
                    local_path = await adapter.download_media(img)
                    img.local_path = str(local_path)
                    logger.info(f"Image downloaded: {img.local_path}")
            except Exception as e:
                logger.error(f"Failed to download image: {e}")

    async def _send_typing(self, message: UnifiedMessage) -> None:
        """发送正在输入状态"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, 'send_typing'):
            try:
                await adapter.send_typing(message.chat_id)
            except Exception:
                pass  # 忽略 typing 发送失败
    
    async def _call_agent_with_typing(self, session: Session, message: UnifiedMessage) -> str:
        """
        调用 Agent 处理消息，期间持续发送 typing 状态
        """
        import asyncio
        
        # 创建 typing 状态持续发送的任务
        typing_task = asyncio.create_task(self._keep_typing(message))
        
        try:
            # 调用 Agent
            response_text = await self._call_agent(session, message)
            return response_text
        finally:
            # 停止 typing 状态发送
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
    
    async def _keep_typing(self, message: UnifiedMessage) -> None:
        """持续发送 typing 状态（每 4 秒一次）"""
        import asyncio
        while True:
            await self._send_typing(message)
            await asyncio.sleep(4)  # Telegram typing 状态持续约 5 秒
    
    async def _call_agent(self, session: Session, message: UnifiedMessage) -> str:
        """
        调用 Agent 处理消息（支持多模态）
        """
        if not self.agent_handler:
            return "Agent handler not configured"
        
        try:
            # 构建输入（文本 + 图片）
            input_text = message.plain_text
            
            # 检查是否有图片需要多模态处理
            images_data = []
            for img in message.content.images:
                if img.local_path and Path(img.local_path).exists():
                    try:
                        with open(img.local_path, "rb") as f:
                            image_data = base64.b64encode(f.read()).decode("utf-8")
                            images_data.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": img.mime_type or "image/jpeg",
                                    "data": image_data,
                                }
                            })
                    except Exception as e:
                        logger.error(f"Failed to read image: {e}")
            
            # 如果有图片，构建多模态输入
            if images_data:
                # 存储图片数据到 session，供 Agent 使用
                session.set_metadata("pending_images", images_data)
                if not input_text.strip():
                    input_text = "[用户发送了图片]"
                logger.info(f"Processing multimodal message with {len(images_data)} images")
            
            # 调用 Agent
            response = await self.agent_handler(session, input_text)
            
            # 清除图片数据
            session.set_metadata("pending_images", None)
            
            return response
            
        except Exception as e:
            logger.error(f"Agent error: {e}")
            return f"处理出错: {str(e)}"
    
    async def _send_response(self, original: UnifiedMessage, response: str) -> None:
        """
        发送响应（带重试和长消息分割）
        """
        import asyncio
        
        adapter = self._adapters.get(original.channel)
        if not adapter:
            logger.error(f"No adapter for channel: {original.channel}")
            return
        
        # 分割长消息（Telegram 限制 4096 字符）
        max_length = 4000  # 留一些余量
        messages = []
        if len(response) <= max_length:
            messages = [response]
        else:
            # 按换行符分割，尽量保持段落完整
            current = ""
            for line in response.split('\n'):
                if len(current) + len(line) + 1 <= max_length:
                    current += line + '\n'
                else:
                    if current:
                        messages.append(current.rstrip())
                    current = line + '\n'
            if current:
                messages.append(current.rstrip())
        
        # 发送每个部分（带重试）
        for i, text in enumerate(messages):
            outgoing = OutgoingMessage.text(
                chat_id=original.chat_id,
                text=text,
                reply_to=original.channel_message_id if i == 0 else None,
                thread_id=original.thread_id,
            )
            
            # 重试最多 3 次
            for attempt in range(3):
                try:
                    await adapter.send_message(outgoing)
                    break
                except Exception as e:
                    if attempt < 2:
                        logger.warning(f"Send failed (attempt {attempt + 1}), retrying: {e}")
                        await asyncio.sleep(1)
                    else:
                        logger.error(f"Failed to send response after 3 attempts: {e}")
                        # 最后一次失败，尝试发送错误提示
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
