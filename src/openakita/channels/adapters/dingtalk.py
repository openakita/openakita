"""
钉钉适配器

基于 dingtalk-stream SDK 实现 Stream 模式:
- WebSocket 长连接接收消息（无需公网 IP）
- 支持文本/图片/语音/文件/视频消息接收
- 支持文本/Markdown/图片/文件消息发送

参考文档:
- Stream 模式: https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/overview
- 机器人接收消息: https://open-dingtalk.github.io/developerpedia/docs/learn/bot/appbot/receive/
- dingtalk-stream SDK: https://pypi.org/project/dingtalk-stream/
"""

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)

# 延迟导入
httpx = None
dingtalk_stream = None


def _import_httpx():
    global httpx
    if httpx is None:
        import httpx as hx

        httpx = hx


def _import_dingtalk_stream():
    global dingtalk_stream
    if dingtalk_stream is None:
        try:
            import dingtalk_stream as ds

            dingtalk_stream = ds
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("dingtalk_stream"))


@dataclass
class DingTalkConfig:
    """钉钉配置"""

    app_key: str
    app_secret: str
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.app_key or not self.app_key.strip():
            raise ValueError("DingTalkConfig: app_key is required")
        if not self.app_secret or not self.app_secret.strip():
            raise ValueError("DingTalkConfig: app_secret is required")


class DingTalkStreamState(Enum):
    """Stream 连接状态机"""
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


@dataclass
class _StreamMetrics:
    """Stream 连接运行指标"""
    connected_since: float | None = None
    last_message_at: float | None = None
    last_reconnect_at: float | None = None
    reconnect_count: int = 0
    ack_fail_count: int = 0
    dedupe_hit_count: int = 0
    messages_received: int = 0


@dataclass
class _CardState:
    """AI/Standard Card 状态跟踪"""
    card_id: str
    is_ai_card: bool = True


class DingTalkAdapter(ChannelAdapter):
    """
    钉钉适配器

    使用 Stream 模式接收消息（推荐）:
    - 无需公网 IP 和域名
    - 通过 WebSocket 长连接接收消息
    - 自动处理连接管理和重连

    支持消息类型:
    - 接收: text, picture, richText, audio, video, file
    - 发送: text, markdown, image, file
    """

    channel_name = "dingtalk"

    capabilities = {
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": False,
        "get_user_info": False,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": True,
    }

    API_BASE = "https://oapi.dingtalk.com"
    API_NEW = "https://api.dingtalk.com/v1.0"

    # AI Card (流式卡片) — 官方 AI 模板，支持原生流式输出
    AI_CARD_TEMPLATE_ID = "382e4302-551d-4880-bf29-a30acfab2e71.schema"
    AI_CARD_CREATE_URL = "https://api.dingtalk.com/v1.0/card/instances"
    AI_CARD_DELIVER_URL = "https://api.dingtalk.com/v1.0/card/instances/deliver"
    AI_CARD_STREAM_URL = "https://api.dingtalk.com/v1.0/card/streaming"

    # StandardCard (降级方案) — 普通互动卡片
    CARD_SEND_URL = "https://api.dingtalk.com/v1.0/im/v1.0/robot/interactiveCards/send"
    CARD_UPDATE_URL = "https://api.dingtalk.com/v1.0/im/robots/interactiveCards"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        agent_id: str | None = None,
        media_dir: Path | None = None,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
    ):
        """
        Args:
            app_key: 应用 Client ID (原 AppKey，在钉钉开发者后台获取)
            app_secret: 应用 Client Secret (原 AppSecret，在钉钉开发者后台获取)
            agent_id: 应用 AgentId (发送消息时需要)
            media_dir: 媒体文件存储目录
            channel_name: 通道名称（多Bot时用于区分实例）
            bot_id: Bot 实例唯一标识
            agent_profile_id: 绑定的 agent profile ID
        """
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)

        self.config = DingTalkConfig(
            app_key=app_key,
            app_secret=app_secret,
            agent_id=agent_id,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/dingtalk")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # 旧版 access_token (oapi.dingtalk.com 接口用)
        self._old_access_token: str | None = None
        self._old_token_expires_at: float = 0
        # 新版 access_token (api.dingtalk.com/v1.0 接口用)
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._token_lock: asyncio.Lock = asyncio.Lock()
        self._old_token_lock: asyncio.Lock = asyncio.Lock()
        self._http_client: Any | None = None

        # Stream 模式
        self._stream_client: Any | None = None
        self._stream_thread: threading.Thread | None = None
        self._stream_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._stream_watchdog_task: asyncio.Task | None = None
        self._stream_restart_count: int = 0
        self._stream_state = DingTalkStreamState.IDLE
        self._stream_metrics = _StreamMetrics()

        # 缓存每个会话的 session webhook、发送者 userId、会话类型
        self._session_webhooks: dict[str, str] = {}
        self._conversation_users: dict[str, str] = {}  # conversationId -> senderId
        self._conversation_types: dict[str, str] = {}  # conversationId -> "1"(单聊)/"2"(群聊)

        # 消息去重：Stream 重连可能导致重复投递
        # key = "{bot_id}:{msgId}", value = timestamp (for TTL)
        self._seen_message_ids: dict[str, float] = {}
        self._seen_message_ids_max = 5000
        self._seen_message_ids_ttl = 60.0

        # 互动卡片 typing 状态: session_key -> _CardState
        self._thinking_cards: dict[str, _CardState] = {}
        # AI Card 可用性 (首次失败后降级为 StandardCard)
        self._ai_card_available: bool = True

        # 流式输出状态
        self._streaming_buffers: dict[str, str] = {}
        self._streaming_last_patch: dict[str, float] = {}
        self._streaming_finalized: set[str] = set()
        self._streaming_throttle_ms: int = 800
        self._streaming_enabled: bool = True

    def _make_session_key(self, chat_id: str, thread_id: str | None = None) -> str:
        return f"{chat_id}:{thread_id or ''}"

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        return self._streaming_enabled

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """逐 token 流式更新互动卡片内容。"""
        sk = self._make_session_key(chat_id, thread_id)
        card_state = self._thinking_cards.get(sk)
        if not card_state:
            return

        buf = self._streaming_buffers.get(sk, "") + token
        self._streaming_buffers[sk] = buf

        now = time.time() * 1000
        last = self._streaming_last_patch.get(sk, 0)
        if now - last < self._streaming_throttle_ms:
            return

        self._streaming_last_patch[sk] = now
        try:
            display = buf + " ▍"
            if card_state.is_ai_card:
                await self._stream_ai_card(card_state.card_id, display)
            else:
                await self._update_interactive_card(card_state.card_id, display)
        except Exception as e:
            logger.debug(f"DingTalk: stream_token patch failed: {e}")

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """完成流式输出，用最终文本更新卡片。"""
        sk = self._make_session_key(chat_id, thread_id)
        card_state = self._thinking_cards.pop(sk, None)

        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)

        if not card_state:
            return False

        try:
            if card_state.is_ai_card:
                await self._stream_ai_card(card_state.card_id, final_text, finished=True)
            else:
                await self._update_interactive_card(card_state.card_id, final_text)
            self._streaming_finalized.add(sk)
            return True
        except Exception as e:
            logger.warning(f"DingTalk: finalize_stream failed: {e}")
            return False

    async def start(self) -> None:
        """启动钉钉适配器 (Stream 模式)"""
        _import_httpx()
        _import_dingtalk_stream()

        self._http_client = httpx.AsyncClient()
        await self._refresh_token()

        self._running = True

        # 记录主事件循环，用于从 Stream 线程投递协程
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

        # 启动 Stream 长连接 (后台线程)
        self._start_stream()

        logger.info("DingTalk adapter started (Stream mode)")

    async def stop(self) -> None:
        """停止钉钉适配器，确保旧 Stream 连接被完全关闭。

        SDK 的 start() 内部是 while True 循环，会捕获所有异常（含 CancelledError）
        并自动重连。必须 monkey-patch open_connection 阻断重连才能真正退出。
        """
        self._running = False
        self._main_loop = None
        self._set_stream_state(DingTalkStreamState.STOPPED)

        # 0) 取消看门狗
        if self._stream_watchdog_task and not self._stream_watchdog_task.done():
            self._stream_watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_watchdog_task
            self._stream_watchdog_task = None

        client = self._stream_client
        stream_loop = self._stream_loop

        # 1) 阻断 SDK 重连：替换 open_connection 使其返回 None
        #    SDK 收到 None 后会 sleep(10) 再重试，但不再建立新连接，
        #    配合 loop.stop() 可在下一次 await 时退出。
        if client is not None:
            client.open_connection = lambda: None

        # 2) 关闭 WebSocket 以中断 Stream recv 循环
        if client is not None and stream_loop is not None:
            ws = getattr(client, "websocket", None)
            if ws is not None:
                try:
                    asyncio.run_coroutine_threadsafe(ws.close(), stream_loop)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

        # 3) 停止 Stream 线程的事件循环
        if stream_loop is not None:
            try:
                stream_loop.call_soon_threadsafe(stream_loop.stop)
            except Exception:
                pass

        # 4) 等待 Stream 线程退出
        stream_thread = self._stream_thread
        if stream_thread is not None and stream_thread.is_alive():
            stream_thread.join(timeout=5)
            if stream_thread.is_alive():
                logger.warning("DingTalk Stream thread did not exit within 5s timeout")

        self._stream_client = None
        self._stream_thread = None
        self._stream_loop = None

        if self._http_client:
            await self._http_client.aclose()

        m = self._stream_metrics
        logger.info(
            f"DingTalk adapter stopped "
            f"(msgs={m.messages_received}, reconnects={m.reconnect_count}, "
            f"dedup_hits={m.dedupe_hit_count})"
        )

    # ==================== Stream 模式 ====================

    def _set_stream_state(self, state: DingTalkStreamState) -> None:
        if self._stream_state != state:
            prev = self._stream_state
            self._stream_state = state
            logger.info(f"DingTalk Stream state: {prev.value} -> {state.value}")

    def _start_stream(self) -> None:
        """在后台线程中启动 Stream 长连接"""
        adapter = self

        class _ChatbotHandler(dingtalk_stream.ChatbotHandler):
            """自定义机器人消息处理器"""

            def __init__(self):
                super(dingtalk_stream.ChatbotHandler, self).__init__()
                self.adapter = adapter

            async def process(self, callback: dingtalk_stream.CallbackMessage):
                """ACK 先行：立即返回 ACK，异步处理消息。
                避免消息处理耗时导致 SDK 超时重发。"""
                asyncio.get_running_loop().create_task(self._safe_handle(callback))
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

            async def _safe_handle(self, callback: dingtalk_stream.CallbackMessage):
                try:
                    await self.adapter._handle_stream_message(callback)
                except Exception as e:
                    logger.error(f"Error handling DingTalk message: {e}", exc_info=True)

        def _run_stream_in_thread() -> None:
            """在独立线程中运行 Stream 客户端。

            使用 loop.run_until_complete(client.start()) 而非 client.start_forever()
            以确保 self._stream_loop 始终指向实际运行的事件循环，使 stop() 能正确中断。
            """
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._stream_loop = loop
            self._set_stream_state(DingTalkStreamState.CONNECTING)

            try:
                credential = dingtalk_stream.Credential(
                    self.config.app_key, self.config.app_secret
                )
                client = dingtalk_stream.DingTalkStreamClient(credential)
                client.register_callback_handler(
                    dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                    _ChatbotHandler(),
                )
                self._stream_client = client
                logger.info("DingTalk Stream client starting...")
                self._set_stream_state(DingTalkStreamState.RUNNING)
                self._stream_metrics.connected_since = time.time()
                loop.run_until_complete(client.start())
            except KeyboardInterrupt:
                pass
            except Exception as e:
                if self._running:
                    logger.error(f"DingTalk Stream error: {e}", exc_info=True)
            finally:
                self._stream_loop = None
                # SDK start() 的 while True 循环会 catch CancelledError 并 continue，
                # 尝试 cancel + gather 会永远阻塞。直接关闭 loop 即可。
                try:
                    loop.close()
                except Exception:
                    pass

        self._stream_thread = threading.Thread(
            target=_run_stream_in_thread,
            daemon=True,
            name="DingTalkStream",
        )
        self._stream_thread.start()
        logger.info("DingTalk Stream client started in background thread")

        # 启动 Stream 看门狗
        if self._stream_watchdog_task is None or self._stream_watchdog_task.done():
            self._stream_watchdog_task = asyncio.create_task(self._stream_watchdog_loop())

    # ==================== Stream 看门狗 ====================

    _STREAM_WATCHDOG_INTERVAL = 15
    _STREAM_WATCHDOG_INITIAL_DELAY = 30
    _STREAM_RECONNECT_MIN_INTERVAL = 10
    _STREAM_RECONNECT_MAX_DELAY = 120
    _STREAM_STABLE_THRESHOLD = 300

    async def _stream_watchdog_loop(self) -> None:
        """周期性检查 Stream 线程是否存活，退出后自动重启。"""
        await asyncio.sleep(self._STREAM_WATCHDOG_INITIAL_DELAY)
        last_restart_time = 0.0
        stable_since = asyncio.get_running_loop().time()

        while self._running:
            await asyncio.sleep(self._STREAM_WATCHDOG_INTERVAL)
            if not self._running:
                break

            st = self._stream_thread
            if st is not None and st.is_alive():
                now = asyncio.get_running_loop().time()
                if self._stream_restart_count > 0 and (now - stable_since) >= self._STREAM_STABLE_THRESHOLD:
                    logger.info("DingTalk Stream watchdog: connection stable, resetting restart count")
                    self._stream_restart_count = 0
                    self._set_stream_state(DingTalkStreamState.RUNNING)
                continue

            self._set_stream_state(DingTalkStreamState.RECONNECTING)
            now = asyncio.get_running_loop().time()
            since_last = now - last_restart_time
            if since_last < self._STREAM_RECONNECT_MIN_INTERVAL:
                continue

            self._stream_restart_count += 1
            self._stream_metrics.reconnect_count += 1
            self._stream_metrics.last_reconnect_at = time.time()
            backoff = min(
                self._STREAM_RECONNECT_MIN_INTERVAL * (2 ** min(self._stream_restart_count - 1, 6)),
                self._STREAM_RECONNECT_MAX_DELAY,
            )
            logger.warning(
                f"DingTalk Stream watchdog: thread exited (restart #{self._stream_restart_count}), "
                f"reconnecting in {backoff:.0f}s"
            )
            await asyncio.sleep(backoff)
            if not self._running:
                break

            try:
                self._start_stream()
                last_restart_time = asyncio.get_running_loop().time()
                stable_since = last_restart_time
                logger.info(
                    f"DingTalk Stream watchdog: reconnected (restart #{self._stream_restart_count})"
                )
            except Exception as e:
                logger.error(f"DingTalk Stream watchdog: reconnect failed: {e}")

    async def _handle_stream_message(
        self, callback: "dingtalk_stream.CallbackMessage"
    ) -> None:
        """
        处理 Stream 模式收到的消息

        SDK 的 ChatbotMessage.from_dict() 仅解析 text/picture/richText，
        audio/video/file 需要从 callback.data 原始字典手动解析。
        """
        raw_data = callback.data
        if not raw_data:
            return

        # 解析基础字段
        msg_type = raw_data.get("msgtype", "text")
        sender_id = raw_data.get("senderStaffId") or raw_data.get("senderId", "")
        conversation_id = raw_data.get("conversationId", "")
        conversation_type = raw_data.get("conversationType", "1")
        msg_id = raw_data.get("msgId", "")

        # 过期消息丢弃（createAt 为毫秒级时间戳）
        create_at_ms = raw_data.get("createAt")
        if create_at_ms and isinstance(create_at_ms, (int, float)):
            age_s = time.time() - create_at_ms / 1000
            if age_s > self.STALE_MESSAGE_THRESHOLD_S:
                logger.info(
                    f"DingTalk: stale message discarded (age={age_s:.0f}s): {msg_id}"
                )
                return
        else:
            logger.debug(f"DingTalk: message missing createAt field: {msg_id}")

        # 消息去重 (bot_id 前缀 + TTL 过期清理)
        if msg_id:
            dedup_key = f"{self.bot_id or ''}:{msg_id}"
            now = time.time()
            if dedup_key in self._seen_message_ids:
                self._stream_metrics.dedupe_hit_count += 1
                logger.debug(f"DingTalk: duplicate message ignored: {msg_id}")
                return
            # TTL 清理：移除过期条目
            if len(self._seen_message_ids) > self._seen_message_ids_max // 2:
                expired = [
                    k for k, ts in self._seen_message_ids.items()
                    if now - ts > self._seen_message_ids_ttl
                ]
                for k in expired:
                    del self._seen_message_ids[k]
            # 容量保护：如果仍超上限，移除最旧的
            if len(self._seen_message_ids) >= self._seen_message_ids_max:
                oldest = min(self._seen_message_ids, key=self._seen_message_ids.get)
                del self._seen_message_ids[oldest]
            self._seen_message_ids[dedup_key] = now

        self._stream_metrics.messages_received += 1
        self._stream_metrics.last_message_at = time.time()

        chat_type = "group" if conversation_type == "2" else "private"

        # 保存 session webhook 用于回复
        session_webhook = raw_data.get("sessionWebhook", "")
        if session_webhook and conversation_id:
            self._session_webhooks[conversation_id] = session_webhook
        if sender_id and conversation_id:
            self._conversation_users[conversation_id] = sender_id
        if conversation_id and conversation_type:
            self._conversation_types[conversation_id] = conversation_type
        metadata = {
            "session_webhook": session_webhook,
            "conversation_type": conversation_type,
            "is_group": chat_type == "group",
            "sender_name": raw_data.get("senderNick", ""),
            "chat_name": raw_data.get("conversationTitle", ""),
        }

        # 根据消息类型构建 content
        content = await self._parse_message_content(msg_type, raw_data)

        is_direct_message = conversation_type == "1"

        # 检测 @机器人：钉钉 isInAtList 字段，或检查 atUsers 列表
        is_mentioned = False
        if raw_data.get("isInAtList") is True:
            is_mentioned = True
        elif not is_mentioned:
            at_users = raw_data.get("atUsers") or []
            robot_code = self.config.app_key
            for at_user in at_users:
                if at_user.get("dingtalkId") == robot_code:
                    is_mentioned = True
                    break

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msg_id,
            user_id=f"dd_{sender_id}",
            channel_user_id=sender_id,
            chat_id=conversation_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=raw_data,
            metadata=metadata,
        )

        self._log_message(unified)

        # 从 Stream 线程投递到主事件循环。
        # 必须使用 run_coroutine_threadsafe：当前线程已有运行中的事件循环（SDK 的 stream loop），
        # 不能使用 asyncio.run()，否则会触发 RuntimeError 导致消息丢失。
        main_loop = self._main_loop
        if main_loop is not None and self._running and not main_loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                self._emit_message(unified), main_loop
            )
            def _on_emit_done(f: "asyncio.futures.Future") -> None:
                try:
                    f.result()
                except Exception as e:
                    logger.error(
                        f"Failed to dispatch DingTalk message to main loop: {e}",
                        exc_info=True,
                    )
            future.add_done_callback(_on_emit_done)
        else:
            logger.warning(
                "DingTalk: dropping message (adapter stopping or main loop unavailable)"
            )

    async def _parse_message_content(
        self, msg_type: str, raw_data: dict
    ) -> MessageContent:
        """根据消息类型解析内容"""

        if msg_type == "text":
            text_body = raw_data.get("text", {})
            text = text_body.get("content", "").strip()
            return MessageContent(text=text)

        elif msg_type == "picture":
            # 图片消息：content 可能是 dict 或 JSON 字符串
            content_raw = raw_data.get("content", {})
            if isinstance(content_raw, str):
                try:
                    content_raw = json.loads(content_raw)
                except (json.JSONDecodeError, TypeError):
                    content_raw = {}

            # 字段名: SDK 使用 downloadCode，部分版本可能用 pictureDownloadCode
            download_code = (
                content_raw.get("downloadCode", "")
                or content_raw.get("pictureDownloadCode", "")
            )

            if not download_code:
                # 兜底：尝试从 SDK ChatbotMessage 解析
                try:
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(raw_data)
                    if hasattr(incoming, "image_content") and incoming.image_content:
                        download_code = getattr(
                            incoming.image_content, "download_code", ""
                        ) or ""
                except Exception as e:
                    logger.warning(f"DingTalk: failed to parse picture via SDK: {e}")

            if not download_code:
                logger.warning("DingTalk: picture message has no downloadCode")
                return MessageContent(text="[图片: 无法获取下载码]")

            media = MediaFile.create(
                filename=f"dingtalk_image_{download_code[:8]}.jpg",
                mime_type="image/jpeg",
                file_id=download_code,
            )
            return MessageContent(images=[media])

        elif msg_type == "richText":
            # 富文本消息：提取文本和图片
            content_raw = raw_data.get("content", {})
            if isinstance(content_raw, str):
                try:
                    content_raw = json.loads(content_raw)
                except (json.JSONDecodeError, TypeError):
                    content_raw = {}
            rich_text = content_raw.get("richText", [])
            text_parts = []
            images = []

            for section in rich_text:
                if "text" in section:
                    text_parts.append(section["text"])
                # 兼容两种字段名
                code = section.get("downloadCode") or section.get("pictureDownloadCode")
                if code:
                    media = MediaFile.create(
                        filename=f"dingtalk_richimg_{code[:8]}.jpg",
                        mime_type="image/jpeg",
                        file_id=code,
                    )
                    images.append(media)
                # 将 @提及 保留为文本，方便 LLM 理解上下文
                if section.get("type") == "at" and section.get("userId"):
                    text_parts.append(f"@{section['userId']}")

            return MessageContent(
                text="\n".join(text_parts) if text_parts else None,
                images=images,
            )

        elif msg_type == "audio":
            # 语音消息 - SDK 不解析，从 raw_data 手动提取
            audio_content = raw_data.get("content", {})
            if isinstance(audio_content, str):
                try:
                    audio_content = json.loads(audio_content)
                except (json.JSONDecodeError, TypeError):
                    audio_content = {}
            download_code = audio_content.get("downloadCode", "")
            duration = audio_content.get("duration", 0)
            recognition = audio_content.get("recognition", "")

            media = MediaFile.create(
                filename=f"dingtalk_voice_{download_code[:8]}.ogg",
                mime_type="audio/ogg",
                file_id=download_code,
            )
            media.duration = float(duration) / 1000.0 if duration else None
            if recognition:
                media.transcription = recognition.strip()

            text = recognition.strip() if recognition else None
            return MessageContent(text=text, voices=[media])

        elif msg_type == "video":
            # 视频消息 - SDK 不解析
            video_content = raw_data.get("content", {})
            if isinstance(video_content, str):
                try:
                    video_content = json.loads(video_content)
                except (json.JSONDecodeError, TypeError):
                    video_content = {}
            download_code = video_content.get("downloadCode", "")
            duration = video_content.get("duration", 0)

            media = MediaFile.create(
                filename=f"dingtalk_video_{download_code[:8]}.mp4",
                mime_type="video/mp4",
                file_id=download_code,
            )
            media.duration = float(duration) / 1000.0 if duration else None
            return MessageContent(videos=[media])

        elif msg_type == "file":
            # 文件消息 - SDK 不解析
            file_content = raw_data.get("content", {})
            if isinstance(file_content, str):
                try:
                    file_content = json.loads(file_content)
                except (json.JSONDecodeError, TypeError):
                    file_content = {}
            download_code = file_content.get("downloadCode", "")
            file_name = file_content.get("fileName", "unknown_file")

            media = MediaFile.create(
                filename=file_name,
                mime_type="application/octet-stream",
                file_id=download_code,
            )
            return MessageContent(files=[media])

        else:
            # 未知消息类型，尝试提取文本
            logger.warning(f"Unknown DingTalk message type: {msg_type}")
            return MessageContent(text=f"[不支持的消息类型: {msg_type}]")

    # ==================== 消息发送 ====================

    def _is_group_chat(self, chat_id: str) -> bool:
        """判断 chat_id 是否为群聊会话"""
        # 优先使用缓存的 conversationType（来自接收消息时的回调数据）
        # "1" = 单聊, "2" = 群聊
        cached_type = self._conversation_types.get(chat_id)
        if cached_type is not None:
            return cached_type == "2"
        # 没有缓存时保守地认为是单聊（避免误调群聊API导致 robot 不存在）
        logger.warning(
            f"No cached conversationType for {chat_id[:20]}..., defaulting to private chat"
        )
        return False

    # ==================== 互动卡片 (Typing / Thinking Card) ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送"思考中..."占位卡片（首次调用时发送，后续调用跳过）。

        优先使用 AI Card (382e4302 模板)，失败时降级为 StandardCard。
        Gateway 的 _keep_typing 每 4 秒调用一次，仅第一次生成卡片。
        """
        sk = self._make_session_key(chat_id, thread_id)
        if sk in self._thinking_cards:
            return
        try:
            card_state = await self._create_card(chat_id)
            self._thinking_cards[sk] = card_state
        except Exception as e:
            logger.debug(f"DingTalk: send_typing card failed: {e}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """清理残留的 thinking card（更新为"处理完成"）。

        正常路径下 send_message 已经消费了卡片，此方法不会做任何事。
        仅在异常路径（Agent + _send_error 双重失败、或 typing 重建后未被消费）时触发。
        """
        sk = self._make_session_key(chat_id, thread_id)
        card_state = self._thinking_cards.pop(sk, None)
        if not card_state:
            return
        with contextlib.suppress(Exception):
            await self._finish_card(card_state, "✅ 处理完成")

    async def _create_card(self, chat_id: str) -> _CardState:
        """创建互动卡片。优先 AI Card，失败降级 StandardCard。"""
        if self._ai_card_available:
            try:
                card_id = await self._create_ai_card(chat_id)
                if card_id:
                    return _CardState(card_id=card_id, is_ai_card=True)
            except Exception as e:
                logger.info(f"DingTalk: AI Card unavailable, falling back to StandardCard: {e}")
                self._ai_card_available = False
        return await self._create_standard_card(chat_id)

    async def _finish_card(self, card_state: _CardState, content: str) -> None:
        """完成卡片更新（AI Card 设置 FINISHED，StandardCard 更新内容）"""
        if card_state.is_ai_card:
            await self._stream_ai_card(card_state.card_id, content, finished=True)
        else:
            await self._update_interactive_card(card_state.card_id, content)

    # --- AI Card (流式卡片) ---

    async def _create_ai_card(self, chat_id: str) -> str | None:
        """创建 AI Card 实例并投递，返回 outTrackId。"""
        await self._refresh_token()
        out_track_id = f"ai_{uuid.uuid4().hex[:16]}"
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        create_body = {
            "cardTemplateId": self.AI_CARD_TEMPLATE_ID,
            "outTrackId": out_track_id,
            "cardData": {
                "cardParamMap": {
                    "flowStatus": "PROCESSING",
                    "msgContent": "💭 正在思考中...",
                }
            },
        }
        resp = await self._http_client.post(
            self.AI_CARD_CREATE_URL, headers=headers, json=create_body
        )
        result = resp.json()
        if not result.get("outTrackId") and not result.get("success"):
            raise RuntimeError(f"AI Card create failed: {result}")

        conv_type = self._conversation_types.get(chat_id, "1")
        if conv_type == "2":
            open_space_id = f"dtv1.card//IM_GROUP.{chat_id}"
        else:
            staff_id = self._conversation_users.get(chat_id)
            if not staff_id or staff_id.startswith("$:LWCP"):
                raise ValueError("No valid staffId for AI Card delivery")
            open_space_id = f"dtv1.card//IM_ROBOT.{staff_id}"

        deliver_body = {
            "outTrackId": out_track_id,
            "openSpaceId": open_space_id,
            "deliverType": "IM",
        }
        resp = await self._http_client.post(
            self.AI_CARD_DELIVER_URL, headers=headers, json=deliver_body
        )
        result = resp.json()
        if not result.get("spaceId") and not result.get("success"):
            raise RuntimeError(f"AI Card deliver failed: {result}")

        logger.debug(f"DingTalk: AI Card created and delivered: {out_track_id}")
        return out_track_id

    async def _stream_ai_card(
        self, out_track_id: str, content: str, *, finished: bool = False
    ) -> None:
        """流式更新 AI Card 内容。finished=True 时将卡片标记为 FINISHED。"""
        await self._refresh_token()
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        if finished:
            body = {
                "outTrackId": out_track_id,
                "cardData": {
                    "cardParamMap": {
                        "flowStatus": "FINISHED",
                        "msgContent": content,
                    }
                },
            }
            resp = await self._http_client.put(
                self.AI_CARD_CREATE_URL, headers=headers, json=body
            )
            result = resp.json()
            if not result.get("success", True):
                logger.debug(f"AI Card finish failed: {result}")
        else:
            body = {
                "outTrackId": out_track_id,
                "guid": uuid.uuid4().hex,
                "key": "msgContent",
                "content": content,
                "isFull": True,
            }
            resp = await self._http_client.put(
                self.AI_CARD_STREAM_URL, headers=headers, json=body
            )
            result = resp.json()
            if not result.get("success", True):
                logger.debug(f"AI Card stream failed: {result}")

    # --- StandardCard (降级方案) ---

    async def _create_standard_card(self, chat_id: str) -> _CardState:
        """发送 StandardCard 互动卡片，返回 CardState。"""
        card_biz_id = f"thinking_{uuid.uuid4().hex[:16]}"
        await self._send_interactive_card(chat_id, card_biz_id, "💭 **正在思考中...**")
        return _CardState(card_id=card_biz_id, is_ai_card=False)

    async def _send_interactive_card(
        self, chat_id: str, card_biz_id: str, content: str
    ) -> None:
        """发送互动卡片（普通版 StandardCard）"""
        await self._refresh_token()
        card_data = json.dumps({
            "config": {"autoLayout": True, "enableForward": False},
            "header": {"title": {"type": "text", "text": ""}},
            "contents": [{"type": "markdown", "text": content, "id": "content_main"}],
        })
        body: dict = {
            "cardTemplateId": "StandardCard",
            "cardBizId": card_biz_id,
            "robotCode": self.config.app_key,
            "cardData": card_data,
            "pullStrategy": False,
        }
        conv_type = self._conversation_types.get(chat_id, "1")
        if conv_type == "2":
            body["openConversationId"] = chat_id
        else:
            staff_id = self._conversation_users.get(chat_id)
            if not staff_id or staff_id.startswith("$:LWCP"):
                raise ValueError("No valid staffId for single chat card")
            body["singleChatReceiver"] = json.dumps({"userId": staff_id})

        headers = {"x-acs-dingtalk-access-token": self._access_token}
        resp = await self._http_client.post(self.CARD_SEND_URL, headers=headers, json=body)
        result = resp.json()
        if "processQueryKey" not in result:
            raise RuntimeError(f"Card send failed: {result}")
        logger.debug(f"DingTalk: thinking card sent, bizId={card_biz_id}")

    async def _update_interactive_card(self, card_biz_id: str, content: str) -> None:
        """更新互动卡片内容（全量替换 cardData）"""
        await self._refresh_token()
        card_data = json.dumps({
            "config": {"autoLayout": True, "enableForward": True},
            "header": {"title": {"type": "text", "text": ""}},
            "contents": [{"type": "markdown", "text": content, "id": "content_main"}],
        })
        body = {"cardBizId": card_biz_id, "cardData": card_data}
        headers = {"x-acs-dingtalk-access-token": self._access_token}
        resp = await self._http_client.put(self.CARD_UPDATE_URL, headers=headers, json=body)
        result = resp.json()
        if "processQueryKey" not in result:
            raise RuntimeError(f"Card update failed: {result}")
        logger.debug(f"DingTalk: card updated, bizId={card_biz_id}")

    async def _patch_card_content(
        self, card_state: "_CardState", text: str,
    ) -> bool:
        """将进度/思考文本写入已存在的 thinking 卡片（不消费卡片）。

        gateway 的 _try_patch_progress_to_card 调用此方法，
        使思考内容直接更新到卡片上，避免发送独立文本消息导致时序错乱。
        """
        if not card_state or not card_state.card_id:
            return False
        try:
            if card_state.is_ai_card:
                await self._stream_ai_card(card_state.card_id, text)
            else:
                await self._update_interactive_card(card_state.card_id, text)
            return True
        except Exception as e:
            logger.debug(f"DingTalk: _patch_card_content failed: {e}")
            return False

    # ==================== 消息发送 ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息 - 智能路由

        路由策略：
        - 所有消息 → 优先 SessionWebhook
          - 纯文本 → text 类型
          - Markdown → markdown 类型
          - 媒体 → 转为 markdown 内嵌 (图片: ![img](@lAL...))
        - Webhook 不可用时 → 回退 OpenAPI
        - OpenAPI 失败时 → 降级为文本

        核心约束: 钉钉 Webhook 只支持 text/markdown/actionCard/feedCard，
        不支持 image/file/voice 原生类型。所有图片必须通过 markdown 嵌入。
        """
        # ---- 流式已 finalize → 跳过重复发送 ----
        sk = self._make_session_key(message.chat_id, message.thread_id)
        if sk in self._streaming_finalized:
            self._streaming_finalized.discard(sk)
            logger.debug(f"DingTalk: send_message skipped (stream finalized): {sk}")
            return f"stream_finalized_{sk}"

        # ---- 思考卡片处理：尝试更新占位卡片为最终回复 ----
        # 流式/非流式保护期间跳过，避免进度消息消费卡片
        if sk in self._streaming_buffers:
            card_state = None
        else:
            card_state = self._thinking_cards.pop(sk, None)
        if card_state:
            text = message.content.text or ""
            if text and not message.content.has_media:
                try:
                    await self._finish_card(card_state, text)
                    return f"card_{card_state.card_id}"
                except Exception as e:
                    logger.warning(f"DingTalk: update thinking card failed, fallback: {e}")
            else:
                with contextlib.suppress(Exception):
                    await self._finish_card(card_state, "✅ 处理完成")

        # 获取 webhook
        session_webhook = message.metadata.get("session_webhook", "")
        if not session_webhook:
            session_webhook = self._session_webhooks.get(message.chat_id, "")

        # 媒体消息：转为 markdown 通过 webhook 发送
        has_media = (
            message.content.images
            or message.content.files
            or message.content.voices
        )

        if has_media and session_webhook:
            md_parts = []
            text_part = message.content.text or ""
            if text_part:
                md_parts.append(text_part)

            # 图片 → 上传获取 media_id，嵌入 markdown
            for img in message.content.images or []:
                mid = img.file_id
                if not mid and img.local_path:
                    try:
                        uploaded = await self.upload_media(
                            Path(img.local_path), img.mime_type or "image/png"
                        )
                        mid = uploaded.file_id
                    except Exception as e:
                        logger.warning(f"Image upload failed: {e}")
                if mid:
                    md_parts.append(f"![image]({mid})")
                else:
                    md_parts.append(f"📎 图片: {img.filename}")

            # 文件 → 只能发文件名
            for f in message.content.files or []:
                md_parts.append(f"📎 文件: {f.filename}")

            # 语音 → 只能发提示
            for v in message.content.voices or []:
                md_parts.append(f"🎤 语音: {v.filename}")

            md_text = "\n\n".join(md_parts)
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": md_text[:20] if md_text else "消息",
                    "text": md_text,
                },
            }
            try:
                response = await self._http_client.post(session_webhook, json=payload)
                result = response.json()
                if result.get("errcode", 0) == 0:
                    logger.info("Sent media via webhook markdown")
                    return f"webhook_{int(time.time())}"
                else:
                    logger.warning(f"Webhook media failed: {result.get('errmsg')}")
            except Exception as e:
                logger.warning(f"Webhook media error: {e}")

            # 降级为纯文本
            fallback_text = message.content.text or "[媒体消息]"
            fallback = OutgoingMessage.text(message.chat_id, fallback_text)
            if session_webhook:
                return await self._send_via_webhook(fallback, session_webhook)

        # 纯文本消息：优先走 Webhook（更快）
        if session_webhook:
            return await self._send_via_webhook(message, session_webhook)

        # 回退到 OpenAPI
        await self._refresh_token()
        is_group = message.metadata.get(
            "is_group", self._is_group_chat(message.chat_id)
        )
        try:
            if is_group:
                result_id = await self._send_group_message(message)
            else:
                result_id = await self._send_via_api(message)
        except RuntimeError as e:
            logger.error(f"OpenAPI send failed: {e}")
            raise

        # OpenAPI _build_msg_key_param 只处理首条媒体，补发剩余图片/文件
        for extra_img in (message.content.images or [])[1:]:
            if extra_img.local_path:
                try:
                    await self.send_image(message.chat_id, extra_img.local_path)
                except Exception as e:
                    logger.warning(f"DingTalk: send extra image failed: {e}")
        for extra_file in (message.content.files or [])[1:]:
            if extra_file.local_path:
                try:
                    await self.send_file(message.chat_id, extra_file.local_path)
                except Exception as e:
                    logger.warning(f"DingTalk: send extra file failed: {e}")

        return result_id

    async def _build_msg_key_param(
        self, message: OutgoingMessage
    ) -> tuple[str, dict]:
        """
        从 OutgoingMessage 构建钉钉消息类型参数

        Returns:
            (msgKey, msgParam) 元组

        消息类型参考: https://open.dingtalk.com/document/development/robot-message-type
        - sampleText:     {"content": "..."}
        - sampleMarkdown: {"title": "...", "text": "..."}
        - sampleImageMsg: {"photoURL": "..."}
        - sampleFile:     {"mediaId": "@...", "fileName": "...", "fileType": "..."}
        - sampleAudio:    {"mediaId": "@...", "duration": "3000"}
        """
        # 图片消息
        if message.content.images:
            image = message.content.images[0]
            photo_url = image.url  # 优先用已有的 URL
            media_id = image.file_id

            if not photo_url and image.local_path:
                try:
                    uploaded = await self.upload_media(
                        Path(image.local_path), image.mime_type or "image/png"
                    )
                    photo_url = uploaded.url  # 临时 URL（仅图片上传返回）
                    media_id = uploaded.file_id
                except Exception as e:
                    logger.error(f"Failed to upload image: {e}")

            # sampleImageMsg 需要 photoURL（可以是 URL 或 @mediaId）
            if photo_url:
                return "sampleImageMsg", {"photoURL": photo_url}
            elif media_id:
                return "sampleImageMsg", {"photoURL": media_id}
            return "sampleText", {"content": message.content.text or "[图片发送失败]"}

        # 文件消息
        if message.content.files:
            file = message.content.files[0]
            media_id = file.file_id

            if not media_id and file.local_path:
                try:
                    uploaded = await self.upload_media(
                        Path(file.local_path),
                        file.mime_type or "application/octet-stream",
                    )
                    media_id = uploaded.file_id
                except Exception as e:
                    logger.error(f"Failed to upload file: {e}")

            if media_id:
                ext = Path(file.filename).suffix.lstrip(".") or "file"
                return "sampleFile", {
                    "mediaId": media_id,
                    "fileName": file.filename,
                    "fileType": ext,
                }
            return "sampleText", {
                "content": message.content.text or f"[文件: {file.filename}]"
            }

        # 语音消息
        if message.content.voices:
            voice = message.content.voices[0]
            media_id = voice.file_id

            if not media_id and voice.local_path:
                try:
                    uploaded = await self.upload_media(
                        Path(voice.local_path), voice.mime_type or "audio/ogg"
                    )
                    media_id = uploaded.file_id
                except Exception as e:
                    logger.error(f"Failed to upload voice: {e}")

            if media_id:
                duration_ms = str(int((voice.duration or 3) * 1000))
                return "sampleAudio", {"mediaId": media_id, "duration": duration_ms}
            return "sampleText", {"content": "[语音发送失败]"}

        # 视频消息
        if message.content.videos:
            video = message.content.videos[0]
            media_id = video.file_id

            if not media_id and video.local_path:
                try:
                    uploaded = await self.upload_media(
                        Path(video.local_path), video.mime_type or "video/mp4"
                    )
                    media_id = uploaded.file_id
                except Exception as e:
                    logger.error(f"Failed to upload video: {e}")

            if media_id:
                duration_ms = str(int((video.duration or 0) * 1000))
                ext = Path(video.filename).suffix.lstrip(".") or "mp4"
                return "sampleVideo", {
                    "mediaId": media_id,
                    "duration": duration_ms,
                    "videoType": ext,
                }
            return "sampleText", {"content": "[视频发送失败]"}

        # 纯文本 / Markdown
        text = message.content.text or ""
        if message.parse_mode == "markdown" or any(
            c in text for c in ["**", "##", "- ", "```"]
        ):
            return "sampleMarkdown", {"title": text[:20], "text": text}
        return "sampleText", {"content": text}

    async def _send_via_webhook(
        self, message: OutgoingMessage, webhook_url: str
    ) -> str:
        """
        通过 SessionWebhook 发送消息（自动分块超长文本）

        仅支持 text 和 markdown 类型，不支持图片/文件/语音。
        参考: https://open.dingtalk.com/document/robots/custom-robot-access/
        """
        text = message.content.text or ""

        is_markdown = message.parse_mode == "markdown" or (
            text and any(c in text for c in ["**", "##", "- ", "```", "[", "]"])
        )

        chunks = self._chunk_markdown_text(text, self._MARKDOWN_MAX_LENGTH) if text else [text]
        if len(chunks) > 1:
            logger.info(f"DingTalk: splitting long message into {len(chunks)} chunks")

        result_id = ""
        for chunk in chunks:
            if is_markdown:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": chunk[:20] if chunk else "消息",
                        "text": chunk,
                    },
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {"content": chunk},
                }

            response = await self._http_client.post(webhook_url, json=payload)
            result = response.json()

            if result.get("errcode", 0) != 0:
                error_msg = result.get("errmsg", "Unknown error")
                logger.error(f"DingTalk webhook send failed: {error_msg}")
                raise RuntimeError(f"Failed to send via webhook: {error_msg}")

            result_id = f"webhook_{int(time.time())}"

        return result_id

    async def _send_group_message(self, message: OutgoingMessage) -> str:
        """
        通过 OpenAPI 发送群聊消息

        API: POST /v1.0/robot/groupMessages/send
        参考: https://open.dingtalk.com/document/group/the-robot-sends-a-group-message
        """
        url = f"{self.API_NEW}/robot/groupMessages/send"
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        msg_key, msg_param = await self._build_msg_key_param(message)

        data = {
            "robotCode": self.config.app_key,
            "openConversationId": message.chat_id,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param),
        }

        logger.info(f"Sending group message: msgKey={msg_key}, chat={message.chat_id[:20]}...")

        response = await self._http_client.post(url, headers=headers, json=data)
        result = response.json()

        if "processQueryKey" not in result:
            error = result.get("message", result.get("errmsg", "Unknown error"))
            logger.error(f"Failed to send group message: {error}, data={data}")
            raise RuntimeError(f"Failed to send group message: {error}")

        return result["processQueryKey"]

    async def _send_via_api(self, message: OutgoingMessage) -> str:
        """
        通过 OpenAPI 发送单聊消息

        API: POST /v1.0/robot/oToMessages/batchSend
        """
        url = f"{self.API_NEW}/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        msg_key, msg_param = await self._build_msg_key_param(message)

        # 优先使用缓存的 userId（chat_id 可能是 conversationId，不能直接当 userId 用）
        user_id = self._conversation_users.get(message.chat_id, message.chat_id)

        data = {
            "robotCode": self.config.app_key,
            "userIds": [user_id],
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param),
        }

        logger.info(f"Sending 1-on-1 message: msgKey={msg_key}, user={user_id[:12]}...")

        response = await self._http_client.post(url, headers=headers, json=data)
        result = response.json()

        if "processQueryKey" not in result:
            error = result.get("message", "Unknown error")
            raise RuntimeError(f"Failed to send message: {error}")

        return result["processQueryKey"]

    async def send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """
        发送图片消息 - 钉钉定制实现

        策略 (按优先级):
        1. 上传图片获取 media_id
        2. 通过 SessionWebhook + Markdown 嵌入图片
           - 优先使用 upload 返回的 URL（如有）
           - 否则用 media_id（@lAL...格式，钉钉内部可渲染）
        3. 尝试旧版 API 工作通知（仅单聊，使用 media_id）
        4. 降级为文本

        参考: https://open.dingtalk.com/document/robots/custom-robot-access/
        """
        path = Path(image_path)

        # Step 1: 上传图片获取 media_id
        try:
            uploaded = await self.upload_media(path, "image/png")
        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            text = f"📎 图片: {path.name}"
            if caption:
                text = f"{caption}\n{text}"
            msg = OutgoingMessage.text(chat_id, text)
            return await self.send_message(msg)

        media_id = uploaded.file_id
        media_url = uploaded.url  # 可能为空
        if not media_id:
            text = f"[图片上传失败: {path.name}]"
            msg = OutgoingMessage.text(chat_id, text)
            return await self.send_message(msg)

        logger.info(
            f"Image uploaded: {path.name} -> media_id={media_id}, url={'YES' if media_url else 'NO'}"
        )

        # Step 2: 尝试 OpenAPI sampleImageMsg（需要权限）
        await self._refresh_token()
        is_group = self._is_group_chat(chat_id)
        # sampleImageMsg 的 photoURL 可以是 URL 或 media_id
        photo_url = media_url or media_id
        msg_param = json.dumps({"photoURL": photo_url})
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        if is_group:
            url = f"{self.API_NEW}/robot/groupMessages/send"
            data = {
                "robotCode": self.config.app_key,
                "openConversationId": chat_id,
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }
        else:
            user_id = self._conversation_users.get(chat_id, chat_id)
            url = f"{self.API_NEW}/robot/oToMessages/batchSend"
            data = {
                "robotCode": self.config.app_key,
                "userIds": [user_id],
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }

        try:
            chat_mode = "group" if is_group else "private"
            logger.info(f"Sending image via OpenAPI ({chat_mode}): {path.name}")
            response = await self._http_client.post(url, headers=headers, json=data)
            result = response.json()
            logger.debug(f"OpenAPI image response: {result}")

            if "processQueryKey" in result:
                logger.info(f"Image sent via OpenAPI ({chat_mode}): {path.name}")
                return result["processQueryKey"]
            else:
                error = result.get("message", result.get("errmsg", "Unknown"))
                perm_hint = (
                    "'企业内部机器人发送群聊消息'" if is_group
                    else "'企业内部机器人发送单聊消息'"
                )
                logger.warning(
                    f"OpenAPI sampleImageMsg failed ({chat_mode}): {error} "
                    f"(hint: 需要在钉钉开发者后台开通{perm_hint}权限)"
                )
        except Exception as e:
            logger.warning(f"OpenAPI image send error: {e}")

        # Step 3: 降级为 webhook markdown 嵌入图片
        session_webhook = self._session_webhooks.get(chat_id, "")
        if session_webhook:
            img_ref = media_url or media_id
            md_text = f"![image]({img_ref})"
            if caption:
                md_text = f"{caption}\n\n{md_text}"

            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": caption or "图片",
                    "text": md_text,
                },
            }

            try:
                response = await self._http_client.post(session_webhook, json=payload)
                result = response.json()
                if result.get("errcode", 0) == 0:
                    logger.info(
                        f"Sent image via webhook markdown: ref={img_ref[:40]}..."
                    )
                    return f"webhook_{int(time.time())}"
                else:
                    logger.warning(
                        f"Webhook markdown image failed: {result.get('errmsg')}"
                    )
            except Exception as e:
                logger.warning(f"Webhook image send error: {e}")

        # Step 4: 降级为文本
        text = f"📎 图片: {path.name}"
        if caption:
            text = f"{caption}\n{text}"
        msg = OutgoingMessage.text(chat_id, text)
        return await self.send_message(msg)

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件

        策略 (按优先级):
        1. 上传文件获取 media_id
        2. 尝试 OpenAPI 发送 sampleFile（需要权限）
        3. 降级为 webhook 文本提示
        """
        path = Path(file_path)

        # Step 1: 上传文件
        media_id = None
        try:
            uploaded = await self.upload_media(path, "application/octet-stream")
            media_id = uploaded.file_id
            logger.info(
                f"File uploaded: {path.name} -> media_id={media_id}, "
                f"url={'YES' if uploaded.url else 'NO'}"
            )
        except Exception as e:
            logger.warning(f"DingTalk upload_media failed for file: {e}")

        # Step 2: 尝试 OpenAPI sampleFile
        if media_id:
            await self._refresh_token()
            ext = path.suffix.lstrip(".") or "file"
            msg_param = json.dumps({
                "mediaId": media_id,
                "fileName": path.name,
                "fileType": ext,
            })

            is_group = self._is_group_chat(chat_id)
            headers = {"x-acs-dingtalk-access-token": self._access_token}

            if is_group:
                url = f"{self.API_NEW}/robot/groupMessages/send"
                data = {
                    "robotCode": self.config.app_key,
                    "openConversationId": chat_id,
                    "msgKey": "sampleFile",
                    "msgParam": msg_param,
                }
            else:
                user_id = self._conversation_users.get(chat_id, chat_id)
                url = f"{self.API_NEW}/robot/oToMessages/batchSend"
                data = {
                    "robotCode": self.config.app_key,
                    "userIds": [user_id],
                    "msgKey": "sampleFile",
                    "msgParam": msg_param,
                }

            try:
                chat_mode = "group" if is_group else "private"
                logger.info(f"Sending file via OpenAPI ({chat_mode}): {path.name}")
                response = await self._http_client.post(
                    url, headers=headers, json=data
                )
                result = response.json()
                logger.debug(f"OpenAPI file response: {result}")

                if "processQueryKey" in result:
                    logger.info(f"File sent via OpenAPI ({chat_mode}): {path.name}")
                    return result["processQueryKey"]
                else:
                    error = result.get("message", result.get("errmsg", "Unknown"))
                    perm_hint = (
                        "'企业内部机器人发送群聊消息'" if is_group
                        else "'企业内部机器人发送单聊消息'"
                    )
                    logger.warning(
                        f"OpenAPI sampleFile failed ({chat_mode}): {error} "
                        f"(hint: 需要在钉钉开发者后台开通{perm_hint}权限)"
                    )
            except Exception as e:
                logger.warning(f"OpenAPI file send error: {e}")

        # Step 3: 降级为 webhook 文本提示
        text = f"📎 文件: {path.name}"
        if caption:
            text = f"{caption}\n{text}"
        msg = OutgoingMessage.text(chat_id, text)
        return await self.send_message(msg)

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送语音

        钉钉 Webhook 不支持语音，降级为文件发送 → 文本
        """
        return await self.send_file(chat_id, voice_path, caption or "语音消息")

    # ==================== 文本分块 ====================

    _MARKDOWN_MAX_LENGTH = 4000

    @staticmethod
    def _chunk_markdown_text(text: str, max_length: int = 4000) -> list[str]:
        """将超长 Markdown 文本分块，避免在代码块中间断开。

        分块策略（按优先级）：
        1. 在段落边界 (\\n\\n) 处断开
        2. 在行边界 (\\n) 处断开
        3. 硬截断
        额外检查：避免在未闭合的 ``` 代码块中间断开。
        """
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break

            chunk = remaining[:max_length]
            split_pos = chunk.rfind("\n\n")
            if split_pos < max_length // 3:
                split_pos = chunk.rfind("\n")
            if split_pos < max_length // 4:
                split_pos = max_length
            else:
                split_pos += 1

            # 检查是否会截断代码块
            open_fences = chunk[:split_pos].count("```")
            if open_fences % 2 != 0:
                fence_pos = chunk[:split_pos].rfind("```")
                if fence_pos > max_length // 4:
                    split_pos = fence_pos

            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos:]

        return chunks

    # ==================== Markdown / 卡片 ====================

    async def send_markdown(
        self,
        user_id: str,
        title: str,
        text: str,
    ) -> str:
        """发送 Markdown 消息"""
        await self._refresh_token()

        url = f"{self.API_NEW}/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        data = {
            "robotCode": self.config.app_key,
            "userIds": [user_id],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({"title": title, "text": text}),
        }

        response = await self._http_client.post(url, headers=headers, json=data)
        result = response.json()
        return result.get("processQueryKey", "")

    async def send_action_card(
        self,
        user_id: str,
        title: str,
        text: str,
        single_title: str,
        single_url: str,
    ) -> str:
        """发送卡片消息"""
        await self._refresh_token()

        url = f"{self.API_NEW}/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": self._access_token}

        data = {
            "robotCode": self.config.app_key,
            "userIds": [user_id],
            "msgKey": "sampleActionCard",
            "msgParam": json.dumps(
                {
                    "title": title,
                    "text": text,
                    "singleTitle": single_title,
                    "singleURL": single_url,
                }
            ),
        }

        response = await self._http_client.post(url, headers=headers, json=data)
        result = response.json()
        return result.get("processQueryKey", "")

    # ==================== 媒体处理 ====================

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.file_id:
            raise ValueError("Media has no file_id (downloadCode)")

        # 使用钉钉新版文件下载 API（POST 方法，新版 token）
        token = await self._refresh_token()
        url = f"{self.API_NEW}/robot/messageFiles/download"
        headers = {"x-acs-dingtalk-access-token": token}
        body = {"downloadCode": media.file_id, "robotCode": self.config.app_key}

        response = await self._http_client.post(url, headers=headers, json=body)
        result = response.json()

        download_url = result.get("downloadUrl")
        if not download_url:
            logger.error(
                f"DingTalk download API failed: status={response.status_code}, "
                f"body={result}, file_id={media.file_id[:16]}..."
            )
            raise RuntimeError(
                f"Failed to get download URL: {result.get('message', 'Unknown')}"
            )

        # 下载文件
        response = await self._http_client.get(download_url, timeout=60.0)
        response.raise_for_status()

        safe_name = Path(media.filename).name or "download"
        local_path = self.media_dir / safe_name
        with open(local_path, "wb") as f:
            f.write(response.content)

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"Downloaded media: {safe_name}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        上传媒体文件到钉钉

        使用钉钉旧版 media/upload API 上传文件，获取 media_id。
        注意: 此接口在 oapi.dingtalk.com 上，需要旧版 access_token。
        """
        old_token = await self._refresh_old_token()

        url = f"{self.API_BASE}/media/upload"
        params = {"access_token": old_token}

        # 根据 mime_type 确定类型
        if mime_type.startswith("image/"):
            media_type = "image"
        elif mime_type.startswith("audio/"):
            media_type = "voice"
        elif mime_type.startswith("video/"):
            media_type = "video"
        else:
            media_type = "file"

        try:
            with open(path, "rb") as f:
                files = {"media": (path.name, f, mime_type)}
                data = {"type": media_type}
                response = await self._http_client.post(
                    url, params=params, files=files, data=data
                )

            result = response.json()
            logger.debug(f"Upload response: {result}")

            if result.get("errcode", 0) != 0:
                raise RuntimeError(
                    f"Upload failed: {result.get('errmsg', 'Unknown error')}"
                )

            media_id = result.get("media_id", "")
            media_url = result.get("url", "")

            media = MediaFile.create(
                filename=path.name,
                mime_type=mime_type,
                file_id=media_id,
                url=media_url,
            )
            media.status = MediaStatus.READY

            logger.info(
                f"Uploaded media: {path.name} -> media_id={media_id}, "
                f"url={'YES' if media_url else 'NO'}, type={media_type}"
            )
            return media

        except Exception as e:
            logger.error(f"Failed to upload media {path.name}: {e}")
            # 返回基础 MediaFile（无 media_id）
            return MediaFile.create(
                filename=path.name,
                mime_type=mime_type,
            )

    # ==================== Token 管理 ====================

    async def _refresh_token(self) -> str:
        """
        刷新新版 access token (用于 api.dingtalk.com/v1.0 接口)

        新版 API (robot/groupMessages/send, robot/oToMessages/batchSend 等)
        需要通过 OAuth2 接口获取的 accessToken，
        放在请求头 x-acs-dingtalk-access-token 中。

        使用 asyncio.Lock 进行 double-check locking，避免并发重复刷新。
        """
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at:
                return self._access_token

            _import_httpx()
            from ..retry import async_with_retry

            async def _do_refresh() -> dict:
                url = f"{self.API_NEW}/oauth2/accessToken"
                body = {
                    "appKey": self.config.app_key,
                    "appSecret": self.config.app_secret,
                }
                response = await self._http_client.post(url, json=body, timeout=10.0)
                data = response.json()
                if "accessToken" not in data:
                    raise RuntimeError(
                        f"Failed to get new access token: {data.get('message', data)}"
                    )
                return data

            data = await async_with_retry(
                _do_refresh, max_retries=2, base_delay=1.0,
                operation_name="DingTalk._refresh_token",
            )
            self._access_token = data["accessToken"]
            self._token_expires_at = time.time() + data.get("expireIn", 7200) - 300
            logger.info("Refreshed new-style access token (OAuth2)")

            return self._access_token

    async def _refresh_old_token(self) -> str:
        """
        刷新旧版 access token (用于 oapi.dingtalk.com 接口)

        旧版 API (media/upload, gettoken 等) 使用 access_token 查询参数。

        使用 asyncio.Lock 进行 double-check locking，避免并发重复刷新。
        """
        if self._old_access_token and time.time() < self._old_token_expires_at:
            return self._old_access_token

        async with self._old_token_lock:
            if self._old_access_token and time.time() < self._old_token_expires_at:
                return self._old_access_token

            _import_httpx()
            from ..retry import async_with_retry

            async def _do_refresh() -> dict:
                url = f"{self.API_BASE}/gettoken"
                params = {
                    "appkey": self.config.app_key,
                    "appsecret": self.config.app_secret,
                }
                response = await self._http_client.get(url, params=params, timeout=10.0)
                data = response.json()
                if data.get("errcode", 0) != 0:
                    raise RuntimeError(f"Failed to get old access token: {data.get('errmsg')}")
                return data

            data = await async_with_retry(
                _do_refresh, max_retries=2, base_delay=1.0,
                operation_name="DingTalk._refresh_old_token",
            )
            self._old_access_token = data["access_token"]
            self._old_token_expires_at = time.time() + data["expires_in"] - 300
            logger.info("Refreshed old-style access token (gettoken)")

            return self._old_access_token
