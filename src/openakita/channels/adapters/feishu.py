"""
飞书适配器

基于 lark-oapi 库实现:
- 事件订阅（支持长连接 WebSocket 和 Webhook 两种方式）
- 卡片消息
- 文本/图片/文件收发

参考文档:
- 机器人概述: https://open.feishu.cn/document/client-docs/bot-v3/bot-overview
- Python SDK: https://github.com/larksuite/oapi-sdk-python
- 事件订阅: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events
"""

import asyncio
import contextlib
import json
import logging
import threading
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
lark_oapi = None


def _import_lark():
    """延迟导入 lark-oapi 库"""
    global lark_oapi
    if lark_oapi is None:
        try:
            import lark_oapi as lark

            lark_oapi = lark
        except ImportError as exc:
            logger.error("lark_oapi import failed: %s", exc, exc_info=True)
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("lark_oapi")) from exc


@dataclass
class FeishuConfig:
    """飞书配置"""

    app_id: str
    app_secret: str
    verification_token: str | None = None  # 用于 Webhook 验证
    encrypt_key: str | None = None  # 用于消息加解密
    log_level: str = "INFO"  # 日志级别: DEBUG, INFO, WARN, ERROR


class FeishuAdapter(ChannelAdapter):
    """
    飞书适配器

    支持:
    - 事件订阅（长连接 WebSocket 或 Webhook）
    - 文本/富文本消息
    - 图片/文件
    - 卡片消息

    使用说明:
    1. 长连接模式（推荐）: start() 会自动启动 WebSocket 连接
    2. Webhook 模式: 使用 handle_event() 处理 HTTP 回调
    """

    channel_name = "feishu"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        media_dir: Path | None = None,
        log_level: str = "INFO",
    ):
        """
        Args:
            app_id: 飞书应用 App ID（在开发者后台获取）
            app_secret: 飞书应用 App Secret（在开发者后台获取）
            verification_token: 事件订阅验证 Token（Webhook 模式需要）
            encrypt_key: 事件加密密钥（如果配置了加密则需要）
            media_dir: 媒体文件存储目录
            log_level: 日志级别 (DEBUG, INFO, WARN, ERROR)
        """
        super().__init__()

        self.config = FeishuConfig(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            log_level=log_level,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/feishu")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._ws_client: Any | None = None
        self._event_dispatcher: Any | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """
        启动飞书客户端并自动建立 WebSocket 长连接

        会自动启动 WebSocket 长连接（非阻塞模式），以便接收消息。
        SDK 会自动管理 access_token，无需手动刷新。
        """
        _import_lark()

        # 创建客户端
        log_level = getattr(lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO)

        self._client = (
            lark_oapi.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(log_level)
            .build()
        )

        self._running = True
        # 记录主事件循环，用于从 WebSocket 线程投递协程
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
        logger.info("Feishu adapter: client initialized")

        # 自动启动 WebSocket 长连接（非阻塞模式）
        try:
            self.start_websocket(blocking=False)
            logger.info("Feishu adapter: WebSocket started in background")
        except Exception as e:
            logger.warning(f"Feishu adapter: WebSocket startup failed: {e}")
            logger.warning("Feishu adapter: falling back to webhook-only mode")

    def start_websocket(self, blocking: bool = True) -> None:
        """
        启动 WebSocket 长连接接收事件（推荐方式）

        注意事项:
        - 仅支持企业自建应用
        - 每个应用最多建立 50 个连接
        - 消息推送为集群模式，同一应用多个客户端只有随机一个会收到消息

        Args:
            blocking: 是否阻塞主线程，默认为 True
        """
        _import_lark()

        if not self._event_dispatcher:
            self._setup_event_dispatcher()

        logger.info("Starting Feishu WebSocket connection...")

        # 关键点：
        # lark_oapi.ws.client 在模块级保存了一个全局 loop 变量（导入时绑定）。
        # 因此必须在 WebSocket 线程里，把该模块的 loop 指向该线程的新事件循环，
        # 否则会错误地复用主线程/主 loop，导致 Python 3.13 报 task 进入/离开错误。

        def _run_ws_in_thread() -> None:
            import importlib

            import lark_oapi.ws.client as ws_client_mod

            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            self._ws_loop = new_loop

            # 覆盖 SDK ws 模块的全局 loop
            ws_client_mod.loop = new_loop
            importlib.reload(ws_client_mod)
            ws_client_mod.loop = new_loop

            try:
                WsClient = ws_client_mod.Client
                ws_client = WsClient(
                    self.config.app_id,
                    self.config.app_secret,
                    event_handler=self._event_dispatcher,
                    log_level=getattr(
                        lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO
                    ),
                )
                self._ws_client = ws_client
                ws_client.start()  # 阻塞运行于该线程
            except Exception as e:
                if self._running:
                    logger.error(f"Feishu WebSocket error: {e}", exc_info=True)
            finally:
                self._ws_loop = None
                with contextlib.suppress(Exception):
                    new_loop.close()

        if blocking:
            _run_ws_in_thread()
        else:
            self._ws_thread = threading.Thread(
                target=_run_ws_in_thread,
                daemon=True,
                name="FeishuWebSocket",
            )
            self._ws_thread.start()
            logger.info("Feishu WebSocket client started in background thread")

    def _setup_event_dispatcher(self) -> None:
        """设置事件分发器"""
        _import_lark()

        # 创建事件分发器
        # verification_token 和 encrypt_key 在长连接模式下必须为空字符串
        builder = (
            lark_oapi.EventDispatcherHandler.builder(
                verification_token="",  # 长连接模式不需要验证
                encrypt_key="",  # 长连接模式不需要加密
            )
            .register_p2_im_message_receive_v1(self._on_message_receive)
        )
        # 注册消息已读事件，避免 SDK 报 "processor not found" ERROR 日志
        try:
            builder = builder.register_p2_im_message_read_v1(self._on_message_read)
        except AttributeError:
            pass
        # 注册机器人进入会话事件
        try:
            builder = builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(
                self._on_bot_chat_entered
            )
        except AttributeError:
            pass
        self._event_dispatcher = builder.build()

    def _on_message_receive(self, data: Any) -> None:
        """
        处理接收到的消息事件 (im.message.receive_v1)

        注意：此方法在 WebSocket 线程中同步调用
        """
        try:
            event = data.event
            message = event.message
            sender = event.sender

            logger.info(f"Feishu: received message from {sender.sender_id.open_id}")

            # 构建消息字典
            msg_dict = {
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "message_type": message.message_type,
                "content": message.content,
                "root_id": getattr(message, "root_id", None),
            }

            sender_dict = {
                "sender_id": {
                    "user_id": getattr(sender.sender_id, "user_id", ""),
                    "open_id": getattr(sender.sender_id, "open_id", ""),
                },
            }

            # 从 WebSocket 线程把协程安全投递到主事件循环。
            # 必须使用 run_coroutine_threadsafe：当前线程已有运行中的事件循环（SDK 的 ws loop），
            # 不能使用 asyncio.run()，否则会触发 "asyncio.run() cannot be called from a running event loop" 导致消息丢失。
            if self._main_loop is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._handle_message_async(msg_dict, sender_dict),
                    self._main_loop,
                )
                # 添加回调以捕获跨线程投递中的异常，避免静默丢失消息
                def _on_dispatch_done(f: "asyncio.futures.Future") -> None:
                    try:
                        f.result()
                    except Exception as e:
                        logger.error(
                            f"Failed to dispatch Feishu message to main loop: {e}",
                            exc_info=True,
                        )
                fut.add_done_callback(_on_dispatch_done)
            else:
                logger.error(
                    "Main event loop not set (Feishu adapter not started from async context?), "
                    "dropping message to avoid asyncio.run() in WebSocket thread"
                )

        except Exception as e:
            logger.error(f"Error handling message event: {e}", exc_info=True)

    def _on_message_read(self, data: Any) -> None:
        """消息已读事件 (im.message.message_read_v1)，仅需静默消费以避免 SDK 报错"""
        pass

    def _on_bot_chat_entered(self, data: Any) -> None:
        """机器人进入会话事件，仅需静默消费以避免 SDK 报错"""
        pass

    async def _handle_message_async(self, msg_dict: dict, sender_dict: dict) -> None:
        """异步处理消息"""
        try:
            unified = await self._convert_message(msg_dict, sender_dict)
            self._log_message(unified)
            await self._emit_message(unified)
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)

    async def stop(self) -> None:
        """停止飞书客户端，确保旧 WebSocket 连接被完全关闭。

        不关闭旧连接会导致飞书平台在新旧连接间随机分发消息，
        发到旧连接上的消息因 _main_loop 已失效而被静默丢弃。
        """
        self._running = False

        # 1) 停止 WS 线程的事件循环 → SDK 的 ws_client.start() 会退出阻塞
        ws_loop = self._ws_loop
        if ws_loop is not None:
            try:
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except Exception:
                pass

        # 2) 等待 WS 线程退出（给 5 秒超时）
        ws_thread = self._ws_thread
        if ws_thread is not None and ws_thread.is_alive():
            ws_thread.join(timeout=5)
            if ws_thread.is_alive():
                logger.warning("Feishu WebSocket thread did not exit within 5s timeout")

        self._ws_client = None
        self._ws_thread = None
        self._ws_loop = None
        self._client = None
        logger.info("Feishu adapter stopped")

    def handle_event(self, body: dict, headers: dict) -> dict:
        """
        处理飞书事件回调（Webhook 模式）

        用于 HTTP 服务器模式，接收飞书推送的事件

        Args:
            body: 请求体
            headers: 请求头

        Returns:
            响应体
        """
        # URL 验证
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        # 验证签名
        if self.config.verification_token:
            token = body.get("token")
            if token != self.config.verification_token:
                logger.warning("Invalid verification token")
                return {"error": "invalid token"}

        # 处理事件
        event_type = body.get("header", {}).get("event_type")
        event = body.get("event", {})

        if event_type == "im.message.receive_v1":
            asyncio.create_task(self._handle_message_event(event))

        return {"success": True}

    async def _handle_message_event(self, event: dict) -> None:
        """处理消息事件（Webhook 模式）"""
        try:
            message = event.get("message", {})
            sender = event.get("sender", {})

            unified = await self._convert_message(message, sender)
            self._log_message(unified)
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message event: {e}")

    async def _convert_message(self, message: dict, sender: dict) -> UnifiedMessage:
        """将飞书消息转换为统一格式"""
        content = MessageContent()

        msg_type = message.get("message_type")
        msg_content = json.loads(message.get("content", "{}"))

        if msg_type == "text":
            content.text = msg_content.get("text", "")

        elif msg_type == "image":
            image_key = msg_content.get("image_key")
            if image_key:
                media = MediaFile.create(
                    filename=f"{image_key}.png",
                    mime_type="image/png",
                    file_id=image_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.images.append(media)

        elif msg_type == "audio":
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.opus",
                    mime_type="audio/opus",
                    file_id=file_key,
                )
                media.duration = msg_content.get("duration", 0) / 1000
                media.extra["message_id"] = message.get("message_id", "")
                content.voices.append(media)

        elif msg_type == "media":
            # 视频消息
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.mp4",
                    mime_type="video/mp4",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.videos.append(media)

        elif msg_type == "file":
            file_key = msg_content.get("file_key")
            file_name = msg_content.get("file_name", "file")
            if file_key:
                media = MediaFile.create(
                    filename=file_name,
                    mime_type="application/octet-stream",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.files.append(media)

        elif msg_type == "sticker":
            # 表情包
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.png",
                    mime_type="image/png",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.images.append(media)

        elif msg_type == "post":
            # 富文本
            content.text = self._parse_post_content(msg_content)

        else:
            # 未知类型
            content.text = f"[不支持的消息类型: {msg_type}]"

        # 确定聊天类型
        chat_type = message.get("chat_type", "p2p")
        if chat_type == "p2p":
            chat_type = "private"
        elif chat_type == "group":
            chat_type = "group"

        sender_id = sender.get("sender_id", {})
        user_id = sender_id.get("user_id") or sender_id.get("open_id", "")

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.get("message_id", ""),
            user_id=f"fs_{user_id}",
            channel_user_id=user_id,
            chat_id=message.get("chat_id", ""),
            content=content,
            chat_type=chat_type,
            reply_to=message.get("root_id"),
            raw={"message": message, "sender": sender},
        )

    def _parse_post_content(self, post: dict) -> str:
        """解析富文本内容"""
        result = []

        title = post.get("title", "")
        if title:
            result.append(title)

        for content in post.get("content", []):
            for item in content:
                if item.get("tag") == "text":
                    result.append(item.get("text", ""))
                elif item.get("tag") == "a":
                    result.append(f"[{item.get('text', '')}]({item.get('href', '')})")
                elif item.get("tag") == "at":
                    result.append(f"@{item.get('user_name', '')}")

        return "\n".join(result)

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # 构建消息内容
        if message.content.text and not message.content.has_media:
            text = message.content.text
            # 检测是否包含 markdown 格式
            if self._contains_markdown(text):
                # 使用卡片消息支持 markdown 渲染
                msg_type = "interactive"
                card = {
                    "config": {"wide_screen_mode": True},
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": text,
                        }
                    ],
                }
                content = json.dumps(card)
            else:
                msg_type = "text"
                content = json.dumps({"text": text})
        elif message.content.images:
            image = message.content.images[0]
            if image.local_path:
                image_key = await self._upload_image(image.local_path)
                msg_type = "image"
                content = json.dumps({"image_key": image_key})
            else:
                msg_type = "text"
                content = json.dumps({"text": message.content.text or "[图片]"})
        else:
            msg_type = "text"
            content = json.dumps({"text": message.content.text or ""})

        # 发送消息（在线程池中执行同步调用）
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(message.chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send message: {response.msg}")

        return response.data.message_id

    def _contains_markdown(self, text: str) -> bool:
        """检测文本是否包含 markdown 格式"""
        import re

        # 常见 markdown 标记模式
        patterns = [
            r"\*\*[^*]+\*\*",  # **bold**
            r"__[^_]+__",  # __bold__
            r"(?<!\*)\*[^*]+\*(?!\*)",  # *italic* (非 **)
            r"(?<!_)_[^_]+_(?!_)",  # _italic_ (非 __)
            r"^#{1,6}\s",  # # heading
            r"\[.+?\]\(.+?\)",  # [link](url)
            r"`[^`]+`",  # `code`
            r"```",  # code block
            r"^[-*+]\s",  # - list item
            r"^\d+\.\s",  # 1. ordered list
            r"^>\s",  # > quote
        ]
        return any(re.search(pattern, text, re.MULTILINE) for pattern in patterns)

    async def _upload_image(self, path: str) -> str:
        """上传图片"""
        with open(path, "rb") as f:
            request = (
                lark_oapi.api.im.v1.CreateImageRequest.builder()
                .request_body(
                    lark_oapi.api.im.v1.CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                )
                .build()
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.create(request)
            )

            if not response.success():
                raise RuntimeError(f"Failed to upload image: {response.msg}")

            return response.data.image_key

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.file_id:
            raise ValueError("Media has no file_id")

        # 根据类型选择下载接口
        message_id = media.extra.get("message_id", "")
        if media.is_image and not message_id:
            # 仅用于下载机器人自己上传的图片（无 message_id）
            request = lark_oapi.api.im.v1.GetImageRequest.builder().image_key(media.file_id).build()

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.get(request)
            )
        else:
            # 用户消息中的图片/音频/视频/文件，统一走 MessageResource 接口
            resource_type = "image" if media.is_image else "file"
            request = (
                lark_oapi.api.im.v1.GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(media.file_id)
                .type(resource_type)
                .build()
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.im.v1.message_resource.get(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to download media: {response.msg}")

        # 保存文件
        local_path = self.media_dir / media.filename
        with open(local_path, "wb") as f:
            f.write(response.file.read())

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """上传媒体文件"""
        if mime_type.startswith("image/"):
            image_key = await self._upload_image(str(path))
            media = MediaFile.create(
                filename=path.name,
                mime_type=mime_type,
                file_id=image_key,
            )
            media.status = MediaStatus.READY
            return media

        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )

    async def send_card(self, chat_id: str, card: dict) -> str:
        """
        发送卡片消息

        Args:
            chat_id: 聊天 ID
            card: 卡片内容（飞书卡片 JSON）

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card))
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send card: {response.msg}")

        return response.data.message_id

    async def reply_message(self, message_id: str, text: str, msg_type: str = "text") -> str:
        """
        回复消息

        Args:
            message_id: 要回复的消息 ID
            text: 回复内容
            msg_type: 消息类型

        Returns:
            新消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        content = json.dumps({"text": text}) if msg_type == "text" else text

        request = (
            lark_oapi.api.im.v1.ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.reply(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to reply message: {response.msg}")

        return response.data.message_id

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> str:
        """
        发送图片

        Args:
            chat_id: 聊天 ID
            photo_path: 图片文件路径
            caption: 图片说明文字

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # 上传图片获取 image_key
        image_key = await self._upload_image(photo_path)

        # 发送图片消息
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}))
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send photo: {response.msg}")

        message_id = response.data.message_id

        # 如果有说明文字，追加发送文本消息
        if caption:
            await self._send_text(chat_id, caption)

        logger.info(f"Sent photo to {chat_id}: {photo_path}")
        return message_id

    async def send_file(self, chat_id: str, file_path: str, caption: str = "") -> str:
        """
        发送文件

        Args:
            chat_id: 聊天 ID
            file_path: 文件路径
            caption: 文件说明文字

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # 上传文件获取 file_key
        file_key = await self._upload_file(file_path)

        # 发送文件消息
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send file: {response.msg}")

        message_id = response.data.message_id

        # 如果有说明文字，追加发送文本消息
        if caption:
            await self._send_text(chat_id, caption)

        logger.info(f"Sent file to {chat_id}: {file_path}")
        return message_id

    async def send_voice(self, chat_id: str, voice_path: str, caption: str | None = None) -> str:
        """
        发送语音消息

        上传音频文件获取 file_key，然后发送 audio 类型消息。
        飞书 Create Message API 支持 msg_type="audio"。

        Args:
            chat_id: 聊天 ID
            voice_path: 语音文件路径
            caption: 语音说明文字

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # 上传音频文件获取 file_key
        file_key = await self._upload_file(voice_path)

        # 发送 audio 消息
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("audio")
                .content(json.dumps({"file_key": file_key}))
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send voice: {response.msg}")

        message_id = response.data.message_id

        # 如果有说明文字，追加发送文本消息
        if caption:
            await self._send_text(chat_id, caption)

        logger.info(f"Sent voice to {chat_id}: {voice_path}")
        return message_id

    async def _send_text(self, chat_id: str, text: str) -> str:
        """发送纯文本消息"""
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send text: {response.msg}")

        return response.data.message_id

    async def _upload_file(self, path: str) -> str:
        """上传文件到飞书"""
        file_name = Path(path).name

        with open(path, "rb") as f:
            request = (
                lark_oapi.api.im.v1.CreateFileRequest.builder()
                .request_body(
                    lark_oapi.api.im.v1.CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(file_name)
                    .file(f)
                    .build()
                )
                .build()
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.im.v1.file.create(request)
            )

            if not response.success():
                raise RuntimeError(f"Failed to upload file: {response.msg}")

            return response.data.file_key

    def build_simple_card(
        self,
        title: str,
        content: str,
        buttons: list[dict] | None = None,
    ) -> dict:
        """
        构建简单卡片

        Args:
            title: 标题
            content: 内容
            buttons: 按钮列表 [{"text": "按钮文字", "value": "回调值"}]

        Returns:
            卡片 JSON
        """
        elements = [
            {
                "tag": "markdown",
                "content": content,
            }
        ]

        if buttons:
            actions = []
            for btn in buttons:
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": btn["text"]},
                        "type": "primary",
                        "value": {"action": btn.get("value", btn["text"])},
                    }
                )

            elements.append(
                {
                    "tag": "action",
                    "actions": actions,
                }
            )

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        }
