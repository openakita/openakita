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
import json
import logging
import threading
import time
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
            raise ImportError(
                "dingtalk-stream not installed. Run: pip install dingtalk-stream"
            )


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

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        agent_id: str | None = None,
        media_dir: Path | None = None,
    ):
        """
        Args:
            app_key: 应用 Client ID (原 AppKey，在钉钉开发者后台获取)
            app_secret: 应用 Client Secret (原 AppSecret，在钉钉开发者后台获取)
            agent_id: 应用 AgentId (发送消息时需要)
            media_dir: 媒体文件存储目录
        """
        super().__init__()

        self.config = DingTalkConfig(
            app_key=app_key,
            app_secret=app_secret,
            agent_id=agent_id,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/dingtalk")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._http_client: Any | None = None

        # Stream 模式
        self._stream_client: Any | None = None
        self._stream_thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # 缓存每个会话的 session webhook 和发送者 userId
        self._session_webhooks: dict[str, str] = {}
        self._conversation_users: dict[str, str] = {}  # conversationId -> senderId

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
        """停止钉钉适配器"""
        self._running = False

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
                logger.error(f"DingTalk Stream error: {e}", exc_info=True)
            finally:
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
        metadata = {
            "session_webhook": session_webhook,
            "conversation_type": conversation_type,
            "is_group": chat_type == "group",
        }

        # 根据消息类型构建 content
        content = await self._parse_message_content(msg_type, raw_data)

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msg_id,
            user_id=f"dd_{sender_id}",
            channel_user_id=sender_id,
            chat_id=conversation_id,
            content=content,
            chat_type=chat_type,
            raw=raw_data,
            metadata=metadata,
        )

        self._log_message(unified)

        # 从 Stream 线程投递到主事件循环
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._emit_message(unified), self._main_loop
            )
        else:
            await self._emit_message(unified)

    async def _parse_message_content(
        self, msg_type: str, raw_data: dict
    ) -> MessageContent:
        """根据消息类型解析内容"""

        if msg_type == "text":
            text_body = raw_data.get("text", {})
            text = text_body.get("content", "").strip()
            return MessageContent(text=text)

        elif msg_type == "picture":
            # SDK 解析的图片消息
            download_code = raw_data.get("content", {}).get("pictureDownloadCode", "")
            if not download_code:
                # 尝试从 chatbotMessage 解析
                try:
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(raw_data)
                    if hasattr(incoming, "image_content") and incoming.image_content:
                        download_code = getattr(
                            incoming.image_content, "download_code", ""
                        )
                except Exception:
                    pass

            media = MediaFile.create(
                filename=f"dingtalk_image_{download_code[:8]}.jpg",
                mime_type="image/jpeg",
                file_id=download_code,
            )
            return MessageContent(images=[media])

        elif msg_type == "richText":
            # 富文本消息：提取文本和图片
            rich_text = raw_data.get("content", {}).get("richText", [])
            text_parts = []
            images = []

            for section in rich_text:
                if "text" in section:
                    text_parts.append(section["text"])
                if "pictureDownloadCode" in section:
                    code = section["pictureDownloadCode"]
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
        # conversationId 以 "cid" 开头的是群聊
        return chat_id.startswith("cid")

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息 - 智能路由

        路由策略：
        - 纯文本/Markdown → 优先 SessionWebhook（快，无限制），回退 OpenAPI
        - 图片/文件/语音等媒体 → 只能用 OpenAPI（Webhook 不支持媒体类型）
          - 群聊 → robot/groupMessages/send
          - 单聊 → robot/oToMessages/batchSend
        """
        await self._refresh_token()

        has_media = (
            message.content.images
            or message.content.files
            or message.content.voices
        )
        is_group = message.metadata.get("is_group", self._is_group_chat(message.chat_id))

        # 媒体消息：必须走 OpenAPI（Webhook 不支持）
        if has_media:
            if is_group:
                return await self._send_group_message(message)
            else:
                return await self._send_via_api(message)

        # 纯文本消息：优先走 Webhook（更快）
        session_webhook = message.metadata.get("session_webhook", "")
        if not session_webhook:
            session_webhook = self._session_webhooks.get(message.chat_id, "")
        if session_webhook:
            return await self._send_via_webhook(message, session_webhook)

        # 回退到 OpenAPI
        if is_group:
            return await self._send_group_message(message)
        else:
            return await self._send_via_api(message)

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

        通过 OpenAPI 发送图片（Webhook 不支持图片类型）:
        - 群聊 → robot/groupMessages/send + sampleImageMsg
        - 单聊 → robot/oToMessages/batchSend + sampleImageMsg
        """
        await self._refresh_token()
        path = Path(image_path)

        # 上传图片获取 media_id 和临时 URL
        try:
            uploaded = await self.upload_media(path, "image/png")
        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            # 降级为发送文件名文本
            text = f"📎 图片: {path.name}"
            if caption:
                text = f"{caption}\n{text}"
            msg = OutgoingMessage.text(chat_id, text)
            return await self.send_message(msg)

        photo_url = uploaded.url or uploaded.file_id
        if not photo_url:
            text = f"[图片上传失败: {path.name}]"
            msg = OutgoingMessage.text(chat_id, text)
            return await self.send_message(msg)

        # 通过 OpenAPI 发送图片
        is_group = self._is_group_chat(chat_id)
        msg_param = json.dumps({"photoURL": photo_url})

        if is_group:
            url = f"{self.API_NEW}/robot/groupMessages/send"
            headers = {"x-acs-dingtalk-access-token": self._access_token}
            data = {
                "robotCode": self.config.app_key,
                "openConversationId": chat_id,
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }
        else:
            user_id = self._conversation_users.get(chat_id, chat_id)
            url = f"{self.API_NEW}/robot/oToMessages/batchSend"
            headers = {"x-acs-dingtalk-access-token": self._access_token}
            data = {
                "robotCode": self.config.app_key,
                "userIds": [user_id],
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }

        logger.info(f"Sending image via API: photoURL={photo_url[:30]}...")
        response = await self._http_client.post(url, headers=headers, json=data)
        result = response.json()

        if "processQueryKey" not in result:
            error = result.get("message", "Unknown error")
            logger.error(f"Failed to send image: {error}")
            # 降级为文件发送
            return await self.send_file(chat_id, image_path, caption)

        return result["processQueryKey"]

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件

        通过 OpenAPI 发送文件 (sampleFile):
        - 先上传到钉钉获取 media_id
        - 再通过群聊/单聊 API 发送 sampleFile 类型
        - 如果上传失败，降级为发送文件名文本
        """
        await self._refresh_token()
        path = Path(file_path)

        try:
            uploaded = await self.upload_media(path, "application/octet-stream")
            if uploaded.file_id:
                ext = path.suffix.lstrip(".") or "file"
                msg_param = json.dumps({
                    "mediaId": uploaded.file_id,
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

                logger.info(f"Sending file via API: {path.name}, mediaId={uploaded.file_id[:20]}...")
                response = await self._http_client.post(url, headers=headers, json=data)
                result = response.json()

                if "processQueryKey" in result:
                    return result["processQueryKey"]
                else:
                    error = result.get("message", "Unknown error")
                    logger.warning(f"sampleFile send failed: {error}, falling back to text")
        except Exception as e:
            logger.warning(f"DingTalk upload_media failed for file: {e}")

        # 降级: 发送文件名文本
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

        通过 OpenAPI 发送语音 (sampleAudio):
        - 先上传到钉钉获取 media_id
        - 再通过群聊/单聊 API 发送 sampleAudio 类型
        - 如果失败，降级为文件发送
        """
        await self._refresh_token()
        path = Path(voice_path)

        try:
            uploaded = await self.upload_media(path, "audio/ogg")
            if uploaded.file_id:
                msg_param = json.dumps({
                    "mediaId": uploaded.file_id,
                    "duration": "3000",  # 默认 3 秒
                })

                is_group = self._is_group_chat(chat_id)
                headers = {"x-acs-dingtalk-access-token": self._access_token}

                if is_group:
                    url = f"{self.API_NEW}/robot/groupMessages/send"
                    data = {
                        "robotCode": self.config.app_key,
                        "openConversationId": chat_id,
                        "msgKey": "sampleAudio",
                        "msgParam": msg_param,
                    }
                else:
                    user_id = self._conversation_users.get(chat_id, chat_id)
                    url = f"{self.API_NEW}/robot/oToMessages/batchSend"
                    data = {
                        "robotCode": self.config.app_key,
                        "userIds": [user_id],
                        "msgKey": "sampleAudio",
                        "msgParam": msg_param,
                    }

                logger.info(f"Sending voice via API: {path.name}")
                response = await self._http_client.post(url, headers=headers, json=data)
                result = response.json()

                if "processQueryKey" in result:
                    return result["processQueryKey"]
                else:
                    error = result.get("message", "Unknown error")
                    logger.warning(f"sampleAudio send failed: {error}, falling back to file")
        except Exception as e:
            logger.warning(f"DingTalk voice upload failed: {e}")

        # 降级: 以文件形式发送
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

        await self._refresh_token()

        # 使用钉钉文件下载 API（POST 方法）
        url = f"{self.API_NEW}/robot/messageFiles/download"
        headers = {"x-acs-dingtalk-access-token": self._access_token}
        body = {"downloadCode": media.file_id, "robotCode": self.config.app_key}

        response = await self._http_client.post(url, headers=headers, json=body)
        result = response.json()

        download_url = result.get("downloadUrl")
        if not download_url:
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

        使用钉钉 media/upload API 上传文件，获取 media_id。
        """
        await self._refresh_token()

        url = f"{self.API_BASE}/media/upload"
        params = {"access_token": self._access_token}

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
        """刷新 access token"""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        _import_httpx()

        url = f"{self.API_BASE}/gettoken"
        params = {
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }

        response = await self._http_client.get(url, params=params)
        data = response.json()

        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"Failed to get access token: {data.get('errmsg')}")

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data["expires_in"] - 60

        return self._access_token
