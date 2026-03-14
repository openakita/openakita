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

    API_BASE = "https://oapi.dingtalk.com"
    API_NEW = "https://api.dingtalk.com/v1.0"
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
        self._http_client: Any | None = None

        # Stream 模式
        self._stream_client: Any | None = None
        self._stream_thread: threading.Thread | None = None
        self._stream_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # 缓存每个会话的 session webhook、发送者 userId、会话类型
        self._session_webhooks: dict[str, str] = {}
        self._conversation_users: dict[str, str] = {}  # conversationId -> senderId
        self._conversation_types: dict[str, str] = {}  # conversationId -> "1"(单聊)/"2"(群聊)

        # 互动卡片 typing 状态: chat_id -> cardBizId
        self._thinking_cards: dict[str, str] = {}

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

        不关闭旧连接会导致钉钉平台在新旧连接间分发消息，
        发到旧连接上的消息因 _main_loop 已失效而被静默丢弃（与飞书同源 Bug）。
        """
        self._running = False

        # 1) 停止 Stream 线程的事件循环
        stream_loop = self._stream_loop
        if stream_loop is not None:
            try:
                stream_loop.call_soon_threadsafe(stream_loop.stop)
            except Exception:
                pass

        # 2) 等待 Stream 线程退出
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

        logger.info("DingTalk adapter stopped")

    # ==================== Stream 模式 ====================

    def _start_stream(self) -> None:
        """在后台线程中启动 Stream 长连接"""
        adapter = self

        class _ChatbotHandler(dingtalk_stream.ChatbotHandler):
            """自定义机器人消息处理器"""

            def __init__(self):
                # 官方 SDK 推荐的 init 模式：跳过 ChatbotHandler.__init__
                super(dingtalk_stream.ChatbotHandler, self).__init__()
                self.adapter = adapter

            async def process(self, callback: dingtalk_stream.CallbackMessage):
                """处理收到的消息回调"""
                try:
                    await self.adapter._handle_stream_message(callback)
                except Exception as e:
                    logger.error(f"Error handling DingTalk message: {e}", exc_info=True)
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        def _run_stream_in_thread() -> None:
            """在独立线程中运行 Stream 客户端"""
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            self._stream_loop = new_loop

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
                client.start_forever()
            except Exception as e:
                if self._running:
                    logger.error(f"DingTalk Stream error: {e}", exc_info=True)
            finally:
                self._stream_loop = None
                new_loop.close()

        self._stream_thread = threading.Thread(
            target=_run_stream_in_thread,
            daemon=True,
            name="DingTalkStream",
        )
        self._stream_thread.start()
        logger.info("DingTalk Stream client started in background thread")

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
        if self._main_loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._emit_message(unified), self._main_loop
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
            logger.error(
                "Main event loop not set (DingTalk adapter not started from async context?), "
                "dropping message to avoid dispatch failure in Stream thread"
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

            media = MediaFile.create(
                filename=f"dingtalk_voice_{download_code[:8]}.ogg",
                mime_type="audio/ogg",
                file_id=download_code,
            )
            media.duration = float(duration) / 1000.0 if duration else None
            return MessageContent(voices=[media])

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

        Gateway 的 _keep_typing 每 4 秒调用一次，仅第一次生成卡片。
        """
        if chat_id in self._thinking_cards:
            return
        card_biz_id = f"thinking_{uuid.uuid4().hex[:16]}"
        self._thinking_cards[chat_id] = card_biz_id
        try:
            await self._send_interactive_card(chat_id, card_biz_id, "💭 **正在思考中...**")
        except Exception as e:
            self._thinking_cards.pop(chat_id, None)
            logger.debug(f"DingTalk: send_typing card failed: {e}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """清理残留的 thinking card（更新为"处理完成"）。

        正常路径下 send_message 已经消费了卡片，此方法不会做任何事。
        仅在异常路径（Agent + _send_error 双重失败、或 typing 重建后未被消费）时触发。
        """
        card_biz_id = self._thinking_cards.pop(chat_id, None)
        if not card_biz_id:
            return
        try:
            await self._update_interactive_card(card_biz_id, "✅ 处理完成")
        except Exception:
            pass

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
        # ---- 思考卡片处理：尝试更新占位卡片为最终回复 ----
        card_biz_id = self._thinking_cards.pop(message.chat_id, None)
        if card_biz_id:
            text = message.content.text or ""
            if text and not message.content.has_media:
                try:
                    await self._update_interactive_card(card_biz_id, text)
                    return f"card_{card_biz_id}"
                except Exception as e:
                    logger.warning(f"DingTalk: update thinking card failed, fallback: {e}")
            else:
                with contextlib.suppress(Exception):
                    await self._update_interactive_card(card_biz_id, "✅ 处理完成")

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

        # 回退到 OpenAPI（文本消息）
        await self._refresh_token()
        is_group = message.metadata.get(
            "is_group", self._is_group_chat(message.chat_id)
        )
        try:
            if is_group:
                return await self._send_group_message(message)
            else:
                return await self._send_via_api(message)
        except RuntimeError as e:
            logger.error(f"OpenAPI send failed: {e}")
            raise

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
        通过 SessionWebhook 发送消息

        仅支持 text 和 markdown 类型，不支持图片/文件/语音。
        参考: https://open.dingtalk.com/document/robots/custom-robot-access/
        """
        text = message.content.text or ""

        # 支持 Markdown 格式
        if message.parse_mode == "markdown" or (
            text and any(c in text for c in ["**", "##", "- ", "```", "[", "]"])
        ):
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": text[:20] if text else "消息",
                    "text": text,
                },
            }
        else:
            payload = {
                "msgtype": "text",
                "text": {"content": text},
            }

        response = await self._http_client.post(webhook_url, json=payload)
        result = response.json()

        if result.get("errcode", 0) != 0:
            error_msg = result.get("errmsg", "Unknown error")
            logger.error(f"DingTalk webhook send failed: {error_msg}")
            raise RuntimeError(f"Failed to send via webhook: {error_msg}")

        return f"webhook_{int(time.time())}"

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
        response = await self._http_client.get(download_url)

        local_path = self.media_dir / media.filename
        with open(local_path, "wb") as f:
            f.write(response.content)

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"Downloaded media: {media.filename}")
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
        """
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        _import_httpx()

        url = f"{self.API_NEW}/oauth2/accessToken"
        body = {
            "appKey": self.config.app_key,
            "appSecret": self.config.app_secret,
        }

        response = await self._http_client.post(url, json=body)
        data = response.json()

        if "accessToken" not in data:
            raise RuntimeError(
                f"Failed to get new access token: {data.get('message', data)}"
            )

        self._access_token = data["accessToken"]
        self._token_expires_at = time.time() + data.get("expireIn", 7200) - 60
        logger.info("Refreshed new-style access token (OAuth2)")

        return self._access_token

    async def _refresh_old_token(self) -> str:
        """
        刷新旧版 access token (用于 oapi.dingtalk.com 接口)

        旧版 API (media/upload, gettoken 等) 使用 access_token 查询参数。
        """
        if self._old_access_token and time.time() < self._old_token_expires_at:
            return self._old_access_token

        _import_httpx()

        url = f"{self.API_BASE}/gettoken"
        params = {
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }

        response = await self._http_client.get(url, params=params)
        data = response.json()

        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"Failed to get old access token: {data.get('errmsg')}")

        self._old_access_token = data["access_token"]
        self._old_token_expires_at = time.time() + data["expires_in"] - 60
        logger.info("Refreshed old-style access token (gettoken)")

        return self._old_access_token
