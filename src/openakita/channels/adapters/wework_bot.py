"""
企业微信智能机器人适配器

基于企业微信智能机器人 API 实现:
- 内置 aiohttp HTTP 服务器接收 JSON 格式回调消息
- 消息加解密（AES-256-CBC，receiveid 为空字符串）
- 文本/图片/图文混排/语音/文件消息接收
- 流式消息回复（stream）
- response_url 主动回复（markdown）

与自建应用（wework.py）的主要区别:
- 回调消息为 JSON 格式（非 XML）
- 不需要 access_token、agent_id、secret
- 通过 response_url 或被动回复发送消息
- receiveid 为空字符串

参考文档:
- 接收消息: https://developer.work.weixin.qq.com/document/path/100719
- 被动回复消息: https://developer.work.weixin.qq.com/document/path/101031
- 加解密方案: https://developer.work.weixin.qq.com/document/path/101033
- 主动回复消息: https://developer.work.weixin.qq.com/document/path/101138
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import struct
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
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
aiohttp = None


def _import_httpx():
    global httpx
    if httpx is None:
        import httpx as hx

        httpx = hx


def _import_aiohttp():
    global aiohttp
    if aiohttp is None:
        try:
            import aiohttp as ah
            import aiohttp.web  # 显式导入 web 子模块

            aiohttp = ah
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("aiohttp"))


# ==================== 智能机器人消息加解密 ====================


class BotMsgCrypt:
    """
    企业微信智能机器人消息加解密工具

    与自建应用 WXBizMsgCrypt 的区别:
    - 回调/回复为 JSON 格式（非 XML）
    - receiveid 为空字符串（文档明确说明）
    - 新增 decrypt_media() 用于解密图片/文件下载内容

    参考: https://developer.work.weixin.qq.com/document/path/101033
    """

    def __init__(self, token: str, encoding_aes_key: str):
        self.token = token
        # EncodingAESKey 是 Base64 编码的 AES 密钥（43 字符 -> 32 字节）
        self.aes_key = base64.b64decode(encoding_aes_key + "=")

    def _get_sha1(self, *args: str) -> str:
        """计算签名"""
        items = sorted(args)
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def _encrypt(self, plaintext: str) -> str:
        """加密消息"""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("Crypto"))

        import os

        # 16 字节随机字符串
        random_str = os.urandom(16)
        text = plaintext.encode("utf-8")
        # 网络字节序的消息长度
        text_length = struct.pack("!I", len(text))
        # 智能机器人 receiveid 为空字符串
        receiveid = b""

        # 明文 = random(16B) + msg_len(4B) + msg + receiveid
        plain = random_str + text_length + text + receiveid

        # PKCS#7 填充到 32 字节的整数倍
        block_size = 32
        pad_len = block_size - (len(plain) % block_size)
        plain += bytes([pad_len]) * pad_len

        # AES-256-CBC 加密
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plain)

        return base64.b64encode(encrypted).decode("utf-8")

    def _decrypt(self, ciphertext: str) -> str:
        """解密消息"""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("Crypto"))

        encrypted = base64.b64decode(ciphertext)

        # AES-256-CBC 解密
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)

        # 去 PKCS#7 填充
        pad_len = decrypted[-1]
        content = decrypted[:-pad_len]

        # 解析: random(16B) + msg_len(4B) + msg + receiveid
        msg_len = struct.unpack("!I", content[16:20])[0]
        msg = content[20 : 20 + msg_len].decode("utf-8")
        # receiveid 部分对于智能机器人应为空字符串，不做校验

        return msg

    def verify_url(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> str:
        """
        验证回调 URL（GET 请求）

        Returns:
            解密后的 echostr（明文，需直接返回给企业微信）
        """
        signature = self._get_sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            raise ValueError("URL verification signature mismatch")
        return self._decrypt(echostr)

    def decrypt_msg(
        self, json_body: str, msg_signature: str, timestamp: str, nonce: str
    ) -> str:
        """
        解密回调消息（POST 请求，JSON 格式）

        Args:
            json_body: POST 请求的 JSON body，格式 {"encrypt": "..."}
            msg_signature: URL 中的 msg_signature 参数
            timestamp: URL 中的 timestamp 参数
            nonce: URL 中的 nonce 参数

        Returns:
            解密后的 JSON 消息字符串
        """
        data = json.loads(json_body)
        encrypt_str = data.get("encrypt")
        if not encrypt_str:
            raise ValueError("Missing 'encrypt' field in callback JSON")

        # 验证签名
        signature = self._get_sha1(self.token, timestamp, nonce, encrypt_str)
        if signature != msg_signature:
            raise ValueError("Message signature mismatch")

        return self._decrypt(encrypt_str)

    def encrypt_reply(
        self, reply_json: str, nonce: str, timestamp: str | None = None
    ) -> str:
        """
        加密被动回复消息

        Args:
            reply_json: 回复内容的 JSON 字符串
            nonce: 回调 URL 中的 nonce（必须与回调一致）
            timestamp: 时间戳（可选，默认当前时间）

        Returns:
            加密后的 JSON 字符串，格式:
            {"encrypt": "...", "msgsignature": "...", "timestamp": 123, "nonce": "..."}
        """
        timestamp = timestamp or str(int(time.time()))
        encrypted = self._encrypt(reply_json)
        signature = self._get_sha1(self.token, timestamp, nonce, encrypted)

        return json.dumps(
            {
                "encrypt": encrypted,
                "msgsignature": signature,
                "timestamp": int(timestamp),
                "nonce": nonce,
            }
        )

    def decrypt_media(self, encrypted_data: bytes) -> bytes:
        """
        解密媒体文件内容

        企业微信智能机器人的图片/文件 URL 下载内容经 AES 加密，
        使用相同的 EncodingAESKey 解密。

        Args:
            encrypted_data: 从 URL 下载的加密原始字节

        Returns:
            解密后的文件内容
        """
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("Crypto"))

        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted_data)

        # PKCS#7 去填充
        pad_len = decrypted[-1]
        return decrypted[:-pad_len]


# ==================== 流式会话 ====================

# 流式会话超时（秒）— 企业微信最多刷新 6 分钟
STREAM_TIMEOUT = 330  # 5.5 分钟，留 30 秒余量

# 流式消息结算延迟（秒）— 标记完成后等待此时间才真正 finish
# 用于等待 send_image 在 send_message 之后入队的场景
STREAM_SETTLE_DELAY = 8


@dataclass
class StreamSession:
    """
    流式消息会话

    管理一次完整的 stream 被动回复生命周期：
    1. 用户消息到达 → 创建 session，返回 stream(finish=false)
    2. 企业微信定期发 stream 刷新回调 → 返回当前内容
    3. Agent 处理完成 → 更新 content + pending_images
    4. 下一次刷新回调 → 返回 finish=true + content + images
    """

    stream_id: str  # 流式会话 ID（唯一）
    chat_id: str  # 聊天 ID
    user_id: str  # 用户 ID
    msgid: str  # 原始用户消息 ID
    response_url: str = ""  # response_url 备用

    # Agent 输出
    content: str = ""  # 文本内容（markdown）
    pending_images: list = dataclass_field(default_factory=list)  # [(base64_str, md5_str)]
    is_finished: bool = False  # Agent 是否已完成处理

    # 时间
    created_at: float = 0.0
    last_updated_at: float = 0.0  # 最近一次 send_message/send_image 更新

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


# ==================== 配置 ====================


@dataclass
class WeWorkBotConfig:
    """企业微信智能机器人配置"""

    corp_id: str  # 企业 ID（用于标识，不参与 API 调用）
    token: str  # 回调 Token
    encoding_aes_key: str  # 回调加密 AES Key
    callback_port: int = 9880  # 回调服务端口
    callback_host: str = "0.0.0.0"  # 回调服务绑定地址


# ==================== 适配器 ====================


class WeWorkBotAdapter(ChannelAdapter):
    """
    企业微信智能机器人适配器

    支持:
    - 内置 HTTP 回调服务器（接收 JSON 格式加密消息）
    - 消息加解密（AES-256-CBC，receiveid 为空）
    - 文本/图片/图文混排/语音/文件消息接收
    - 流式被动回复（stream）— 支持文字 + 图片混排
    - response_url 主动回复（markdown，备用）

    消息回复机制（流式被动回复）:
    1. 收到用户消息 → 创建 StreamSession，被动回复 stream(finish=false)
    2. 企业微信定期发送 stream 刷新回调（约每 1-2 秒）
    3. Agent 处理中 → 刷新回调返回当前内容(finish=false)
    4. Agent 完成 → send_message 更新文本，send_image 队列图片
    5. 下一次刷新回调 → 返回 finish=true + content + images
    6. 图片通过 stream.msg_item 以 base64+md5 发送（仅 finish=true 时）

    限制:
    - 图片仅支持 JPG/PNG，单张 ≤ 10MB，最多 10 张
    - stream 最长 6 分钟，超时后降级 response_url

    注意: 回调 URL 需要公网可访问。
    """

    channel_name = "wework"

    # 过期清理间隔
    CLEANUP_INTERVAL = 120

    def __init__(
        self,
        corp_id: str,
        token: str,
        encoding_aes_key: str,
        callback_port: int = 9880,
        callback_host: str = "0.0.0.0",
        media_dir: Path | None = None,
    ):
        super().__init__()

        self.config = WeWorkBotConfig(
            corp_id=corp_id,
            token=token,
            encoding_aes_key=encoding_aes_key,
            callback_port=callback_port,
            callback_host=callback_host,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/wework")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._http_client: Any | None = None
        self._crypt: BotMsgCrypt | None = None

        # HTTP 回调服务器
        self._callback_app: Any | None = None
        self._callback_runner: Any | None = None
        self._callback_site: Any | None = None

        # ── 流式会话管理 ──
        self._stream_sessions: dict[str, StreamSession] = {}  # stream_id → session
        self._chat_streams: dict[str, str] = {}  # chat_key → stream_id
        self._msgid_to_stream: dict[str, str] = {}  # msgid → stream_id
        self._stream_lock = asyncio.Lock()

        # response_url 备用存储（stream 超时降级时使用）
        self._msgid_response_urls: dict[str, str] = {}
        self._response_urls: dict[str, list[str]] = {}

        # 清理任务
        self._cleanup_task: asyncio.Task | None = None

    def _chat_key(self, chat_id: str, user_id: str) -> str:
        """生成聊天会话唯一键"""
        return f"{chat_id}:{user_id}"

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动智能机器人适配器（含 HTTP 回调服务器）"""
        _import_httpx()

        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._running = True

        # 初始化加解密工具
        self._crypt = BotMsgCrypt(
            token=self.config.token,
            encoding_aes_key=self.config.encoding_aes_key,
        )
        logger.info("WeWorkBot: message encryption initialized")

        # 启动 HTTP 回调服务器
        await self._start_callback_server()

        # 启动过期 response_url 清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_urls())

        logger.info("WeWorkBot adapter started (stream mode + response_url fallback)")

    async def stop(self) -> None:
        """停止适配器"""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        if self._callback_site:
            await self._callback_site.stop()
        if self._callback_runner:
            await self._callback_runner.cleanup()

        if self._http_client:
            await self._http_client.aclose()

        logger.info("WeWorkBot adapter stopped")

    # ==================== HTTP 回调服务器 ====================

    async def _start_callback_server(self) -> None:
        """启动 aiohttp HTTP 回调服务器"""
        _import_aiohttp()

        app = aiohttp.web.Application()
        app.router.add_get("/callback", self._handle_get_callback)
        app.router.add_post("/callback", self._handle_post_callback)
        app.router.add_get("/health", self._handle_health)

        self._callback_app = app
        self._callback_runner = aiohttp.web.AppRunner(app)
        await self._callback_runner.setup()

        self._callback_site = aiohttp.web.TCPSite(
            self._callback_runner,
            self.config.callback_host,
            self.config.callback_port,
        )

        try:
            await self._callback_site.start()
            logger.info(
                f"WeWorkBot callback server listening on "
                f"{self.config.callback_host}:{self.config.callback_port}"
            )
        except OSError as e:
            if e.errno == 10048 or "Address already in use" in str(e):
                logger.error(
                    f"WeWorkBot: Port {self.config.callback_port} already in use. "
                    f"Change WEWORK_CALLBACK_PORT in .env"
                )
            raise

    async def _handle_health(
        self, request: "aiohttp.web.Request"
    ) -> "aiohttp.web.Response":
        """健康检查端点"""
        return aiohttp.web.json_response(
            {"status": "ok", "channel": "wework", "mode": "bot"}
        )

    async def _handle_get_callback(
        self, request: "aiohttp.web.Request"
    ) -> "aiohttp.web.Response":
        """处理 GET 回调 — 企业微信 URL 验证"""
        if not self._crypt:
            return aiohttp.web.Response(text="Encryption not configured", status=500)

        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")

        try:
            reply_echostr = self._crypt.verify_url(
                msg_signature, timestamp, nonce, echostr
            )
            logger.info("WeWorkBot: URL verification successful")
            return aiohttp.web.Response(text=reply_echostr)
        except Exception as e:
            logger.error(f"WeWorkBot: URL verification failed: {e}")
            return aiohttp.web.Response(text="Verification failed", status=403)

    async def _handle_post_callback(
        self, request: "aiohttp.web.Request"
    ) -> "aiohttp.web.Response":
        """
        处理 POST 回调 — 接收加密 JSON 消息

        流式模式:
        - 新用户消息 → 创建 StreamSession, 被动回复 stream(finish=false)
        - stream 刷新回调 → 返回当前内容 / finish=true + images
        """
        if not self._crypt:
            return aiohttp.web.Response(text="Encryption not configured", status=500)

        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")

        body = await request.text()

        try:
            # 解密 JSON 消息
            decrypted_json = self._crypt.decrypt_msg(
                body, msg_signature, timestamp, nonce
            )
            logger.debug(f"WeWorkBot: Decrypted: {decrypted_json[:300]}")

            msg_data = json.loads(decrypted_json)
            msg_type = msg_data.get("msgtype", "")

            if msg_type == "stream":
                return await self._handle_stream_refresh(
                    msg_data, nonce, timestamp
                )
            else:
                # 新用户消息
                return await self._handle_new_message(msg_data, nonce, timestamp)

        except Exception as e:
            logger.error(
                f"WeWorkBot: Message processing failed: {e}", exc_info=True
            )
            # 返回空 200 避免企业微信重试
            return aiohttp.web.Response(text="", status=200)

    async def _handle_stream_refresh(
        self, msg_data: dict, nonce: str, timestamp: str
    ) -> "aiohttp.web.Response":
        """
        处理 stream 刷新回调

        企业微信约每 1-2 秒发一次刷新回调，携带 stream.id。
        我们返回当前 Agent 的输出状态:
        - Agent 未完成 → finish=false, content=当前文本
        - Agent 已完成 → finish=true, content=最终文本, msg_item=图片列表
        - 未知 stream_id → finish=true 终止（防止残留 stream 卡住）
        """
        stream_data = msg_data.get("stream", {})
        stream_id = stream_data.get("id", "")

        async with self._stream_lock:
            session = self._stream_sessions.get(stream_id)

        if not session:
            # 未知 stream_id → 直接终止
            logger.warning(
                f"WeWorkBot: Unknown stream_id={stream_id}, terminating"
            )
            reply_payload = json.dumps(
                {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "finish": True, "content": ""},
                },
                ensure_ascii=False,
            )
            encrypted = self._crypt.encrypt_reply(
                reply_payload, nonce, timestamp
            )
            return aiohttp.web.Response(
                text=encrypted, content_type="application/json"
            )

        # 检查是否超时
        elapsed = time.time() - session.created_at
        if elapsed > STREAM_TIMEOUT and not session.is_finished:
            logger.warning(
                f"WeWorkBot: Stream {stream_id} timeout ({elapsed:.0f}s), "
                f"force finishing"
            )
            session.is_finished = True
            if not session.content:
                session.content = "⏳ 处理超时，请重新发送消息"

        # 判断是否真正可以结束 stream
        # is_finished=True 表示 Agent 已调用 send_message，但还需等待 settle 延迟
        # 期间 send_image 仍可入队图片
        ready_to_finish = False
        if session.is_finished:
            settle_elapsed = time.time() - session.last_updated_at
            if settle_elapsed >= STREAM_SETTLE_DELAY:
                ready_to_finish = True
            else:
                logger.debug(
                    f"WeWorkBot: Stream {stream_id} settling "
                    f"({settle_elapsed:.1f}s / {STREAM_SETTLE_DELAY}s)"
                )

        if ready_to_finish:
            # ── settle 完成: 返回 finish=true + content + images ──
            reply_stream: dict[str, Any] = {
                "id": stream_id,
                "finish": True,
                "content": session.content or "",
            }

            # 附加图片到 msg_item（仅 finish=true 时有效）
            if session.pending_images:
                msg_items = []
                for b64_data, md5_hash in session.pending_images:
                    msg_items.append(
                        {
                            "msgtype": "image",
                            "image": {
                                "base64": b64_data,
                                "md5": md5_hash,
                            },
                        }
                    )
                reply_stream["msg_item"] = msg_items
                logger.info(
                    f"WeWorkBot: Stream {stream_id} finishing with "
                    f"{len(msg_items)} image(s)"
                )

            reply_payload = json.dumps(
                {"msgtype": "stream", "stream": reply_stream},
                ensure_ascii=False,
            )

            # 清理 session
            await self._cleanup_stream_session(stream_id)

            logger.info(
                f"WeWorkBot: Stream {stream_id} finished, "
                f"content={len(session.content)} chars, "
                f"images={len(session.pending_images)}"
            )
        else:
            # ── Agent 处理中 / settle 等待中: 返回 finish=false + 当前内容 ──
            # 注意: 即使 is_finished=True 但 settle 未到，也返回 finish=false
            # 用户会在企业微信中看到实时文字内容（stream 持续显示）
            reply_payload = json.dumps(
                {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": False,
                        "content": session.content or "",
                    },
                },
                ensure_ascii=False,
            )

        encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
        return aiohttp.web.Response(
            text=encrypted, content_type="application/json"
        )

    async def _cleanup_stream_session(self, stream_id: str) -> None:
        """清理已完成的 stream session 及其关联映射"""
        async with self._stream_lock:
            session = self._stream_sessions.pop(stream_id, None)
            if session:
                # 清理 chat_key → stream_id
                chat_key = self._chat_key(session.chat_id, session.user_id)
                if self._chat_streams.get(chat_key) == stream_id:
                    self._chat_streams.pop(chat_key, None)
                # 清理 msgid → stream_id
                if self._msgid_to_stream.get(session.msgid) == stream_id:
                    self._msgid_to_stream.pop(session.msgid, None)

    # ==================== 新消息处理 ====================

    async def _handle_new_message(
        self, msg_data: dict, nonce: str, timestamp: str
    ) -> "aiohttp.web.Response":
        """
        处理新用户消息（流式模式）:
        1. 创建 StreamSession，生成唯一 stream_id
        2. 存储 response_url（备用，stream 超时时降级）
        3. 被动回复 stream(finish=false) 开启流式会话
        4. 异步处理消息并 emit 到网关
        5. Agent 回复时通过 stream session 传递内容
        6. 企业微信的 stream 刷新回调会读取 session 内容
        """
        msg_type = msg_data.get("msgtype", "")
        msgid = msg_data.get("msgid", "")
        chatid = msg_data.get("chatid", "")  # 群聊才有
        chattype = msg_data.get("chattype", "single")
        from_info = msg_data.get("from", {})
        userid = from_info.get("userid", "")
        response_url = msg_data.get("response_url", "")

        # 确定 chat_id
        chat_id = chatid if chattype == "group" else userid
        chat_key = self._chat_key(chat_id, userid)

        # 存储 response_url（备用降级）
        if response_url:
            if msgid:
                self._msgid_response_urls[msgid] = response_url
            if chat_key not in self._response_urls:
                self._response_urls[chat_key] = []
            self._response_urls[chat_key].append(response_url)

        logger.info(
            f"WeWorkBot: New message from {userid} in {chat_id}, "
            f"msgtype={msg_type}, has_response_url={bool(response_url)}"
        )

        # event 类型（进入会话等）只记录，不 emit
        if msg_type == "event":
            logger.info(f"WeWorkBot: Event from {userid}, type ignored")
            # event 不需要 stream，返回空 JSON
            reply_payload = json.dumps({})
            encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
            return aiohttp.web.Response(
                text=encrypted, content_type="application/json"
            )

        # ── 创建 StreamSession ──
        stream_id = f"stream_{msgid}_{int(time.time())}"

        session = StreamSession(
            stream_id=stream_id,
            chat_id=chat_id,
            user_id=userid,
            msgid=msgid,
            response_url=response_url,
        )

        async with self._stream_lock:
            self._stream_sessions[stream_id] = session
            self._chat_streams[chat_key] = stream_id
            if msgid:
                self._msgid_to_stream[msgid] = stream_id

        logger.info(
            f"WeWorkBot: Created stream session {stream_id} "
            f"for msgid={msgid}, chat={chat_id}"
        )

        # 异步处理实际消息
        asyncio.create_task(self._process_message(msg_data))

        # 被动回复: 开启 stream（finish=false，空内容）
        reply_payload = json.dumps(
            {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": False,
                    "content": "",
                },
            },
            ensure_ascii=False,
        )
        encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
        return aiohttp.web.Response(
            text=encrypted, content_type="application/json"
        )

    # ==================== 消息解析 ====================

    async def _process_message(self, msg_data: dict) -> None:
        """解析解密后的 JSON 消息，转换为 UnifiedMessage 并 emit"""
        try:
            msg_type = msg_data.get("msgtype", "")
            msgid = msg_data.get("msgid", "")
            chatid = msg_data.get("chatid", "")
            chattype = msg_data.get("chattype", "single")
            from_info = msg_data.get("from", {})
            userid = from_info.get("userid", "")

            chat_id = chatid if chattype == "group" else userid
            chat_type_str = "group" if chattype == "group" else "private"

            if msg_type == "text":
                await self._handle_text_message(
                    msg_data, msgid, userid, chat_id, chat_type_str
                )
            elif msg_type == "image":
                await self._handle_image_message(
                    msg_data, msgid, userid, chat_id, chat_type_str
                )
            elif msg_type == "mixed":
                await self._handle_mixed_message(
                    msg_data, msgid, userid, chat_id, chat_type_str
                )
            elif msg_type == "voice":
                await self._handle_voice_message(
                    msg_data, msgid, userid, chat_id, chat_type_str
                )
            elif msg_type == "file":
                await self._handle_file_message(
                    msg_data, msgid, userid, chat_id, chat_type_str
                )
            else:
                logger.info(f"WeWorkBot: Unhandled message type: {msg_type}")

        except Exception as e:
            logger.error(
                f"WeWorkBot: Error processing message: {e}", exc_info=True
            )

    async def _handle_text_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """处理文本消息"""
        text_data = msg_data.get("text", {})
        text_content = text_data.get("content", "")

        # 处理引用消息
        quote_data = msg_data.get("quote")
        if quote_data:
            quote_text = self._extract_quote_text(quote_data)
            if quote_text:
                text_content = f"[引用: {quote_text}]\n{text_content}"

        # 群聊中去除 @机器人 的 mention
        if msg_data.get("chattype") == "group":
            text_content = re.sub(r"^@\S+\s*", "", text_content).strip()

        content = MessageContent(text=text_content)

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=msg_data,
        )

        self._log_message(unified)
        await self._emit_message(unified)

    async def _handle_image_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """处理图片消息（仅单聊支持）"""
        image_data = msg_data.get("image", {})
        image_url = image_data.get("url", "")

        media = MediaFile.create(
            filename=f"{msgid}.jpg",
            mime_type="image/jpeg",
            url=image_url,
        )
        # 标记 URL 内容经 AES 加密（下载时需解密）
        media.extra = {"aes_encrypted": True}

        content = MessageContent(images=[media])

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=msg_data,
        )

        self._log_message(unified)
        await self._emit_message(unified)

    async def _handle_mixed_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """处理图文混排消息"""
        mixed_data = msg_data.get("mixed", {})
        msg_items = mixed_data.get("msg_item", [])

        text_parts = []
        images = []

        for item in msg_items:
            item_type = item.get("msgtype", "")
            if item_type == "text":
                text_parts.append(item.get("text", {}).get("content", ""))
            elif item_type == "image":
                image_url = item.get("image", {}).get("url", "")
                media = MediaFile.create(
                    filename=f"{msgid}_{len(images)}.jpg",
                    mime_type="image/jpeg",
                    url=image_url,
                )
                media.extra = {"aes_encrypted": True}
                images.append(media)

        # 处理引用
        quote_data = msg_data.get("quote")
        if quote_data:
            quote_text = self._extract_quote_text(quote_data)
            if quote_text:
                text_parts.insert(0, f"[引用: {quote_text}]")

        # 群聊去除 @mention
        combined_text = "\n".join(text_parts) if text_parts else None
        if combined_text and msg_data.get("chattype") == "group":
            combined_text = re.sub(r"^@\S+\s*", "", combined_text).strip()

        content = MessageContent(
            text=combined_text,
            images=images,
        )

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=msg_data,
        )

        self._log_message(unified)
        await self._emit_message(unified)

    async def _handle_voice_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """
        处理语音消息（仅单聊支持）

        企业微信已自动将语音转为文字。
        """
        voice_data = msg_data.get("voice", {})
        transcription = voice_data.get("content", "")

        # 语音已自动转文字，作为文本消息处理
        if transcription:
            content = MessageContent(text=f"[语音转文字] {transcription}")
        else:
            content = MessageContent(text="[语音消息，无法识别]")

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=msg_data,
        )

        self._log_message(unified)
        await self._emit_message(unified)

    async def _handle_file_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """处理文件消息（仅单聊支持，最大 100M）"""
        file_data = msg_data.get("file", {})
        file_url = file_data.get("url", "")

        media = MediaFile.create(
            filename=f"file_{msgid}",
            mime_type="application/octet-stream",
            url=file_url,
        )
        # URL 内容经 AES 加密
        media.extra = {"aes_encrypted": True}

        content = MessageContent(files=[media])

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=msg_data,
        )

        self._log_message(unified)
        await self._emit_message(unified)

    def _extract_quote_text(self, quote_data: dict) -> str:
        """从引用结构中提取文本内容"""
        quote_type = quote_data.get("msgtype", "")
        if quote_type == "text":
            return quote_data.get("text", {}).get("content", "")
        elif quote_type == "mixed":
            items = quote_data.get("mixed", {}).get("msg_item", [])
            parts = []
            for item in items:
                if item.get("msgtype") == "text":
                    parts.append(item.get("text", {}).get("content", ""))
                elif item.get("msgtype") == "image":
                    parts.append("[图片]")
            return " ".join(parts)
        elif quote_type == "image":
            return "[图片]"
        elif quote_type == "voice":
            return quote_data.get("voice", {}).get("content", "[语音]")
        elif quote_type == "file":
            return "[文件]"
        return ""

    # ==================== 消息发送 ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息（流式模式）

        查找关联的 StreamSession，更新其文本内容并标记完成。
        下一次 stream 刷新回调会读取 session 并返回 finish=true。

        如果 stream session 不存在（超时被清理），降级使用 response_url。

        查找策略:
        1. reply_to → 按 msgid 查找 stream session
        2. chat_id → 按 chat_key 查找 stream session
        3. 降级 → response_url（stream 超时或已完成时）
        """
        text = message.content.text or ""
        chat_id = message.chat_id

        # ── 策略 1: reply_to → 精确匹配 stream session ──
        if message.reply_to:
            stream_id = self._msgid_to_stream.get(message.reply_to)
            if stream_id:
                session = self._stream_sessions.get(stream_id)
                if session:
                    session.content = text
                    session.is_finished = True
                    session.last_updated_at = time.time()
                    logger.info(
                        f"WeWorkBot: Stream {stream_id} content updated "
                        f"({len(text)} chars), marked finished "
                        f"(settle {STREAM_SETTLE_DELAY}s)"
                    )
                    return f"stream:{stream_id}"

        # ── 策略 2: chat_id + user_id 匹配 stream session ──
        # 群聊中需要 user_id 精确匹配，避免匹配到其他用户的 stream
        user_id = message.metadata.get("channel_user_id") if message.metadata else None
        stream_id = self._find_stream_by_chat(chat_id, user_id)
        if stream_id:
            session = self._stream_sessions.get(stream_id)
            if session:
                session.content = text
                session.is_finished = True
                session.last_updated_at = time.time()
                logger.info(
                    f"WeWorkBot: Stream {stream_id} (via chat_key, "
                    f"user={user_id}) content updated ({len(text)} chars), "
                    f"marked finished (settle {STREAM_SETTLE_DELAY}s)"
                )
                return f"stream:{stream_id}"

        # ── 降级: response_url ──
        logger.info(
            f"WeWorkBot: No active stream for chat_id={chat_id}, "
            f"falling back to response_url"
        )
        return await self._send_via_response_url_fallback(
            chat_id, message.reply_to, text
        )

    def _find_stream_by_chat(
        self, chat_id: str, user_id: str | None = None
    ) -> str | None:
        """
        查找活跃的 stream session

        优先按 chat_key (chat_id:user_id) 精确匹配（群聊需要）。
        无 user_id 时降级为 chat_id 前缀匹配（单聊兼容）。
        """
        if user_id:
            # 精确匹配 — 群聊中每个用户有独立的 stream
            chat_key = self._chat_key(chat_id, user_id)
            sid = self._chat_streams.get(chat_key)
            if sid:
                session = self._stream_sessions.get(sid)
                if session:
                    return sid

        # 降级: 前缀匹配（单聊中 chat_id == user_id，只有一个匹配）
        for key, sid in list(self._chat_streams.items()):
            if key.startswith(f"{chat_id}:"):
                session = self._stream_sessions.get(sid)
                if session:
                    return sid
        return None

    # ── response_url 降级方法（stream 不可用时） ──

    async def _send_via_response_url_fallback(
        self, chat_id: str, reply_to: str | None, text: str
    ) -> str:
        """
        降级通过 response_url 发送 markdown 消息

        仅在 stream 不可用时（超时、已完成）调用。
        response_url 有效期 1 小时，只能调用一次。
        """
        # 按 msgid 精确匹配
        url = None
        if reply_to:
            url = self._msgid_response_urls.pop(reply_to, None)
            if url:
                self._remove_url_from_lists(url)

        # 按 chat_key 匹配
        if not url:
            url = self._pop_response_url(chat_id)
            if url:
                self._remove_url_from_msgid_map(url)

        if not url:
            logger.debug(
                f"WeWorkBot: No response_url for chat_id={chat_id}, "
                f"already consumed or expired"
            )
            return ""

        data = {
            "msgtype": "markdown",
            "markdown": {"content": text},
        }

        try:
            response = await self._http_client.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
            )
            result = response.json()

            if result.get("errcode", 0) != 0:
                logger.error(
                    f"WeWorkBot: response_url reply failed: "
                    f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
                )
                return ""

            logger.info(
                f"WeWorkBot: Sent via response_url fallback ({len(text)} chars)"
            )
            return "response_url_sent"

        except Exception as e:
            logger.error(f"WeWorkBot: response_url request failed: {e}")
            return ""

    def _pop_response_url(self, chat_id: str) -> str | None:
        """从 chat_key 备用队列中弹出一个可用的 response_url"""
        for key, urls in list(self._response_urls.items()):
            if key.startswith(f"{chat_id}:") and urls:
                url = urls.pop(0)
                if not urls:
                    self._response_urls.pop(key, None)
                return url
        return None

    def _remove_url_from_lists(self, url: str) -> None:
        """从 chat_key 备用列表中移除已使用的 url"""
        for key, urls in list(self._response_urls.items()):
            if url in urls:
                urls.remove(url)
                if not urls:
                    self._response_urls.pop(key, None)
                return

    def _remove_url_from_msgid_map(self, url: str) -> None:
        """从 msgid 映射中移除已使用的 url"""
        for msgid, stored_url in list(self._msgid_response_urls.items()):
            if stored_url == url:
                self._msgid_response_urls.pop(msgid, None)
                return

    async def send_markdown(self, chat_id: str, content: str) -> str:
        """发送 Markdown 消息（便捷方法）"""
        msg = OutgoingMessage(
            chat_id=chat_id,
            content=MessageContent(text=content),
        )
        return await self.send_message(msg)

    async def send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """
        发送图片消息（通过 stream msg_item，base64+md5）

        图片会被队列到关联的 StreamSession，在 stream finish=true 时
        随文本一起发送。

        限制:
        - 仅支持 JPG/PNG 格式，其他格式自动转为 JPG
        - 单张 ≤ 10MB
        - 单条消息最多 10 张图片
        - 图片仅在 finish=true 时随 msg_item 发出

        如果 stream session 不存在，降级为 markdown 描述。
        """
        # 查找关联的 stream session
        stream_id = None
        if reply_to:
            stream_id = self._msgid_to_stream.get(reply_to)
        if not stream_id:
            # 群聊中需要 user_id 精确匹配
            user_id = kwargs.get("channel_user_id") or (
                kwargs.get("metadata", {}).get("channel_user_id")
                if isinstance(kwargs.get("metadata"), dict)
                else None
            )
            stream_id = self._find_stream_by_chat(chat_id, user_id)

        session = self._stream_sessions.get(stream_id) if stream_id else None

        if not session:
            # 无活跃 stream → raise 让 im_channel handler 回退到 send_file
            # 不能调 send_markdown，否则会消耗 response_url 导致 Agent 最终文字被丢弃
            filename = Path(image_path).name
            logger.warning(
                f"WeWorkBot: No active stream for image: {filename}. "
                f"Raising NotImplementedError for handler fallback."
            )
            raise NotImplementedError(
                f"WeWork Smart Robot: stream session expired, "
                f"cannot send image {filename}. "
                f"Image sending requires an active stream session."
            )

        # 读取图片 → 转格式(如需) → base64 + md5
        try:
            b64_data, md5_hash = await self._prepare_image_for_stream(
                image_path
            )
        except Exception as e:
            # 图片处理失败 → raise 让 handler 处理，不消耗 stream/response_url
            logger.error(
                f"WeWorkBot: Failed to prepare image {image_path}: {e}"
            )
            raise RuntimeError(
                f"Failed to prepare image for stream: {e}"
            ) from e

        # 检查限制
        if len(session.pending_images) >= 10:
            logger.warning(
                f"WeWorkBot: Stream {stream_id} already has 10 images, "
                f"skipping {image_path}"
            )
            return f"stream:{stream_id}:image_limit"

        # 入队 + 重置 settle 计时器
        session.pending_images.append((b64_data, md5_hash))
        session.last_updated_at = time.time()

        logger.info(
            f"WeWorkBot: Image queued to stream {stream_id} "
            f"(total: {len(session.pending_images)}), "
            f"file={Path(image_path).name}"
        )
        return f"stream:{stream_id}:image_queued"

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """
        发送文件

        企业微信智能机器人的流式回复仅支持图片（JPG/PNG），
        不支持文件类型。raise 让 handler 返回错误给 Agent。
        """
        raise NotImplementedError(
            "WeWork Smart Robot (Bot mode) does not support sending files. "
            "Stream only supports JPG/PNG images via msg_item."
        )

    # ── 图片处理 ──

    async def _prepare_image_for_stream(
        self, image_path: str
    ) -> tuple[str, str]:
        """
        准备图片用于 stream msg_item

        1. 读取文件
        2. 检查/转换格式（仅支持 JPG/PNG，其他格式转 JPG）
        3. 检查大小（≤ 10MB）
        4. 返回 (base64_str, md5_hex)
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        raw_data = path.read_bytes()
        file_ext = path.suffix.lower()

        # 判断是否需要格式转换
        is_jpg = file_ext in (".jpg", ".jpeg") or raw_data[:2] == b"\xff\xd8"
        is_png = file_ext == ".png" or raw_data[:4] == b"\x89PNG"

        if not is_jpg and not is_png:
            # 需要转换为 JPG
            raw_data = await self._convert_image_to_jpg(raw_data, path.name)
            logger.info(
                f"WeWorkBot: Converted {path.name} to JPG "
                f"({len(raw_data)} bytes)"
            )

        # 检查大小 (10MB)
        if len(raw_data) > 10 * 1024 * 1024:
            raise ValueError(
                f"Image too large: {len(raw_data)} bytes (max 10MB)"
            )

        b64_data = base64.b64encode(raw_data).decode("utf-8")
        md5_hash = hashlib.md5(raw_data).hexdigest()

        return b64_data, md5_hash

    async def _convert_image_to_jpg(
        self, raw_data: bytes, filename: str
    ) -> bytes:
        """
        将图片转换为 JPG 格式

        优先使用 Pillow，不可用时尝试直接使用原始数据。
        """
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(raw_data))
            # 转换为 RGB（移除 alpha 通道）
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=90)
            return output.getvalue()
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("PIL")
            logger.warning(f"WeWorkBot: {hint}，无法转换 {filename}，尝试发送原始数据")
            return raw_data
        except Exception as e:
            logger.error(
                f"WeWorkBot: Image conversion failed for {filename}: {e}"
            )
            raise

    # ==================== 媒体处理 ====================

    async def download_media(self, media: MediaFile) -> Path:
        """
        下载媒体文件

        智能机器人的图片/文件 URL 内容经 AES 加密，
        下载后需用 EncodingAESKey 解密。URL 有效期 5 分钟。
        """
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.url:
            raise ValueError("Media has no URL to download")

        # 下载
        response = await self._http_client.get(media.url)
        raw_data = response.content

        # 如果标记了 AES 加密，解密内容
        if media.extra and media.extra.get("aes_encrypted") and self._crypt:
            try:
                raw_data = self._crypt.decrypt_media(raw_data)
                logger.debug(
                    f"WeWorkBot: Decrypted media {media.filename} "
                    f"({len(raw_data)} bytes)"
                )
            except Exception as e:
                logger.error(
                    f"WeWorkBot: Failed to decrypt media {media.filename}: {e}"
                )
                # 如果解密失败，仍然保存原始数据
                pass

        # 保存到本地
        local_path = self.media_dir / media.filename
        with open(local_path, "wb") as f:
            f.write(raw_data)

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"WeWorkBot: Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        上传媒体文件

        智能机器人模式不需要预上传媒体。
        图片通过 stream msg_item 的 base64+md5 直接内联发送。
        """
        raise NotImplementedError(
            "WeWork Smart Robot sends images inline via stream msg_item (base64+md5). "
            "No separate upload API is needed. Use send_image() instead."
        )

    # ==================== 清理 ====================

    async def _cleanup_expired_urls(self) -> None:
        """定期清理过期的 stream session 和 response_url 缓存"""
        while self._running:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)

                now = time.time()

                # ── 清理超时的 stream session ──
                expired_streams = []
                async with self._stream_lock:
                    for sid, session in list(self._stream_sessions.items()):
                        age = now - session.created_at
                        if age > STREAM_TIMEOUT + 60:
                            # 超过超时 + 1 分钟缓冲，强制清理
                            expired_streams.append(sid)

                for sid in expired_streams:
                    await self._cleanup_stream_session(sid)

                if expired_streams:
                    logger.info(
                        f"WeWorkBot: Cleaned {len(expired_streams)} "
                        f"expired stream sessions"
                    )

                # ── 清理 response_url 缓存 ──
                if len(self._msgid_response_urls) > 200:
                    excess = len(self._msgid_response_urls) - 100
                    keys = list(self._msgid_response_urls.keys())[:excess]
                    for k in keys:
                        self._msgid_response_urls.pop(k, None)
                    logger.info(
                        f"WeWorkBot: Cleaned {excess} expired msgid→url entries"
                    )

                if len(self._response_urls) > 100:
                    excess = len(self._response_urls) - 50
                    keys = list(self._response_urls.keys())[:excess]
                    for k in keys:
                        self._response_urls.pop(k, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WeWorkBot: Cleanup error: {e}")
