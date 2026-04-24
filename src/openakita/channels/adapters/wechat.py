"""
微信个人号适配器

基于腾讯 iLink Bot API（与 OpenClaw 相同的开源协议）实现:
- HTTP 长轮询接收消息 (getUpdates)
- HTTP API 发送消息 (sendMessage)
- CDN 媒体上传/下载 + AES-128-ECB 加解密
- 扫码登录获取 Bearer token
- Typing 指示器 (sendTyping)
- 无需公网 IP

API Base: https://ilinkai.weixin.qq.com
CDN Base: https://novac2c.cdn.weixin.qq.com/c2c
协议参考: @tencent-weixin/openclaw-weixin v2.1.8 (MIT)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import struct
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

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

httpx = None


def _import_httpx():
    global httpx
    if httpx is None:
        try:
            import httpx as _httpx

            httpx = _httpx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")


# ---------------------------------------------------------------------------
# 常量 (对齐 @tencent-weixin/openclaw-weixin 源码)
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_ILINK_BOT_TYPE = "3"
DEFAULT_CHANNEL_VERSION = "2.0.0"

# ---------------------------------------------------------------------------
# 协议兼容参数 — 对齐 @tencent-weixin/openclaw-weixin v2.1.8
# 可通过环境变量紧急覆盖，无需改代码
# ---------------------------------------------------------------------------

OPENCLAW_COMPAT_VERSION = os.environ.get("WECHAT_OPENCLAW_COMPAT_VERSION", "2.1.8")
ILINK_APP_ID = os.environ.get("WECHAT_ILINK_APP_ID", "bot")


def _encode_client_version(semver: str) -> int:
    """将语义化版本号编码为 uint32: 0x00MMNNPP (major<<16 | minor<<8 | patch)"""
    parts = semver.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major << 16) | (minor << 8) | patch


ILINK_APP_CLIENT_VERSION = str(_encode_client_version(OPENCLAW_COMPAT_VERSION))

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000
DEFAULT_CONFIG_TIMEOUT_MS = 10_000

MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30.0
RETRY_DELAY_S = 2.0
SESSION_PAUSE_DURATION_S = 3600  # 1 hour
SESSION_EXPIRED_ERRCODE = -14

UPLOAD_MAX_RETRIES = 3
CONFIG_CACHE_TTL_S = 86400  # 24h
CONFIG_CACHE_INITIAL_RETRY_S = 2.0
CONFIG_CACHE_MAX_RETRY_S = 3600.0

DEDUP_TTL_S = 600  # 10 min
DEDUP_MAX_SIZE = 500

SEND_MIN_INTERVAL_S = 2.5
SEND_RATE_LIMIT_RETRIES = 4
SEND_RATE_LIMIT_BASE_DELAY_S = 5.0

# MessageItemType
ITEM_NONE = 0
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

# MessageType
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2

# MessageState
MSG_STATE_NEW = 0
MSG_STATE_GENERATING = 1
MSG_STATE_FINISH = 2

# UploadMediaType
UPLOAD_IMAGE = 1
UPLOAD_VIDEO = 2
UPLOAD_FILE = 3
UPLOAD_VOICE = 4

# TypingStatus
TYPING_START = 1
TYPING_CANCEL = 2


# ---------------------------------------------------------------------------
# AES-128-ECB 加解密
# ---------------------------------------------------------------------------


def _encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES

    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len] * pad_len)
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(padded)


def _decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES

    cipher = AES.new(key, AES.MODE_ECB)
    padded = cipher.decrypt(ciphertext)
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16:
        return padded
    return padded[:-pad_len]


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    return math.ceil((plaintext_size + 1) / 16) * 16


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 AES key，支持两种格式:
    - base64(16 raw bytes)
    - base64(32 hex chars) → hex decode → 16 bytes
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            hex_str = decoded.decode("ascii")
            if all(c in "0123456789abcdefABCDEF" for c in hex_str):
                return bytes.fromhex(hex_str)
        except (UnicodeDecodeError, ValueError):
            pass
    raise ValueError(
        f"aes_key must decode to 16 raw bytes or 32-char hex, got {len(decoded)} bytes"
    )


# ---------------------------------------------------------------------------
# Streaming Markdown Filter
# Character-level state machine that strips Markdown syntax while preserving
# readable content.  Works with both complete text and incremental chunks.
# ---------------------------------------------------------------------------


class StreamingMarkdownFilter:
    """Character-level Markdown→plaintext filter.

    Supports streaming: call feed() with arbitrary chunks, then flush().
    Also usable as a one-shot via the class method ``filter_text()``.
    """

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._out: list[str] = []
        self._in_code_fence = False
        self._fence_tick_count = 0
        self._fence_opening = True
        self._skip_to_eol = False
        self._inline_code = False
        self._star_count = 0
        self._link_phase = 0  # 0=none 1=[text 2=]( 3=url)
        self._link_text: list[str] = []
        self._link_is_image = False
        self._line_start = True
        self._heading_hashes = 0
        self._pending_bang = False

    # -- public API --

    def feed(self, chunk: str) -> str:
        for ch in chunk:
            self._process_char(ch)
        result = "".join(self._out)
        self._out.clear()
        return result

    def flush(self) -> str:
        self._star_count = 0
        if self._pending_bang:
            self._out.append("!")
            self._pending_bang = False
        if self._link_phase == 1:
            self._out.append("[" + "".join(self._link_text))
            self._link_text.clear()
            self._link_phase = 0
        result = "".join(self._out)
        self._out.clear()
        return result

    _RE_TABLE_SEP = re.compile(r"^[\s\t:|\-]+$", re.MULTILINE)

    @classmethod
    def filter_text(cls, text: str) -> str:
        f = cls()
        out = f.feed(text)
        out += f.flush()
        out = cls._RE_TABLE_SEP.sub("", out)
        return out.strip()

    # -- internal --

    def _emit(self, ch: str) -> None:
        self._out.append(ch)
        self._line_start = ch == "\n"

    def _process_char(self, ch: str) -> None:
        # inside code fence — pass through until closing fence
        if self._in_code_fence:
            if ch == "\n" and self._fence_opening:
                self._fence_opening = False
                return
            if ch == "`":
                self._fence_tick_count += 1
                if self._fence_tick_count >= 3:
                    self._in_code_fence = False
                    self._fence_tick_count = 0
                    self._skip_to_eol = True
                return
            else:
                if self._fence_tick_count:
                    self._emit("`" * self._fence_tick_count)
                    self._fence_tick_count = 0
                self._emit(ch)
            return

        # skip rest of line (after closing fence / heading)
        if self._skip_to_eol:
            if ch == "\n":
                self._skip_to_eol = False
                self._emit("\n")
            return

        # heading detection at line start
        if self._line_start and ch == "#":
            self._heading_hashes += 1
            return
        if self._heading_hashes > 0:
            if ch == " " and self._heading_hashes <= 6:
                self._heading_hashes = 0
                return
            else:
                self._emit("#" * self._heading_hashes)
                self._heading_hashes = 0

        # backtick handling
        if ch == "`":
            self._fence_tick_count += 1
            if self._fence_tick_count >= 3 and not self._inline_code:
                self._in_code_fence = True
                self._fence_opening = True
                self._fence_tick_count = 0
                return
            return
        if self._fence_tick_count:
            if self._fence_tick_count < 3:
                self._inline_code = not self._inline_code
            self._fence_tick_count = 0

        if self._inline_code:
            self._emit(ch)
            return

        # link / image: ![alt](url) or [text](url)
        if self._pending_bang:
            self._pending_bang = False
            if ch == "[":
                self._link_phase = 1
                self._link_is_image = True
                self._link_text.clear()
                return
            self._emit("!")

        if ch == "!" and self._link_phase == 0:
            self._pending_bang = True
            return
        if ch == "[" and self._link_phase == 0:
            self._link_phase = 1
            self._link_is_image = False
            self._link_text.clear()
            return
        if self._link_phase == 1:
            if ch == "]":
                self._link_phase = 2
            else:
                self._link_text.append(ch)
            return
        if self._link_phase == 2:
            if ch == "(":
                self._link_phase = 3
            else:
                if not self._link_is_image:
                    self._emit("".join(self._link_text))
                self._link_text.clear()
                self._link_phase = 0
                self._emit(ch)
            return
        if self._link_phase == 3:
            if ch == ")":
                if not self._link_is_image:
                    self._emit("".join(self._link_text))
                self._link_text.clear()
                self._link_phase = 0
            return

        # bold/italic stars
        if ch == "*":
            self._star_count += 1
            return
        if self._star_count:
            self._star_count = 0

        # table: pipe characters → tab separation
        if ch == "|":
            if not self._line_start:
                self._emit("\t")
            return

        self._emit(ch)


def _markdown_to_plaintext(text: str) -> str:
    return StreamingMarkdownFilter.filter_text(text)


# ---------------------------------------------------------------------------
# MIME 工具
# ---------------------------------------------------------------------------


def _try_silk_to_wav(silk_path: Path) -> Path | None:
    """Attempt to transcode SILK audio to WAV. Returns WAV path on success, None on failure."""
    try:
        import pilk  # type: ignore[import-untyped]

        pcm_path = silk_path.with_suffix(".pcm")
        wav_path = silk_path.with_suffix(".wav")
        pilk.decode(str(silk_path), str(pcm_path), pcm_rate=24000)

        import wave

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm_path.read_bytes())
        pcm_path.unlink(missing_ok=True)
        return wav_path
    except ImportError:
        pass
    except Exception:
        logger.debug("SILK→WAV transcode failed, keeping .silk", exc_info=True)
    return None


def _voice_duration_seconds(path: Path) -> int:
    """估算语音时长（秒），供 iLink voice_item 使用。"""
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            n = w.getnframes()
            if rate:
                return max(1, min(300, int(n / rate)))
    except Exception:
        pass
    try:
        raw = path.stat().st_size
        return max(1, min(300, raw // 1600))
    except OSError:
        return 1


def _guess_mime(filepath: str) -> str:
    mime, _ = mimetypes.guess_type(filepath)
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# Log Redaction — 敏感信息脱敏
# ---------------------------------------------------------------------------


def _redact_token(token: str) -> str:
    if not token or len(token) <= 6:
        return "***"
    return f"{token[:6]}...({len(token)})"


def _redact_id(user_id: str) -> str:
    if not user_id or len(user_id) <= 8:
        return user_id
    return f"{user_id[:4]}...{user_id[-4:]}"


def _redact_url(url: str) -> str:
    """Strip query parameters (may contain signatures) from a URL for logging."""
    return url.split("?")[0] if "?" in url else url


def _guess_extension(content_type: str | None, url: str = "") -> str:
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    if "." in url.split("?")[0].split("/")[-1]:
        return "." + url.split("?")[0].split("/")[-1].rsplit(".", 1)[-1].lower()
    return ".bin"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class WeChatConfig:
    token: str = ""
    base_url: str = DEFAULT_BASE_URL
    cdn_base_url: str = DEFAULT_CDN_BASE_URL
    long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS
    api_timeout_ms: int = DEFAULT_API_TIMEOUT_MS
    route_tag: str = ""


# ---------------------------------------------------------------------------
# TypingTicket 缓存
# ---------------------------------------------------------------------------


@dataclass
class _TicketEntry:
    ticket: str = ""
    next_fetch_at: float = 0.0
    retry_delay_s: float = CONFIG_CACHE_INITIAL_RETRY_S
    ever_succeeded: bool = False


# ---------------------------------------------------------------------------
# WeChatAdapter
# ---------------------------------------------------------------------------


class WeChatAdapter(ChannelAdapter):
    """微信个人号适配器 (iLink Bot API)"""

    channel_name: str = "wechat"

    capabilities: ClassVar[dict[str, bool]] = {
        "streaming": False,
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
        "markdown": False,
    }

    def __init__(
        self,
        token: str = "",
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
        footer_elapsed: bool | None = None,
        route_tag: str = "",
    ):
        super().__init__(
            channel_name=channel_name,
            bot_id=bot_id,
            agent_profile_id=agent_profile_id,
        )
        self.config = WeChatConfig(
            token=token,
            base_url=base_url.rstrip("/") if base_url else DEFAULT_BASE_URL,
            cdn_base_url=cdn_base_url.rstrip("/") if cdn_base_url else DEFAULT_CDN_BASE_URL,
            route_tag=route_tag,
        )
        self._route_tag = route_tag

        self._http: Any = None
        self._poll_task: asyncio.Task | None = None

        # 同步游标
        self._get_updates_buf: str = ""
        self._sync_buf_dir = Path("data/wechat_sync")

        # context_token 缓存 (user_id → token)
        self._context_tokens: dict[str, str] = {}

        # Typing ticket 缓存
        self._ticket_cache: dict[str, _TicketEntry] = {}

        # 消息去重
        self._seen_msg_ids: OrderedDict[int, float] = OrderedDict()

        # Session 过期管理
        self._session_paused_until: float = 0.0

        # 动态轮询超时
        self._next_poll_timeout_ms: int = self.config.long_poll_timeout_ms

        # 连续失败计数
        self._consecutive_failures: int = 0

        # 发送限流 (user_id → 上次发送时间戳)
        self._last_send_ts: dict[str, float] = {}

        # 耗时统计 (chat_id → 首次 send_typing 的 time.time())
        self._typing_start_time: dict[str, float] = {}
        self._footer_elapsed: bool = (
            footer_elapsed
            if footer_elapsed is not None
            else (os.environ.get("WECHAT_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes"))
        )

        # 统计指标
        self._msg_count: int = 0
        self._send_count: int = 0

        # per-user debug mode for /toggle-debug
        self._debug_users: set[str] = set()

        self.media_dir = Path("data/media/wechat")

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        if not self.config.token:
            raise ValueError("微信 Token 未配置，请先在 Bot 配置中扫码登录获取 Token。")

        _import_httpx()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(self._next_poll_timeout_ms / 1000) + 10,
                write=10.0,
                pool=10.0,
            )
        )
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._sync_buf_dir.mkdir(parents=True, exist_ok=True)
        self._load_sync_buf()
        self._load_context_tokens()

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"{self.channel_name}: WeChat adapter started (base_url={self.config.base_url})"
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        self._save_sync_buf()
        self._save_context_tokens()
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info(
            f"{self.channel_name}: WeChat adapter stopped "
            f"(msgs_received={self._msg_count}, msgs_sent={self._send_count})"
        )

    # ==================== 请求基础 ====================

    def _build_headers(self) -> dict[str, str]:
        uint32 = struct.unpack(">I", os.urandom(4))[0]
        uin_b64 = base64.b64encode(str(uint32).encode()).decode()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": uin_b64,
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if self._route_tag:
            headers["SKRouteTag"] = self._route_tag
        return headers

    def _is_session_paused(self) -> bool:
        if self._session_paused_until <= 0:
            return False
        if time.time() >= self._session_paused_until:
            self._session_paused_until = 0
            return False
        return True

    def _pause_session(self) -> None:
        self._session_paused_until = time.time() + SESSION_PAUSE_DURATION_S
        remaining_min = SESSION_PAUSE_DURATION_S // 60
        logger.error(
            f"{self.channel_name}: session expired (errcode {SESSION_EXPIRED_ERRCODE}), "
            f"pausing all API calls for {remaining_min} min"
        )

    async def _api_post(self, endpoint: str, body: dict, *, timeout_s: float | None = None) -> dict:
        if self._is_session_paused():
            remaining = int(self._session_paused_until - time.time())
            raise RuntimeError(f"session paused, {remaining}s remaining")

        body.setdefault("base_info", {"channel_version": OPENCLAW_COMPAT_VERSION})

        url = f"{self.config.base_url}/{endpoint}"
        to = timeout_s or (self.config.api_timeout_ms / 1000)
        resp = await self._http.post(
            url,
            headers=self._build_headers(),
            json=body,
            timeout=to,
        )
        resp.raise_for_status()
        return resp.json()

    async def _rate_limit_wait(self, user_id: str) -> None:
        """Enforce minimum interval between sends to the same user."""
        now = time.time()
        last = self._last_send_ts.get(user_id, 0.0)
        gap = now - last
        if gap < SEND_MIN_INTERVAL_S:
            await asyncio.sleep(SEND_MIN_INTERVAL_S - gap)
        self._last_send_ts[user_id] = time.time()

    def _check_send_response(self, resp: dict, *, action: str = "sendmessage") -> None:
        """检查 sendmessage / sendtyping 等 API 响应中的业务层错误。

        iLink Bot API 在 HTTP 200 下可能返回 {"ret": -14, "errmsg": "..."}，
        如果不检查会导致消息静默丢失。
        """
        ret = resp.get("ret")
        errcode = resp.get("errcode")

        is_error = (ret not in (None, 0)) or (errcode not in (None, 0))
        if not is_error:
            return

        code = ret if ret not in (None, 0) else errcode
        errmsg = resp.get("errmsg", "")

        if code == SESSION_EXPIRED_ERRCODE:
            self._pause_session()
            raise RuntimeError(
                f"WeChat {action} failed: session expired "
                f"(ret={ret}, errcode={errcode}, errmsg={errmsg})"
            )

        raise RuntimeError(f"WeChat {action} failed: ret={ret}, errcode={errcode}, errmsg={errmsg}")

    # ==================== 长轮询 ====================

    async def _poll_loop(self) -> None:
        logger.info(f"{self.channel_name}: poll loop started")
        while self._running:
            try:
                if self._is_session_paused():
                    remaining = self._session_paused_until - time.time()
                    await asyncio.sleep(max(remaining, 1))
                    continue

                resp = await self._get_updates()
                if resp is None:
                    continue

                is_error = (resp.get("ret") not in (None, 0)) or (
                    resp.get("errcode") not in (None, 0)
                )
                if is_error:
                    errcode = resp.get("errcode") or resp.get("ret")
                    if errcode == SESSION_EXPIRED_ERRCODE:
                        self._pause_session()
                        self._consecutive_failures = 0
                        continue

                    self._consecutive_failures += 1
                    logger.error(
                        f"{self.channel_name}: getUpdates error "
                        f"ret={resp.get('ret')} errcode={resp.get('errcode')} "
                        f"errmsg={resp.get('errmsg', '')} "
                        f"({self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                    )
                    if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        self._consecutive_failures = 0
                        await asyncio.sleep(BACKOFF_DELAY_S)
                    else:
                        await asyncio.sleep(RETRY_DELAY_S)
                    continue

                self._consecutive_failures = 0

                if resp.get("longpolling_timeout_ms"):
                    self._next_poll_timeout_ms = resp["longpolling_timeout_ms"]

                new_buf = resp.get("get_updates_buf")
                if new_buf:
                    self._get_updates_buf = new_buf
                    self._save_sync_buf()

                for msg in resp.get("msgs") or []:
                    try:
                        await self._process_message(msg)
                    except Exception:
                        logger.exception(
                            f"{self.channel_name}: error processing message "
                            f"from={_redact_id(msg.get('from_user_id', ''))}"
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                if not self._running:
                    break
                self._consecutive_failures += 1
                logger.exception(
                    f"{self.channel_name}: poll loop error "
                    f"({self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                )
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._consecutive_failures = 0
                    await asyncio.sleep(BACKOFF_DELAY_S)
                else:
                    await asyncio.sleep(RETRY_DELAY_S)

        logger.info(f"{self.channel_name}: poll loop ended")

    async def _get_updates(self) -> dict | None:
        url = f"{self.config.base_url}/ilink/bot/getupdates"
        timeout_s = self._next_poll_timeout_ms / 1000 + 5
        body = {
            "get_updates_buf": self._get_updates_buf or "",
            "base_info": {"channel_version": OPENCLAW_COMPAT_VERSION},
        }
        try:
            resp = await self._http.post(
                url,
                headers=self._build_headers(),
                json=body,
                timeout=timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ReadTimeout:
            logger.debug(f"{self.channel_name}: getUpdates timeout (normal)")
            return {"ret": 0, "msgs": [], "get_updates_buf": self._get_updates_buf}
        except httpx.HTTPStatusError as e:
            logger.error(f"{self.channel_name}: getUpdates HTTP {e.response.status_code}")
            raise

    # ==================== 消息解析 ====================

    def _dedup_check(self, message_id: int | None) -> bool:
        if message_id is None:
            return False
        now = time.time()
        if message_id in self._seen_msg_ids:
            return True
        # 清理过期条目
        while self._seen_msg_ids:
            oldest_id, oldest_ts = next(iter(self._seen_msg_ids.items()))
            if now - oldest_ts > DEDUP_TTL_S or len(self._seen_msg_ids) >= DEDUP_MAX_SIZE:
                self._seen_msg_ids.pop(oldest_id)
            else:
                break
        self._seen_msg_ids[message_id] = now
        return False

    def _extract_text_body(self, item_list: list[dict] | None) -> str:
        if not item_list:
            return ""
        for item in item_list:
            if item.get("type") == ITEM_TEXT:
                text = (item.get("text_item") or {}).get("text", "")
                ref = item.get("ref_msg")
                if not ref:
                    return text
                ref_item = ref.get("message_item")
                if ref_item and self._is_media_item(ref_item):
                    return text
                parts = []
                if ref.get("title"):
                    parts.append(ref["title"])
                if ref_item:
                    ref_text = self._extract_text_body([ref_item])
                    if ref_text:
                        parts.append(ref_text)
                if parts:
                    return f"[引用: {' | '.join(parts)}]\n{text}"
                return text
            if item.get("type") == ITEM_VOICE:
                voice_text = (item.get("voice_item") or {}).get("text")
                if voice_text:
                    return voice_text
        return ""

    @staticmethod
    def _is_media_item(item: dict) -> bool:
        return item.get("type") in (ITEM_IMAGE, ITEM_VIDEO, ITEM_FILE, ITEM_VOICE)

    @staticmethod
    def _media_downloadable(media: dict) -> bool:
        return bool(media.get("encrypt_query_param") or media.get("full_url"))

    def _find_media_item(self, item_list: list[dict] | None) -> dict | None:
        """按优先级 IMAGE > VIDEO > FILE > VOICE 查找第一个可下载的媒体 item"""
        if not item_list:
            return None
        for target_type, media_key in [
            (ITEM_IMAGE, "image_item"),
            (ITEM_VIDEO, "video_item"),
            (ITEM_FILE, "file_item"),
        ]:
            for item in item_list:
                if item.get("type") == target_type:
                    sub = item.get(media_key) or {}
                    media = sub.get("media") or {}
                    if self._media_downloadable(media):
                        return item
        for item in item_list:
            if item.get("type") == ITEM_VOICE:
                voice = item.get("voice_item") or {}
                if not voice.get("text"):
                    media = voice.get("media") or {}
                    if self._media_downloadable(media):
                        return item
        return None

    def _extract_cdn_params(self, sub: dict, media: dict) -> tuple[str, str, bytes | None]:
        """Extract (encrypt_query_param, full_url, aes_key) from a media item."""
        param = media.get("encrypt_query_param", "")
        cdn_full = (media.get("full_url") or "").strip()
        aes_key_raw = sub.get("aeskey")
        if aes_key_raw:
            key = bytes.fromhex(aes_key_raw)
        else:
            aes_b64 = media.get("aes_key", "")
            key = _parse_aes_key(aes_b64) if aes_b64 else None
        return param, cdn_full, key

    async def _download_media_item(self, item: dict) -> tuple[Path | None, str]:
        """下载并解密一个媒体 item，返回 (local_path, mime_type)"""
        item_type = item.get("type")
        try:
            if item_type == ITEM_IMAGE:
                img = item.get("image_item") or {}
                media = img.get("media") or {}
                param, cdn_full, key = self._extract_cdn_params(img, media)
                if not param and not cdn_full:
                    return None, ""
                buf = await self._cdn_download(param, key, full_url=cdn_full)
                path = self.media_dir / f"wechat_img_{uuid.uuid4().hex[:8]}.jpg"
                path.write_bytes(buf)
                return path, "image/jpeg"

            if item_type == ITEM_VIDEO:
                video = item.get("video_item") or {}
                media = video.get("media") or {}
                param, cdn_full, key = self._extract_cdn_params(video, media)
                if not param and not cdn_full:
                    return None, ""
                buf = await self._cdn_download(param, key, full_url=cdn_full)
                path = self.media_dir / f"wechat_video_{uuid.uuid4().hex[:8]}.mp4"
                path.write_bytes(buf)
                return path, "video/mp4"

            if item_type == ITEM_FILE:
                file_info = item.get("file_item") or {}
                media = file_info.get("media") or {}
                param, cdn_full, key = self._extract_cdn_params(file_info, media)
                if not param and not cdn_full:
                    return None, ""
                buf = await self._cdn_download(param, key, full_url=cdn_full)
                fname = file_info.get("file_name", f"file_{uuid.uuid4().hex[:8]}")
                path = self.media_dir / f"wechat_{fname}"
                path.write_bytes(buf)
                mime = _guess_mime(fname)
                return path, mime

            if item_type == ITEM_VOICE:
                voice = item.get("voice_item") or {}
                media = voice.get("media") or {}
                param, cdn_full, key = self._extract_cdn_params(voice, media)
                if not param and not cdn_full:
                    return None, ""
                buf = await self._cdn_download(param, key, full_url=cdn_full)
                silk_path = self.media_dir / f"wechat_voice_{uuid.uuid4().hex[:8]}.silk"
                silk_path.write_bytes(buf)
                wav_path = _try_silk_to_wav(silk_path)
                if wav_path:
                    return wav_path, "audio/wav"
                return silk_path, "audio/silk"

        except Exception:
            logger.exception(f"{self.channel_name}: media download failed type={item_type}")
        return None, ""

    async def _process_message(self, msg: dict) -> None:
        msg_id = msg.get("message_id")
        if self._dedup_check(msg_id):
            return

        msg_type = msg.get("message_type")
        if msg_type == MSG_TYPE_BOT:
            return

        from_user = msg.get("from_user_id", "")
        if not from_user:
            return

        ctx_token = msg.get("context_token")
        if ctx_token:
            self._context_tokens[from_user] = ctx_token
            self._save_context_tokens()

        item_list = msg.get("item_list") or []
        text_body = self._extract_text_body(item_list)

        if await self._handle_slash_command(text_body, from_user, ctx_token or "", msg):
            return

        content = MessageContent()
        content.text = text_body

        # 查找并下载媒体
        media_item = self._find_media_item(item_list)
        if not media_item:
            # 回退检查引用消息中的媒体
            for item in item_list:
                if item.get("type") == ITEM_TEXT:
                    ref = item.get("ref_msg", {})
                    ref_item = ref.get("message_item")
                    if ref_item and self._is_media_item(ref_item):
                        media_item = ref_item
                        break

        if media_item:
            local_path, mime_type = await self._download_media_item(media_item)
            if local_path and local_path.exists():
                media = MediaFile.create(
                    filename=local_path.name,
                    mime_type=mime_type,
                    size=local_path.stat().st_size,
                )
                media.local_path = str(local_path)
                media.status = MediaStatus.READY
                if mime_type.startswith("image/"):
                    content.images.append(media)
                elif mime_type.startswith("video/"):
                    content.videos.append(media)
                elif mime_type.startswith("audio/"):
                    content.voices.append(media)
                else:
                    content.files.append(media)
            else:
                item_type = media_item.get("type", ITEM_FILE)
                _type_map = {
                    ITEM_IMAGE: ("image/jpeg", "images"),
                    ITEM_VIDEO: ("video/mp4", "videos"),
                    ITEM_VOICE: ("audio/silk", "voices"),
                }
                _mime, _list_name = _type_map.get(item_type, ("application/octet-stream", "files"))
                failed = MediaFile.create(filename="media", mime_type=_mime)
                failed.status = MediaStatus.FAILED
                failed.description = "CDN 下载失败"
                getattr(content, _list_name).append(failed)

        ts_ms = msg.get("create_time_ms") or 0
        timestamp = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()

        group_id = msg.get("group_id", "")
        chat_type = "group" if group_id else "private"
        chat_id = group_id if group_id else from_user

        meta: dict[str, Any] = {
            "context_token": ctx_token or "",
            "session_id": msg.get("session_id", ""),
        }
        if group_id:
            meta["group_id"] = group_id

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=str(msg_id or ""),
            user_id=from_user,
            channel_user_id=from_user,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_direct_message=not group_id,
            is_mentioned=True,
            timestamp=timestamp,
            metadata=meta,
        )
        self._msg_count += 1
        self._log_message(unified)
        await self._emit_message(unified)

    # ==================== 斜杠命令 ====================

    async def _handle_slash_command(
        self,
        text: str,
        user_id: str,
        ctx_token: str,
        msg: dict,
    ) -> bool:
        """Handle /echo and /toggle-debug. Returns True if consumed."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return False

        if stripped.startswith("/echo"):
            payload = stripped[5:].strip() or "(empty)"
            ts_ms = msg.get("create_time_ms") or 0
            latency = ""
            if ts_ms:
                latency = f"\n平台延迟: {(time.time() * 1000 - ts_ms):.0f}ms"
            reply = f"[echo] {payload}{latency}"
            try:
                await self._send_text(user_id, reply, ctx_token)
            except Exception:
                logger.debug("slash /echo reply failed", exc_info=True)
            return True

        if stripped == "/toggle-debug":
            if user_id in self._debug_users:
                self._debug_users.discard(user_id)
                reply = "🔧 Debug 模式已关闭"
            else:
                self._debug_users.add(user_id)
                reply = "🔧 Debug 模式已开启，消息尾部将附加耗时信息"
            try:
                await self._send_text(user_id, reply, ctx_token)
            except Exception:
                logger.debug("slash /toggle-debug reply failed", exc_info=True)
            return True

        return False

    # ==================== 消息发送 ====================

    async def _send_error_notice(
        self,
        chat_id: str,
        notice: str,
        ctx_token: str = "",
    ) -> None:
        """Fire-and-forget: send a visible error notice to the user."""
        try:
            await self._send_text(chat_id, notice, ctx_token)
        except Exception:
            logger.debug(f"{self.channel_name}: error notice send failed (ignored)")

    async def send_message(self, message: OutgoingMessage) -> str:
        if self._is_session_paused():
            raise RuntimeError("WeChat session paused")

        chat_id = message.chat_id
        ctx_token = self._resolve_context_token(chat_id, message.metadata)
        if not ctx_token:
            logger.warning(
                f"{self.channel_name}: no context_token for {_redact_id(chat_id)}, message may fail"
            )

        text = message.content.text or ""

        # 发送媒体
        all_media = message.content.all_media
        if all_media:
            media = all_media[0]
            if media.local_path and Path(media.local_path).exists():
                try:
                    return await self._send_media_by_mime(
                        chat_id,
                        media.local_path,
                        _guess_mime(media.local_path),
                        caption=text,
                        ctx_token=ctx_token,
                    )
                except Exception as exc:
                    logger.exception(
                        f"{self.channel_name}: media send failed, falling back to text"
                    )
                    err_str = str(exc)
                    if "upload" in err_str.lower() or "CDN" in err_str:
                        notice = "⚠️ 媒体文件上传失败，请稍后重试。"
                    elif "download" in err_str.lower():
                        notice = "⚠️ 媒体文件下载失败，请检查链接是否可访问。"
                    else:
                        notice = "⚠️ 媒体文件发送失败，请稍后重试。"
                    asyncio.create_task(self._send_error_notice(chat_id, notice, ctx_token))
                    if not text:
                        text = f"[媒体消息: {media.filename}]"

        # 发送纯文本
        plain = _markdown_to_plaintext(text) if text else ""
        if not plain:
            return ""
        return await self._send_text(chat_id, plain, ctx_token)

    def _resolve_context_token(
        self,
        chat_id: str,
        metadata: dict | None = None,
    ) -> str:
        """返回最新可用的 context_token。

        优先级: 最新缓存（来自最近一次 getUpdates） > 消息 metadata 中的原始 token。
        长耗时任务中原始 token 可能已过期，缓存中的更可能有效。
        """
        cached = self._context_tokens.get(chat_id, "")
        meta_token = (metadata or {}).get("context_token", "")

        if cached:
            return cached
        return meta_token

    async def _send_text(self, to: str, text: str, ctx_token: str = "") -> str:
        client_id = f"openakita-wechat-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": client_id,
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
                "context_token": ctx_token or None,
            }
        }
        for attempt in range(1, SEND_RATE_LIMIT_RETRIES + 1):
            await self._rate_limit_wait(to)
            resp = await self._api_post("ilink/bot/sendmessage", body)
            try:
                self._check_send_response(resp, action="sendmessage(text)")
                break
            except RuntimeError:
                if resp.get("ret") == SESSION_EXPIRED_ERRCODE:
                    raise
                if attempt < SEND_RATE_LIMIT_RETRIES:
                    delay = SEND_RATE_LIMIT_BASE_DELAY_S * (2 ** (attempt - 1))
                    logger.warning(
                        f"{self.channel_name}: sendmessage(text) ret={resp.get('ret')}, "
                        f"retry {attempt}/{SEND_RATE_LIMIT_RETRIES} after {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    body["msg"]["client_id"] = f"openakita-wechat-{uuid.uuid4().hex[:12]}"
                else:
                    raise
        self._send_count += 1
        logger.info(f"{self.channel_name}: text sent to={_redact_id(to)}, len={len(text)}")
        return body["msg"]["client_id"]

    async def _send_media_by_mime(
        self,
        chat_id: str,
        file_path: str,
        mime: str,
        *,
        caption: str = "",
        ctx_token: str = "",
    ) -> str:
        uploaded = await self._cdn_upload(
            file_path,
            chat_id,
            mime,
            context_token=ctx_token,
        )
        plain_caption = _markdown_to_plaintext(caption) if caption else ""

        client_ids: list[str] = []

        # 先发 caption
        if plain_caption:
            cid = await self._send_text(chat_id, plain_caption, ctx_token)
            client_ids.append(cid)

        # 构造媒体 item
        client_id = f"openakita-wechat-{uuid.uuid4().hex[:12]}"
        aeskey_hex = uploaded["aeskey"]
        media_ref = {
            "encrypt_query_param": uploaded["download_param"],
            "aes_key": base64.b64encode(aeskey_hex.encode()).decode(),
            "encrypt_type": 1,
        }

        if mime.startswith("image/"):
            item = {
                "type": ITEM_IMAGE,
                "image_item": {
                    "aeskey": aeskey_hex,
                    "media": media_ref,
                    "mid_size": uploaded["filesize_cipher"],
                },
            }
        elif mime.startswith("video/"):
            item = {
                "type": ITEM_VIDEO,
                "video_item": {
                    "aeskey": aeskey_hex,
                    "media": media_ref,
                    "video_size": uploaded["filesize_cipher"],
                },
            }
        elif mime.startswith("audio/"):
            vsec = _voice_duration_seconds(Path(file_path))
            item = {
                "type": ITEM_VOICE,
                "voice_item": {
                    "aeskey": aeskey_hex,
                    "media": media_ref,
                    "voice_time": vsec,
                    "mid_size": uploaded["filesize_cipher"],
                },
            }
        else:
            fname = Path(file_path).name
            item = {
                "type": ITEM_FILE,
                "file_item": {
                    "aeskey": aeskey_hex,
                    "media": media_ref,
                    "file_name": fname,
                    "len": str(uploaded["filesize_raw"]),
                },
            }

        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": chat_id,
                "client_id": client_id,
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [item],
                "context_token": ctx_token or None,
            }
        }
        for attempt in range(1, SEND_RATE_LIMIT_RETRIES + 1):
            await self._rate_limit_wait(chat_id)
            resp = await self._api_post("ilink/bot/sendmessage", body)
            try:
                self._check_send_response(resp, action="sendmessage(media)")
                break
            except RuntimeError:
                if resp.get("ret") == SESSION_EXPIRED_ERRCODE:
                    raise
                if attempt < SEND_RATE_LIMIT_RETRIES:
                    delay = SEND_RATE_LIMIT_BASE_DELAY_S * (2 ** (attempt - 1))
                    logger.warning(
                        f"{self.channel_name}: sendmessage(media) ret={resp.get('ret')}, "
                        f"retry {attempt}/{SEND_RATE_LIMIT_RETRIES} after {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    body["msg"]["client_id"] = f"openakita-wechat-{uuid.uuid4().hex[:12]}"
                else:
                    raise
        self._send_count += 1
        logger.info(
            f"{self.channel_name}: media sent to={_redact_id(chat_id)}, "
            f"mime={mime}, file={Path(file_path).name}"
        )
        return body["msg"]["client_id"]

    async def send_file(self, chat_id: str, file_path: str, caption: str | None = None) -> str:
        ctx_token = self._resolve_context_token(chat_id)
        mime = _guess_mime(file_path)
        return await self._send_media_by_mime(
            chat_id,
            file_path,
            mime,
            caption=caption or "",
            ctx_token=ctx_token,
        )

    def _prepare_voice_file_for_wechat(self, voice_path: str) -> tuple[str, list[Path]]:
        """返回可上传的 SILK 路径及需在发送后删除的临时文件。"""
        import io
        import wave

        src = Path(voice_path)
        if not src.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")
        cleanup: list[Path] = []
        if src.suffix.lower() in (".silk", ".slk"):
            return str(src), cleanup
        try:
            import pilk  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "发送微信语音需要 pilk。请运行: pip install pilk（或安装 qqbot optional extra）"
            ) from e
        raw_bytes = src.read_bytes()
        pcm_data: bytes
        sample_rate = 24000
        try:
            with wave.open(io.BytesIO(raw_bytes)) as wf:
                sample_rate = wf.getframerate()
                pcm_data = wf.readframes(wf.getnframes())
        except wave.Error:
            pcm_data = raw_bytes
        fd_pcm, pcm_name = tempfile.mkstemp(suffix=".pcm", prefix="wechat_v_")
        fd_silk, silk_name = tempfile.mkstemp(suffix=".silk", prefix="wechat_v_")
        os.close(fd_pcm)
        os.close(fd_silk)
        tmp_pcm = Path(pcm_name)
        tmp_silk = Path(silk_name)
        cleanup.extend([tmp_pcm, tmp_silk])
        try:
            tmp_pcm.write_bytes(pcm_data)
            pilk.encode(str(tmp_pcm), str(tmp_silk), pcm_rate=sample_rate, tencent=True)
        except Exception:
            for p in cleanup:
                p.unlink(missing_ok=True)
            raise
        return str(tmp_silk), cleanup

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        silk_path, cleanup = self._prepare_voice_file_for_wechat(voice_path)
        try:
            ctx_token = self._resolve_context_token(chat_id)
            return await self._send_media_by_mime(
                chat_id,
                silk_path,
                "audio/silk",
                caption=caption or "",
                ctx_token=ctx_token,
            )
        finally:
            for p in cleanup:
                p.unlink(missing_ok=True)

    # ==================== Typing ====================
    # iLink Bot API 不支持通过同一 client_id 更新消息（API 按 client_id 去重），
    # 因此无法实现"单气泡流式更新"。仅使用原生 typing 指示器。

    _TYPING_STALE_THRESHOLD_S = 1800  # 30 min

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        existing = self._typing_start_time.get(chat_id)
        if existing is None or (time.time() - existing) > self._TYPING_STALE_THRESHOLD_S:
            self._typing_start_time[chat_id] = time.time()
        ticket = await self._get_typing_ticket(chat_id)
        if not ticket:
            return
        try:
            await self._api_post(
                "ilink/bot/sendtyping",
                {
                    "ilink_user_id": chat_id,
                    "typing_ticket": ticket,
                    "status": TYPING_START,
                },
                timeout_s=DEFAULT_CONFIG_TIMEOUT_MS / 1000,
            )
        except Exception:
            logger.debug(f"{self.channel_name}: sendTyping failed (ignored)")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        ticket = await self._get_typing_ticket(chat_id)
        if not ticket:
            return
        try:
            await self._api_post(
                "ilink/bot/sendtyping",
                {
                    "ilink_user_id": chat_id,
                    "typing_ticket": ticket,
                    "status": TYPING_CANCEL,
                },
                timeout_s=DEFAULT_CONFIG_TIMEOUT_MS / 1000,
            )
        except Exception:
            logger.debug(f"{self.channel_name}: clearTyping failed (ignored)")

    def format_final_footer(self, chat_id: str, thread_id: str | None = None) -> str | None:
        start = self._typing_start_time.pop(chat_id, None)
        if start is None:
            return None
        elapsed = time.time() - start
        is_debug = chat_id in self._debug_users
        if not self._footer_elapsed and not is_debug:
            return None
        if elapsed < 60:
            footer = f"\n\n⏱ 耗时 {elapsed:.1f}s"
        else:
            minutes = int(elapsed // 60)
            secs = elapsed % 60
            footer = f"\n\n⏱ 耗时 {minutes}m{secs:.0f}s"
        if is_debug:
            footer += (
                f"\n[debug] msgs_recv={self._msg_count} msgs_sent={self._send_count}"
                f" ctx_tokens={len(self._context_tokens)}"
                f" ver={OPENCLAW_COMPAT_VERSION}"
            )
        return footer

    async def _get_typing_ticket(self, user_id: str) -> str:
        now = time.time()
        entry = self._ticket_cache.get(user_id)
        if entry and now < entry.next_fetch_at:
            return entry.ticket

        ctx_token = self._context_tokens.get(user_id, "")
        try:
            resp = await self._api_post(
                "ilink/bot/getconfig",
                {"ilink_user_id": user_id, "context_token": ctx_token or None},
                timeout_s=DEFAULT_CONFIG_TIMEOUT_MS / 1000,
            )
            if resp.get("ret", 0) == 0:
                ticket = resp.get("typing_ticket", "")
                import random

                self._ticket_cache[user_id] = _TicketEntry(
                    ticket=ticket,
                    next_fetch_at=now + random.random() * CONFIG_CACHE_TTL_S,
                    retry_delay_s=CONFIG_CACHE_INITIAL_RETRY_S,
                    ever_succeeded=True,
                )
                return ticket
        except Exception:
            logger.debug(f"{self.channel_name}: getConfig failed for {_redact_id(user_id)}")

        if entry:
            new_delay = min(entry.retry_delay_s * 2, CONFIG_CACHE_MAX_RETRY_S)
            entry.next_fetch_at = now + new_delay
            entry.retry_delay_s = new_delay
        else:
            self._ticket_cache[user_id] = _TicketEntry(
                next_fetch_at=now + CONFIG_CACHE_INITIAL_RETRY_S,
            )
        return entry.ticket if entry else ""

    # ==================== CDN 媒体 ====================

    async def _cdn_download(
        self,
        encrypt_query_param: str,
        aes_key: bytes | None,
        *,
        full_url: str = "",
    ) -> bytes:
        if full_url:
            url = full_url
        else:
            url = (
                f"{self.config.cdn_base_url}/download"
                f"?encrypted_query_param={quote(encrypt_query_param, safe='')}"
            )
        resp = await self._http.get(url, timeout=30.0)
        if not resp.is_success:
            err_msg = resp.headers.get("x-error-message", f"status {resp.status_code}")
            raise RuntimeError(f"CDN download error {resp.status_code}: {err_msg}")
        data = resp.content
        if aes_key and len(aes_key) == 16:
            data = _decrypt_aes_ecb(data, aes_key)
        return data

    async def _cdn_upload(
        self,
        file_path: str,
        to_user_id: str,
        mime: str,
        *,
        context_token: str = "",
    ) -> dict:
        plaintext = Path(file_path).read_bytes()
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        filesize = _aes_ecb_padded_size(rawsize)
        filekey = os.urandom(16).hex()
        aeskey = os.urandom(16)

        if mime.startswith("image/"):
            media_type = UPLOAD_IMAGE
        elif mime.startswith("video/"):
            media_type = UPLOAD_VIDEO
        elif mime.startswith("audio/"):
            media_type = UPLOAD_VOICE
        else:
            media_type = UPLOAD_FILE

        upload_body: dict = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey.hex(),
        }
        if context_token:
            upload_body["context_token"] = context_token
        upload_resp = await self._api_post("ilink/bot/getuploadurl", upload_body)
        self._check_send_response(upload_resp, action="getuploadurl")
        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param")
        if not upload_full_url and not upload_param:
            logger.error(
                f"{self.channel_name}: getUploadUrl returned no upload URL, "
                f"resp keys={list(upload_resp.keys())}, "
                f"ret={upload_resp.get('ret')}, errmsg={upload_resp.get('errmsg', '')!r}"
            )
            raise RuntimeError(
                f"getUploadUrl returned no upload URL "
                f"(ret={upload_resp.get('ret')}, errmsg={upload_resp.get('errmsg', '')})"
            )

        ciphertext = _encrypt_aes_ecb(plaintext, aeskey)
        if upload_full_url:
            cdn_url = upload_full_url
        else:
            cdn_url = (
                f"{self.config.cdn_base_url}/upload"
                f"?encrypted_query_param={quote(upload_param, safe='')}"
                f"&filekey={quote(filekey, safe='')}"
            )

        download_param: str | None = None
        last_err: Exception | None = None
        for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
            try:
                resp = await self._http.post(
                    cdn_url,
                    content=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=60.0,
                )
                if 400 <= resp.status_code < 500:
                    err_msg = resp.headers.get("x-error-message", f"status {resp.status_code}")
                    raise RuntimeError(f"CDN upload client error {resp.status_code}: {err_msg}")
                if not resp.is_success:
                    err_msg = resp.headers.get("x-error-message", f"status {resp.status_code}")
                    raise RuntimeError(f"CDN upload server error: {err_msg}")
                download_param = resp.headers.get("x-encrypted-param")
                if not download_param:
                    raise RuntimeError("CDN response missing x-encrypted-param")
                break
            except Exception as e:
                last_err = e
                if isinstance(e, RuntimeError) and "client error" in str(e):
                    raise
                if attempt < UPLOAD_MAX_RETRIES:
                    logger.warning(f"{self.channel_name}: CDN upload attempt {attempt} failed: {e}")
                else:
                    logger.error(
                        f"{self.channel_name}: CDN upload all {UPLOAD_MAX_RETRIES} attempts failed"
                    )

        if not download_param:
            raise last_err or RuntimeError("CDN upload failed")

        return {
            "filekey": filekey,
            "download_param": download_param,
            "aeskey": aeskey.hex(),
            "filesize_raw": rawsize,
            "filesize_cipher": filesize,
        }

    # ==================== 媒体接口（基类） ====================

    async def download_media(self, media: MediaFile) -> Path:
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)
        extra = media.extra or {}
        param = extra.get("encrypt_query_param")
        aes_b64 = extra.get("aes_key", "")
        if not param:
            raise ValueError("WeChat media: missing encrypt_query_param")
        key = _parse_aes_key(aes_b64) if aes_b64 else None
        buf = await self._cdn_download(param, key)
        local = self.media_dir / media.filename
        local.write_bytes(buf)
        media.local_path = str(local)
        media.status = MediaStatus.READY
        return local

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        uploaded = await self._cdn_upload(str(path), "", mime_type)
        mf = MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
            size=path.stat().st_size,
        )
        mf.extra = {
            "encrypt_query_param": uploaded["download_param"],
            "aes_key": base64.b64encode(bytes.fromhex(uploaded["aeskey"])).decode(),
            "filekey": uploaded["filekey"],
        }
        mf.status = MediaStatus.READY
        return mf

    # ==================== 同步游标 & Context Token 持久化 ====================

    def _sync_buf_path(self) -> Path:
        safe_id = self.bot_id.replace(":", "_").replace("/", "_")
        return self._sync_buf_dir / f"{safe_id}.buf"

    def _context_tokens_path(self) -> Path:
        safe_id = self.bot_id.replace(":", "_").replace("/", "_")
        return self._sync_buf_dir / f"{safe_id}.context_tokens.json"

    def _save_sync_buf(self) -> None:
        if not self._get_updates_buf:
            return
        try:
            self._sync_buf_path().write_text(self._get_updates_buf, encoding="utf-8")
        except Exception:
            logger.debug(f"{self.channel_name}: failed to save sync buf")

    def _load_sync_buf(self) -> None:
        p = self._sync_buf_path()
        if p.exists():
            try:
                self._get_updates_buf = p.read_text(encoding="utf-8").strip()
                if self._get_updates_buf:
                    logger.info(
                        f"{self.channel_name}: loaded sync buf ({len(self._get_updates_buf)} bytes)"
                    )
            except Exception:
                self._get_updates_buf = ""

    def _save_context_tokens(self) -> None:
        if not self._context_tokens:
            return
        try:
            self._context_tokens_path().write_text(
                json.dumps(self._context_tokens), encoding="utf-8"
            )
        except Exception:
            logger.debug(f"{self.channel_name}: failed to save context tokens")

    def _load_context_tokens(self) -> None:
        p = self._context_tokens_path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._context_tokens = data
                    logger.info(f"{self.channel_name}: loaded {len(data)} context tokens from disk")
            except Exception:
                logger.debug(f"{self.channel_name}: failed to load context tokens")
