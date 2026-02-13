"""
QQ 官方机器人适配器

基于 QQ 官方机器人 API v2 实现 (使用 botpy SDK):
- AppID + AppSecret 鉴权 (OAuth2 Access Token)
- 支持 WebSocket 和 Webhook 两种事件接收模式
- 支持群聊、单聊 (C2C)、频道消息
- 文本/图片/富媒体消息收发

模式说明:
- websocket (默认): 使用 botpy SDK 建立 WebSocket 长连接，无需公网 IP
- webhook: QQ 服务器主动推送事件到 HTTP 回调端点，需要公网 IP/域名

官方文档: https://bot.q.qq.com/wiki/develop/api-v2/
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
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
botpy = None
botpy_message = None


def _import_botpy():
    global botpy, botpy_message
    if botpy is None:
        try:
            import botpy as _botpy
            from botpy import message as _msg

            botpy = _botpy
            botpy_message = _msg
        except ImportError:
            raise ImportError(
                "qq-botpy not installed. Run: pip install qq-botpy"
            )


class QQBotAdapter(ChannelAdapter):
    """
    QQ 官方机器人适配器

    通过 QQ 开放平台官方 API 接入，使用 botpy SDK。

    支持:
    - 群聊 @机器人消息 (GROUP_AT_MESSAGE_CREATE)
    - 单聊消息 (C2C_MESSAGE_CREATE)
    - 频道 @消息 (AT_MESSAGE_CREATE)
    - 文本消息收发
    """

    channel_name = "qqbot"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        sandbox: bool = False,
        mode: str = "websocket",
        webhook_port: int = 9890,
        webhook_path: str = "/qqbot/callback",
        media_dir: Path | None = None,
    ):
        """
        Args:
            app_id: QQ 机器人 AppID (在 q.qq.com 开发设置中获取)
            app_secret: QQ 机器人 AppSecret
            sandbox: 是否使用沙箱环境
            mode: 接入模式 "websocket" 或 "webhook"
            webhook_port: Webhook 回调服务端口（仅 webhook 模式）
            webhook_path: Webhook 回调路径（仅 webhook 模式）
            media_dir: 媒体文件存储目录
        """
        super().__init__()

        self.app_id = app_id
        self.app_secret = app_secret
        self.sandbox = sandbox
        self.mode = mode.lower().strip()
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/qqbot")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._task: asyncio.Task | None = None
        self._retry_delay: int = 5  # 重连延迟（秒），on_ready 时重置
        self._webhook_runner: Any | None = None  # aiohttp web runner
        self._access_token: str | None = None  # OAuth2 access token (webhook 模式)
        self._token_expires: float = 0

    async def start(self) -> None:
        """启动 QQ 官方机器人"""
        self._running = True

        if self.mode == "webhook":
            self._task = asyncio.create_task(self._run_webhook_server())
            logger.info(
                f"QQ Official Bot adapter starting in WEBHOOK mode "
                f"(AppID: {self.app_id}, port: {self.webhook_port}, "
                f"path: {self.webhook_path})"
            )
        else:
            _import_botpy()
            self._task = asyncio.create_task(self._run_client())
            logger.info(
                f"QQ Official Bot adapter starting in WEBSOCKET mode "
                f"(AppID: {self.app_id}, sandbox: {self.sandbox})"
            )

    async def _run_client(self) -> None:
        """在后台运行 botpy 客户端 (带自动重连) — WebSocket 模式"""
        max_delay = 120

        while self._running:
            try:
                # 每次循环都重新创建 client，避免旧 client 状态残留
                _import_botpy()
                intents = botpy.Intents(
                    public_guild_messages=True,
                    public_messages=True,
                )
                self._client = _create_botpy_client(
                    adapter=self,
                    is_sandbox=self.sandbox,
                    intents=intents,
                )

                # botpy Client.start() 是一个阻塞协程，会保持 WebSocket 连接
                async with self._client:
                    await self._client.start(
                        appid=self.app_id,
                        secret=self.app_secret,
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.error(f"QQ Official Bot error: {e}")
                logger.info(f"QQ Official Bot: reconnecting in {self._retry_delay}s...")
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, max_delay)

    # ==================== Webhook 模式 ====================

    async def _get_access_token(self) -> str:
        """获取 QQ 官方 API 的 OAuth2 access_token（用于 Webhook 模式下主动发消息）"""
        now = time.time()
        if self._access_token and now < self._token_expires - 60:
            return self._access_token

        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        async with hx.AsyncClient() as client:
            resp = await client.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={
                    "appId": self.app_id,
                    "clientSecret": self.app_secret,
                },
            )
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expires = now + int(data.get("expires_in", 7200))
            logger.info("QQ Bot access_token refreshed")
            return self._access_token

    def _verify_signature(self, body: bytes, signature: str, timestamp: str) -> bool:
        """
        验证 QQ Webhook 回调签名 (ed25519)。

        QQ 官方 Webhook 使用 ed25519 签名验证：
        - 签名内容: timestamp + body
        - 密钥: 由 app_secret + bot_secret seed 派生的 ed25519 密钥
        - 签名值: 在 X-Signature-Ed25519 header 中

        简化实现：使用 HMAC-SHA256 作为备选验签方式（部分旧版本 API 支持）。
        如需完整 ed25519 验签，需安装 PyNaCl。
        """
        try:
            # 尝试 ed25519 验签（需要 PyNaCl）
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError

            # QQ 使用 bot_secret 的前 32 字节作为 ed25519 seed
            seed = self.app_secret.encode("utf-8")
            # 签名验证的消息体是 timestamp + body
            msg = timestamp.encode("utf-8") + body
            sig_bytes = bytes.fromhex(signature)

            # QQ 的 ed25519 公钥需要从 seed 派生
            # 这里我们从 seed 生成签名密钥对并验证
            # 注意：QQ 文档中 seed 的具体处理方式可能有差异
            verify_key = VerifyKey(seed[:32].ljust(32, b'\x00'))
            try:
                verify_key.verify(msg, sig_bytes)
                return True
            except BadSignatureError:
                pass
        except ImportError:
            pass

        # 备选：HMAC-SHA256 验签
        msg = timestamp.encode("utf-8") + body
        expected = hmac.new(
            self.app_secret.encode("utf-8"), msg, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _run_webhook_server(self) -> None:
        """启动 Webhook HTTP 回调服务器"""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError(
                "aiohttp not installed. Run: pip install aiohttp"
            )

        async def handle_callback(request: web.Request) -> web.Response:
            """处理 QQ Webhook 回调"""
            body = await request.read()

            # QQ Webhook 验签
            signature = request.headers.get("X-Signature-Ed25519", "")
            timestamp = request.headers.get("X-Signature-Timestamp", "")

            if signature and not self._verify_signature(body, signature, timestamp):
                logger.warning("QQ Webhook signature verification failed")
                return web.Response(status=401, text="Signature verification failed")

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return web.Response(status=400, text="Invalid JSON")

            op = payload.get("op")

            # op=13: 验证回调 URL (Validation)
            if op == 13:
                d = payload.get("d", {})
                plain_token = d.get("plain_token", "")
                event_ts = d.get("event_ts", "")
                # 回复验证：用 app_secret 对 event_ts + plain_token 签名
                msg = event_ts.encode("utf-8") + plain_token.encode("utf-8")
                sig = hmac.new(
                    self.app_secret.encode("utf-8"), msg, hashlib.sha256
                ).hexdigest()
                return web.json_response({
                    "plain_token": plain_token,
                    "signature": sig,
                })

            # op=0: 事件分发 (Dispatch)
            if op == 0:
                event_type = payload.get("t", "")
                event_data = payload.get("d", {})
                asyncio.create_task(
                    self._handle_webhook_event(event_type, event_data)
                )
                return web.json_response({"status": "ok"})

            # 其他 op 码（如心跳等）
            logger.debug(f"QQ Webhook received op={op}")
            return web.json_response({"status": "ok"})

        app = web.Application()
        app.router.add_post(self.webhook_path, handle_callback)

        runner = web.AppRunner(app)
        await runner.setup()
        self._webhook_runner = runner

        site = web.TCPSite(runner, "0.0.0.0", self.webhook_port)
        await site.start()

        logger.info(
            f"QQ Webhook server listening on 0.0.0.0:{self.webhook_port}{self.webhook_path}"
        )

        # 保持运行直到被取消
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    async def _handle_webhook_event(self, event_type: str, data: dict) -> None:
        """处理 Webhook 推送的事件"""
        try:
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                unified = self._convert_webhook_group_message(data)
            elif event_type == "C2C_MESSAGE_CREATE":
                unified = self._convert_webhook_c2c_message(data)
            elif event_type == "AT_MESSAGE_CREATE":
                unified = self._convert_webhook_channel_message(data)
            else:
                logger.debug(f"QQ Webhook: unhandled event type {event_type}")
                return

            self._log_message(unified)
            await self._emit_message(unified)
        except Exception as e:
            logger.error(f"Error handling QQ Webhook event {event_type}: {e}")

    def _convert_webhook_group_message(self, data: dict) -> UnifiedMessage:
        """将 Webhook 群聊消息转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        # Webhook 的附件格式
        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_openid = author.get("member_openid", "")
        group_openid = data.get("group_openid", "")

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=group_openid,
            content=content,
            chat_type="group",
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "group",
                "is_group": True,
                "group_openid": group_openid,
                "msg_id": data.get("id", ""),
            },
        )

    def _convert_webhook_c2c_message(self, data: dict) -> UnifiedMessage:
        """将 Webhook 单聊消息转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_openid = author.get("user_openid", "")

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=user_openid,
            content=content,
            chat_type="private",
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "c2c",
                "is_group": False,
                "user_openid": user_openid,
                "msg_id": data.get("id", ""),
            },
        )

    def _convert_webhook_channel_message(self, data: dict) -> UnifiedMessage:
        """将 Webhook 频道消息转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_id = author.get("id", "")
        channel_id = data.get("channel_id", "")
        guild_id = data.get("guild_id", "")

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_id}",
            channel_user_id=user_id,
            chat_id=channel_id,
            content=content,
            chat_type="group",
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "channel",
                "is_group": True,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "msg_id": data.get("id", ""),
            },
        )

    @staticmethod
    def _parse_webhook_attachments(attachments: list | None, content: MessageContent) -> None:
        """解析 Webhook 回调中的附件"""
        if not attachments:
            return
        for att in attachments:
            ct = att.get("content_type", "")
            url = att.get("url")
            filename = att.get("filename", "file")

            if ct.startswith("image/"):
                content.images.append(MediaFile.create(filename=filename, mime_type=ct, url=url))
            elif ct.startswith("audio/"):
                content.voices.append(MediaFile.create(filename=filename, mime_type=ct, url=url))
            elif ct.startswith("video/"):
                content.videos.append(MediaFile.create(filename=filename, mime_type=ct, url=url))
            else:
                content.files.append(MediaFile.create(
                    filename=filename, mime_type=ct or "application/octet-stream", url=url,
                ))

    async def stop(self) -> None:
        """停止 QQ 官方机器人"""
        self._running = False

        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        logger.info(f"QQ Official Bot adapter stopped (mode: {self.mode})")

    @staticmethod
    def _parse_attachments(attachments: list | None, content: MessageContent) -> None:
        """
        解析 botpy 消息附件，填充到 MessageContent。

        支持图片、语音、视频、文件等多种类型。
        """
        if not attachments:
            return

        for att in attachments:
            ct = att.get("content_type", "")
            url = att.get("url")
            filename = att.get("filename", "file")

            if ct.startswith("image/"):
                media = MediaFile.create(
                    filename=filename,
                    mime_type=ct,
                    url=url,
                )
                content.images.append(media)
            elif ct.startswith("audio/"):
                media = MediaFile.create(
                    filename=filename,
                    mime_type=ct,
                    url=url,
                )
                content.voices.append(media)
            elif ct.startswith("video/"):
                media = MediaFile.create(
                    filename=filename,
                    mime_type=ct,
                    url=url,
                )
                content.videos.append(media)
            else:
                # 其他类型视为文件
                media = MediaFile.create(
                    filename=filename,
                    mime_type=ct or "application/octet-stream",
                    url=url,
                )
                content.files.append(media)

    def _convert_group_message(self, message: Any) -> UnifiedMessage:
        """将 botpy GroupMessage 转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (message.content or "").strip()

        # 解析附件（图片、语音、视频、文件）
        self._parse_attachments(
            getattr(message, "attachments", None),
            content,
        )

        user_openid = getattr(message.author, "member_openid", "") or ""
        group_openid = getattr(message, "group_openid", "") or ""

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=group_openid,
            content=content,
            chat_type="group",
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "group",
                "is_group": True,
                "group_openid": group_openid,
                "msg_id": message.id,
            },
        )

    def _convert_c2c_message(self, message: Any) -> UnifiedMessage:
        """将 botpy C2CMessage 转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (message.content or "").strip()

        # 解析附件
        self._parse_attachments(
            getattr(message, "attachments", None),
            content,
        )

        user_openid = getattr(message.author, "user_openid", "") or ""

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=user_openid,
            content=content,
            chat_type="private",
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "c2c",
                "is_group": False,
                "user_openid": user_openid,
                "msg_id": message.id,
            },
        )

    def _convert_channel_message(self, message: Any) -> UnifiedMessage:
        """将 botpy Message (频道消息) 转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (message.content or "").strip()

        # 解析附件
        self._parse_attachments(
            getattr(message, "attachments", None),
            content,
        )

        author = message.author
        user_id = getattr(author, "id", "") or ""
        channel_id = getattr(message, "channel_id", "") or ""
        guild_id = getattr(message, "guild_id", "") or ""

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_id}",
            channel_user_id=user_id,
            chat_id=channel_id,
            content=content,
            chat_type="group",
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "channel",
                "is_group": True,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "msg_id": message.id,
            },
        )

    # ==================== 富媒体上传 ====================

    async def _upload_rich_media(
        self,
        api: Any,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str,
        srv_send_msg: bool = False,
    ) -> Any:
        """
        上传富媒体资源到 QQ 服务器。

        QQ 官方 API 的群/C2C 富媒体消息需要两步:
        1. 先 POST /v2/groups/{openid}/files 或 /v2/users/{openid}/files 上传
        2. 返回 file_info 用于消息发送

        Args:
            api: botpy API client
            chat_type: "group" 或 "c2c"
            target_id: group_openid 或 user openid
            file_type: 1=图片, 2=视频, 3=语音, 4=文件(暂未开放)
            url: 媒体资源 URL (必须为公网可访问的 http/https URL)
            srv_send_msg: True 则服务端直接发送（占主动消息频次）

        Returns:
            API 响应，包含 file_info / file_uuid / ttl 等字段
        """
        if chat_type == "group":
            return await api.post_group_file(
                group_openid=target_id,
                file_type=file_type,
                url=url,
                srv_send_msg=srv_send_msg,
            )
        else:  # c2c
            return await api.post_c2c_file(
                openid=target_id,
                file_type=file_type,
                url=url,
                srv_send_msg=srv_send_msg,
            )

    async def _send_rich_media(
        self,
        api: Any,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str,
        msg_id: str | None = None,
    ) -> str:
        """
        完整的富媒体发送流程（两步）：上传 + 发消息。

        Args:
            api: botpy API client
            chat_type: "group" 或 "c2c"
            target_id: 目标 openid
            file_type: 1=图片, 2=视频, 3=语音
            url: 公网可访问的媒体 URL
            msg_id: 被动回复的消息 ID（可选）

        Returns:
            发送后的消息 ID
        """
        # Step 1: 上传富媒体资源获取 file_info
        upload_result = await self._upload_rich_media(
            api, chat_type, target_id,
            file_type=file_type,
            url=url,
            srv_send_msg=False,
        )

        file_info = (
            getattr(upload_result, "file_info", None)
            or (upload_result.get("file_info") if isinstance(upload_result, dict) else None)
        )
        if not file_info:
            raise RuntimeError(
                f"Rich media upload did not return file_info: {upload_result}"
            )

        # Step 2: 发送消息 msg_type=7 (media)
        result = await self._send_to_target(
            api, chat_type, target_id,
            msg_type=7,
            media={"file_info": file_info},
            msg_id=msg_id,
        )
        return str(getattr(result, "id", ""))

    # ==================== 消息发送 ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息

        支持:
        - 文本消息 (msg_type=0)
        - 图片消息 (频道: content+image/file_image; 群/C2C: 两步富媒体上传)
        """
        chat_type = message.metadata.get("chat_type", "group")
        msg_id = message.metadata.get("msg_id")

        # Webhook 模式使用 HTTP API 发送
        if self.mode == "webhook":
            return await self._send_message_via_http(message, chat_type, msg_id)

        if not self._client or not self._client.api:
            raise RuntimeError("QQ Official Bot not started")

        api = self._client.api

        text = message.content.text or ""

        # 检查是否有图片需要发送
        has_image = bool(message.content.images)
        image_url: str | None = None
        image_path: str | None = None
        if has_image:
            img = message.content.images[0]
            if img.url:
                image_url = img.url
            elif img.local_path:
                image_path = img.local_path

        try:
            if chat_type == "channel":
                return await self._send_channel_message(
                    api, message.chat_id, text, image_url, image_path, msg_id,
                )
            else:
                return await self._send_group_or_c2c_message(
                    api, chat_type, message.chat_id,
                    text, image_url, image_path, msg_id,
                )
        except Exception as e:
            logger.error(f"Failed to send QQ Official Bot message: {e}")
            raise

    async def _send_message_via_http(
        self, message: OutgoingMessage, chat_type: str, msg_id: str | None,
    ) -> str:
        """Webhook 模式：通过 HTTP API 发送消息"""
        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        token = await self._get_access_token()
        base_url = (
            "https://sandbox.api.sgroup.qq.com"
            if self.sandbox
            else "https://api.sgroup.qq.com"
        )
        headers = {
            "Authorization": f"QQBotToken {self.app_id}.{token}",
            "Content-Type": "application/json",
        }

        text = message.content.text or ""
        target_id = message.chat_id

        async with hx.AsyncClient(base_url=base_url, headers=headers) as client:
            body: dict[str, Any] = {"msg_type": 0, "content": text}
            if msg_id:
                body["msg_id"] = msg_id

            if chat_type == "group":
                url = f"/v2/groups/{target_id}/messages"
            elif chat_type == "c2c":
                url = f"/v2/users/{target_id}/messages"
            elif chat_type == "channel":
                url = f"/channels/{target_id}/messages"
            else:
                url = f"/v2/groups/{target_id}/messages"

            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))

    async def _send_channel_message(
        self,
        api: Any,
        channel_id: str,
        text: str,
        image_url: str | None,
        image_path: str | None,
        msg_id: str | None,
    ) -> str:
        """频道消息：支持 content + image/file_image 在同一条消息中"""
        kwargs: dict[str, Any] = {
            "channel_id": channel_id,
            "msg_id": msg_id,
        }
        if text:
            kwargs["content"] = text
        if image_url:
            kwargs["image"] = image_url
        elif image_path:
            with open(image_path, "rb") as f:
                kwargs["file_image"] = f.read()

        result = await api.post_message(**kwargs)
        return str(getattr(result, "id", ""))

    async def _send_group_or_c2c_message(
        self,
        api: Any,
        chat_type: str,
        target_id: str,
        text: str,
        image_url: str | None,
        image_path: str | None,
        msg_id: str | None,
    ) -> str:
        """
        群聊 / C2C 消息发送。

        QQ 官方 API 群/C2C 不支持文本+图片同时发送，需要分两条消息:
        1. 文本消息 (msg_type=0)
        2. 图片通过富媒体 API 两步上传后发送 (msg_type=7)
        """
        result_id = ""

        # 发送文本
        if text:
            result = await self._send_to_target(
                api, chat_type, target_id,
                msg_type=0, content=text, msg_id=msg_id,
            )
            result_id = str(getattr(result, "id", ""))

        # 发送图片（需要公网 URL）
        if image_url:
            try:
                media_id = await self._send_rich_media(
                    api, chat_type, target_id,
                    file_type=1,  # 图片
                    url=image_url,
                    msg_id=msg_id,
                )
                result_id = result_id or media_id
            except Exception as img_err:
                logger.warning(f"Failed to send image via rich media API: {img_err}")
        elif image_path:
            logger.warning(
                f"QQ Official Bot: local image path not supported for group/C2C "
                f"(file_data API 暂未开放). image_path={image_path}"
            )

        return result_id

    async def _send_to_target(
        self, api: Any, chat_type: str, target_id: str, **kwargs
    ) -> Any:
        """根据 chat_type 发送消息到对应目标"""
        if chat_type == "group":
            return await api.post_group_message(
                group_openid=target_id, **kwargs,
            )
        elif chat_type == "c2c":
            return await api.post_c2c_message(
                openid=target_id, **kwargs,
            )
        else:
            # 默认群聊
            return await api.post_group_message(
                group_openid=target_id, **kwargs,
            )

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件

        注意: QQ 官方 API 的 file_type=4 (文件) 暂未开放。
        当前实现会记录警告并 fallback 到发送文本提示。
        """
        logger.warning(
            f"QQ Official Bot: send_file not supported "
            f"(file_type=4 暂未开放). file_path={file_path}"
        )

        # Fallback: 发送文本提示
        if caption and self._client and self._client.api:
            # 尝试获取当前 chat_type，默认 group
            try:
                api = self._client.api
                result = await self._send_to_target(
                    api, "group", chat_id,
                    msg_type=0, content=f"{caption}\n[文件: {Path(file_path).name}]",
                )
                return str(getattr(result, "id", ""))
            except Exception as e:
                logger.warning(f"Fallback text send also failed: {e}")

        raise NotImplementedError(
            "QQ Official Bot file sending (file_type=4) is not yet available"
        )

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送语音消息

        QQ 官方 API 语音 (file_type=3) 仅支持 silk 格式且需要公网 URL。
        本地文件暂无法直接上传 (file_data 暂未支持)。
        """
        logger.warning(
            f"QQ Official Bot: send_voice requires public URL + silk format. "
            f"Local file upload not supported. voice_path={voice_path}"
        )

        # 如果有 caption，发送文本提示
        if caption and self._client and self._client.api:
            try:
                api = self._client.api
                result = await self._send_to_target(
                    api, "group", chat_id,
                    msg_type=0, content=f"{caption}\n[语音消息]",
                )
                return str(getattr(result, "id", ""))
            except Exception as e:
                logger.warning(f"Fallback text send also failed: {e}")

        raise NotImplementedError(
            "QQ Official Bot voice sending requires public URL in silk format"
        )

    # ==================== 媒体下载/上传 ====================

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if media.url:
            try:
                import httpx as hx
            except ImportError:
                raise ImportError("httpx not installed. Run: pip install httpx")

            async with hx.AsyncClient() as client:
                response = await client.get(media.url)

                local_path = self.media_dir / media.filename
                with open(local_path, "wb") as f:
                    f.write(response.content)

                media.local_path = str(local_path)
                media.status = MediaStatus.READY
                return local_path

        raise ValueError("Media has no url")

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """上传媒体文件"""
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )


def _create_botpy_client(adapter: "QQBotAdapter", is_sandbox: bool = False, **kwargs):
    """
    创建 botpy Client 子类实例。

    使用工厂函数延迟创建，避免模块加载时 botpy 未导入的问题。
    """
    _import_botpy()

    class _InternalBotpyClient(botpy.Client):
        """
        botpy Client 子类，桥接 botpy 事件到 QQBotAdapter。

        botpy 的事件分发机制：
        - WebSocket 收到事件后，调用 on_<event_name> 方法
        - 我们覆写这些方法，将事件转换为 UnifiedMessage 并传给 adapter
        """

        def __init__(self, _adapter, _is_sandbox=False, **kw):
            super().__init__(**kw)
            self._adapter = _adapter
            self.is_sandbox = _is_sandbox

        async def on_group_at_message_create(self, message):
            """群聊 @机器人消息"""
            try:
                unified = self._adapter._convert_group_message(message)
                self._adapter._log_message(unified)
                await self._adapter._emit_message(unified)
            except Exception as e:
                logger.error(f"Error handling group message: {e}")

        async def on_c2c_message_create(self, message):
            """单聊消息"""
            try:
                unified = self._adapter._convert_c2c_message(message)
                self._adapter._log_message(unified)
                await self._adapter._emit_message(unified)
            except Exception as e:
                logger.error(f"Error handling C2C message: {e}")

        async def on_at_message_create(self, message):
            """频道 @机器人消息"""
            try:
                unified = self._adapter._convert_channel_message(message)
                self._adapter._log_message(unified)
                await self._adapter._emit_message(unified)
            except Exception as e:
                logger.error(f"Error handling channel message: {e}")

        async def on_ready(self):
            """机器人就绪，重置重连延迟"""
            logger.info(f"QQ Official Bot ready (user: {self.robot.name})")
            # 成功连接后重置重连延迟，避免之前的失败导致延迟膨胀
            self._adapter._retry_delay = 5

    return _InternalBotpyClient(
        _adapter=adapter,
        _is_sandbox=is_sandbox,
        **kwargs,
    )
