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
import collections
import contextlib
import hashlib
import hmac
import json
import logging
import shutil
import time
import uuid
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
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("botpy"))


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

    capabilities = {
        "streaming": False,
        "send_image": True,
        "send_file": False,
        "send_voice": False,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": False,
        "get_user_info": False,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": True,
    }

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        sandbox: bool = False,
        mode: str = "websocket",
        webhook_port: int = 9890,
        webhook_path: str = "/qqbot/callback",
        media_dir: Path | None = None,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
        public_api_url: str = "",
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
            channel_name: 通道名称（多Bot时用于区分实例）
            bot_id: Bot 实例唯一标识
            agent_profile_id: 绑定的 agent profile ID
            public_api_url: OpenAkita API 的公网 URL（如 https://example.com），
                用于将本地图片转为 QQ 可访问的公网 URL。不配置则群/C2C 无法发送本地图片。
        """
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)

        self.app_id = app_id
        self.app_secret = app_secret
        self.sandbox = sandbox
        self.mode = mode.lower().strip()
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path
        self.public_api_url = public_api_url.rstrip("/") if public_api_url else ""
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/qqbot")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._task: asyncio.Task | None = None
        self._retry_delay: int = 5  # 重连延迟（秒），on_ready 时重置
        self._webhook_runner: Any | None = None  # aiohttp web runner
        self._access_token: str | None = None  # OAuth2 access token (webhook 模式)
        self._token_expires: float = 0

        # ---- chat_id 路由表 ----
        # QQ 的 send_text() 等便捷方法不带 metadata，需要根据 chat_id 反查 chat_type
        # {chat_id: "group" | "c2c" | "channel"}
        self._chat_type_map: dict[str, str] = {}
        # {chat_id: 最近一条收到的 msg_id}（被动回复需要）
        self._last_msg_id: dict[str, str] = {}
        # {chat_id: 最近一条收到的 event_id}（msg_id 过期时回退使用）
        self._last_event_id: dict[str, str] = {}
        # {msg_id: msg_seq} — QQ API 要求同一 msg_id 的多条回复递增 msg_seq 避免去重
        # 以 msg_id（被回复的消息 ID）为键，而非 chat_id，避免同会话不同消息混用 seq
        self._msg_seq: dict[str, int] = {}
        self._msg_seq_max_entries = 500
        # {chat_id: message_id} — "正在思考中..."提示消息 ID（send_typing 发出，clear_typing 撤回）
        self._typing_msg_ids: dict[str, str] = {}
        # Markdown 能力是否可用（自定义 markdown 需内邀开通，首次失败后自动降级）
        self._markdown_available: bool = True

        # 消息去重：Webhook/WebSocket 可能重复投递
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

    def _remember_chat(
        self, chat_id: str, chat_type: str, msg_id: str = "", event_id: str = "",
    ) -> None:
        """记录 chat_id 的路由信息（收到消息时调用）"""
        self._chat_type_map[chat_id] = chat_type
        if msg_id:
            self._last_msg_id[chat_id] = msg_id
        if event_id:
            self._last_event_id[chat_id] = event_id

    def _next_msg_seq(self, seq_key: str) -> int:
        """获取并递增 msg_seq（QQ API 去重需要）。

        seq_key 应为被回复的 msg_id（被动回复场景）或 chat_id（主动发送场景）。
        """
        seq = self._msg_seq.get(seq_key, 0) + 1
        self._msg_seq[seq_key] = seq
        # 清理旧条目防止内存泄漏
        if len(self._msg_seq) > self._msg_seq_max_entries:
            keys = list(self._msg_seq.keys())
            for k in keys[: len(keys) // 2]:
                self._msg_seq.pop(k, None)
        return seq

    def _resolve_chat_type(self, chat_id: str, metadata: dict | None = None) -> str:
        """
        解析 chat_type，优先级:
        1. OutgoingMessage.metadata 中的 chat_type
        2. 路由表 _chat_type_map（收消息时记录的）
        3. 默认 "group"
        """
        if metadata:
            ct = metadata.get("chat_type")
            if ct:
                return ct
        return self._chat_type_map.get(chat_id, "group")

    def _resolve_msg_id(self, chat_id: str, metadata: dict | None = None) -> str | None:
        """
        解析 msg_id（被动回复需要），优先级:
        1. OutgoingMessage.metadata 中的 msg_id
        2. 路由表 _last_msg_id（最近收到的消息 ID）
        """
        if metadata:
            mid = metadata.get("msg_id")
            if mid:
                return mid
        return self._last_msg_id.get(chat_id)

    def _local_path_to_public_url(self, local_path: str) -> str | None:
        """将本地文件复制到 uploads 目录，返回公网可访问的 URL。

        需要配置 public_api_url 才能生效。
        """
        if not self.public_api_url:
            return None

        src = Path(local_path)
        if not src.exists():
            logger.warning(f"Local file not found: {local_path}")
            return None

        try:
            from openakita.api.routes.upload import get_upload_dir

            upload_dir = get_upload_dir()
            unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{src.suffix}"
            dest = upload_dir / unique_name
            shutil.copy2(src, dest)
            url = f"{self.public_api_url}/api/uploads/{unique_name}"
            logger.info(f"Local image served as public URL: {url}")
            return url
        except Exception as e:
            logger.warning(f"Failed to make local image publicly accessible: {e}")
            return None

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

    # 不可重试的配置类错误关键词（遇到后大幅延长重试间隔）
    _FATAL_KEYWORDS = ("不在白名单", "invalid appid", "invalid secret", "鉴权失败")

    async def _run_client(self) -> None:
        """在后台运行 botpy 客户端 (带自动重连) — WebSocket 模式"""
        max_delay = 120
        fatal_max_delay = 600  # 配置错误时最大等 10 分钟
        consecutive_fatal = 0

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

                err_msg = str(e)
                is_fatal = any(kw in err_msg for kw in self._FATAL_KEYWORDS)

                if is_fatal:
                    consecutive_fatal += 1
                    cap = fatal_max_delay
                    # 首次报错详细日志，后续只在间隔翻倍时提醒
                    if consecutive_fatal == 1:
                        logger.error(
                            f"QQ Official Bot 配置错误: {err_msg}\n"
                            f"  → 请检查 QQ 开放平台配置（IP 白名单 / AppID / AppSecret）\n"
                            f"  → 将持续后台重试，修复配置后自动恢复"
                        )
                    elif consecutive_fatal % 5 == 0:
                        logger.warning(
                            f"QQ Official Bot 仍无法连接 (已重试 {consecutive_fatal} 次): {err_msg}"
                        )
                else:
                    consecutive_fatal = 0
                    cap = max_delay
                    logger.error(f"QQ Official Bot error: {err_msg}")

                logger.info(f"QQ Official Bot: reconnecting in {self._retry_delay}s...")
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, cap)

    # ==================== Webhook 模式 ====================

    async def _get_access_token(self) -> str:
        """获取 QQ 官方 API 的 OAuth2 access_token（用于 Webhook 模式下主动发消息）"""
        now = time.time()
        if self._access_token and now < self._token_expires - 300:
            return self._access_token

        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        from ..retry import async_with_retry

        async def _do_fetch() -> dict:
            async with hx.AsyncClient() as client:
                resp = await client.post(
                    "https://bots.qq.com/app/getAppAccessToken",
                    json={
                        "appId": self.app_id,
                        "clientSecret": self.app_secret,
                    },
                    timeout=10.0,
                )
                return resp.json()

        data = await async_with_retry(
            _do_fetch, max_retries=2, base_delay=1.0,
            operation_name="QQ._get_access_token",
        )
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
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey

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

    def _is_duplicate(self, msg_id: str) -> bool:
        """检查消息是否重复，并记录到 LRU 缓存"""
        if not msg_id:
            return False
        if msg_id in self._seen_message_ids:
            logger.debug(f"QQ: duplicate message ignored: {msg_id}")
            return True
        self._seen_message_ids[msg_id] = None
        while len(self._seen_message_ids) > self._seen_message_ids_max:
            self._seen_message_ids.popitem(last=False)
        return False

    async def _handle_webhook_event(self, event_type: str, data: dict) -> None:
        """处理 Webhook 推送的事件"""
        try:
            import time as _time
            from datetime import datetime
            ts_str = data.get("timestamp")
            if ts_str and isinstance(ts_str, str):
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_s = _time.time() - dt.timestamp()
                    if age_s > self.STALE_MESSAGE_THRESHOLD_S:
                        logger.info(
                            f"QQ: stale webhook message discarded "
                            f"(age={age_s:.0f}s): {data.get('id', '?')}"
                        )
                        return
                except (ValueError, OSError):
                    pass

            if event_type == "GROUP_AT_MESSAGE_CREATE":
                unified = self._convert_webhook_group_message(data)
            elif event_type == "C2C_MESSAGE_CREATE":
                unified = self._convert_webhook_c2c_message(data)
            elif event_type == "AT_MESSAGE_CREATE":
                unified = self._convert_webhook_channel_message(data)
            else:
                logger.debug(f"QQ Webhook: unhandled event type {event_type}")
                return

            if self._is_duplicate(unified.channel_message_id):
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

        self._remember_chat(
            group_openid, "group", data.get("id", ""), data.get("event_id", ""),
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=group_openid,
            content=content,
            chat_type="group",
            is_mentioned=True,
            is_direct_message=False,
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "group",
                "is_group": True,
                "group_openid": group_openid,
                "msg_id": data.get("id", ""),
                "sender_name": "",
                "chat_name": "",
            },
        )

    def _convert_webhook_c2c_message(self, data: dict) -> UnifiedMessage:
        """将 Webhook 单聊消息转换为 UnifiedMessage"""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_openid = author.get("user_openid", "")

        self._remember_chat(
            user_openid, "c2c", data.get("id", ""), data.get("event_id", ""),
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=user_openid,
            content=content,
            chat_type="private",
            is_mentioned=False,
            is_direct_message=True,
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "c2c",
                "is_group": False,
                "user_openid": user_openid,
                "msg_id": data.get("id", ""),
                "sender_name": "",
                "chat_name": "",
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

        self._remember_chat(
            channel_id, "channel", data.get("id", ""), data.get("event_id", ""),
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=data.get("id", ""),
            user_id=f"qqbot_{user_id}",
            channel_user_id=user_id,
            chat_id=channel_id,
            content=content,
            chat_type="group",
            is_mentioned=True,
            is_direct_message=False,
            raw={"event_id": data.get("event_id")},
            metadata={
                "chat_type": "channel",
                "is_group": True,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "msg_id": data.get("id", ""),
                "sender_name": author.get("username", ""),
                "chat_name": "",
            },
        )

    @staticmethod
    def _parse_webhook_attachments(attachments: list | None, content: MessageContent) -> None:
        """解析 Webhook 回调中的附件（使用 _guess_media_type 做扩展名兜底）"""
        if not attachments:
            return
        for att in attachments:
            ct = att.get("content_type", "")
            url = att.get("url")
            if not url:
                continue
            filename = att.get("filename", "file")
            media_type = QQBotAdapter._guess_media_type(ct, filename)

            mime = ct or {
                "image": "image/png",
                "audio": "audio/amr",
                "video": "video/mp4",
            }.get(media_type, "application/octet-stream")

            media = MediaFile.create(filename=filename, mime_type=mime, url=url)
            if media_type == "image":
                content.images.append(media)
            elif media_type == "audio":
                content.voices.append(media)
            elif media_type == "video":
                content.videos.append(media)
            else:
                content.files.append(media)

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

    # 文件扩展名 → 媒体类型的回退映射（QQ 附件 content_type 经常为空）
    _EXT_AUDIO = {".amr", ".silk", ".slk", ".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac"}
    _EXT_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    _EXT_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}

    @staticmethod
    def _guess_media_type(content_type: str, filename: str) -> str:
        """
        根据 content_type 和文件扩展名推断媒体类别。

        QQ 附件的 content_type 经常为空或不标准，需要用扩展名兜底。
        返回: "image" | "audio" | "video" | "file"
        """
        ct = content_type.lower()
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("audio/"):
            return "audio"
        if ct.startswith("video/"):
            return "video"

        # content_type 不可靠，用扩展名兜底
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in QQBotAdapter._EXT_AUDIO:
            return "audio"
        if ext in QQBotAdapter._EXT_IMAGE:
            return "image"
        if ext in QQBotAdapter._EXT_VIDEO:
            return "video"

        return "file"

    @staticmethod
    def _parse_attachments(attachments: list | None, content: MessageContent) -> None:
        """
        解析 botpy 消息附件，填充到 MessageContent。

        botpy 的附件是 _Attachments 对象（属性访问），不是 dict。
        支持图片、语音、视频、文件等多种类型。
        QQ 附件的 content_type 经常为空，需要通过文件扩展名回退判断。
        """
        if not attachments:
            return

        for att in attachments:
            # 兼容 _Attachments 对象和 dict 两种格式
            if isinstance(att, dict):
                ct = att.get("content_type", "") or ""
                url = att.get("url")
                filename = att.get("filename", "file")
            else:
                ct = getattr(att, "content_type", "") or ""
                url = getattr(att, "url", None)
                filename = getattr(att, "filename", "file") or "file"

            if not url:
                continue

            media_type = QQBotAdapter._guess_media_type(ct, filename)

            # 为缺失 content_type 的附件补全 MIME
            if not ct:
                mime_map = {
                    "audio": "audio/amr",
                    "image": "image/png",
                    "video": "video/mp4",
                    "file": "application/octet-stream",
                }
                ct = mime_map.get(media_type, "application/octet-stream")

            media = MediaFile.create(filename=filename, mime_type=ct, url=url)

            if media_type == "image":
                content.images.append(media)
            elif media_type == "audio":
                content.voices.append(media)
            elif media_type == "video":
                content.videos.append(media)
            else:
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

        self._remember_chat(
            group_openid, "group", message.id or "",
            getattr(message, "event_id", "") or "",
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=group_openid,
            content=content,
            chat_type="group",
            is_mentioned=True,
            is_direct_message=False,
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "group",
                "is_group": True,
                "group_openid": group_openid,
                "msg_id": message.id,
                "sender_name": "",
                "chat_name": "",
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

        self._remember_chat(
            user_openid, "c2c", message.id or "",
            getattr(message, "event_id", "") or "",
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_openid}",
            channel_user_id=user_openid,
            chat_id=user_openid,
            content=content,
            chat_type="private",
            is_mentioned=False,
            is_direct_message=True,
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "c2c",
                "is_group": False,
                "user_openid": user_openid,
                "msg_id": message.id,
                "sender_name": "",
                "chat_name": "",
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

        self._remember_chat(
            channel_id, "channel", message.id or "",
            getattr(message, "event_id", "") or "",
        )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.id or "",
            user_id=f"qqbot_{user_id}",
            channel_user_id=user_id,
            chat_id=channel_id,
            content=content,
            chat_type="group",
            is_mentioned=True,
            is_direct_message=False,
            raw={"event_id": getattr(message, "event_id", None)},
            metadata={
                "chat_type": "channel",
                "is_group": True,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "msg_id": message.id,
                "sender_name": getattr(author, "username", "") or "",
                "chat_name": "",
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

    async def _build_api_headers(self, content_type: str = "application/json") -> dict:
        """构建 QQ API V2 请求头，使用正确的 QQBot {access_token} 格式。"""
        token = await self._get_access_token()
        headers = {"Authorization": f"QQBot {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _api_base_url(self) -> str:
        return (
            "https://sandbox.api.sgroup.qq.com"
            if self.sandbox
            else "https://api.sgroup.qq.com"
        )

    async def _upload_rich_media_base64(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        file_data: str,
        srv_send_msg: bool = False,
    ) -> dict:
        """通过 file_data (base64) 直接上传富媒体到 QQ 服务器，绕过 botpy SDK。

        botpy SDK 的 post_group_file / post_c2c_file 仅支持 url 参数，
        但 QQ 官方 API 同时支持 file_data（base64 编码的二进制内容）。
        此方法直接调用 REST API 实现本地文件上传。
        """
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"{base_url}/v2/groups/{target_id}/files"
        else:
            url = f"{base_url}/v2/users/{target_id}/files"

        payload = {
            "file_type": file_type,
            "file_data": file_data,
            "srv_send_msg": srv_send_msg,
        }
        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def _send_media_message_via_http(
        self,
        chat_type: str,
        target_id: str,
        file_info: str,
        msg_id: str | None = None,
    ) -> str:
        """Webhook 模式下通过 HTTP 直接发送媒体消息 (msg_type=7)。"""
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"{base_url}/v2/groups/{target_id}/messages"
        else:
            url = f"{base_url}/v2/users/{target_id}/messages"

        seq_key = msg_id or target_id
        payload: dict[str, Any] = {
            "msg_type": 7,
            "media": {"file_info": file_info},
            "msg_seq": self._next_msg_seq(seq_key),
        }
        if msg_id:
            payload["msg_id"] = msg_id

        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))

    async def _send_rich_media(
        self,
        api: Any,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str | None = None,
        msg_id: str | None = None,
        local_path: str | None = None,
    ) -> str:
        """
        完整的富媒体发送流程（两步）：上传 + 发消息。

        支持两种上传方式（二选一）：
        - url: 公网可访问的媒体 URL（走 botpy SDK）
        - local_path: 本地文件路径（读取后 base64 编码，直接调用 QQ API）

        Args:
            api: botpy API client
            chat_type: "group" 或 "c2c"
            target_id: 目标 openid
            file_type: 1=图片, 2=视频, 3=语音
            url: 公网可访问的媒体 URL
            msg_id: 被动回复的消息 ID（可选）
            local_path: 本地文件路径（可选，与 url 二选一）

        Returns:
            发送后的消息 ID
        """
        import base64 as b64

        # Step 1: 上传富媒体资源获取 file_info
        if local_path:
            with open(local_path, "rb") as f:
                file_data = b64.standard_b64encode(f.read()).decode("ascii")
            upload_result = await self._upload_rich_media_base64(
                chat_type, target_id,
                file_type=file_type,
                file_data=file_data,
                srv_send_msg=False,
            )
        elif url:
            upload_result = await self._upload_rich_media(
                api, chat_type, target_id,
                file_type=file_type,
                url=url,
                srv_send_msg=False,
            )
        else:
            raise ValueError("_send_rich_media requires either url or local_path")

        file_info = (
            getattr(upload_result, "file_info", None)
            or (upload_result.get("file_info") if isinstance(upload_result, dict) else None)
        )
        if not file_info:
            raise RuntimeError(
                f"Rich media upload did not return file_info: {upload_result}"
            )

        # Step 2: 发送消息 msg_type=7 (media)
        if api is not None:
            result = await self._send_to_target(
                api, chat_type, target_id,
                msg_type=7,
                media={"file_info": file_info},
                msg_id=msg_id,
            )
            return str(getattr(result, "id", ""))

        # Webhook 模式：api 为 None，直接用 HTTP 发送媒体消息
        return await self._send_media_message_via_http(
            chat_type, target_id, file_info, msg_id,
        )

    # ==================== 消息发送 ====================

    @staticmethod
    def _has_markdown_features(text: str) -> bool:
        """检测文本是否包含 Markdown 格式特征"""
        markers = ("**", "##", "- ", "```", "~~", "[", "](", "> ", "---")
        return any(m in text for m in markers)

    def _should_try_markdown(self, parse_mode: str | None, text: str) -> bool:
        """判断是否应尝试以 Markdown 格式发送"""
        if not self._markdown_available:
            return False
        if not text:
            return False
        return parse_mode == "markdown" and self._has_markdown_features(text)

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息

        支持:
        - 文本消息 (msg_type=0)
        - Markdown 消息 (msg_type=2, 需内邀开通，失败自动降级)
        - 图片消息 (频道: content+image/file_image; 群/C2C: 两步富媒体上传)
        """
        chat_type = self._resolve_chat_type(message.chat_id, message.metadata)
        msg_id = self._resolve_msg_id(message.chat_id, message.metadata)
        parse_mode = message.parse_mode

        # Webhook 模式使用 HTTP API 发送
        if self.mode == "webhook":
            return await self._send_message_via_http(
                message, chat_type, msg_id, parse_mode,
            )

        if not self._client or not self._client.api:
            raise RuntimeError("QQ Official Bot not started")

        api = self._client.api

        text = message.content.text or ""

        # 提取首张图片随主消息一起发送
        first_image_url: str | None = None
        first_image_path: str | None = None
        if message.content.images:
            img = message.content.images[0]
            if img.url:
                first_image_url = img.url
            elif img.local_path:
                first_image_path = img.local_path

        try:
            if chat_type == "channel":
                result_id = await self._send_channel_message(
                    api, message.chat_id, text, first_image_url, first_image_path,
                    msg_id, parse_mode,
                )
            else:
                result_id = await self._send_group_or_c2c_message(
                    api, chat_type, message.chat_id,
                    text, first_image_url, first_image_path, msg_id, parse_mode,
                )
        except Exception as e:
            logger.error(f"Failed to send QQ Official Bot message: {e}")
            raise

        # 循环发送剩余图片（不带文本，仅图片）
        for extra_img in message.content.images[1:]:
            extra_url = extra_img.url if extra_img.url else None
            extra_path = extra_img.local_path if not extra_url and extra_img.local_path else None
            if not extra_url and not extra_path:
                continue
            try:
                if chat_type == "channel":
                    await self._send_channel_message(
                        api, message.chat_id, "", extra_url, extra_path,
                        msg_id, None,
                    )
                else:
                    await self._send_group_or_c2c_message(
                        api, chat_type, message.chat_id,
                        "", extra_url, extra_path, msg_id, None,
                    )
            except Exception as e:
                logger.warning(f"QQ: send extra image failed: {e}")

        return result_id

    async def _send_message_via_http(
        self,
        message: OutgoingMessage,
        chat_type: str,
        msg_id: str | None,
        parse_mode: str | None = None,
    ) -> str:
        """Webhook 模式：通过 HTTP API 发送消息（文本/Markdown/图片）"""
        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        text = message.content.text or ""
        target_id = message.chat_id

        if chat_type == "group":
            url = f"/v2/groups/{target_id}/messages"
        elif chat_type == "c2c":
            url = f"/v2/users/{target_id}/messages"
        elif chat_type == "channel":
            url = f"/channels/{target_id}/messages"
        else:
            url = f"/v2/groups/{target_id}/messages"

        seq_key = msg_id or target_id

        # 提取图片信息
        first_image_url: str | None = None
        first_image_path: str | None = None
        if message.content.images:
            img = message.content.images[0]
            if img.url:
                first_image_url = img.url
            elif img.local_path:
                first_image_path = img.local_path

        result_id = ""

        if text:
            async with hx.AsyncClient(base_url=base_url, headers=headers) as client:
                # 尝试 Markdown 发送
                sent_as_md = False
                if self._should_try_markdown(parse_mode, text):
                    md_body: dict[str, Any] = {
                        "msg_type": 2,
                        "markdown": {"content": text},
                        "msg_seq": self._next_msg_seq(seq_key),
                    }
                    if msg_id:
                        md_body["msg_id"] = msg_id
                    try:
                        resp = await client.post(url, json=md_body)
                        resp.raise_for_status()
                        data = resp.json()
                        result_id = str(data.get("id", ""))
                        sent_as_md = True
                    except Exception as e:
                        self._markdown_available = False
                        logger.warning(
                            "QQ Markdown 发送失败，已降级为纯文本（后续消息将跳过 Markdown）: %s",
                            e,
                        )

                # 纯文本发送（含 40054005 去重重试，最多 2 次）
                if not sent_as_md:
                    for attempt in range(2):
                        body: dict[str, Any] = {
                            "msg_type": 0,
                            "content": text,
                            "msg_seq": self._next_msg_seq(seq_key),
                        }
                        if msg_id:
                            body["msg_id"] = msg_id

                        resp = await client.post(url, json=body)
                        if resp.status_code == 200:
                            data = resp.json()
                            result_id = str(data.get("id", ""))
                            break
                        if "40054005" in resp.text and attempt < 1:
                            logger.warning(
                                f"QQ HTTP 40054005 dedup (attempt {attempt + 1}), retrying"
                            )
                            continue
                        resp.raise_for_status()

        # 发送图片（公网 URL 或本地文件 base64 上传）
        if first_image_url or first_image_path:
            media_id = await self._send_rich_media(
                None, chat_type, target_id,
                file_type=1,
                url=first_image_url,
                msg_id=msg_id,
                local_path=first_image_path if not first_image_url else None,
            )
            result_id = result_id or media_id

        return result_id

    async def _send_channel_message(
        self,
        api: Any,
        channel_id: str,
        text: str,
        image_url: str | None,
        image_path: str | None,
        msg_id: str | None,
        parse_mode: str | None = None,
    ) -> str:
        """频道消息：支持 content + image/file_image 在同一条消息中，支持 Markdown"""
        # 尝试 Markdown 发送（仅纯文本时，有图片时走普通消息）
        if self._should_try_markdown(parse_mode, text) and not image_url and not image_path:
            try:
                md_kwargs: dict[str, Any] = {
                    "channel_id": channel_id,
                    "msg_id": msg_id,
                    "msg_type": 2,
                    "markdown": {"content": text},
                }
                result = await api.post_message(**md_kwargs)
                return str(getattr(result, "id", ""))
            except Exception as e:
                self._markdown_available = False
                logger.warning(
                    "QQ 频道 Markdown 发送失败，已降级为纯文本: %s", e,
                )

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
        parse_mode: str | None = None,
    ) -> str:
        """
        群聊 / C2C 消息发送。

        QQ 官方 API 群/C2C 不支持文本+图片同时发送，需要分两条消息:
        1. 文本消息 (msg_type=0) 或 Markdown (msg_type=2)
        2. 图片通过富媒体 API 两步上传后发送 (msg_type=7)
        """
        result_id = ""

        # 发送文本（优先尝试 Markdown）
        if text:
            sent_as_md = False
            if self._should_try_markdown(parse_mode, text):
                try:
                    result = await self._send_to_target(
                        api, chat_type, target_id,
                        msg_type=2, markdown={"content": text}, msg_id=msg_id,
                    )
                    result_id = str(getattr(result, "id", ""))
                    sent_as_md = True
                except Exception as e:
                    self._markdown_available = False
                    logger.warning(
                        "QQ 群/C2C Markdown 发送失败，已降级为纯文本: %s", e,
                    )

            if not sent_as_md:
                result = await self._send_to_target(
                    api, chat_type, target_id,
                    msg_type=0, content=text, msg_id=msg_id,
                )
                result_id = str(getattr(result, "id", ""))

        # 发送图片（需要公网 URL）
        # 本地图片自动转公网 URL（需要配置 public_api_url）
        if not image_url and image_path:
            image_url = self._local_path_to_public_url(image_path)
            if not image_url:
                logger.warning(
                    f"QQ Official Bot: 无法发送本地图片到群/C2C。"
                    f"请配置 public_api_url 使本地图片可通过公网访问。"
                    f" image_path={image_path}"
                )
        if image_url:
            media_id = await self._send_rich_media(
                api, chat_type, target_id,
                file_type=1,
                url=image_url,
                msg_id=msg_id,
            )
            result_id = result_id or media_id
        elif image_path:
            media_id = await self._send_rich_media(
                api, chat_type, target_id,
                file_type=1,
                msg_id=msg_id,
                local_path=image_path,
            )
            result_id = result_id or media_id

        return result_id

    @staticmethod
    def _is_dedup_error(exc: BaseException) -> bool:
        """检测是否为 QQ API 40054005 消息去重错误。"""
        return "40054005" in str(exc)

    async def _do_send(self, api: Any, chat_type: str, target_id: str, **kwargs) -> Any:
        """底层发送调用，根据 chat_type 路由到对应 API。"""
        if chat_type == "group":
            return await api.post_group_message(group_openid=target_id, **kwargs)
        elif chat_type == "c2c":
            return await api.post_c2c_message(openid=target_id, **kwargs)
        else:
            return await api.post_group_message(group_openid=target_id, **kwargs)

    async def _send_to_target(
        self, api: Any, chat_type: str, target_id: str, **kwargs
    ) -> Any:
        """
        根据 chat_type 发送消息到对应目标。

        自动注入递增的 msg_seq 以避免 QQ API 的消息去重拦截 (40054005)。
        seq_key 优先使用 msg_id（被动回复），无 msg_id 时回退到 target_id（主动发送）。
        遇到 40054005 去重错误时自动递增 msg_seq 重试（最多 5 次）。
        若 msg_id 被动回复失败（窗口过期），自动尝试 event_id 回退。
        """
        seq_key = kwargs.get("msg_id") or target_id
        max_dedup_retries = 1
        last_error: Exception | None = None

        for attempt in range(max_dedup_retries + 1):
            if "msg_seq" not in kwargs or attempt > 0:
                kwargs["msg_seq"] = self._next_msg_seq(seq_key)

            try:
                return await self._do_send(api, chat_type, target_id, **kwargs)
            except Exception as e:
                last_error = e
                if self._is_dedup_error(e) and attempt < max_dedup_retries:
                    logger.warning(
                        f"QQ API 40054005 dedup error (attempt {attempt + 1}), "
                        f"retrying with incremented msg_seq: {e}"
                    )
                    continue
                break

        # msg_id 回复失败时尝试 event_id 回退
        msg_id = kwargs.get("msg_id")
        if msg_id and last_error:
            event_id = self._last_event_id.get(target_id)
            if event_id and event_id != msg_id:
                logger.warning(
                    f"QQ: msg_id reply failed, retrying with event_id: {event_id}"
                )
                kwargs["event_id"] = event_id
                kwargs.pop("msg_id", None)
                seq_key = event_id
                kwargs["msg_seq"] = self._next_msg_seq(seq_key)
                try:
                    return await self._do_send(api, chat_type, target_id, **kwargs)
                except Exception as e2:
                    logger.warning(f"QQ: event_id fallback also failed: {e2}")

        if last_error:
            raise last_error

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件

        注意: QQ 官方 API 的 file_type=4 (文件) 暂未开放。
        """
        raise NotImplementedError(
            "QQ 官方机器人暂不支持发送文件（file_type=4 API 未开放）"
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
        本地文件暂无法直接上传。
        """
        raise NotImplementedError(
            "QQ 官方机器人暂不支持发送语音（需要公网 URL + silk 格式，本地文件不支持）"
        )

    # ==================== Typing 提示 ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送"正在思考中..."提示消息（QQ 官方无 typing API，用文本消息替代）。

        幂等：同一 chat_id 只发一次，后续调用（_keep_typing 循环）直接跳过。
        """
        if chat_id in self._typing_msg_ids:
            return

        # 立即占位，防止 _keep_typing 循环在 await 期间重入
        self._typing_msg_ids[chat_id] = ""

        chat_type = self._resolve_chat_type(chat_id)
        msg_id = self._resolve_msg_id(chat_id)

        try:
            if self.mode == "webhook":
                sent_id = await self._send_typing_via_http(chat_id, chat_type, msg_id)
            else:
                if not self._client or not self._client.api:
                    return
                result = await self._send_to_target(
                    self._client.api, chat_type, chat_id,
                    msg_type=0, content="正在思考中...",
                    msg_id=msg_id,
                )
                sent_id = str(getattr(result, "id", ""))

            if sent_id:
                self._typing_msg_ids[chat_id] = sent_id
        except Exception as e:
            logger.debug(f"QQ Official Bot: send_typing failed: {e}")

    async def _send_typing_via_http(
        self, chat_id: str, chat_type: str, msg_id: str | None,
    ) -> str:
        """Webhook 模式下通过 HTTP API 发送思考提示"""
        try:
            import httpx as hx
        except ImportError:
            return ""

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        body: dict[str, Any] = {"msg_type": 0, "content": "正在思考中..."}
        if msg_id:
            body["msg_id"] = msg_id
        seq_key = msg_id or chat_id
        body["msg_seq"] = self._next_msg_seq(seq_key)

        if chat_type == "group":
            url = f"/v2/groups/{chat_id}/messages"
        elif chat_type == "c2c":
            url = f"/v2/users/{chat_id}/messages"
        else:
            url = f"/channels/{chat_id}/messages"

        async with hx.AsyncClient(base_url=base_url, headers=headers) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """撤回之前发送的"正在思考中..."消息（2 分钟内有效）"""
        sent_id = self._typing_msg_ids.pop(chat_id, None)
        if not sent_id:
            return

        chat_type = self._resolve_chat_type(chat_id)

        try:
            if self.mode == "webhook":
                await self._recall_message_via_http(chat_id, chat_type, sent_id)
            elif self._client and self._client.api:
                api = self._client.api
                if chat_type == "group":
                    await api.recall_group_message(
                        group_openid=chat_id, message_id=sent_id,
                    )
                elif chat_type == "c2c":
                    await api.recall_c2c_message(
                        openid=chat_id, message_id=sent_id,
                    )
                elif chat_type == "channel":
                    await api.recall_message(
                        channel_id=chat_id, message_id=sent_id,
                    )
        except Exception as e:
            logger.debug(f"QQ Official Bot: clear_typing (recall) failed: {e}")

    async def _recall_message_via_http(
        self, chat_id: str, chat_type: str, message_id: str,
    ) -> None:
        """Webhook 模式下通过 HTTP API 撤回消息"""
        try:
            import httpx as hx
        except ImportError:
            return

        headers = await self._build_api_headers(content_type="")
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"/v2/groups/{chat_id}/messages/{message_id}"
        elif chat_type == "c2c":
            url = f"/v2/users/{chat_id}/messages/{message_id}"
        else:
            url = f"/channels/{chat_id}/messages/{message_id}"

        async with hx.AsyncClient(base_url=base_url, headers=headers) as client:
            await client.delete(url)

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

            async with hx.AsyncClient(timeout=60.0) as client:
                response = await client.get(media.url)
                response.raise_for_status()

                safe_name = Path(media.filename).name or "download"
                local_path = self.media_dir / safe_name
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
            # is_sandbox 必须在 super().__init__() 中传入，
            # 因为 botpy.Client.__init__ 会把它传给 BotHttp 用于构建 API URL
            super().__init__(is_sandbox=_is_sandbox, **kw)
            self._adapter = _adapter

        async def on_group_at_message_create(self, message):
            """群聊 @机器人消息"""
            try:
                unified = self._adapter._convert_group_message(message)
                if self._adapter._is_duplicate(unified.channel_message_id):
                    return
                self._adapter._log_message(unified)
                await self._adapter._emit_message(unified)
            except Exception as e:
                logger.error(f"Error handling group message: {e}")

        async def on_c2c_message_create(self, message):
            """单聊消息"""
            try:
                unified = self._adapter._convert_c2c_message(message)
                if self._adapter._is_duplicate(unified.channel_message_id):
                    return
                self._adapter._log_message(unified)
                await self._adapter._emit_message(unified)
            except Exception as e:
                logger.error(f"Error handling C2C message: {e}")

        async def on_at_message_create(self, message):
            """频道 @机器人消息"""
            try:
                unified = self._adapter._convert_channel_message(message)
                if self._adapter._is_duplicate(unified.channel_message_id):
                    return
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
