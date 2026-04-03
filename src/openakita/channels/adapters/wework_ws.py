"""
企业微信智能机器人 WebSocket 长连接适配器

基于企业微信智能机器人 WebSocket 协议实现:
- WebSocket 长连接 (wss://openws.work.weixin.qq.com)
- 认证 / 心跳 / 指数退避重连
- 消息接收 (text/image/mixed/voice/file/video)
- 流式回复 (stream) / 模板卡片 / 主动推送
- 文件下载 + AES-256-CBC 逐文件解密
- WebSocket 分片上传临时素材 (upload_media)
- response_url HTTP 回退

Official protocol doc:
https://developer.work.weixin.qq.com/document/path/101463
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from collections import OrderedDict
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

# ---------------------------------------------------------------------------
# 延迟导入
# ---------------------------------------------------------------------------
websockets: Any = None
httpx: Any = None


def _import_websockets():
    global websockets
    if websockets is None:
        try:
            import websockets as ws
            websockets = ws
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("websockets"))


def _import_httpx():
    global httpx
    if httpx is None:
        try:
            import httpx as hx
            httpx = hx
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("httpx"))


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
WS_DEFAULT_URL = "wss://openws.work.weixin.qq.com"

CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_RESPONSE = "aibot_respond_msg"
CMD_RESPONSE_WELCOME = "aibot_respond_welcome_msg"
CMD_RESPONSE_UPDATE = "aibot_respond_update_msg"
CMD_SEND_MSG = "aibot_send_msg"
CMD_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"
CMD_UPLOAD_INIT = "aibot_upload_media_init"
CMD_UPLOAD_CHUNK = "aibot_upload_media_chunk"
CMD_UPLOAD_FINISH = "aibot_upload_media_finish"

STREAM_CONTENT_MAX_BYTES = 20480
STREAM_KEEPALIVE_INTERVAL_S = 240  # 4 minutes; WeCom expires streams at 6 min
MAX_INTERMEDIATE_STREAM_MSGS = 85  # WeCom SDK limit ~100 non-final; keep headroom

# WebSocket upload constraints (official protocol limits)
UPLOAD_CHUNK_MAX_BYTES = 512 * 1024  # 512KB raw per chunk (before base64)
UPLOAD_MAX_CHUNKS = 100
UPLOAD_SESSION_TIMEOUT = 30 * 60  # 30 minutes

# Upload size limits per media type (bytes)
UPLOAD_SIZE_LIMITS: dict[str, int] = {
    "image": 10 * 1024 * 1024,
    "voice": 2 * 1024 * 1024,
    "video": 10 * 1024 * 1024,
    "file": 20 * 1024 * 1024,
}
UPLOAD_ABSOLUTE_MAX = 20 * 1024 * 1024

# msg_item base64 image limit - DEPRECATED by official docs (2026/03).
# Kept for legacy fallback only; new code should use upload_media + media_id.
MSG_ITEM_IMAGE_MAX_BYTES = 200 * 1024
MSG_ITEM_IMAGE_MAX_WIDTH = 1920

# Message processing timeout (seconds)
MSG_PROCESS_TIMEOUT_S = 300  # 5 minutes

# Message dedup TTL (seconds)
DEDUP_TTL_S = 600  # 10 minutes

# Pending reply queue limits
PENDING_REPLY_TTL_S = 300  # 5 minutes
PENDING_REPLY_MAX = 50

# Rate limit tracking (aligned with OpenClaw / WeCom platform limits)
RATE_REPLY_PER_24H = 30      # replies per 24h sliding window per chat
RATE_ACTIVE_PER_DAY = 10     # active (bot-initiated) sends per day per chat
RATE_WARN_THRESHOLD = 0.8    # warn at 80% of limit


# ---------------------------------------------------------------------------
# req_id 生成
# ---------------------------------------------------------------------------
def _generate_req_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# AMR 转换
# ---------------------------------------------------------------------------
async def _ensure_amr(voice_path: str) -> str:
    """Ensure a voice file is in AMR format (required by WeCom voice API)."""
    path = Path(voice_path)
    if path.suffix.lower() == ".amr":
        return voice_path
    amr_path = str(path.with_suffix(".amr"))
    if Path(amr_path).exists() and Path(amr_path).stat().st_size > 0:
        return amr_path
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", voice_path,
        "-ar", "8000", "-ac", "1", "-ab", "12.2k",
        "-y", amr_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"AMR conversion failed: {stderr[:300]}")
    return amr_path


# ---------------------------------------------------------------------------
# 频率限制追踪器
# ---------------------------------------------------------------------------
class _RateLimitTracker:
    """Per-chat sliding-window rate limit tracker aligned with OpenClaw model.

    - Replies: 30 per 24-hour window per chat (window resets on new inbound)
    - Active sends (bot-initiated): 10 per calendar day per chat
    """

    def __init__(self) -> None:
        self._reply_buckets: dict[str, list[float]] = {}
        self._active_buckets: dict[str, list[float]] = {}
        self._max_chats = 500

    def _evict_if_full(self, buckets: dict[str, list[float]]) -> None:
        if len(buckets) >= self._max_chats:
            oldest = min(buckets, key=lambda k: buckets[k][-1] if buckets[k] else 0)
            del buckets[oldest]

    def record_reply(self, chat_id: str) -> None:
        """Record a reply (response to user message)."""
        now = time.time()
        if chat_id not in self._reply_buckets:
            self._evict_if_full(self._reply_buckets)
            self._reply_buckets[chat_id] = []
        self._reply_buckets[chat_id].append(now)

    def record_active(self, chat_id: str) -> None:
        """Record an active (bot-initiated) send."""
        now = time.time()
        if chat_id not in self._active_buckets:
            self._evict_if_full(self._active_buckets)
            self._active_buckets[chat_id] = []
        self._active_buckets[chat_id].append(now)

    def reset_reply_window(self, chat_id: str) -> None:
        """Reset the reply window on new inbound message (OpenClaw behavior)."""
        self._reply_buckets.pop(chat_id, None)

    def record(self, chat_id: str) -> None:
        """Legacy compat: record as active send."""
        self.record_active(chat_id)

    def check(self, chat_id: str) -> None:
        """Log warnings if approaching rate limits."""
        now = time.time()
        day_ago = now - 86400

        replies = self._reply_buckets.get(chat_id, [])
        if replies:
            recent = sum(1 for t in replies if t > day_ago)
            if recent >= int(RATE_REPLY_PER_24H * RATE_WARN_THRESHOLD):
                logger.warning(
                    f"[RateLimit] chat={chat_id}: {recent}/{RATE_REPLY_PER_24H} "
                    f"replies in 24h window, approaching limit"
                )
            self._reply_buckets[chat_id] = [t for t in replies if t > day_ago]

        actives = self._active_buckets.get(chat_id, [])
        if actives:
            recent = sum(1 for t in actives if t > day_ago)
            if recent >= int(RATE_ACTIVE_PER_DAY * RATE_WARN_THRESHOLD):
                logger.warning(
                    f"[RateLimit] chat={chat_id}: {recent}/{RATE_ACTIVE_PER_DAY} "
                    f"active sends today, approaching limit"
                )
            self._active_buckets[chat_id] = [t for t in actives if t > day_ago]


# ---------------------------------------------------------------------------
# 引用消息解析
# ---------------------------------------------------------------------------
def _parse_quote_content(body: dict) -> tuple[str | None, list[MediaFile]]:
    """Extract text and media from a body.quote object.

    Returns (quote_text, quote_media_list). quote_text is None if no quote.
    """
    quote = body.get("quote")
    if not quote or not isinstance(quote, dict):
        return None, []

    media_list: list[MediaFile] = []
    qtype = quote.get("msgtype", "")

    if qtype == "text" and quote.get("text", {}).get("content"):
        return quote["text"]["content"], media_list

    if qtype == "voice" and quote.get("voice", {}).get("content"):
        return quote["voice"]["content"], media_list

    if qtype == "image" and quote.get("image", {}).get("url"):
        img = quote["image"]
        m = MediaFile.create(filename="quote_image.jpg", mime_type="image/jpeg", url=img.get("url"))
        m.extra = {"aeskey": img.get("aeskey")}
        media_list.append(m)
        return "[引用图片]", media_list

    if qtype == "file" and quote.get("file", {}).get("url"):
        f = quote["file"]
        m = MediaFile.create(filename=f.get("filename", "file"), mime_type="application/octet-stream", url=f.get("url"))
        m.extra = {"aeskey": f.get("aeskey")}
        media_list.append(m)
        return f"[引用文件: {m.filename}]", media_list

    if qtype == "mixed":
        text_parts: list[str] = []
        for item in (quote.get("mixed", {}).get("msg_item") or []):
            if item.get("msgtype") == "text" and item.get("text", {}).get("content"):
                text_parts.append(item["text"]["content"])
            elif item.get("msgtype") == "image" and item.get("image", {}).get("url"):
                img = item["image"]
                m = MediaFile.create(filename="quote_image.jpg", mime_type="image/jpeg", url=img.get("url"))
                m.extra = {"aeskey": img.get("aeskey")}
                media_list.append(m)
        return "\n".join(text_parts) or "[引用图文]", media_list

    return None, []


# ---------------------------------------------------------------------------
# think 标签归一化 (D3)
# ---------------------------------------------------------------------------
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)


def _normalize_think_tags(text: str) -> str:
    """Normalize <think> tags: ensure every open tag is properly closed.

    WeCom client renders <think>...</think> as a collapsible thinking block.
    Unclosed or extra tags can break rendering.
    """
    if not text or "<think" not in text.lower():
        return text

    opens = len(_THINK_OPEN_RE.findall(text))
    closes = len(_THINK_CLOSE_RE.findall(text))

    if opens > closes:
        text += "</think>" * (opens - closes)
    elif closes > opens:
        text = "<think>" * (closes - opens) + text
    return text


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class WeWorkWsConfig:
    """企业微信 WebSocket 适配器配置"""
    bot_id: str
    secret: str
    ws_url: str = WS_DEFAULT_URL
    heartbeat_interval: float = 30.0
    max_missed_pong: int = 2
    max_reconnect_attempts: int = -1
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    reply_ack_timeout: float = 5.0
    max_reply_queue_size: int = 100

    def __post_init__(self) -> None:
        if not self.bot_id or not self.bot_id.strip():
            raise ValueError("WeWorkWsConfig: bot_id is required")
        if not self.secret or not self.secret.strip():
            raise ValueError("WeWorkWsConfig: secret is required")


# ---------------------------------------------------------------------------
# AES-256-CBC 文件解密 (per-file aeskey)
# ---------------------------------------------------------------------------
def _decrypt_file(encrypted: bytes, aes_key_b64: str) -> bytes:
    """解密企业微信文件 (AES-256-CBC, PKCS#7 pad to 32-byte block)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(aes_key_b64)
    if len(key) != 32:
        raise ValueError(f"AES key must be 32 bytes, got {len(key)}")
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    # PKCS#7 unpad (block_size=32)
    if not decrypted:
        raise ValueError("Decrypted data is empty")
    pad_len = decrypted[-1]
    if pad_len < 1 or pad_len > 32 or pad_len > len(decrypted):
        raise ValueError(f"Invalid PKCS#7 padding value: {pad_len}")
    for i in range(len(decrypted) - pad_len, len(decrypted)):
        if decrypted[i] != pad_len:
            raise ValueError("Invalid PKCS#7 padding: bytes mismatch")
    return decrypted[: len(decrypted) - pad_len]


# ---------------------------------------------------------------------------
# Webhook 辅助发送器（图片/语音/文件）
# ---------------------------------------------------------------------------
class _WebhookSender:
    """通过企微群机器人 Webhook URL 发送富媒体消息（图片/语音/文件）。

    自 2026/03 起，WS 长连接已支持通过分片上传协议直接发送媒体消息，
    因此 Webhook 现在作为 fallback 通道使用。

    Webhook API 参考:
    https://developer.work.weixin.qq.com/document/path/91770
    """

    def __init__(self, webhook_url: str):
        from urllib.parse import parse_qs, urlparse

        self._send_url = webhook_url
        parsed = urlparse(webhook_url)
        self._key = parse_qs(parsed.query).get("key", [""])[0]
        self._upload_url = (
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media"
            f"?key={self._key}&type={{media_type}}"
        )
        self._client: Any = None

    async def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            _import_httpx()
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    async def send_image(self, image_path: str) -> bool:
        """发送图片（base64 + md5，无需上传）。"""
        try:
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"[WebhookSender] Image not found: {image_path}")
                return False
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            md5 = hashlib.md5(data).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_image failed: {e}")
            return False

    async def send_voice(self, voice_path: str) -> bool:
        """发送语音（需先转 AMR 再 upload_media 获取 media_id）。"""
        try:
            amr_path = await self._ensure_amr(voice_path)
            media_id = await self._upload_media(amr_path, "voice")
            if not media_id:
                return False
            payload = {
                "msgtype": "voice",
                "voice": {"media_id": media_id},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_voice failed: {e}")
            return False

    async def send_file(self, file_path: str) -> bool:
        """发送文件（upload_media 获取 media_id）。"""
        try:
            media_id = await self._upload_media(file_path, "file")
            if not media_id:
                return False
            payload = {
                "msgtype": "file",
                "file": {"media_id": media_id},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_file failed: {e}")
            return False

    async def _upload_media(self, file_path: str, media_type: str) -> str | None:
        """上传媒体文件到企微获取 media_id。"""
        client = await self._ensure_client()
        url = self._upload_url.format(media_type=media_type)
        path = Path(file_path)
        try:
            files = {"media": (path.name, path.read_bytes())}
            resp = await client.post(url, files=files)
            result = resp.json()
            if result.get("errcode", 0) != 0:
                logger.error(
                    f"[WebhookSender] upload_media failed: "
                    f"{result.get('errcode')} {result.get('errmsg')}"
                )
                return None
            media_id = result.get("media_id", "")
            logger.info(f"[WebhookSender] Uploaded {media_type}: {path.name} → {media_id[:20]}...")
            return media_id
        except Exception as e:
            logger.error(f"[WebhookSender] upload_media error: {e}")
            return None

    async def _ensure_amr(self, voice_path: str) -> str:
        """确保语音文件为 AMR 格式（Webhook voice 要求 AMR）。"""
        return await _ensure_amr(voice_path)

    async def _post(self, payload: dict) -> bool:
        """发送 Webhook 请求。"""
        client = await self._ensure_client()
        try:
            resp = await client.post(self._send_url, json=payload)
            result = resp.json()
            if result.get("errcode", 0) != 0:
                logger.error(
                    f"[WebhookSender] Post failed: "
                    f"{result.get('errcode')} {result.get('errmsg')}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"[WebhookSender] Post error: {e}")
            return False

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class WeWorkWsAdapter(ChannelAdapter):
    """
    企业微信智能机器人 WebSocket 长连接适配器

    通过 WebSocket 与企业微信服务端保持长连接，实现:
    - 消息接收 (text/image/mixed/voice/file/video)
    - 流式回复 (stream) 和模板卡片回复
    - 事件接收 (enter_chat/template_card_event/feedback_event/disconnected_event)
    - 主动消息推送 (markdown/template_card/image/file/voice/video)
    - WebSocket 分片上传临时素材
    - 文件下载 + AES-256-CBC 解密
    """

    channel_name = "wework_ws"
    _THINK_TAG_NATIVE = True

    capabilities = {
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "send_video": True,
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
        bot_id: str,
        secret: str,
        ws_url: str = WS_DEFAULT_URL,
        media_dir: Path | None = None,
        *,
        channel_name: str | None = None,
        bot_id_alias: str | None = None,
        agent_profile_id: str = "default",
        webhook_url: str = "",
        welcome_message: str = "",
    ):
        super().__init__(
            channel_name=channel_name,
            bot_id=bot_id_alias,
            agent_profile_id=agent_profile_id,
        )

        self.config = WeWorkWsConfig(bot_id=bot_id, secret=secret, ws_url=ws_url)
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/wework_ws")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._welcome_message: str = welcome_message

        # Webhook 辅助发送器（用于发送图片/语音/文件）
        self._webhook: _WebhookSender | None = (
            _WebhookSender(webhook_url) if webhook_url else None
        )

        # WebSocket state
        self._ws: Any = None
        self._connection_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._authenticated = asyncio.Event()
        self._missed_pong = 0
        self._displaced = False  # set when disconnected_event received

        # reply ack
        self._pending_acks: dict[str, asyncio.Future] = {}
        self._reply_locks: dict[str, asyncio.Lock] = {}

        # message dedup: msgid → timestamp (TTL-based + size-limited)
        self._seen_msg_ids: OrderedDict[str, float] = OrderedDict()
        self._seen_msg_ids_max = 500

        # response_url cache: req_id → url
        self._response_urls: dict[str, str] = {}

        # thinking indicator: pre-created stream_id per req_id
        self._pre_streams: dict[str, str] = {}
        self._thinking_tasks: dict[str, asyncio.Task] = {}

        # queued image items (legacy msg_item, kept for fallback)
        self._pending_image_items: dict[str, list[dict]] = {}

        # queued media messages: send_image/send_file/send_voice queue media_id
        # messages here; _send_stream_reply sends them after the stream finishes
        self._pending_media_msgs: dict[str, list[dict]] = {}

        # per-peer serialization locks (A6)
        self._peer_locks: dict[str, asyncio.Lock] = {}

        # pending reply queue for retry on reconnect (A9)
        self._pending_replies: list[dict] = []

        # rate limit tracker (A10)
        self._rate_tracker = _RateLimitTracker()

        # D1: intermediate stream message counter per stream_id
        self._stream_msg_count: dict[str, int] = {}

        # D6: last stream send timestamp per stream_id (for smart keepalive)
        self._last_stream_sent: dict[str, float] = {}

        # background tasks ref holder
        self._bg_tasks: set[asyncio.Task] = set()

        # Auth failure tracking (instance-level to survive adapter restarts)
        self._consecutive_auth_failures: int = 0
        self._auth_disabled: bool = False

        # Streaming state (for gateway streaming path)
        self._chat_to_req: dict[str, str] = {}
        self._typing_start_time: dict[str, float] = {}
        self._streaming_thinking: dict[str, str] = {}
        self._streaming_chain: dict[str, list[str]] = {}
        self._streaming_buffers: dict[str, str] = {}
        self._streaming_last_patch: dict[str, float] = {}

    # ==================== Properties ====================

    @property
    def supports_streaming(self) -> bool:
        return True

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        return True

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        return chat_id

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        _import_websockets()
        if self._auth_disabled:
            raise ConnectionError(
                "企业微信 WebSocket 适配器因连续鉴权失败已被禁用。"
                "请检查 Bot ID 和 Secret 是否正确，修正后重启服务。"
            )
        self._running = True
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info(
            f"WeWork WS adapter starting, will connect to {self.config.ws_url}"
        )

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._connection_task:
            self._connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connection_task
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._webhook:
            await self._webhook.close()
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()

        self._reject_all_pending("adapter stopped")
        logger.info("WeWork WS adapter stopped")

    # ==================== Connection loop ====================

    async def _connection_loop(self) -> None:
        """Main connection loop with exponential back-off reconnect."""
        _MAX_AUTH_FAILURES = 3
        attempt = 0
        while self._running:
            if self._auth_disabled:
                logger.warning(
                    "[WeWork WS] Auth permanently failed, not reconnecting. "
                    "Fix bot_id/secret and restart."
                )
                return

            try:
                await self._connect_and_run()
                attempt = 0
                self._consecutive_auth_failures = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"WeWork WS connection error: {e}")

            if not self._running:
                return

            if self._displaced:
                logger.error(
                    "Bot was displaced by another connection (disconnected_event). "
                    "Stopping reconnect to avoid infinite loop."
                )
                return

            if getattr(self, "_auth_fatal", False):
                self._consecutive_auth_failures += 1
                if self._consecutive_auth_failures >= _MAX_AUTH_FAILURES:
                    self._auth_disabled = True
                    last_err = getattr(self, '_auth_error', '?')
                    reason = (
                        f"连续 {self._consecutive_auth_failures} 次认证失败 "
                        f"(错误: {last_err})。"
                        "请检查企业微信 Bot ID / Secret 配置"
                    )
                    logger.error(f"[WeWork WS] {reason}")
                    self._running = False
                    self._report_failure(reason)
                    return

            # check max reconnect
            max_att = self.config.max_reconnect_attempts
            if max_att != -1 and attempt >= max_att:
                logger.error(
                    f"Max reconnect attempts ({max_att}) reached, giving up"
                )
                return

            attempt += 1
            delay = min(
                self.config.reconnect_base_delay * (2 ** (attempt - 1)),
                self.config.reconnect_max_delay,
            )
            logger.info(f"Reconnecting in {delay:.1f}s (attempt {attempt})...")
            await asyncio.sleep(delay)

    async def _connect_and_run(self) -> None:
        """Single connection lifetime: connect → auth → heartbeat + receive."""
        self._authenticated.clear()
        self._missed_pong = 0
        self._auth_error: str | None = None
        self._auth_fatal: bool = False
        self._reject_all_pending("reconnecting")

        async with websockets.connect(
            self.config.ws_url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            logger.info(f"WebSocket connected to {self.config.ws_url}")

            receive_task = asyncio.create_task(self._receive_loop(ws))

            await self._send_auth()
            try:
                await asyncio.wait_for(
                    self._authenticated.wait(), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.error("Authentication timeout (10s)")
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
                raise ConnectionError(
                    self._auth_error or "Authentication timeout"
                )

            logger.info("WebSocket authenticated successfully")

            # Flush any pending replies from previous disconnection
            await self._flush_pending_replies()

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                await receive_task
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._heartbeat_task
                self._ws = None

    # ==================== Auth ====================

    async def _send_auth(self) -> None:
        frame = {
            "cmd": CMD_SUBSCRIBE,
            "headers": {"req_id": _generate_req_id(CMD_SUBSCRIBE)},
            "body": {
                "bot_id": self.config.bot_id,
                "secret": self.config.secret,
            },
        }
        await self._ws_send(frame)
        logger.debug("Auth frame sent")

    # ==================== Heartbeat ====================

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat every interval; kill connection on too many missed pongs."""
        try:
            while self._running and self._ws:
                await asyncio.sleep(self.config.heartbeat_interval)

                if self._missed_pong >= self.config.max_missed_pong:
                    logger.warning(
                        f"No heartbeat ack for {self._missed_pong} pings, "
                        "connection considered dead"
                    )
                    if self._ws:
                        await self._ws.close()
                    return

                self._missed_pong += 1
                frame = {
                    "cmd": CMD_HEARTBEAT,
                    "headers": {"req_id": _generate_req_id(CMD_HEARTBEAT)},
                }
                try:
                    await self._ws_send(frame)
                except Exception as e:
                    logger.error(f"Failed to send heartbeat: {e}")
                    return
        except asyncio.CancelledError:
            return

    # ==================== Receive loop ====================

    async def _receive_loop(self, ws) -> None:
        """Read frames and route them."""
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from WS: {raw!r:.200}")
                    continue
                try:
                    await self._route_frame(frame)
                except Exception as e:
                    logger.error(f"Error routing frame: {e}", exc_info=True)
        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket closed: {e}")
        except asyncio.CancelledError:
            raise

    # ==================== Frame router ====================

    async def _route_frame(self, frame: dict) -> None:
        cmd = frame.get("cmd")
        req_id: str = frame.get("headers", {}).get("req_id", "")

        # 1. Message callback (with timeout protection)
        if cmd == CMD_CALLBACK:
            task = asyncio.create_task(self._handle_msg_callback_safe(frame))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return

        # 2. Event callback
        if cmd == CMD_EVENT_CALLBACK:
            task = asyncio.create_task(self._handle_event_callback(frame))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return

        # 3. No cmd → ack / auth response / heartbeat response
        if cmd is None or cmd == "":
            # reply ack
            if req_id in self._pending_acks:
                fut = self._pending_acks.pop(req_id)
                if not fut.done():
                    fut.set_result(frame)
                return

            errcode = frame.get("errcode")

            # auth response
            if req_id.startswith(CMD_SUBSCRIBE):
                if errcode == 0:
                    self._authenticated.set()
                else:
                    errmsg = frame.get("errmsg", "unknown")
                    self._auth_error = f"{errcode} {errmsg}"
                    logger.error(f"Auth failed: {errcode} {errmsg}")
                    _FATAL_AUTH_CODES = {600041, 600042, 600043}
                    if errcode in _FATAL_AUTH_CODES:
                        self._auth_fatal = True
                return

            # heartbeat response
            if req_id.startswith(CMD_HEARTBEAT):
                if errcode == 0:
                    self._missed_pong = 0
                return

        logger.debug(f"Unhandled frame cmd={cmd} req_id={req_id}")

    # ==================== Message handling ====================

    async def _handle_msg_callback_safe(self, frame: dict) -> None:
        """Timeout-protected wrapper around _handle_msg_callback."""
        msgid = frame.get("body", {}).get("msgid", "unknown")
        try:
            await asyncio.wait_for(
                self._handle_msg_callback(frame),
                timeout=MSG_PROCESS_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Message processing timed out ({MSG_PROCESS_TIMEOUT_S}s), "
                f"msgid={msgid}"
            )
            req_id = frame.get("headers", {}).get("req_id", "")
            if req_id:
                await self._send_error_finish(req_id, "处理超时，请重新发送。")
        except Exception as e:
            # D4: general exception fallback — always close the stream
            logger.error(f"Message processing failed: {e}", exc_info=True)
            req_id = frame.get("headers", {}).get("req_id", "")
            if req_id:
                await self._send_error_finish(req_id, f"处理出错：{type(e).__name__}")

    async def _send_error_finish(self, req_id: str, error_text: str) -> None:
        """Send a finish=true stream with error text, suppressing all exceptions."""
        try:
            self._cancel_thinking_task(req_id)
            stream_id = self._pre_streams.pop(req_id, None) or secrets.token_hex(16)
            body: dict = {
                "msgtype": "stream",
                "stream": {"id": stream_id, "finish": True, "content": error_text},
            }
            await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
        except Exception:
            pass

    async def _handle_msg_callback(self, frame: dict) -> None:
        body: dict = frame.get("body", {})
        req_id: str = frame.get("headers", {}).get("req_id", "")
        msgid = body.get("msgid", "")

        # TTL-based dedup (A12)
        now = time.time()
        if msgid in self._seen_msg_ids:
            ts = self._seen_msg_ids[msgid]
            if now - ts < DEDUP_TTL_S:
                logger.debug(f"Duplicate msgid={msgid}, skipping")
                return
        self._seen_msg_ids[msgid] = now
        self._prune_seen_msg_ids(now)

        # cache response_url
        response_url = body.get("response_url")
        if response_url and req_id:
            self._response_urls[req_id] = response_url
            self._cleanup_response_urls()

        msgtype = body.get("msgtype", "")
        chattype = body.get("chattype", "single")
        chat_type = "group" if chattype == "group" else "private"
        from_user = body.get("from", {}).get("userid", "unknown")
        chat_id = body.get("chatid", from_user)

        # parse content + quote (A2)
        content, media_list = self._parse_content(body, msgtype)
        quote_text, quote_media = _parse_quote_content(body)
        if quote_text and content.text:
            if quote_text != content.text:
                content = MessageContent(
                    text=f"> {quote_text}\n\n{content.text}",
                    images=content.images,
                    files=content.files,
                )
            media_list.extend(quote_media)
        elif quote_text and not content.text:
            content = MessageContent(
                text=quote_text,
                images=(content.images or []) + [m for m in quote_media if m.mime_type and m.mime_type.startswith("image/")],
                files=content.files,
            )
            media_list.extend(quote_media)

        is_mentioned = True
        is_direct = chat_type == "private"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{from_user}",
            channel_user_id=from_user,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct,
            raw=body,
            metadata={
                "req_id": req_id,
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": body.get("chatname", ""),
            },
        )

        self._log_message(unified)

        # D9: reset reply rate window on new inbound message
        self._rate_tracker.reset_reply_window(chat_id)

        # Store chat_id -> req_id mapping for streaming methods
        self._chat_to_req[chat_id] = req_id
        self._typing_start_time[chat_id] = time.time()

        # Per-peer serialization lock (A6): serialize messages from the same chat
        lock = self._get_peer_lock(chat_id)
        async with lock:
            # thinking indicator MUST be sent before _emit_message
            await self._maybe_send_thinking_indicator(req_id)
            await self._emit_message(unified)

    def _prune_seen_msg_ids(self, now: float) -> None:
        """Remove expired and excess dedup entries."""
        # remove expired by TTL
        while self._seen_msg_ids:
            oldest_key, oldest_ts = next(iter(self._seen_msg_ids.items()))
            if now - oldest_ts >= DEDUP_TTL_S:
                self._seen_msg_ids.popitem(last=False)
            else:
                break
        # cap by size
        while len(self._seen_msg_ids) > self._seen_msg_ids_max:
            self._seen_msg_ids.popitem(last=False)

    def _get_peer_lock(self, chat_id: str) -> asyncio.Lock:
        """Get or create a per-peer lock, pruning if too many."""
        if chat_id not in self._peer_locks:
            if len(self._peer_locks) > 500:
                oldest = next(iter(self._peer_locks))
                del self._peer_locks[oldest]
            self._peer_locks[chat_id] = asyncio.Lock()
        return self._peer_locks[chat_id]

    def _parse_content(
        self, body: dict, msgtype: str
    ) -> tuple[MessageContent, list[MediaFile]]:
        """Parse message body into MessageContent + media list."""
        media_list: list[MediaFile] = []

        if msgtype == "text":
            text_data = body.get("text", {})
            return MessageContent(text=text_data.get("content", "")), media_list

        if msgtype == "image":
            img = body.get("image", {})
            media = MediaFile.create(
                filename="image.jpg",
                mime_type="image/jpeg",
                url=img.get("url"),
            )
            media.extra = {"aeskey": img.get("aeskey")}
            media_list.append(media)
            return MessageContent(images=[media]), media_list

        if msgtype == "mixed":
            mixed_data = body.get("mixed", {})
            items = mixed_data.get("msg_item", [])
            text_parts: list[str] = []
            images: list[MediaFile] = []
            for item in items:
                item_type = item.get("msgtype", "")
                if item_type == "text":
                    text_parts.append(item.get("text", {}).get("content", ""))
                elif item_type == "image":
                    img_data = item.get("image", {})
                    media = MediaFile.create(
                        filename=f"image_{len(images)}.jpg",
                        mime_type="image/jpeg",
                        url=img_data.get("url"),
                    )
                    media.extra = {"aeskey": img_data.get("aeskey")}
                    images.append(media)
                    media_list.append(media)
            return (
                MessageContent(text="\n".join(text_parts) or None, images=images),
                media_list,
            )

        if msgtype == "voice":
            voice_data = body.get("voice", {})
            platform_text = voice_data.get("content", "").strip()
            if platform_text:
                return (MessageContent(text=platform_text), media_list)
            logger.warning(
                "[WeWorkWS] Voice transcription empty, msgid=%s",
                body.get("msgid"),
            )
            return (
                MessageContent(
                    text="[语音消息，平台未能识别，请重新发送或改用文字]"
                ),
                media_list,
            )

        if msgtype == "file":
            file_data = body.get("file", {})
            media = MediaFile.create(
                filename=file_data.get("filename", "file"),
                mime_type="application/octet-stream",
                url=file_data.get("url"),
            )
            media.extra = {"aeskey": file_data.get("aeskey")}
            media_list.append(media)
            return MessageContent(files=[media]), media_list

        if msgtype == "video":
            video_data = body.get("video", {})
            media = MediaFile.create(
                filename=video_data.get("filename", "video.mp4"),
                mime_type="video/mp4",
                url=video_data.get("url"),
            )
            media.extra = {"aeskey": video_data.get("aeskey")}
            media_list.append(media)
            return MessageContent(files=[media]), media_list

        logger.debug(f"Unhandled msgtype: {msgtype}")
        return MessageContent(text=f"[不支持的消息类型: {msgtype}]"), media_list

    # ==================== Event handling ====================

    async def _handle_event_callback(self, frame: dict) -> None:
        body: dict = frame.get("body", {})
        req_id: str = frame.get("headers", {}).get("req_id", "")
        event_data = body.get("event", {})
        event_type = event_data.get("eventtype", "")

        logger.info(f"Event received: {event_type}")

        if event_type == "enter_chat":
            # Send welcome message if configured (A7)
            if self._welcome_message and req_id and self._ws:
                try:
                    welcome_body: dict = {
                        "msgtype": "text",
                        "text": {"content": self._welcome_message},
                    }
                    await self._send_reply_with_ack(req_id, welcome_body, CMD_RESPONSE_WELCOME)
                    logger.info("Welcome message sent for enter_chat")
                except Exception as e:
                    logger.warning(f"Failed to send welcome message: {e}")

            await self._emit_event("enter_chat", {
                "req_id": req_id,
                "chatid": body.get("chatid", ""),
                "chattype": body.get("chattype", ""),
                "userid": body.get("from", {}).get("userid", ""),
                "aibotid": body.get("aibotid", ""),
            })
        elif event_type == "template_card_event":
            await self._emit_event("template_card_event", {
                "req_id": req_id,
                "event_key": event_data.get("event_key", ""),
                "task_id": event_data.get("task_id", ""),
                "chatid": body.get("chatid", ""),
                "userid": body.get("from", {}).get("userid", ""),
            })
        elif event_type == "feedback_event":
            await self._emit_event("feedback_event", {
                "req_id": req_id,
                "chatid": body.get("chatid", ""),
                "userid": body.get("from", {}).get("userid", ""),
                "raw": body,
            })
        elif event_type == "disconnected_event":
            # A4: mark displaced to prevent infinite reconnect loop
            self._displaced = True
            self._running = False
            logger.error(
                "Received disconnected_event: another connection took over. "
                "Stopping reconnect to avoid infinite loop. "
                "Only one active connection per botId is allowed."
            )
            if self._ws:
                await self._ws.close()
        else:
            logger.debug(f"Unhandled event type: {event_type}")

    # ==================== Thinking indicator ====================

    async def _maybe_send_thinking_indicator(self, req_id: str) -> None:
        """Pre-send an animated 'thinking' stream and start a counting task."""
        from openakita.config import settings

        if not getattr(settings, "wework_ws_thinking_indicator", True):
            return
        if not req_id or not self._ws:
            return

        stream_id = secrets.token_hex(16)
        body: dict = {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": False,
                "content": "思考中...",
            },
        }
        try:
            await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
            self._pre_streams[req_id] = stream_id
            # D1/D6: initialize stream tracking for thinking indicator
            self._stream_msg_count[stream_id] = 1
            self._last_stream_sent[stream_id] = time.time()
        except Exception as e:
            logger.debug(f"Thinking indicator send failed (non-fatal): {e}")
            return

        # Start animated counter task (A8)
        task = asyncio.create_task(
            self._thinking_counter_loop(req_id, stream_id)
        )
        self._thinking_tasks[req_id] = task

    async def _thinking_counter_loop(self, req_id: str, stream_id: str) -> None:
        """Send periodic 'waiting N s' updates until cancelled.

        Sends a stream update every SEND_INTERVAL seconds (not every second)
        to avoid hitting WeCom's undocumented per-stream rate/count limits.
        Tolerates up to MAX_SEND_FAILURES consecutive send failures before
        giving up.
        """
        SEND_INTERVAL = 5
        MAX_SEND_FAILURES = 3
        seconds = 1
        consecutive_failures = 0
        try:
            while self._ws and req_id in self._pre_streams:
                await asyncio.sleep(1.0)
                seconds += 1
                if req_id not in self._pre_streams:
                    break
                if seconds % SEND_INTERVAL != 0:
                    continue
                # D1: respect intermediate stream message limit
                count = self._stream_msg_count.get(stream_id, 0)
                if count >= MAX_INTERMEDIATE_STREAM_MSGS:
                    logger.debug(f"[thinking] Stream {stream_id[:8]} hit intermediate limit, stopping counter")
                    break
                content = "思考中..."
                body: dict = {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "finish": False, "content": content},
                }
                try:
                    await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
                    self._stream_msg_count[stream_id] = count + 1
                    self._last_stream_sent[stream_id] = time.time()
                    consecutive_failures = 0
                except Exception as e:
                    consecutive_failures += 1
                    logger.debug(f"[thinking] Counter send failed (sec={seconds}): {e}")
                    if consecutive_failures >= MAX_SEND_FAILURES:
                        logger.debug(f"[thinking] {MAX_SEND_FAILURES} consecutive failures, stopping counter")
                        break
        except asyncio.CancelledError:
            pass
        finally:
            self._thinking_tasks.pop(req_id, None)

    def _cancel_thinking_task(self, req_id: str) -> None:
        """Cancel the animated thinking counter for a given req_id."""
        task = self._thinking_tasks.pop(req_id, None)
        if task and not task.done():
            task.cancel()

    # ==================== Streaming display ====================

    _STREAM_THINKING_THROTTLE = 2.0
    _STREAM_TOKEN_THROTTLE = 0.8
    _STREAM_MSG_RESERVE = 5  # reserve for finalize_stream finish=true frame

    def _compose_stream_display(self, sk: str) -> str:
        """Build display content from accumulated thinking + chain + reply."""
        thinking = self._streaming_thinking.get(sk, "")
        chain = self._streaming_chain.get(sk, [])
        reply = self._streaming_buffers.get(sk, "")

        think_parts: list[str] = []
        if thinking:
            preview = thinking.strip().replace("\n", " ")[:200]
            if len(thinking) > 200:
                preview += "..."
            think_parts.append(f"💭 {preview}")
        if chain:
            think_parts.extend(chain[-8:])

        content = ""
        if think_parts:
            content = "<think>\n" + "\n".join(think_parts) + "\n</think>\n"
        if reply:
            content += reply
        return content or "<think>处理中...</think>"

    async def _update_stream(
        self, req_id: str, stream_id: str, content: str,
    ) -> bool:
        """Send a non-final stream frame. Returns True on success."""
        count = self._stream_msg_count.get(stream_id, 0)
        if count >= MAX_INTERMEDIATE_STREAM_MSGS - self._STREAM_MSG_RESERVE:
            return False
        encoded = content.encode("utf-8")
        if len(encoded) > STREAM_CONTENT_MAX_BYTES:
            content = encoded[:STREAM_CONTENT_MAX_BYTES].decode("utf-8", errors="ignore")
        body: dict = {
            "msgtype": "stream",
            "stream": {"id": stream_id, "finish": False, "content": content},
        }
        try:
            await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
            self._stream_msg_count[stream_id] = count + 1
            self._last_stream_sent[stream_id] = time.time()
            return True
        except Exception as e:
            logger.debug(f"[streaming] Update failed (non-fatal): {e}")
            return False

    async def stream_thinking(
        self,
        chat_id: str,
        thinking_text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
        duration_ms: int | None = None,
    ) -> None:
        """Receive thinking content delta and update the stream display."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_thinking[sk] = self._streaming_thinking.get(sk, "") + thinking_text
        req_id = self._chat_to_req.get(chat_id)
        if not req_id:
            return
        self._cancel_thinking_task(req_id)
        now = time.time()
        last = self._streaming_last_patch.get(sk, 0)
        if now - last < self._STREAM_THINKING_THROTTLE:
            return
        stream_id = self._pre_streams.get(req_id)
        if stream_id:
            display = self._compose_stream_display(sk)
            if await self._update_stream(req_id, stream_id, display):
                self._streaming_last_patch[sk] = now

    async def stream_chain_text(
        self,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Append a tool progress line and update the stream display."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_chain.setdefault(sk, []).append(text)
        req_id = self._chat_to_req.get(chat_id)
        if not req_id:
            return
        self._cancel_thinking_task(req_id)
        now = time.time()
        last = self._streaming_last_patch.get(sk, 0)
        if now - last < self._STREAM_THINKING_THROTTLE:
            return
        stream_id = self._pre_streams.get(req_id)
        if stream_id:
            display = self._compose_stream_display(sk)
            if await self._update_stream(req_id, stream_id, display):
                self._streaming_last_patch[sk] = now

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Accumulate a reply token and periodically update the stream."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_buffers[sk] = self._streaming_buffers.get(sk, "") + token
        req_id = self._chat_to_req.get(chat_id)
        if not req_id:
            return
        self._cancel_thinking_task(req_id)
        now = time.time()
        last = self._streaming_last_patch.get(sk, 0)
        if now - last < self._STREAM_TOKEN_THROTTLE:
            return
        stream_id = self._pre_streams.get(req_id)
        if stream_id:
            display = self._compose_stream_display(sk)
            if await self._update_stream(req_id, stream_id, display):
                self._streaming_last_patch[sk] = now

    def _cleanup_streaming_state(self, chat_id: str, sk: str) -> None:
        """Remove all streaming state entries for a given session."""
        self._chat_to_req.pop(chat_id, None)
        self._streaming_thinking.pop(sk, None)
        self._streaming_chain.pop(sk, None)
        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)
        self._typing_start_time.pop(sk, None)

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """Send the final stream frame with integrated thinking/chain/reply."""
        sk = self._make_session_key(chat_id, thread_id)
        req_id = self._chat_to_req.get(chat_id)
        if not req_id:
            return False

        self._cancel_thinking_task(req_id)

        thinking = self._streaming_thinking.get(sk, "")
        chain = self._streaming_chain.get(sk, [])

        if thinking or chain:
            think_lines: list[str] = []
            if thinking:
                preview = thinking.strip()[:500]
                if len(preview) < len(thinking.strip()):
                    preview += "..."
                think_lines.append(f"💭 {preview}")
            think_lines.extend(chain)
            final_text = "<think>\n" + "\n".join(think_lines) + "\n</think>\n" + final_text

        start = self._typing_start_time.get(sk)
        if start:
            elapsed = time.time() - start
            final_text += f"\n\n---\n⏱ {elapsed:.1f}s"

        msg = OutgoingMessage.text(
            chat_id=chat_id, text=final_text,
            metadata={"req_id": req_id},
        )
        try:
            await self._send_stream_reply(req_id, final_text, msg)
            return True
        except Exception as e:
            logger.error(f"[finalize_stream] Failed: {e}")
            return False
        finally:
            self._cleanup_streaming_state(chat_id, sk)

    # ==================== Sending ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """Send a message (reply via stream or active push via markdown)."""
        text = message.content.text or ""
        chat_id = message.chat_id
        req_id = message.metadata.get("req_id", "")

        # Determine chat_type from metadata for active push
        is_group = message.metadata.get("is_group", False)
        chat_type = 2 if is_group else 1

        # If we have a req_id, this is a reply to an incoming message
        if req_id:
            return await self._send_stream_reply(req_id, text, message)

        # Otherwise, active push
        return await self._send_active_message(chat_id, text, chat_type=chat_type)

    async def send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send image: prefer WS upload, fallback to webhook, then markdown hint."""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"[send_image] Image file not found: {path}")
            return ""

        req_id = (kwargs.get("metadata") or {}).get("req_id", "")

        # 1. WS upload + media_id (preferred, works in reply and active push)
        if self._ws:
            try:
                media_id = await self._ws_upload_media(path, "image/jpeg")
                if req_id:
                    self._pending_media_msgs.setdefault(req_id, []).append(
                        {"msgtype": "image", "image": {"media_id": media_id}}
                    )
                    logger.info(
                        f"[send_image] Queued image media_id for reply: "
                        f"req_id={req_id}, file={path.name}"
                    )
                    return f"queued:{req_id}"
                else:
                    return await self._send_active_media_message(
                        chat_id, "image", {"media_id": media_id}
                    )
            except Exception as e:
                logger.warning(f"[send_image] WS upload failed, trying fallback: {e}")

        # 2. Webhook fallback
        if self._webhook:
            ok = await self._webhook.send_image(image_path)
            if ok:
                logger.info(f"[send_image] Sent via webhook: {path.name}")
                return "webhook:image_sent"

        # 3. Markdown hint fallback
        label = caption or path.name
        if req_id:
            logger.info(f"[send_image] All methods failed, skipping: {path.name}")
            return f"skipped:{req_id}"
        desc = f"> **{label}**\n> （图片发送失败，请稍后重试）"
        return await self._send_active_message(chat_id, desc)

    @staticmethod
    def _compress_image(path: Path) -> bytes:
        """Compress image for msg_item: resize + JPEG conversion if oversized."""
        raw = path.read_bytes()
        if len(raw) <= MSG_ITEM_IMAGE_MAX_BYTES:
            return raw
        try:
            import io
            from PIL import Image

            img = Image.open(path)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            if img.width > MSG_ITEM_IMAGE_MAX_WIDTH:
                ratio = MSG_ITEM_IMAGE_MAX_WIDTH / img.width
                img = img.resize(
                    (MSG_ITEM_IMAGE_MAX_WIDTH, int(img.height * ratio)),
                    Image.LANCZOS,
                )
            quality = 85
            for _ in range(5):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                if len(data) <= MSG_ITEM_IMAGE_MAX_BYTES:
                    logger.info(
                        f"[compress_image] {path.name}: "
                        f"{len(raw)} -> {len(data)} bytes (q={quality})"
                    )
                    return data
                quality -= 10
            logger.info(
                f"[compress_image] {path.name}: "
                f"{len(raw)} -> {len(data)} bytes (best effort, q={quality + 10})"
            )
            return data
        except Exception as e:
            logger.warning(f"[compress_image] Failed to compress {path.name}: {e}")
            return raw

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> str:
        """Send a file: prefer WS upload, fallback to webhook, then markdown hint."""
        path = Path(file_path)
        req_id = (kwargs.get("metadata") or {}).get("req_id", "")

        # 1. WS upload + media_id
        if self._ws:
            try:
                media_id = await self._ws_upload_media(path, "application/octet-stream")
                if req_id:
                    self._pending_media_msgs.setdefault(req_id, []).append(
                        {"msgtype": "file", "file": {"media_id": media_id}}
                    )
                    logger.info(
                        f"[send_file] Queued file media_id for reply: "
                        f"req_id={req_id}, file={path.name}"
                    )
                    return f"queued:{req_id}"
                else:
                    return await self._send_active_media_message(
                        chat_id, "file", {"media_id": media_id}
                    )
            except Exception as e:
                logger.warning(f"[send_file] WS upload failed, trying fallback: {e}")

        # 2. Webhook fallback
        if self._webhook:
            ok = await self._webhook.send_file(file_path)
            if ok:
                logger.info(f"[send_file] Sent via webhook: {path.name}")
                return "webhook:file_sent"

        # 3. Markdown hint fallback
        try:
            size_bytes = path.stat().st_size
            size_str = (
                f"{size_bytes / (1024 * 1024):.1f}MB"
                if size_bytes >= 1024 * 1024
                else f"{size_bytes / 1024:.0f}KB"
            )
        except OSError:
            size_str = ""
        label = caption or path.name
        size_part = f" ({size_str})" if size_str else ""
        desc = f"> **{label}**{size_part}\n> （文件发送失败，请稍后重试）"
        return await self._send_active_message(chat_id, desc)

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send voice: prefer WS upload (AMR only), fallback to file type (A11)."""
        path = Path(voice_path)
        req_id = (kwargs.get("metadata") or {}).get("req_id", "")

        # 1. WS upload (requires AMR format)
        if self._ws:
            try:
                amr_path = path
                if path.suffix.lower() != ".amr":
                    amr_path = Path(await _ensure_amr(str(path)))
                media_id = await self._ws_upload_media(amr_path, "audio/amr")
                if req_id:
                    self._pending_media_msgs.setdefault(req_id, []).append(
                        {"msgtype": "voice", "voice": {"media_id": media_id}}
                    )
                    return f"queued:{req_id}"
                else:
                    return await self._send_active_media_message(
                        chat_id, "voice", {"media_id": media_id}
                    )
            except RuntimeError as e:
                # AMR conversion failed (ffmpeg unavailable etc.) — downgrade to file (A11)
                logger.warning(
                    f"[send_voice] AMR conversion failed, downgrading to file: {e}"
                )
                return await self.send_file(
                    chat_id, voice_path, caption=caption or path.name, **kwargs
                )
            except Exception as e:
                logger.warning(f"[send_voice] WS upload failed, trying fallback: {e}")

        # 2. Webhook fallback
        if self._webhook:
            try:
                ok = await self._webhook.send_voice(voice_path)
                if ok:
                    logger.info(f"[send_voice] Sent via webhook: {path.name}")
                    return "webhook:voice_sent"
            except RuntimeError:
                logger.warning("[send_voice] Webhook AMR conversion also failed, sending as file")
                return await self.send_file(
                    chat_id, voice_path, caption=caption or path.name, **kwargs
                )

        # 3. Last resort: send as file instead of raising
        logger.warning("[send_voice] All voice methods failed, sending as file fallback")
        return await self.send_file(
            chat_id, voice_path, caption=caption or path.name, **kwargs
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send video via WS upload."""
        path = Path(video_path)
        req_id = (kwargs.get("metadata") or {}).get("req_id", "")

        if self._ws:
            try:
                media_id = await self._ws_upload_media(path, "video/mp4")
                media_body = {"media_id": media_id}
                if caption:
                    media_body["title"] = caption[:64]
                if req_id:
                    self._pending_media_msgs.setdefault(req_id, []).append(
                        {"msgtype": "video", "video": media_body}
                    )
                    return f"queued:{req_id}"
                else:
                    return await self._send_active_media_message(
                        chat_id, "video", media_body
                    )
            except Exception as e:
                logger.error(f"[send_video] WS upload failed: {e}")

        raise NotImplementedError(
            "Video send failed: WS upload unavailable"
        )

    async def _send_stream_reply(
        self, req_id: str, text: str, message: OutgoingMessage
    ) -> str:
        """Send a stream reply for an incoming message.

        After the stream finishes, any queued media messages (images/files/voice
        uploaded via _ws_upload_media) are sent as separate reply messages.
        Includes a keepalive timer (A1) to prevent 6-minute stream expiry.
        """
        # Cancel animated thinking counter (A8)
        self._cancel_thinking_task(req_id)

        pre_stream_id = self._pre_streams.pop(req_id, None)
        legacy_msg_items = self._pending_image_items.pop(req_id, [])

        # If a pre-created stream has expired (>5.5 min since last send),
        # skip stream protocol entirely and use response_url fallback.
        _STREAM_EXPIRY_S = 330  # 5.5 min safety margin (WeCom hard limit is 6 min)
        if pre_stream_id:
            last_sent = self._last_stream_sent.get(pre_stream_id, 0)
            if last_sent and (time.time() - last_sent) > _STREAM_EXPIRY_S:
                logger.warning(
                    f"[stream_reply] Pre-created stream {pre_stream_id[:8]} expired "
                    f"({time.time() - last_sent:.0f}s since last send), using fallback"
                )
                self._stream_msg_count.pop(pre_stream_id, None)
                self._last_stream_sent.pop(pre_stream_id, None)
                ok = await self._response_url_fallback(req_id, text)
                if ok:
                    pending_media = self._pending_media_msgs.pop(req_id, [])
                    for media_msg in pending_media:
                        try:
                            await self._send_reply_with_ack(req_id, media_msg, CMD_RESPONSE)
                        except Exception:
                            pass
                    return f"fallback:{req_id}"
                self._enqueue_pending_reply(req_id, text, message)
                raise RuntimeError("WeWorkWS: stream expired and fallback failed")

        # Legacy compatibility: if a thinking pre-stream exists and the reply
        # must carry msg_item payloads, close the old stream and start a fresh one.
        if pre_stream_id and legacy_msg_items:
            try:
                await self._send_reply_with_ack(
                    req_id,
                    {
                        "msgtype": "stream",
                        "stream": {
                            "id": pre_stream_id,
                            "finish": True,
                            "content": "",
                        },
                    },
                    CMD_RESPONSE,
                )
            except Exception as e:
                logger.debug(f"[stream_reply] Failed to close pre-stream cleanly: {e}")
            self._stream_msg_count.pop(pre_stream_id, None)
            self._last_stream_sent.pop(pre_stream_id, None)
            pre_stream_id = None

        stream_id = pre_stream_id or secrets.token_hex(16)

        # D3: normalize think tags before sending
        text = _normalize_think_tags(text)

        encoded = text.encode("utf-8")

        # Collect queued media messages (uploaded via send_image/send_file/send_voice)
        pending_media = self._pending_media_msgs.pop(req_id, [])

        # Also handle OutgoingMessage images: upload them now if not already queued
        for media in (message.content.images or [])[:10]:
            if media.local_path:
                try:
                    media_id = await self._ws_upload_media(
                        Path(media.local_path), media.mime_type or "image/jpeg"
                    )
                    pending_media.append(
                        {"msgtype": "image", "image": {"media_id": media_id}}
                    )
                except Exception as e:
                    logger.warning(f"Failed to upload image {media.local_path}: {e}")

        # Split text into chunks
        chunks = []
        offset = 0
        while offset < len(encoded):
            chunk = encoded[offset : offset + STREAM_CONTENT_MAX_BYTES]
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                chunk = chunk[:-1]
                while chunk and chunk[-1] & 0xC0 == 0x80:
                    chunk = chunk[:-1]
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="ignore"))
            offset += len(chunk)

        if not chunks:
            chunks = [""]

        # Start keepalive timer (A1): prevent 6-minute stream expiry
        keepalive_task = asyncio.create_task(
            self._stream_keepalive_loop(req_id, stream_id, text)
        )

        try:
            for i, chunk_text in enumerate(chunks):
                is_last = i == len(chunks) - 1
                # D1: check intermediate stream message limit for non-final chunks
                if not is_last:
                    count = self._stream_msg_count.get(stream_id, 0)
                    if count >= MAX_INTERMEDIATE_STREAM_MSGS:
                        logger.warning(f"[stream_reply] Hit intermediate limit at chunk {i}, forcing finish")
                        is_last = True
                body: dict = {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": is_last,
                        "content": chunk_text,
                    },
                }
                if is_last and legacy_msg_items:
                    body["stream"]["msg_item"] = legacy_msg_items
                try:
                    await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
                    if not is_last:
                        self._stream_msg_count[stream_id] = self._stream_msg_count.get(stream_id, 0) + 1
                    self._last_stream_sent[stream_id] = time.time()
                except Exception as e:
                    logger.error(f"Stream reply failed at chunk {i}: {e}")
                    if i == 0:
                        ok = await self._response_url_fallback(req_id, text)
                        if ok:
                            logger.info(f"[stream_reply] Fallback via response_url succeeded, skipping raise")
                            return
                        self._enqueue_pending_reply(req_id, text, message)
                    raise RuntimeError(
                        f"WeWorkWS: stream reply failed at chunk {i}"
                    ) from e
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task
            # Clean up D1/D6 per-stream state
            self._stream_msg_count.pop(stream_id, None)
            self._last_stream_sent.pop(stream_id, None)

        # D9: track reply rate
        chat_id = message.chat_id
        self._rate_tracker.record_reply(chat_id)
        self._rate_tracker.check(chat_id)

        # After stream finishes, send queued media as separate reply messages
        # D11: collect media errors and notify user
        media_errors: list[str] = []
        for media_msg in pending_media:
            try:
                await self._send_reply_with_ack(req_id, media_msg, CMD_RESPONSE)
                logger.info(
                    f"[stream_reply] Sent media after stream: "
                    f"type={media_msg['msgtype']}, req_id={req_id}"
                )
            except Exception as e:
                logger.error(f"Media reply failed after stream: {e}")
                media_errors.append(f"{media_msg.get('msgtype', 'unknown')}: {e}")

        if media_errors:
            error_text = "文件发送失败：\n" + "\n".join(media_errors)
            try:
                await self._send_active_message(chat_id, error_text)
            except Exception:
                logger.warning(f"[stream_reply] Failed to send media error notification")

        self._reply_locks.pop(req_id, None)
        return stream_id

    async def _stream_keepalive_loop(
        self, req_id: str, stream_id: str, text: str
    ) -> None:
        """Send keepalive updates every 4 minutes to prevent stream expiry (A1).

        D6: Defers keepalive if a recent stream frame was sent within the interval.
        D1: Respects intermediate stream message limit.
        """
        try:
            while True:
                await asyncio.sleep(STREAM_KEEPALIVE_INTERVAL_S)
                if not self._ws:
                    break
                # D6: skip if a stream frame was sent recently
                last_sent = self._last_stream_sent.get(stream_id, 0)
                if last_sent and (time.time() - last_sent) < STREAM_KEEPALIVE_INTERVAL_S:
                    logger.debug(f"[keepalive] Deferred: recent stream activity for {stream_id[:8]}")
                    continue
                # D1: check intermediate limit
                count = self._stream_msg_count.get(stream_id, 0)
                if count >= MAX_INTERMEDIATE_STREAM_MSGS:
                    logger.debug(f"[keepalive] Stream {stream_id[:8]} hit intermediate limit")
                    break
                keepalive_content = text[:100] if text else "处理中..."
                body: dict = {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "finish": False, "content": keepalive_content},
                }
                try:
                    await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
                    self._stream_msg_count[stream_id] = count + 1
                    self._last_stream_sent[stream_id] = time.time()
                    logger.debug(f"[keepalive] Sent for stream_id={stream_id[:8]}")
                except Exception as e:
                    logger.warning(f"[keepalive] Failed (non-fatal): {e}")
                    break
        except asyncio.CancelledError:
            pass

    async def _send_active_message(
        self, chat_id: str, text: str, *, chat_type: int = 0
    ) -> str:
        """Send an active push message (markdown).

        Args:
            chat_type: 1=single chat (userid), 2=group chat, 0=auto (default).
        """
        # D3: normalize think tags
        text = _normalize_think_tags(text)

        self._rate_tracker.record(chat_id)
        self._rate_tracker.check(chat_id)

        req_id = _generate_req_id(CMD_SEND_MSG)
        body: dict = {
            "chatid": chat_id,
            "msgtype": "markdown",
            "markdown": {"content": text},
        }
        if chat_type:
            body["chat_type"] = chat_type
        try:
            await self._send_reply_with_ack(req_id, body, CMD_SEND_MSG)
        except Exception as e:
            logger.error(f"Active message send failed: {e}")
        return req_id

    async def _send_active_media_message(
        self, chat_id: str, msgtype: str, media_body: dict, *, chat_type: int = 0
    ) -> str:
        """Send an active push message with media (image/file/voice/video)."""
        req_id = _generate_req_id(CMD_SEND_MSG)
        body: dict = {
            "chatid": chat_id,
            "msgtype": msgtype,
            msgtype: media_body,
        }
        if chat_type:
            body["chat_type"] = chat_type
        try:
            await self._send_reply_with_ack(req_id, body, CMD_SEND_MSG)
            logger.info(f"[active_media] Sent {msgtype} to {chat_id}")
        except Exception as e:
            logger.error(f"Active media message send failed: {e}")
        return req_id

    # ==================== Reply with ack ====================

    async def _send_reply_with_ack(
        self, req_id: str, body: dict, cmd: str
    ) -> dict:
        """Send a reply frame and wait for ack, with per-req_id serial ordering."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected")

        # get or create per-req_id lock for serial sending
        if req_id not in self._reply_locks:
            self._reply_locks[req_id] = asyncio.Lock()
        lock = self._reply_locks[req_id]

        async with lock:
            frame = {
                "cmd": cmd,
                "headers": {"req_id": req_id},
                "body": body,
            }

            # register ack future before sending
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending_acks[req_id] = fut

            try:
                await self._ws_send(frame)
            except Exception:
                self._pending_acks.pop(req_id, None)
                raise

            try:
                result = await asyncio.wait_for(
                    fut, timeout=self.config.reply_ack_timeout
                )
            except asyncio.TimeoutError:
                self._pending_acks.pop(req_id, None)
                raise TimeoutError(
                    f"Reply ack timeout ({self.config.reply_ack_timeout}s) "
                    f"for req_id={req_id}"
                )

            errcode = result.get("errcode")
            if errcode is not None and errcode != 0:
                errmsg = result.get("errmsg", "unknown")
                raise RuntimeError(f"Reply rejected: {errcode} {errmsg}")

            return result

    # ==================== response_url fallback ====================

    async def _response_url_fallback(self, req_id: str, text: str) -> bool:
        """Try to send via response_url when WS reply fails."""
        url = self._response_urls.get(req_id)
        if not url:
            logger.debug(f"No response_url for req_id={req_id}, cannot fallback")
            return False

        _import_httpx()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": text},
                }
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info(f"response_url fallback succeeded for {req_id}")
                    return True
                logger.warning(
                    f"response_url fallback status={resp.status_code} for {req_id}"
                )
        except Exception as e:
            logger.error(f"response_url fallback failed: {e}")
        return False

    # ==================== Media ====================

    async def download_media(self, media: MediaFile) -> Path:
        """Download and optionally decrypt media file."""
        if not media.url:
            raise ValueError("Media has no URL")

        _import_httpx()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0)
        ) as client:
            resp = await client.get(media.url)
            resp.raise_for_status()

            data = resp.content

            # parse filename from Content-Disposition
            cd = resp.headers.get("content-disposition", "")
            filename = media.filename
            if cd:
                m = re.search(r"filename\*=UTF-8''([^;\s]+)", cd, re.IGNORECASE)
                if m:
                    from urllib.parse import unquote
                    filename = unquote(m.group(1))
                else:
                    m = re.search(r'filename="?([^";\s]+)"?', cd, re.IGNORECASE)
                    if m:
                        from urllib.parse import unquote
                        filename = unquote(m.group(1))

            # decrypt if aeskey provided
            aeskey = (media.extra or {}).get("aeskey")
            if aeskey:
                try:
                    loop = asyncio.get_running_loop()
                    data = await loop.run_in_executor(
                        None, _decrypt_file, data, aeskey
                    )
                except Exception:
                    media.status = MediaStatus.FAILED
                    raise

            from openakita.channels.base import sanitize_filename
            safe_filename = sanitize_filename(Path(filename).name or "download")
            local_path = self.media_dir / f"{media.id}_{safe_filename}"
            await asyncio.get_running_loop().run_in_executor(
                None, local_path.write_bytes, data
            )

            media.local_path = str(local_path)
            media.status = MediaStatus.READY
            media.filename = filename
            logger.info(f"Media downloaded: {local_path}")
            return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """Public generic upload is not exposed for this adapter."""
        raise NotImplementedError(
            "WeWorkWsAdapter does not expose generic upload_media; "
            "use send_image/send_file/send_voice/send_video instead."
        )

    async def _ws_upload_media(self, path: Path, mime_type: str) -> str:
        """Low-level WebSocket chunked upload: init -> chunks -> finish -> media_id."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected, cannot upload")

        file_data = await asyncio.get_running_loop().run_in_executor(
            None, path.read_bytes
        )
        total_size = len(file_data)
        if total_size < 5:
            raise ValueError("File too small (min 5 bytes)")

        media_type = self._mime_to_upload_type(mime_type)
        media_type, downgraded, note = self._check_upload_size(total_size, media_type, mime_type=mime_type)
        if downgraded:
            logger.info(f"[ws_upload] Media type downgraded: {note}")

        md5_hex = hashlib.md5(file_data).hexdigest()

        chunk_size = UPLOAD_CHUNK_MAX_BYTES
        total_chunks = (total_size + chunk_size - 1) // chunk_size
        if total_chunks > UPLOAD_MAX_CHUNKS:
            chunk_size = (total_size + UPLOAD_MAX_CHUNKS - 1) // UPLOAD_MAX_CHUNKS
            total_chunks = (total_size + chunk_size - 1) // chunk_size

        # Step 1: init
        init_req_id = _generate_req_id(CMD_UPLOAD_INIT)
        init_body = {
            "type": media_type,
            "filename": path.name,
            "total_size": total_size,
            "total_chunks": total_chunks,
            "md5": md5_hex,
        }
        init_frame = {
            "cmd": CMD_UPLOAD_INIT,
            "headers": {"req_id": init_req_id},
            "body": init_body,
        }
        init_resp = await self._ws_send_and_wait_ack(init_req_id, init_frame)
        upload_id = init_resp.get("body", {}).get("upload_id")
        if not upload_id:
            raise RuntimeError(f"Upload init failed, no upload_id: {init_resp}")
        logger.info(
            f"[ws_upload] Init OK: upload_id={upload_id}, "
            f"file={path.name}, size={total_size}, chunks={total_chunks}"
        )

        # Step 2: upload chunks
        for i in range(total_chunks):
            offset = i * chunk_size
            chunk_data = file_data[offset : offset + chunk_size]
            b64_chunk = base64.b64encode(chunk_data).decode("ascii")

            chunk_req_id = _generate_req_id(CMD_UPLOAD_CHUNK)
            chunk_frame = {
                "cmd": CMD_UPLOAD_CHUNK,
                "headers": {"req_id": chunk_req_id},
                "body": {
                    "upload_id": upload_id,
                    "chunk_index": i,
                    "base64_data": b64_chunk,
                },
            }
            await self._ws_send_and_wait_ack(chunk_req_id, chunk_frame)

        # Step 3: finish
        finish_req_id = _generate_req_id(CMD_UPLOAD_FINISH)
        finish_frame = {
            "cmd": CMD_UPLOAD_FINISH,
            "headers": {"req_id": finish_req_id},
            "body": {"upload_id": upload_id},
        }
        finish_resp = await self._ws_send_and_wait_ack(finish_req_id, finish_frame)
        media_id = finish_resp.get("body", {}).get("media_id")
        if not media_id:
            raise RuntimeError(f"Upload finish failed, no media_id: {finish_resp}")

        logger.info(
            f"[ws_upload] Complete: {path.name} -> media_id={media_id[:20]}..."
        )
        return media_id

    async def _ws_send_and_wait_ack(self, req_id: str, frame: dict) -> dict:
        """Send a frame and wait for server ack (used for upload protocol)."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected")

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_acks[req_id] = fut

        try:
            await self._ws_send(frame)
        except Exception:
            self._pending_acks.pop(req_id, None)
            raise

        try:
            result = await asyncio.wait_for(fut, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending_acks.pop(req_id, None)
            raise TimeoutError(f"Upload ack timeout for req_id={req_id}")

        errcode = result.get("errcode")
        if errcode is not None and errcode != 0:
            errmsg = result.get("errmsg", "unknown")
            raise RuntimeError(f"Upload rejected: {errcode} {errmsg}")
        return result

    @staticmethod
    def _mime_to_upload_type(mime_type: str) -> str:
        """Map MIME type to WeCom upload media type."""
        mt = mime_type.lower()
        if mt.startswith("image/"):
            return "image"
        if mt.startswith("audio/") or mt in ("audio/amr", "audio/mpeg"):
            return "voice"
        if mt.startswith("video/"):
            return "video"
        return "file"

    @staticmethod
    def _check_upload_size(
        total_size: int, media_type: str, *, mime_type: str = ""
    ) -> tuple[str, bool, str]:
        """Validate size and auto-downgrade oversized media to 'file' type (A3).

        D5: Also downgrades voice to 'file' if the MIME type is not AMR,
        since WeCom voice messages require AMR format.

        Returns (final_type, downgraded, note).
        Raises ValueError only if the file exceeds the absolute max (20MB).
        """
        if total_size > UPLOAD_ABSOLUTE_MAX:
            raise ValueError(
                f"File size {total_size} exceeds absolute max "
                f"{UPLOAD_ABSOLUTE_MAX} bytes"
            )
        # D5: non-AMR voice → downgrade to file
        if media_type == "voice" and mime_type and "amr" not in mime_type.lower():
            note = f"voice MIME={mime_type} is not AMR, downgraded to 'file'"
            logger.info(f"[upload_size] {note}")
            return "file", True, note
        limit = UPLOAD_SIZE_LIMITS.get(media_type, UPLOAD_ABSOLUTE_MAX)
        if total_size <= limit:
            return media_type, False, ""
        # auto-downgrade to file type
        file_limit = UPLOAD_SIZE_LIMITS["file"]
        if total_size <= file_limit:
            note = (
                f"{media_type} size {total_size} exceeds {limit} bytes, "
                f"downgraded to 'file' type"
            )
            logger.info(f"[upload_size] {note}")
            return "file", True, note
        raise ValueError(
            f"{media_type} size {total_size} exceeds file limit {file_limit} bytes"
        )

    # ==================== Helpers ====================

    async def _ws_send(self, frame: dict) -> None:
        """Send a JSON frame over WebSocket."""
        if self._ws is None:
            raise ConnectionError("WebSocket not connected")
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    def _reject_all_pending(self, reason: str) -> None:
        """Reject all pending ack futures and clear connection-scoped state."""
        for req_id, fut in list(self._pending_acks.items()):
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        self._pending_acks.clear()
        self._reply_locks.clear()
        # Cancel all thinking tasks
        for task in self._thinking_tasks.values():
            if not task.done():
                task.cancel()
        self._thinking_tasks.clear()
        self._pre_streams.clear()
        self._pending_image_items.clear()
        self._pending_media_msgs.clear()

    # ==================== Pending reply queue (A9) ====================

    def _enqueue_pending_reply(
        self, req_id: str, text: str, message: OutgoingMessage
    ) -> None:
        """Enqueue a failed reply for retry after reconnection."""
        now = time.time()
        self._pending_replies = [
            r for r in self._pending_replies if now - r["ts"] < PENDING_REPLY_TTL_S
        ]
        if len(self._pending_replies) >= PENDING_REPLY_MAX:
            logger.warning("[pending_reply] Queue full, dropping oldest entry")
            self._pending_replies.pop(0)
        self._pending_replies.append({
            "ts": now,
            "req_id": req_id,
            "text": text,
            "chat_id": message.chat_id,
            "is_group": message.metadata.get("is_group", False),
        })
        logger.info(
            f"[pending_reply] Enqueued failed reply for retry: "
            f"req_id={req_id}, queue_size={len(self._pending_replies)}"
        )

    async def _flush_pending_replies(self) -> None:
        """Retry pending replies after reconnection via response_url or active push."""
        if not self._pending_replies:
            return
        now = time.time()
        to_retry = [
            r for r in self._pending_replies if now - r["ts"] < PENDING_REPLY_TTL_S
        ]
        self._pending_replies.clear()
        logger.info(f"[pending_reply] Flushing {len(to_retry)} pending replies")
        for entry in to_retry:
            req_id = entry["req_id"]
            text = entry["text"]
            chat_id = entry["chat_id"]
            # try response_url first
            ok = await self._response_url_fallback(req_id, text)
            if ok:
                continue
            # fallback to active push
            chat_type = 2 if entry.get("is_group") else 1
            try:
                await self._send_active_message(chat_id, text, chat_type=chat_type)
                logger.info(f"[pending_reply] Retried via active push: {req_id}")
            except Exception as e:
                logger.error(f"[pending_reply] Retry failed for {req_id}: {e}")

    # cleanup response_url cache periodically (keep last 200)
    def _cleanup_response_urls(self) -> None:
        if len(self._response_urls) > 200:
            keys = list(self._response_urls.keys())
            for k in keys[: len(keys) - 200]:
                del self._response_urls[k]
