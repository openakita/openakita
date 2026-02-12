"""
消息网关

统一消息入口/出口:
- 消息路由
- 会话管理集成
- 媒体预处理（图片、语音）
- Agent 调用
- 消息中断机制（支持在工具调用间隙插入新消息）
- 系统级命令拦截（模型切换等）
"""

import asyncio
import base64
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..sessions import Session, SessionManager
from .base import ChannelAdapter
from .types import OutgoingMessage, UnifiedMessage

if TYPE_CHECKING:
    from ..core.brain import Brain

logger = logging.getLogger(__name__)

# Agent 处理函数类型
AgentHandler = Callable[[Session, str], Awaitable[str]]


class InterruptPriority(Enum):
    """中断优先级"""

    NORMAL = 0  # 普通消息，排队等待
    HIGH = 1  # 高优先级，在工具间隙插入
    URGENT = 2  # 紧急，尝试立即中断


@dataclass
class InterruptMessage:
    """中断消息封装"""

    message: UnifiedMessage
    priority: InterruptPriority = InterruptPriority.HIGH
    timestamp: datetime = field(default_factory=datetime.now)

    def __lt__(self, other: "InterruptMessage") -> bool:
        """优先级队列比较：优先级高的先处理，同优先级按时间"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.timestamp < other.timestamp


# ==================== 模型切换命令处理 ====================


@dataclass
class ModelSwitchSession:
    """模型切换交互会话"""

    session_key: str
    mode: str  # "switch" | "priority" | "restore"
    step: str  # "select" | "confirm"
    selected_model: str | None = None
    selected_priority: list[str] | None = None
    started_at: datetime = field(default_factory=datetime.now)
    timeout_minutes: int = 5

    @property
    def is_expired(self) -> bool:
        """检查会话是否已超时"""
        return datetime.now() > self.started_at + timedelta(minutes=self.timeout_minutes)


class ModelCommandHandler:
    """
    模型命令处理器

    系统级命令拦截，不经过大模型处理，确保即使模型崩溃也能切换。

    支持的命令:
    - /model: 显示当前模型和可用列表
    - /switch [模型名]: 临时切换模型（12小时）
    - /priority: 调整模型优先级（永久）
    - /restore: 恢复默认模型
    - /cancel: 取消当前操作
    """

    # 命令列表
    MODEL_COMMANDS = {"/model", "/switch", "/priority", "/restore", "/cancel"}

    def __init__(self, brain: Optional["Brain"] = None):
        self._brain: Brain | None = brain
        # 进行中的切换会话 {session_key: ModelSwitchSession}
        self._switch_sessions: dict[str, ModelSwitchSession] = {}

    def set_brain(self, brain: "Brain") -> None:
        """设置 Brain 实例"""
        self._brain = brain

    def is_model_command(self, text: str) -> bool:
        """检查是否是模型相关命令"""
        if not text:
            return False
        text_lower = text.lower().strip()
        # 完整命令或带参数的命令
        for cmd in self.MODEL_COMMANDS:
            if text_lower == cmd or text_lower.startswith(cmd + " "):
                return True
        return False

    def is_in_session(self, session_key: str) -> bool:
        """检查是否在交互会话中"""
        if session_key not in self._switch_sessions:
            return False
        session = self._switch_sessions[session_key]
        if session.is_expired:
            del self._switch_sessions[session_key]
            return False
        return True

    async def handle_command(self, session_key: str, text: str) -> str | None:
        """
        处理模型命令

        Args:
            session_key: 会话标识
            text: 用户输入

        Returns:
            响应文本，如果不是命令返回 None
        """
        if not self._brain:
            return "❌ 模型管理功能未初始化"

        text = text.strip()
        text_lower = text.lower()

        # /model - 显示当前模型状态
        if text_lower == "/model":
            return self._format_model_status()

        # /switch - 切换模型
        if text_lower == "/switch":
            return self._start_switch_session(session_key)

        if text_lower.startswith("/switch "):
            model_name = text[8:].strip()
            return self._start_switch_session(session_key, model_name)

        # /priority - 调整优先级
        if text_lower == "/priority":
            return self._start_priority_session(session_key)

        # /restore - 恢复默认
        if text_lower == "/restore":
            return self._start_restore_session(session_key)

        # /cancel - 取消操作
        if text_lower == "/cancel":
            return self._cancel_session(session_key)

        return None

    async def handle_input(self, session_key: str, text: str) -> str:
        """
        处理交互会话中的用户输入

        Args:
            session_key: 会话标识
            text: 用户输入

        Returns:
            响应文本
        """
        if not self._brain:
            return "❌ 模型管理功能未初始化"

        # 检查是否取消
        if text.lower().strip() == "/cancel":
            return self._cancel_session(session_key)

        session = self._switch_sessions.get(session_key)
        if not session:
            return "会话已结束"

        if session.is_expired:
            del self._switch_sessions[session_key]
            return "⏰ 操作超时（5分钟），已自动取消"

        # 根据模式和步骤处理
        if session.mode == "switch":
            return self._handle_switch_input(session_key, session, text)
        elif session.mode == "priority":
            return self._handle_priority_input(session_key, session, text)
        elif session.mode == "restore":
            return self._handle_restore_input(session_key, session, text)

        return "未知操作"

    def _format_model_status(self) -> str:
        """格式化模型状态信息"""
        models = self._brain.list_available_models()
        override = self._brain.get_override_status()

        lines = ["📋 **模型状态**\n"]

        for i, m in enumerate(models):
            status = ""
            if m["is_current"]:
                status = " ⬅️ 当前（临时）" if m["is_override"] else " ⬅️ 当前"
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        if override:
            lines.append(f"\n⏱️ 临时切换剩余: {override['remaining_hours']:.1f} 小时")
            lines.append(f"   到期时间: {override['expires_at']}")

        lines.append("\n💡 命令: /switch 切换 | /priority 调整优先级 | /restore 恢复默认")

        return "\n".join(lines)

    def _start_switch_session(self, session_key: str, model_name: str = "") -> str:
        """开始切换会话"""
        models = self._brain.list_available_models()

        # 如果指定了模型名，跳到确认步骤
        if model_name:
            # 查找模型
            target = None
            for m in models:
                if (
                    m["name"].lower() == model_name.lower()
                    or m["model"].lower() == model_name.lower()
                ):
                    target = m
                    break

            if not target:
                # 尝试数字索引
                try:
                    idx = int(model_name) - 1
                    if 0 <= idx < len(models):
                        target = models[idx]
                except ValueError:
                    pass

            if not target:
                available = ", ".join(m["name"] for m in models)
                return f"❌ 未找到模型 '{model_name}'\n可用模型: {available}"

            # 创建会话并进入确认步骤
            self._switch_sessions[session_key] = ModelSwitchSession(
                session_key=session_key,
                mode="switch",
                step="confirm",
                selected_model=target["name"],
            )

            return (
                f"⚠️ 确认切换到 **{target['name']}** ({target['model']})?\n\n"
                f"临时切换有效期: 12小时\n"
                f"输入 **yes** 确认，其他任意内容取消"
            )

        # 没有指定模型，显示选择列表
        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="switch",
            step="select",
        )

        lines = ["📋 **可用模型**\n"]
        for i, m in enumerate(models):
            status = " ⬅️ 当前" if m["is_current"] else ""
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        lines.append("\n请输入数字或模型名称选择，/cancel 取消")

        return "\n".join(lines)

    def _start_priority_session(self, session_key: str) -> str:
        """开始优先级调整会话"""
        models = self._brain.list_available_models()

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="priority",
            step="select",
        )

        lines = ["📋 **当前优先级** (数字越小越优先)\n"]
        for i, m in enumerate(models):
            lines.append(f"{i}. {m['name']}")

        lines.append("\n请按顺序输入模型名称，用空格分隔")
        lines.append("例如: claude kimi dashscope minimax")
        lines.append("/cancel 取消")

        return "\n".join(lines)

    def _start_restore_session(self, session_key: str) -> str:
        """开始恢复默认会话"""
        override = self._brain.get_override_status()

        if not override:
            return "当前没有临时切换，已在使用默认模型"

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="restore",
            step="confirm",
        )

        return (
            f"⚠️ 确认恢复默认模型?\n\n"
            f"当前临时使用: {override['endpoint_name']}\n"
            f"剩余时间: {override['remaining_hours']:.1f} 小时\n\n"
            f"输入 **yes** 确认，其他任意内容取消"
        )

    def _cancel_session(self, session_key: str) -> str:
        """取消当前会话"""
        if session_key in self._switch_sessions:
            del self._switch_sessions[session_key]
            return "✅ 操作已取消"
        return "没有进行中的操作"

    def _handle_switch_input(self, session_key: str, session: ModelSwitchSession, text: str) -> str:
        """处理切换会话的输入"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            target = None

            # 尝试数字索引
            try:
                idx = int(text) - 1
                if 0 <= idx < len(models):
                    target = models[idx]
            except ValueError:
                # 尝试名称匹配
                for m in models:
                    if m["name"].lower() == text.lower() or m["model"].lower() == text.lower():
                        target = m
                        break

            if not target:
                return f"❌ 未找到模型 '{text}'，请重新输入或 /cancel 取消"

            # 进入确认步骤
            session.selected_model = target["name"]
            session.step = "confirm"

            return (
                f"⚠️ 确认切换到 **{target['name']}** ({target['model']})?\n\n"
                f"临时切换有效期: 12小时\n"
                f"输入 **yes** 确认，其他任意内容取消"
            )

        elif session.step == "confirm":
            if text.lower() == "yes":
                # 执行切换
                success, msg = self._brain.switch_model(
                    session.selected_model, conversation_id=session_key
                )
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}\n\n发送 /model 查看状态"
                else:
                    return f"❌ 切换失败: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ 操作已取消"

        return "未知步骤"

    def _handle_priority_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """处理优先级调整的输入"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            model_names = {m["name"].lower(): m["name"] for m in models}

            # 解析用户输入
            input_names = text.split()
            priority_order = []

            for name in input_names:
                name_lower = name.lower()
                if name_lower in model_names:
                    priority_order.append(model_names[name_lower])
                else:
                    return f"❌ 未找到模型 '{name}'，请重新输入或 /cancel 取消"

            if len(priority_order) != len(models):
                return f"❌ 请输入所有 {len(models)} 个模型的顺序"

            # 进入确认步骤
            session.selected_priority = priority_order
            session.step = "confirm"

            lines = ["⚠️ 确认调整优先级为:\n"]
            for i, name in enumerate(priority_order):
                lines.append(f"{i}. {name}")
            lines.append("\n**这是永久更改！** 输入 **yes** 确认")

            return "\n".join(lines)

        elif session.step == "confirm":
            if text.lower() == "yes":
                # 执行优先级更新
                success, msg = self._brain.update_model_priority(session.selected_priority)
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}"
                else:
                    return f"❌ 更新失败: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ 操作已取消"

        return "未知步骤"

    def _handle_restore_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """处理恢复默认的输入"""
        if text.lower() == "yes":
            success, msg = self._brain.restore_default_model(conversation_id=session_key)
            del self._switch_sessions[session_key]

            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        else:
            del self._switch_sessions[session_key]
            return "✅ 操作已取消"


class MessageGateway:
    """
    统一消息网关

    职责:
    - 管理多个通道适配器
    - 将收到的消息路由到会话
    - 调用 Agent 处理
    - 将回复发送回通道
    """

    # 支持 .en 专用模型的 Whisper 尺寸（large 无 .en 变体）
    _EN_MODEL_SIZES = {"tiny", "base", "small", "medium"}

    def __init__(
        self,
        session_manager: SessionManager,
        agent_handler: AgentHandler | None = None,
        whisper_model: str = "base",
        whisper_language: str = "zh",
    ):
        """
        Args:
            session_manager: 会话管理器
            agent_handler: Agent 处理函数 (session, message) -> response
            whisper_model: Whisper 模型大小 (tiny, base, small, medium, large)，默认 base
            whisper_language: 语音识别语言 (zh/en/auto/其他语言代码)
        """
        self.session_manager = session_manager
        self.agent_handler = agent_handler

        # 注册的适配器 {channel_name: adapter}
        self._adapters: dict[str, ChannelAdapter] = {}

        # 消息处理队列
        self._message_queue: asyncio.Queue[UnifiedMessage] = asyncio.Queue()

        # 处理任务
        self._processing_task: asyncio.Task | None = None
        self._running = False

        # 中间件
        self._pre_process_hooks: list[Callable[[UnifiedMessage], Awaitable[UnifiedMessage]]] = []
        self._post_process_hooks: list[Callable[[UnifiedMessage, str], Awaitable[str]]] = []

        # Whisper 语音识别模型（延迟加载或启动时预加载）
        self._whisper_language = whisper_language.lower().strip()
        # 英语且模型尺寸有 .en 变体时，自动切换到更小更快的 .en 模型
        if self._whisper_language == "en" and whisper_model in self._EN_MODEL_SIZES:
            self._whisper_model_name = f"{whisper_model}.en"
            logger.info(
                f"Whisper language=en → auto-selected English-only model: "
                f"{self._whisper_model_name}"
            )
        else:
            self._whisper_model_name = whisper_model
        self._whisper = None
        self._whisper_loaded = False

        # ==================== 消息中断机制 ====================
        # 会话级中断队列 {session_key: asyncio.PriorityQueue[InterruptMessage]}
        self._interrupt_queues: dict[str, asyncio.PriorityQueue] = {}

        # 正在处理的会话 {session_key: bool}
        self._processing_sessions: dict[str, bool] = {}

        # 中断锁（防止并发修改）
        self._interrupt_lock = asyncio.Lock()

        # 中断处理回调（由 Agent 设置）
        self._interrupt_callbacks: dict[str, Callable[[], Awaitable[str | None]]] = {}

        # 模型命令处理器（系统级命令拦截）
        self._model_cmd_handler: ModelCommandHandler = ModelCommandHandler()

        # ==================== 进度事件流（Plan/Deliver 等）====================
        # 目标：把“执行过程进度展示”下沉到网关侧，避免模型/工具刷屏。
        self._progress_buffers: dict[str, list[str]] = {}  # session_key -> [lines]
        self._progress_flush_tasks: dict[str, asyncio.Task] = {}  # session_key -> flush task
        self._progress_throttle_seconds: float = 2.0  # 默认节流窗口

    async def start(self) -> None:
        """启动网关"""
        self._running = True

        # 预加载 Whisper 语音识别模型（在后台线程中执行，不阻塞启动）
        asyncio.create_task(self._preload_whisper_async())

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

    async def _preload_whisper_async(self) -> None:
        """异步预加载 Whisper 模型"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_whisper_model)
        except Exception as e:
            logger.warning(f"Failed to preload Whisper model: {e}")

    def _ensure_ffmpeg(self) -> None:
        """确保 ffmpeg 可用（优先使用系统已有的，否则自动下载静态版本）"""
        import shutil

        if shutil.which("ffmpeg"):
            logger.debug("ffmpeg found in system PATH")
            return

        try:
            import static_ffmpeg

            static_ffmpeg.add_paths(weak=True)  # weak=True: 不覆盖已有
            logger.info("ffmpeg auto-configured via static-ffmpeg")
        except ImportError:
            logger.warning(
                "ffmpeg not found and static-ffmpeg not installed. "
                "Voice transcription may fail. "
                "Install: pip install static-ffmpeg"
            )

    def _load_whisper_model(self) -> None:
        """加载 Whisper 模型（在线程池中执行）"""
        if self._whisper_loaded:
            return

        # 确保 ffmpeg 可用（Whisper 依赖 ffmpeg 解码音频）
        self._ensure_ffmpeg()

        try:
            import hashlib
            import os

            import whisper
            from whisper import _MODELS

            model_name = self._whisper_model_name

            # 获取模型缓存路径
            cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
            model_file = os.path.join(cache_dir, f"{model_name}.pt")

            # 检查本地模型 hash（仅提醒，不阻塞）
            if os.path.exists(model_file) and os.path.getsize(model_file) > 1000000:
                model_url = _MODELS.get(model_name, "")
                if model_url:
                    url_parts = model_url.split("/")
                    expected_hash = url_parts[-2] if len(url_parts) >= 2 else ""

                    if expected_hash and len(expected_hash) > 5:
                        sha256 = hashlib.sha256()
                        with open(model_file, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                sha256.update(chunk)
                        local_hash = sha256.hexdigest()

                        if not local_hash.startswith(expected_hash):
                            logger.info(
                                f"Whisper model '{model_name}' may have updates available. "
                                f"Delete {model_file} to re-download if needed."
                            )

            # 正常加载
            logger.info(f"Loading Whisper model '{model_name}'...")
            self._whisper = whisper.load_model(model_name)
            self._whisper_loaded = True
            logger.info(f"Whisper model '{model_name}' loaded successfully")

        except ImportError:
            logger.warning(
                "Whisper not installed. Voice transcription will not be available. "
                "Run: pip install openai-whisper"
            )
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")

    async def stop(self) -> None:
        """停止网关"""
        self._running = False

        # 停止处理循环
        if self._processing_task:
            self._processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task

        # 停止所有适配器
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
                logger.info(f"Stopped adapter: {name}")
            except Exception as e:
                logger.error(f"Failed to stop adapter {name}: {e}")

        logger.info("MessageGateway stopped")

    def set_brain(self, brain: "Brain") -> None:
        """
        设置 Brain 实例（用于模型切换命令）

        Args:
            brain: Brain 实例
        """
        self._model_cmd_handler.set_brain(brain)
        logger.info("ModelCommandHandler brain set")

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

    def get_adapter(self, channel: str) -> ChannelAdapter | None:
        """获取适配器"""
        return self._adapters.get(channel)

    def list_adapters(self) -> list[str]:
        """列出所有适配器"""
        return list(self._adapters.keys())

    # ==================== 消息处理 ====================

    async def _on_message(self, message: UnifiedMessage) -> None:
        """
        消息回调（由适配器调用）

        如果该会话正在处理中，将消息放入中断队列。
        如果消息是停止指令，立即触发任务取消。
        """
        session_key = f"{message.channel}:{message.chat_id}:{message.user_id}"

        async with self._interrupt_lock:
            if self._processing_sessions.get(session_key, False):
                # 会话正在处理中
                user_text = (message.plain_text or "").strip()

                # C8: 检测停止指令 → 立即取消当前任务
                if self.agent_handler and self.agent_handler.is_stop_command(user_text):
                    self.agent_handler.cancel_current_task(f"用户发送停止指令: {user_text}")
                    logger.info(
                        f"[Interrupt] Stop command detected, cancelling task for {session_key}: {user_text}"
                    )
                    # 同时也将停止指令放入中断队列，让任务取消后处理
                    # （agent 循环退出后会看到这条消息并立即回复确认）

                # 放入中断队列
                await self._add_interrupt_message(session_key, message)
                logger.info(
                    f"[Interrupt] Message queued for session {session_key}: {message.plain_text}"
                )
                return

        # 正常入队
        await self._message_queue.put(message)

    # ==================== 中断机制 ====================

    async def _add_interrupt_message(
        self,
        session_key: str,
        message: UnifiedMessage,
        priority: InterruptPriority = InterruptPriority.HIGH,
    ) -> None:
        """
        添加中断消息到会话队列

        Args:
            session_key: 会话标识
            message: 消息
            priority: 优先级
        """
        if session_key not in self._interrupt_queues:
            self._interrupt_queues[session_key] = asyncio.PriorityQueue()

        interrupt_msg = InterruptMessage(message=message, priority=priority)
        await self._interrupt_queues[session_key].put(interrupt_msg)

        logger.debug(f"[Interrupt] Added to queue: {session_key}, priority={priority.name}")

    def _get_session_key(self, message: UnifiedMessage) -> str:
        """获取会话标识"""
        return f"{message.channel}:{message.chat_id}:{message.user_id}"

    def _mark_session_processing(self, session_key: str, processing: bool) -> None:
        """标记会话处理状态"""
        self._processing_sessions[session_key] = processing
        if not processing and session_key in self._interrupt_callbacks:
            del self._interrupt_callbacks[session_key]

    async def check_interrupt(self, session_key: str) -> UnifiedMessage | None:
        """
        检查会话是否有待处理的中断消息

        Args:
            session_key: 会话标识

        Returns:
            待处理的消息，如果没有则返回 None
        """
        queue = self._interrupt_queues.get(session_key)
        if not queue or queue.empty():
            return None

        try:
            interrupt_msg = queue.get_nowait()
            logger.info(
                f"[Interrupt] Retrieved message for {session_key}: {interrupt_msg.message.plain_text}"
            )
            return interrupt_msg.message
        except asyncio.QueueEmpty:
            return None

    def has_pending_interrupt(self, session_key: str) -> bool:
        """
        检查会话是否有待处理的中断消息

        Args:
            session_key: 会话标识

        Returns:
            是否有待处理消息
        """
        queue = self._interrupt_queues.get(session_key)
        return queue is not None and not queue.empty()

    def get_interrupt_count(self, session_key: str) -> int:
        """
        获取待处理的中断消息数量

        Args:
            session_key: 会话标识

        Returns:
            待处理消息数量
        """
        queue = self._interrupt_queues.get(session_key)
        return queue.qsize() if queue else 0

    def register_interrupt_callback(
        self,
        session_key: str,
        callback: Callable[[], Awaitable[str | None]],
    ) -> None:
        """
        注册中断检查回调（由 Agent 调用）

        当工具调用间隙，Agent 会调用此回调检查是否需要处理新消息

        Args:
            session_key: 会话标识
            callback: 回调函数，返回需要插入的消息文本或 None
        """
        self._interrupt_callbacks[session_key] = callback
        logger.debug(f"[Interrupt] Registered callback for {session_key}")

    async def _process_loop(self) -> None:
        """消息处理循环"""
        while self._running:
            try:
                # 从队列获取消息
                message = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)

                # 处理消息
                await self._handle_message(message)

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_message(self, message: UnifiedMessage) -> None:
        """
        处理单条消息
        """
        session_key = self._get_session_key(message)
        user_text = message.plain_text.strip() if message.plain_text else ""

        logger.info(
            f"[IM] <<< 收到消息: channel={message.channel}, user={message.user_id}, "
            f"text=\"{user_text[:100]}\""
        )

        try:
            # 标记会话开始处理
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, True)

            # ==================== 系统级命令拦截 ====================
            # 在处理 Agent 之前，检查是否是模型切换相关命令
            # 这确保即使大模型崩溃也能执行切换操作

            # 检查是否在模型切换交互会话中
            if self._model_cmd_handler.is_in_session(session_key):
                response_text = await self._model_cmd_handler.handle_input(session_key, user_text)
                await self._send_response(message, response_text)
                return

            # 检查是否是模型相关命令
            if self._model_cmd_handler.is_model_command(user_text):
                response_text = await self._model_cmd_handler.handle_command(session_key, user_text)
                if response_text:
                    await self._send_response(message, response_text)
                    return

            # ==================== 正常消息处理流程 ====================

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

            # 4.5 推送未送达的自检报告（每天第一条消息时触发，最多一次）
            await self._maybe_deliver_pending_selfcheck_report(message)

            # 5. 记录消息到会话
            session.add_message(
                role="user",
                content=message.plain_text,
                message_id=message.id,
                channel_message_id=message.channel_message_id,
            )
            self.session_manager.mark_dirty()  # 触发保存

            # 6. 调用 Agent 处理（支持中断检查）
            response_text = await self._call_agent_with_typing(session, message)

            # 7. 后处理钩子
            for hook in self._post_process_hooks:
                response_text = await hook(message, response_text)

            # 8. 记录响应到会话
            session.add_message(
                role="assistant",
                content=response_text,
            )
            self.session_manager.mark_dirty()  # 触发保存

            # 9. 发送响应
            logger.info(
                f"[IM] >>> 回复完成: channel={message.channel}, user={message.user_id}, "
                f"len={len(response_text)}, preview=\"{response_text[:80]}\""
            )
            await self._send_response(message, response_text)

            # 10. 处理剩余的中断消息
            await self._process_pending_interrupts(session_key, session)

        except Exception as e:
            logger.error(f"Error handling message {message.id}: {e}")
            # 发送错误提示
            await self._send_error(message, str(e))
        finally:
            # 标记会话处理完成
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, False)

    async def _process_pending_interrupts(self, session_key: str, session: Session) -> None:
        """
        处理会话中剩余的中断消息

        在当前消息处理完成后，继续处理排队的中断消息
        """
        while self.has_pending_interrupt(session_key):
            interrupt_msg = await self.check_interrupt(session_key)
            if not interrupt_msg:
                break

            logger.info(f"[Interrupt] Processing pending message for {session_key}")

            try:
                # 预处理媒体
                await self._preprocess_media(interrupt_msg)

                # 记录到会话
                session.add_message(
                    role="user",
                    content=interrupt_msg.plain_text,
                    message_id=interrupt_msg.id,
                    channel_message_id=interrupt_msg.channel_message_id,
                    is_interrupt=True,  # 标记为中断消息
                )
                self.session_manager.mark_dirty()  # 触发保存

                # 调用 Agent 处理
                response_text = await self._call_agent_with_typing(session, interrupt_msg)

                # 后处理钩子
                for hook in self._post_process_hooks:
                    response_text = await hook(interrupt_msg, response_text)

                # 记录响应
                session.add_message(
                    role="assistant",
                    content=response_text,
                )
                self.session_manager.mark_dirty()  # 触发保存

                # 发送响应
                await self._send_response(interrupt_msg, response_text)

            except Exception as e:
                logger.error(f"Error processing interrupt message: {e}")
                await self._send_error(interrupt_msg, str(e))

    async def _preprocess_media(self, message: UnifiedMessage) -> None:
        """
        预处理媒体文件（下载语音、图片到本地，语音自动转文字）
        """
        adapter = self._adapters.get(message.channel)
        if not adapter:
            return

        import asyncio

        # 并发下载/转写（避免多媒体消息逐个串行导致延迟叠加）
        sem = asyncio.Semaphore(4)

        async def _process_voice(voice) -> None:
            try:
                async with sem:
                    if not voice.local_path:
                        local_path = await adapter.download_media(voice)
                        voice.local_path = str(local_path)
                        logger.info(f"Voice downloaded: {voice.local_path}")

                # 转写放在 download 之后；转写内部已使用线程池，不阻塞事件循环
                if voice.local_path and not voice.transcription:
                    transcription = await self._transcribe_voice_local(voice.local_path)
                    if transcription:
                        voice.transcription = transcription
                        logger.info(f"Voice transcribed: {transcription}")
                    else:
                        voice.transcription = "[语音识别失败]"
            except Exception as e:
                logger.error(f"Failed to process voice: {e}")

        async def _process_image(img) -> None:
            try:
                if img.local_path:
                    return
                async with sem:
                    local_path = await adapter.download_media(img)
                    img.local_path = str(local_path)
                    logger.info(f"Image downloaded: {img.local_path}")
            except Exception as e:
                logger.error(f"Failed to download image: {e}")

        tasks = []
        for voice in getattr(message.content, "voices", []) or []:
            tasks.append(_process_voice(voice))
        for img in getattr(message.content, "images", []) or []:
            tasks.append(_process_image(img))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    async def _transcribe_voice_local(self, audio_path: str) -> str | None:
        """
        使用本地 Whisper 进行语音转文字

        使用预加载的模型，避免每次都重新加载
        """
        import asyncio

        try:
            # 检查文件是否存在
            if not Path(audio_path).exists():
                logger.error(f"Audio file not found: {audio_path}")
                return None

            # 确保模型已加载
            if not self._whisper_loaded:
                # 同步加载模型（如果还没加载）
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_whisper_model)

            # 检查模型是否可用
            if self._whisper is None:
                logger.error("Whisper model not available")
                return None

            # 在线程池中运行转写（避免阻塞事件循环）
            whisper_lang = self._whisper_language

            def transcribe():
                # auto 模式不传 language，让 Whisper 自动检测
                kwargs = {}
                if whisper_lang and whisper_lang != "auto":
                    kwargs["language"] = whisper_lang
                result = self._whisper.transcribe(audio_path, **kwargs)
                return result["text"].strip()

            # 异步执行
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, transcribe)

            return text if text else None

        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            return None

    async def _send_typing(self, message: UnifiedMessage) -> None:
        """发送正在输入状态"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, "send_typing"):
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
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    async def _keep_typing(self, message: UnifiedMessage) -> None:
        """持续发送 typing 状态（每 4 秒一次）"""
        import asyncio

        while True:
            await self._send_typing(message)
            await asyncio.sleep(4)  # Telegram typing 状态持续约 5 秒

    async def _call_agent(self, session: Session, message: UnifiedMessage) -> str:
        """
        调用 Agent 处理消息（支持多模态：图片、语音）

        支持中断机制：将 gateway 引用存入 session.metadata，供 Agent 检查中断
        """
        if not self.agent_handler:
            return "Agent handler not configured"

        try:
            # 构建输入（文本 + 图片 + 语音）
            input_text = message.plain_text

            # 处理语音文件 - 如果已有转写结果，直接使用
            for voice in message.content.voices:
                if voice.transcription and voice.transcription not in ("[语音识别失败]", ""):
                    # 语音已转写，用转写文字替换输入
                    if not input_text.strip() or "[语音:" in input_text:
                        input_text = voice.transcription
                        logger.info(f"Using voice transcription as input: {input_text}")
                    else:
                        # 追加到输入
                        input_text = f"{input_text}\n\n[语音内容: {voice.transcription}]"
                elif voice.local_path:
                    # 语音未转写成功，保存路径供 Agent 手动处理
                    session.set_metadata(
                        "pending_voices",
                        [
                            {
                                "local_path": voice.local_path,
                                "duration": voice.duration,
                            }
                        ],
                    )
                    if not input_text.strip() or "[语音:" in input_text:
                        input_text = (
                            f"[用户发送了语音消息，但自动识别失败。文件路径: {voice.local_path}]"
                        )
                    logger.info(f"Voice transcription failed, file: {voice.local_path}")

            # 处理图片文件 - 多模态输入
            images_data = []
            for img in message.content.images:
                if img.local_path and Path(img.local_path).exists():
                    try:
                        with open(img.local_path, "rb") as f:
                            image_data = base64.b64encode(f.read()).decode("utf-8")
                            images_data.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": img.mime_type or "image/jpeg",
                                        "data": image_data,
                                    },
                                    "local_path": img.local_path,  # 也保存路径
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to read image: {e}")

            # 如果有图片，构建多模态输入
            if images_data:
                # 存储图片数据到 session，供 Agent 使用
                session.set_metadata("pending_images", images_data)
                if not input_text.strip():
                    input_text = "[用户发送了图片]"
                logger.info(f"Processing multimodal message with {len(images_data)} images")

            # === 中断机制：传递 gateway 引用和会话标识 ===
            session_key = self._get_session_key(message)
            session.set_metadata("_gateway", self)
            session.set_metadata("_session_key", session_key)
            session.set_metadata("_current_message", message)

            # 调用 Agent
            response = await self.agent_handler(session, input_text)

            # 清除临时数据
            session.set_metadata("pending_images", None)
            session.set_metadata("pending_voices", None)
            session.set_metadata("_gateway", None)
            session.set_metadata("_session_key", None)
            session.set_metadata("_current_message", None)

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
            for line in response.split("\n"):
                if len(current) + len(line) + 1 <= max_length:
                    current += line + "\n"
                else:
                    if current:
                        messages.append(current.rstrip())
                    current = line + "\n"
            if current:
                messages.append(current.rstrip())

        # 发送每个部分（带重试）
        for i, text in enumerate(messages):
            # 合并 metadata，注入 channel_user_id 用于群聊精确路由
            outgoing_meta = dict(original.metadata) if original.metadata else {}
            if original.channel_user_id:
                outgoing_meta["channel_user_id"] = original.channel_user_id

            outgoing = OutgoingMessage.text(
                chat_id=original.chat_id,
                text=text,
                reply_to=original.channel_message_id if i == 0 else None,
                thread_id=original.thread_id,
                parse_mode="markdown",  # 启用 Markdown 格式
                metadata=outgoing_meta,  # 透传元数据 + channel_user_id
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
                        with contextlib.suppress(BaseException):
                            await adapter.send_text(
                                chat_id=original.chat_id,
                                text="消息发送失败，请稍后重试。",
                            )

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

    # ==================== 待推送自检报告 ====================

    async def _maybe_deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        检查并推送未送达的自检报告

        自检在凌晨 4:00 运行，但此时通常没有活跃会话（30 分钟超时），
        报告会以 reported=false 状态保存在 data/selfcheck/ 目录下。
        当用户发消息时，这里会把未送达的报告补推给用户。

        去重由报告 JSON 的 reported 字段保证，无需额外的日期锁。
        """
        try:
            await self._deliver_pending_selfcheck_report(message)
        except Exception as e:
            logger.error(f"Pending selfcheck report delivery failed: {e}")

    async def _deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        读取 data/selfcheck/ 中未推送的报告并发送给用户

        检查今天和昨天的报告文件，找到第一个 reported=false 的报告推送。
        直接通过适配器发送，不写入会话上下文（避免污染对话历史）。
        """
        import json
        from datetime import date as date_type

        from ..config import settings

        selfcheck_dir = settings.selfcheck_dir
        if not selfcheck_dir.exists():
            return

        today = date_type.today()
        # 检查今天和昨天的报告（自检在凌晨 4:00 生成当天日期的报告）
        candidates = [
            today.isoformat(),
            (today - timedelta(days=1)).isoformat(),
        ]

        for report_date in candidates:
            json_file = selfcheck_dir / f"{report_date}_report.json"
            md_file = selfcheck_dir / f"{report_date}_report.md"

            if not json_file.exists():
                continue

            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                # 已推送过则跳过
                if data.get("reported"):
                    continue

                if not md_file.exists():
                    continue

                with open(md_file, encoding="utf-8") as f:
                    report_md = f.read()

                if not report_md.strip():
                    continue

                # 通过适配器直接发送（不写入会话上下文）
                adapter = self._adapters.get(message.channel)
                if not adapter or not adapter.is_running:
                    continue

                header = f"📋 每日系统自检报告（{report_date}）\n\n"
                full_text = header + report_md

                # 分段发送（兼容 Telegram 4096 限制）
                max_len = 3500
                text = full_text
                while text:
                    if len(text) <= max_len:
                        await adapter.send_text(message.chat_id, text)
                        break
                    cut = text.rfind("\n", 0, max_len)
                    if cut < 1000:
                        cut = max_len
                    await adapter.send_text(message.chat_id, text[:cut].rstrip())
                    text = text[cut:].lstrip()

                # 标记为已推送
                data["reported"] = True
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                logger.info(
                    f"Delivered pending selfcheck report for {report_date} "
                    f"to {message.channel}/{message.chat_id}"
                )
                break  # 只推送最近一份未读报告

            except Exception as e:
                logger.error(f"Failed to deliver pending selfcheck report for {report_date}: {e}")

    # ==================== 主动发送 ====================

    async def send(
        self,
        channel: str,
        chat_id: str,
        text: str,
        record_to_session: bool = True,
        user_id: str = "system",
        **kwargs,
    ) -> str | None:
        """
        主动发送消息

        Args:
            channel: 目标通道
            chat_id: 目标聊天
            text: 消息文本
            record_to_session: 是否记录到会话历史
            user_id: 发送者标识

        Returns:
            消息 ID 或 None
        """
        adapter = self._adapters.get(channel)
        if not adapter:
            logger.error(f"No adapter for channel: {channel}")
            return None

        try:
            result = await adapter.send_text(chat_id, text, **kwargs)

            # 记录到 session 历史
            if record_to_session and self.session_manager:
                try:
                    self.session_manager.add_message(
                        channel=channel,
                        chat_id=chat_id,
                        user_id=user_id,
                        role="system",  # 系统发送的消息
                        content=text,
                        source="gateway.send",
                    )
                except Exception as e:
                    logger.warning(f"Failed to record message to session: {e}")

            return result
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None

    async def send_to_session(
        self,
        session: Session,
        text: str,
        role: str = "assistant",
        **kwargs,
    ) -> str | None:
        """
        发送消息到会话
        """
        result = await self.send(
            channel=session.channel,
            chat_id=session.chat_id,
            text=text,
            record_to_session=False,  # 下面手动记录
            **kwargs,
        )

        # 记录到 session 历史（用指定的 role）
        if self.session_manager:
            try:
                session.add_message(role=role, content=text, source="send_to_session")
                self.session_manager.mark_dirty()  # 触发保存
            except Exception as e:
                logger.warning(f"Failed to record message to session: {e}")

        return result

    async def emit_progress_event(
        self,
        session: Session,
        text: str,
        *,
        throttle_seconds: float | None = None,
        role: str = "system",
    ) -> None:
        """
        发出“进度事件”并由网关节流/合并后发送。

        - 多条事件会在节流窗口内合并为一条，避免刷屏。
        - 进度消息默认以 system role 记录到 session（不影响模型对话历史）。
        """
        if not session or not text:
            return

        session_key = session.session_key
        throttle = self._progress_throttle_seconds if throttle_seconds is None else throttle_seconds

        buf = self._progress_buffers.setdefault(session_key, [])
        buf.append(text)

        existing = self._progress_flush_tasks.get(session_key)
        if existing and not existing.done():
            return

        async def _flush() -> None:
            try:
                await asyncio.sleep(max(0.0, float(throttle)))
                lines = self._progress_buffers.get(session_key, [])
                if not lines:
                    return
                # 合并并清空
                combined = "\n".join(lines[:20])  # 强上限：最多合并 20 行
                self._progress_buffers[session_key] = []

                # 尽量回复到当前消息（若存在）
                reply_to = None
                try:
                    current_message = session.get_metadata("_current_message")
                    reply_to = (
                        getattr(current_message, "channel_message_id", None)
                        if current_message
                        else None
                    )
                except Exception:
                    reply_to = None

                await self.send_to_session(session, combined, role=role, reply_to=reply_to)
            except Exception as e:
                logger.warning(f"[Progress] flush failed: {e}")

        self._progress_flush_tasks[session_key] = asyncio.create_task(_flush())

    async def broadcast(
        self,
        text: str,
        channels: list[str] | None = None,
        user_ids: list[str] | None = None,
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
            "adapters": {name: adapter.is_running for name, adapter in self._adapters.items()},
            "queue_size": self._message_queue.qsize(),
            "sessions": self.session_manager.get_session_count(),
        }
