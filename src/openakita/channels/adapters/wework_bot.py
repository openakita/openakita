"""
WeCom Smart Bot adapter

Implements the WeCom Smart Bot API:
- Built-in aiohttp HTTP server for JSON-format callback messages
- Message encryption/decryption (AES-256-CBC, empty receiveid)
- Text / image / mixed / voice / file message reception
- Streaming reply (stream)
- response_url active reply (markdown)
- Markdown @member (<@userid>) / @all (<@all>)

Main differences vs. the self-built app adapter (wework.py):
- Callback messages are JSON (not XML)
- No access_token, agent_id, or secret required
- Messages sent via response_url or passive reply
- receiveid is an empty string

Reference docs:
- Receive messages: https://developer.work.weixin.qq.com/document/path/100719
- Passive reply: https://developer.work.weixin.qq.com/document/path/101031
- Encryption scheme: https://developer.work.weixin.qq.com/document/path/101033
- Active reply: https://developer.work.weixin.qq.com/document/path/101138
"""

import asyncio
import base64
import collections
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

# Lazy imports
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
            import aiohttp.web  # Explicitly import the web submodule

            aiohttp = ah
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("aiohttp"))


# ==================== Smart Bot message encryption/decryption ====================


class BotMsgCrypt:
    """
    WeCom Smart Bot message encryption/decryption utility.

    Differences from the self-built-app WXBizMsgCrypt:
    - Callback/reply uses JSON (not XML)
    - receiveid is the empty string (as specified by the docs)
    - Adds decrypt_media() for decrypting downloaded image/file contents

    Ref: https://developer.work.weixin.qq.com/document/path/101033
    """

    def __init__(self, token: str, encoding_aes_key: str):
        self.token = token
        # EncodingAESKey is a Base64-encoded AES key (43 chars -> 32 bytes)
        self.aes_key = base64.b64decode(encoding_aes_key + "=")

    def _get_sha1(self, *args: str) -> str:
        """Compute the signature."""
        items = sorted(args)
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt a message."""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("Crypto"))

        import os

        # 16-byte random string
        random_str = os.urandom(16)
        text = plaintext.encode("utf-8")
        # Message length in network byte order
        text_length = struct.pack("!I", len(text))
        # Smart Bot uses an empty receiveid
        receiveid = b""

        # plaintext = random(16B) + msg_len(4B) + msg + receiveid
        plain = random_str + text_length + text + receiveid

        # PKCS#7 pad to a multiple of 32 bytes
        block_size = 32
        pad_len = block_size - (len(plain) % block_size)
        plain += bytes([pad_len]) * pad_len

        # AES-256-CBC encrypt
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plain)

        return base64.b64encode(encrypted).decode("utf-8")

    @staticmethod
    def _validate_pkcs7_padding(data: bytes, block_size: int = 32) -> int:
        """Validate PKCS#7 padding and return pad_len; raise ValueError on failure."""
        if not data:
            raise ValueError("empty data for PKCS#7 unpadding")
        pad_len = data[-1]
        if pad_len < 1 or pad_len > block_size or pad_len > len(data):
            raise ValueError(
                f"invalid PKCS#7 pad_len={pad_len} (block_size={block_size}, data_len={len(data)})"
            )
        for i in range(1, pad_len + 1):
            if data[-i] != pad_len:
                raise ValueError(
                    f"PKCS#7 padding byte mismatch at offset -{i}: "
                    f"expected {pad_len}, got {data[-i]}"
                )
        return pad_len

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt a message."""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("Crypto"))

        encrypted = base64.b64decode(ciphertext)

        # AES-256-CBC decrypt
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)

        # Remove PKCS#7 padding (with validation)
        pad_len = self._validate_pkcs7_padding(decrypted)
        content = decrypted[:-pad_len]

        # Parse: random(16B) + msg_len(4B) + msg + receiveid
        msg_len = struct.unpack("!I", content[16:20])[0]
        if 20 + msg_len > len(content):
            raise ValueError(
                f"msg_len={msg_len} exceeds content boundary (content_len={len(content)})"
            )
        msg = content[20 : 20 + msg_len].decode("utf-8")

        return msg

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """
        Verify the callback URL (GET request).

        Returns:
            The decrypted echostr (plaintext, must be returned directly to WeCom).
        """
        signature = self._get_sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            raise ValueError("URL verification signature mismatch")
        return self._decrypt(echostr)

    def decrypt_msg(self, json_body: str, msg_signature: str, timestamp: str, nonce: str) -> str:
        """
        Decrypt a callback message (POST request, JSON format).

        Args:
            json_body: POST request JSON body, format {"encrypt": "..."}
            msg_signature: msg_signature query parameter
            timestamp: timestamp query parameter
            nonce: nonce query parameter

        Returns:
            The decrypted JSON message string.
        """
        data = json.loads(json_body)
        encrypt_str = data.get("encrypt")
        if not encrypt_str:
            raise ValueError("Missing 'encrypt' field in callback JSON")

        # Verify signature
        signature = self._get_sha1(self.token, timestamp, nonce, encrypt_str)
        if signature != msg_signature:
            raise ValueError("Message signature mismatch")

        return self._decrypt(encrypt_str)

    def encrypt_reply(self, reply_json: str, nonce: str, timestamp: str | None = None) -> str:
        """
        Encrypt a passive reply message.

        Args:
            reply_json: JSON string of the reply content
            nonce: nonce from the callback URL (must match the callback)
            timestamp: timestamp (optional, defaults to current time)

        Returns:
            Encrypted JSON string, format:
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
        Decrypt media file content.

        WeCom Smart Bot image/file URL download content is AES-encrypted;
        decrypt using the same EncodingAESKey.

        Args:
            encrypted_data: raw encrypted bytes downloaded from the URL

        Returns:
            Decrypted file content.
        """
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("Crypto"))

        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted_data)

        # Remove PKCS#7 padding (with validation)
        pad_len = self._validate_pkcs7_padding(decrypted)
        return decrypted[:-pad_len]


# ==================== Streaming session ====================

# Streaming session timeout (seconds) — WeCom refreshes for at most 6 minutes
STREAM_TIMEOUT = 330  # 5.5 minutes, leaving 30 seconds of margin

# Streaming message settle delay (seconds) — after marking finished, wait this long before actually finishing.
# Used to wait for send_image that is enqueued after send_message.
STREAM_SETTLE_DELAY = 8


@dataclass
class StreamSession:
    """
    Streaming message session.

    Manages the full lifecycle of a single stream passive reply:
    1. User message arrives -> create session, return stream(finish=false)
    2. WeCom periodically sends stream refresh callbacks -> return current content
    3. Agent finishes -> updates content + pending_images
    4. Next refresh callback -> return finish=true + content + images
    """

    stream_id: str  # Streaming session ID (unique)
    chat_id: str  # Chat ID
    user_id: str  # User ID
    msgid: str  # Original user message ID
    response_url: str = ""  # response_url fallback

    # Agent output
    content: str = ""  # Text content (markdown)
    pending_images: list = dataclass_field(default_factory=list)  # [(base64_str, md5_str)]
    is_finished: bool = False  # Whether the Agent has finished processing

    # Timing
    created_at: float = 0.0
    last_updated_at: float = 0.0  # Last send_message / send_image update

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


# ==================== Configuration ====================


@dataclass
class WeWorkBotConfig:
    """WeCom Smart Bot configuration."""

    corp_id: str
    token: str
    encoding_aes_key: str
    callback_port: int = 9880
    callback_host: str = "0.0.0.0"

    def __post_init__(self) -> None:
        if not self.corp_id or not self.corp_id.strip():
            raise ValueError("WeWorkBotConfig: corp_id is required")
        if not self.token or not self.token.strip():
            raise ValueError("WeWorkBotConfig: token is required")
        if not self.encoding_aes_key or not self.encoding_aes_key.strip():
            raise ValueError("WeWorkBotConfig: encoding_aes_key is required")
        if not (1 <= self.callback_port <= 65535):
            raise ValueError(f"WeWorkBotConfig: invalid callback_port {self.callback_port}")


# ==================== Adapter ====================


class WeWorkBotAdapter(ChannelAdapter):
    """
    WeCom Smart Bot adapter.

    Supports:
    - Built-in HTTP callback server (receives JSON-encrypted messages)
    - Message encryption/decryption (AES-256-CBC, empty receiveid)
    - Text / image / mixed / voice / file message reception
    - Streaming passive reply (stream) — supports mixed text + images
    - response_url active reply (markdown, fallback)

    Reply mechanism (streaming passive reply):
    1. Receive user message -> create StreamSession, passive-reply stream(finish=false)
    2. WeCom periodically sends stream refresh callbacks (about every 1-2s)
    3. Agent still processing -> refresh callback returns current content (finish=false)
    4. Agent finishes -> send_message updates text, send_image queues images
    5. Next refresh callback -> returns finish=true + content + images
    6. Images are sent via stream.msg_item as base64+md5 (only when finish=true)

    Limits:
    - Images: JPG/PNG only, <= 10MB each, up to 10 images
    - Stream lasts at most 6 minutes; on timeout, falls back to response_url

    Note: the callback URL must be publicly accessible.
    """

    channel_name = "wework"

    capabilities = {
        "streaming": True,
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
        "mention": True,
    }

    # Expiration cleanup interval
    CLEANUP_INTERVAL = 120

    @staticmethod
    def _truncate_utf8(text: str, max_bytes: int) -> str:
        """Truncate text to at most max_bytes bytes while preserving UTF-8 integrity."""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        truncated = encoded[:max_bytes]
        while truncated and truncated[-1] & 0xC0 == 0x80:
            truncated = truncated[:-1]
        if truncated and truncated[-1] & 0x80:
            truncated = truncated[:-1]
        return truncated.decode("utf-8", errors="ignore")

    def __init__(
        self,
        corp_id: str,
        token: str,
        encoding_aes_key: str,
        callback_port: int = 9880,
        callback_host: str = "0.0.0.0",
        media_dir: Path | None = None,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
    ):
        super().__init__(
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id
        )

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

        # HTTP callback server
        self._callback_app: Any | None = None
        self._callback_runner: Any | None = None
        self._callback_site: Any | None = None

        # ── Stream session management ──
        self._stream_sessions: dict[str, StreamSession] = {}  # stream_id -> session
        self._chat_streams: dict[str, str] = {}  # chat_key -> stream_id
        self._msgid_to_stream: dict[str, str] = {}  # msgid -> stream_id
        self._stream_lock = asyncio.Lock()

        # response_url fallback storage (used when stream times out)
        self._msgid_response_urls: dict[str, str] = {}
        self._response_urls: dict[str, list[str]] = {}

        # Message dedup: HTTP callbacks may be redelivered due to retries
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

    def _chat_key(self, chat_id: str, user_id: str) -> str:
        """Generate a unique key for a chat session."""
        return f"{chat_id}:{user_id}"

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        """Start the Smart Bot adapter (including the HTTP callback server)."""
        _import_httpx()

        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._running = True

        # Initialize the crypto utility
        self._crypt = BotMsgCrypt(
            token=self.config.token,
            encoding_aes_key=self.config.encoding_aes_key,
        )
        logger.info("WeWorkBot: message encryption initialized")

        # Start the HTTP callback server
        await self._start_callback_server()

        # Start the expired response_url cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_urls())

        logger.info("WeWorkBot adapter started (stream mode + response_url fallback)")

    async def stop(self) -> None:
        """Stop the adapter."""
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

    # ==================== HTTP callback server ====================

    async def _start_callback_server(self) -> None:
        """Start the aiohttp HTTP callback server."""
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
            if e.errno in (10048, 98) or "Address already in use" in str(e):
                raise ConnectionError(
                    f"WeCom callback port {self.config.callback_port} is already in use; "
                    f"change WEWORK_CALLBACK_PORT or free the port."
                ) from e
            if e.errno in (10013, 13) or "Permission" in str(e):
                raise ConnectionError(
                    f"Failed to bind WeCom callback port {self.config.callback_port}: permission denied; "
                    f"try using a port above 1024."
                ) from e
            raise ConnectionError(f"WeCom callback server failed to start: {e}") from e

    async def _handle_health(self, request: "aiohttp.web.Request") -> "aiohttp.web.Response":
        """Health check endpoint."""
        return aiohttp.web.json_response({"status": "ok", "channel": "wework", "mode": "bot"})

    async def _handle_get_callback(self, request: "aiohttp.web.Request") -> "aiohttp.web.Response":
        """Handle GET callback — WeCom URL verification."""
        if not self._crypt:
            return aiohttp.web.Response(text="Encryption not configured", status=500)

        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")

        try:
            reply_echostr = self._crypt.verify_url(msg_signature, timestamp, nonce, echostr)
            logger.info("WeWorkBot: URL verification successful")
            return aiohttp.web.Response(text=reply_echostr)
        except Exception as e:
            logger.error(f"WeWorkBot: URL verification failed: {e}")
            return aiohttp.web.Response(text="Verification failed", status=403)

    async def _handle_post_callback(self, request: "aiohttp.web.Request") -> "aiohttp.web.Response":
        """
        Handle POST callback — receive encrypted JSON messages.

        Streaming mode:
        - New user message -> create StreamSession, passive-reply stream(finish=false)
        - Stream refresh callback -> return current content / finish=true + images
        """
        if not self._crypt:
            return aiohttp.web.Response(text="Encryption not configured", status=500)

        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")

        body = await request.text()

        try:
            # Decrypt the JSON message
            decrypted_json = self._crypt.decrypt_msg(body, msg_signature, timestamp, nonce)
            logger.debug(f"WeWorkBot: Decrypted: {decrypted_json[:300]}")

            msg_data = json.loads(decrypted_json)
            msg_type = msg_data.get("msgtype", "")

            if msg_type == "stream":
                return await self._handle_stream_refresh(msg_data, nonce, timestamp)
            else:
                # New user message
                return await self._handle_new_message(msg_data, nonce, timestamp)

        except Exception as e:
            logger.error(f"WeWorkBot: Message processing failed: {e}", exc_info=True)
            # Return empty 200 to avoid WeCom retries
            return aiohttp.web.Response(text="", status=200)

    async def _handle_stream_refresh(
        self, msg_data: dict, nonce: str, timestamp: str
    ) -> "aiohttp.web.Response":
        """
        Handle a stream refresh callback.

        WeCom sends a refresh callback roughly every 1-2 seconds with stream.id.
        We return the current Agent output state:
        - Agent not finished -> finish=false, content=current text
        - Agent finished -> finish=true, content=final text, msg_item=image list
        - Unknown stream_id -> finish=true to terminate (prevents stuck stream)
        """
        stream_data = msg_data.get("stream", {})
        stream_id = stream_data.get("id", "")

        async with self._stream_lock:
            session = self._stream_sessions.get(stream_id)

        if not session:
            # Unknown stream_id -> terminate directly
            logger.warning(f"WeWorkBot: Unknown stream_id={stream_id}, terminating")
            reply_payload = json.dumps(
                {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "finish": True, "content": ""},
                },
                ensure_ascii=False,
            )
            encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
            return aiohttp.web.Response(text=encrypted, content_type="application/json")

        # Check for timeout
        elapsed = time.time() - session.created_at
        if elapsed > STREAM_TIMEOUT and not session.is_finished:
            logger.warning(
                f"WeWorkBot: Stream {stream_id} timeout ({elapsed:.0f}s), force finishing"
            )
            session.is_finished = True
            if not session.content:
                session.content = "Processing timeout, please resend the message"

        # Determine whether we can truly finish the stream.
        # is_finished=True means the Agent has called send_message, but we still wait for the settle delay
        # during which send_image can still enqueue images.
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
            # ── settle done: return finish=true + content + images ──
            final_content = self._truncate_utf8(session.content or "", 20480)
            reply_stream: dict[str, Any] = {
                "id": stream_id,
                "finish": True,
                "content": final_content,
            }

            # Attach images to msg_item (only effective when finish=true)
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
                    f"WeWorkBot: Stream {stream_id} finishing with {len(msg_items)} image(s)"
                )

            reply_payload = json.dumps(
                {"msgtype": "stream", "stream": reply_stream},
                ensure_ascii=False,
            )

            # Clean up session
            await self._cleanup_stream_session(stream_id)

            logger.info(
                f"WeWorkBot: Stream {stream_id} finished, "
                f"content={len(session.content)} chars, "
                f"images={len(session.pending_images)}"
            )
        else:
            # ── Agent still processing / settle waiting: return finish=false + current content ──
            # Note: even if is_finished=True but settle hasn't elapsed, return finish=false;
            # the user will see real-time text content in WeCom (stream continues to display)
            reply_payload = json.dumps(
                {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": False,
                        "content": self._truncate_utf8(session.content or "", 20480),
                    },
                },
                ensure_ascii=False,
            )

        encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
        return aiohttp.web.Response(text=encrypted, content_type="application/json")

    async def _cleanup_stream_session(self, stream_id: str) -> None:
        """Clean up a completed stream session and its associated mappings."""
        async with self._stream_lock:
            session = self._stream_sessions.pop(stream_id, None)
            if session:
                # Clean up chat_key -> stream_id
                chat_key = self._chat_key(session.chat_id, session.user_id)
                if self._chat_streams.get(chat_key) == stream_id:
                    self._chat_streams.pop(chat_key, None)
                # Clean up msgid -> stream_id
                if self._msgid_to_stream.get(session.msgid) == stream_id:
                    self._msgid_to_stream.pop(session.msgid, None)

    # ==================== New message handling ====================

    async def _handle_new_message(
        self, msg_data: dict, nonce: str, timestamp: str
    ) -> "aiohttp.web.Response":
        """
        Handle a new user message (streaming mode):
        1. Create StreamSession and generate a unique stream_id
        2. Store response_url (fallback when stream times out)
        3. Passive-reply stream(finish=false) to open the streaming session
        4. Asynchronously process the message and emit it to the gateway
        5. The Agent's reply is passed through the stream session
        6. WeCom's stream refresh callbacks read session content
        """
        msg_type = msg_data.get("msgtype", "")
        msgid = msg_data.get("msgid", "")
        chatid = msg_data.get("chatid", "")  # Only present in group chats
        chattype = msg_data.get("chattype", "single")
        from_info = msg_data.get("from", {})
        userid = from_info.get("userid", "")
        response_url = msg_data.get("response_url", "")

        # Determine chat_id
        chat_id = chatid if chattype == "group" else userid
        chat_key = self._chat_key(chat_id, userid)

        # Store response_url (fallback)
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

        # event type (e.g. entering a chat)
        if msg_type == "event":
            event_data = msg_data.get("event", {})
            event_type = event_data.get("eventtype", msg_data.get("event_type", "unknown"))
            logger.info(
                "WeWorkBot: Event received: type=%s, user=%s, chat=%s, data=%s",
                event_type,
                userid,
                chat_id,
                json.dumps(event_data, ensure_ascii=False)[:200],
            )
            await self._emit_event(
                event_type,
                {
                    "chatid": chat_id,
                    "chattype": chattype,
                    "userid": userid,
                    "raw": msg_data,
                },
            )
            reply_payload = json.dumps({})
            encrypted = self._crypt.encrypt_reply(reply_payload, nonce, timestamp)
            return aiohttp.web.Response(text=encrypted, content_type="application/json")

        # ── Create StreamSession ──
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
            f"WeWorkBot: Created stream session {stream_id} for msgid={msgid}, chat={chat_id}"
        )

        # Asynchronously process the actual message
        asyncio.create_task(self._process_message(msg_data))

        # Passive reply: open the stream (finish=false, empty content)
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
        return aiohttp.web.Response(text=encrypted, content_type="application/json")

    # ==================== Message parsing ====================

    async def _process_message(self, msg_data: dict) -> None:
        """Parse the decrypted JSON message, convert to UnifiedMessage and emit."""
        try:
            msg_type = msg_data.get("msgtype", "")
            msgid = msg_data.get("msgid", "")
            chatid = msg_data.get("chatid", "")
            chattype = msg_data.get("chattype", "single")
            from_info = msg_data.get("from", {})
            userid = from_info.get("userid", "")

            # Message dedup
            if msgid:
                if msgid in self._seen_message_ids:
                    logger.debug(f"WeWorkBot: duplicate message ignored: {msgid}")
                    return
                self._seen_message_ids[msgid] = None
                while len(self._seen_message_ids) > self._seen_message_ids_max:
                    self._seen_message_ids.popitem(last=False)

            chat_id = chatid if chattype == "group" else userid
            chat_type_str = "group" if chattype == "group" else "private"

            if msg_type == "text":
                await self._handle_text_message(msg_data, msgid, userid, chat_id, chat_type_str)
            elif msg_type == "image":
                await self._handle_image_message(msg_data, msgid, userid, chat_id, chat_type_str)
            elif msg_type == "mixed":
                await self._handle_mixed_message(msg_data, msgid, userid, chat_id, chat_type_str)
            elif msg_type == "voice":
                await self._handle_voice_message(msg_data, msgid, userid, chat_id, chat_type_str)
            elif msg_type == "file":
                await self._handle_file_message(msg_data, msgid, userid, chat_id, chat_type_str)
            elif msg_type == "video":
                await self._handle_video_message(msg_data, msgid, userid, chat_id, chat_type_str)
            else:
                logger.info(f"WeWorkBot: Unhandled message type: {msg_type}")

        except Exception as e:
            logger.error(f"WeWorkBot: Error processing message: {e}", exc_info=True)

    async def _handle_text_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """Handle a text message."""
        text_data = msg_data.get("text", {})
        text_content = text_data.get("content", "")

        # Handle quoted messages
        quote_data = msg_data.get("quote")
        if quote_data:
            quote_text = self._extract_quote_text(quote_data)
            if quote_text:
                text_content = f"[Quote: {quote_text}]\n{text_content}"

        # In WeCom Smart Bot HTTP callback mode, group messages are only delivered when the bot is @-mentioned,
        # so all group messages reaching here have actually mentioned the bot. The ^@\S+ regex is a conservative
        # secondary check (message text starts with @mention); for DMs this check is unnecessary.
        is_mentioned = bool(
            msg_data.get("chattype") == "group" and re.match(r"^@\S+", text_content)
        )
        is_direct_message = chat_type == "private"

        # Strip the @bot mention in group chats
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
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
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
        """Handle an image message (DM-only)."""
        image_data = msg_data.get("image", {})
        image_url = image_data.get("url", "")

        media = MediaFile.create(
            filename=f"{msgid}.jpg",
            mime_type="image/jpeg",
            url=image_url,
        )
        # Mark URL content as AES-encrypted (must be decrypted on download)
        media.extra = {"aes_encrypted": True}

        content = MessageContent(images=[media])

        is_direct_message = chat_type == "private"
        # Smart Bot HTTP callbacks only deliver group messages when @-mentioned, so group image messages have is_mentioned=True
        is_mentioned = msg_data.get("chattype") == "group"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
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
        """Handle a mixed text-and-image message."""
        mixed_data = msg_data.get("mixed", {})
        msg_items = mixed_data.get("msg_item", [])

        text_parts = []
        images = []
        files = []

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
            elif item_type == "file":
                file_data = item.get("file", {})
                file_url = file_data.get("url", "")
                file_name = file_data.get("filename", f"{msgid}_{len(files)}")
                media = MediaFile.create(
                    filename=file_name,
                    mime_type="application/octet-stream",
                    url=file_url,
                )
                media.extra = {"aes_encrypted": True}
                files.append(media)

        # Handle quote
        quote_data = msg_data.get("quote")
        if quote_data:
            quote_text = self._extract_quote_text(quote_data)
            if quote_text:
                text_parts.insert(0, f"[Quote: {quote_text}]")

        # Smart Bot HTTP callbacks only deliver group messages when @-mentioned; ^@\S+ is a conservative secondary check
        combined_text = "\n".join(text_parts) if text_parts else None
        is_mentioned = bool(
            msg_data.get("chattype") == "group"
            and combined_text
            and re.match(r"^@\S+", combined_text)
        )
        is_direct_message = chat_type == "private"

        # Strip @mention in group chats
        if combined_text and msg_data.get("chattype") == "group":
            combined_text = re.sub(r"^@\S+\s*", "", combined_text).strip()

        content = MessageContent(
            text=combined_text,
            images=images,
            files=files,
        )

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
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
        Handle a voice message (DM-only).

        WeCom auto-transcribes voice to text.
        """
        voice_data = msg_data.get("voice", {})
        transcription = voice_data.get("content", "").strip()

        if transcription:
            content = MessageContent(text=transcription)
        else:
            logger.warning("[WeWorkBot] Voice transcription empty, msgid=%s", msgid)
            content = MessageContent(text="[Voice message not recognized by the platform; please resend or use text]")

        is_direct_message = chat_type == "private"
        # Smart Bot HTTP callbacks only deliver group messages when @-mentioned, so group voice messages have is_mentioned=True
        is_mentioned = msg_data.get("chattype") == "group"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
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
        """Handle a file message (DM-only, max 100MB)."""
        file_data = msg_data.get("file", {})
        file_url = file_data.get("url", "")

        media = MediaFile.create(
            filename=f"file_{msgid}",
            mime_type="application/octet-stream",
            url=file_url,
        )
        # URL content is AES-encrypted
        media.extra = {"aes_encrypted": True}

        content = MessageContent(files=[media])

        is_direct_message = chat_type == "private"
        # Smart Bot HTTP callbacks only deliver group messages when @-mentioned, so group file messages have is_mentioned=True
        is_mentioned = msg_data.get("chattype") == "group"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
        )

        self._log_message(unified)
        await self._emit_message(unified)

    async def _handle_video_message(
        self,
        msg_data: dict,
        msgid: str,
        userid: str,
        chat_id: str,
        chat_type: str,
    ) -> None:
        """Handle a video message."""
        video_data = msg_data.get("video", {})
        video_url = video_data.get("url", "")

        media = MediaFile.create(
            filename=video_data.get("filename", f"video_{msgid}.mp4"),
            mime_type="video/mp4",
            url=video_url,
        )
        media.extra = {"aes_encrypted": True}

        content = MessageContent(videos=[media])

        is_direct_message = chat_type == "private"
        is_mentioned = msg_data.get("chattype") == "group"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"ww_{userid}",
            channel_user_id=userid,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            raw=msg_data,
            metadata={
                "is_group": chat_type == "group",
                "sender_name": "",
                "chat_name": msg_data.get("chatname", ""),
            },
        )

        self._log_message(unified)
        await self._emit_message(unified)

    def _extract_quote_text(self, quote_data: dict) -> str:
        """Extract text content from a quote structure."""
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
                    parts.append("[Image]")
            return " ".join(parts)
        elif quote_type == "image":
            return "[Image]"
        elif quote_type == "voice":
            return quote_data.get("voice", {}).get("content", "[Voice]")
        elif quote_type == "file":
            return "[File]"
        return ""

    # ==================== Message sending ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        Send a message (streaming mode).

        Locate the associated StreamSession, update its text content, and mark it finished.
        The next stream refresh callback reads the session and returns finish=true.

        If the stream session doesn't exist (cleaned up due to timeout), falls back to response_url.

        Lookup strategy:
        1. reply_to -> locate stream session by msgid
        2. chat_id -> locate stream session by chat_key
        3. Fallback -> response_url (when stream has timed out or finished)
        """
        text = message.content.text or ""
        chat_id = message.chat_id

        # ── Strategy 1: reply_to -> exact match on stream session ──
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

        # ── Strategy 2: match stream session by chat_id + user_id ──
        # In group chats we need an exact user_id match to avoid matching other users' streams
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

        # ── Fallback: response_url ──
        logger.info(
            f"WeWorkBot: No active stream for chat_id={chat_id}, falling back to response_url"
        )
        return await self._send_via_response_url_fallback(chat_id, message.reply_to, text)

    def _find_stream_by_chat(self, chat_id: str, user_id: str | None = None) -> str | None:
        """
        Look up an active stream session.

        Prefers exact chat_key (chat_id:user_id) match (required for group chats).
        Without a user_id, falls back to chat_id prefix matching (DM compatibility).
        """
        if user_id:
            # Exact match — each user has an independent stream in group chats
            chat_key = self._chat_key(chat_id, user_id)
            sid = self._chat_streams.get(chat_key)
            if sid:
                session = self._stream_sessions.get(sid)
                if session:
                    return sid

        # Fallback: prefix match (in DMs chat_id == user_id, so there is exactly one match)
        for key, sid in list(self._chat_streams.items()):
            if key.startswith(f"{chat_id}:"):
                session = self._stream_sessions.get(sid)
                if session:
                    return sid
        return None

    # ── response_url fallback (used when stream is unavailable) ──

    async def _send_via_response_url_fallback(
        self, chat_id: str, reply_to: str | None, text: str
    ) -> str:
        """
        Fallback: send a markdown message via response_url.

        Called only when stream is unavailable (timed out or already finished).
        response_url is valid for 1 hour and can be called only once.

        Markdown supports ``<@userid>`` to mention members and ``<@all>`` to mention everyone.
        """
        # Exact match by msgid
        url = None
        if reply_to:
            url = self._msgid_response_urls.pop(reply_to, None)
            if url:
                self._remove_url_from_lists(url)

        # Match by chat_key
        if not url:
            url = self._pop_response_url(chat_id)
            if url:
                self._remove_url_from_msgid_map(url)

        if not url:
            raise RuntimeError(
                f"WeWorkBot: No response_url for chat_id={chat_id}, already consumed or expired"
            )

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
                raise RuntimeError(
                    f"WeWorkBot: response_url reply failed: "
                    f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
                )

            logger.info(f"WeWorkBot: Sent via response_url fallback ({len(text)} chars)")
            return "response_url_sent"

        except Exception as e:
            raise RuntimeError(f"WeWorkBot: response_url request failed: {e}") from e

    def _pop_response_url(self, chat_id: str) -> str | None:
        """Pop a usable response_url from the chat_key fallback queue."""
        for key, urls in list(self._response_urls.items()):
            if key.startswith(f"{chat_id}:") and urls:
                url = urls.pop(0)
                if not urls:
                    self._response_urls.pop(key, None)
                return url
        return None

    def _remove_url_from_lists(self, url: str) -> None:
        """Remove a consumed URL from the chat_key fallback list."""
        for key, urls in list(self._response_urls.items()):
            if url in urls:
                urls.remove(url)
                if not urls:
                    self._response_urls.pop(key, None)
                return

    def _remove_url_from_msgid_map(self, url: str) -> None:
        """Remove a consumed URL from the msgid mapping."""
        for msgid, stored_url in list(self._msgid_response_urls.items()):
            if stored_url == url:
                self._msgid_response_urls.pop(msgid, None)
                return

    async def send_markdown(self, chat_id: str, content: str) -> str:
        """Send a Markdown message (convenience helper)."""
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
        Send an image message (via stream msg_item, base64+md5).

        Images are queued on the associated StreamSession and sent together with text when finish=true.

        Limits:
        - Only JPG/PNG are supported; other formats are auto-converted to JPG
        - <= 10MB per image
        - Up to 10 images per message
        - Images are sent via msg_item only when finish=true

        If no stream session exists, raises an error so the handler can fall back.
        """
        # Look up the associated stream session
        stream_id = None
        if reply_to:
            stream_id = self._msgid_to_stream.get(reply_to)
        if not stream_id:
            # Group chats need exact user_id matching
            user_id = kwargs.get("channel_user_id") or (
                kwargs.get("metadata", {}).get("channel_user_id")
                if isinstance(kwargs.get("metadata"), dict)
                else None
            )
            stream_id = self._find_stream_by_chat(chat_id, user_id)

        session = self._stream_sessions.get(stream_id) if stream_id else None

        if not session:
            # No active stream -> raise so im_channel handler falls back to send_file.
            # We must not call send_markdown; that would consume response_url and cause the Agent's final text to be dropped.
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

        # Read image -> convert format if needed -> base64 + md5
        try:
            b64_data, md5_hash = await self._prepare_image_for_stream(image_path)
        except Exception as e:
            # Image processing failed -> raise so the handler can handle it without consuming stream/response_url
            logger.error(f"WeWorkBot: Failed to prepare image {image_path}: {e}")
            raise RuntimeError(f"Failed to prepare image for stream: {e}") from e

        # Check limits
        if len(session.pending_images) >= 10:
            logger.warning(
                f"WeWorkBot: Stream {stream_id} already has 10 images, skipping {image_path}"
            )
            return f"stream:{stream_id}:image_limit"

        # Enqueue + reset settle timer
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
        Send a file.

        WeCom Smart Bot streaming reply only supports images (JPG/PNG),
        not file types. Raises so the handler can surface an error to the Agent.
        """
        raise NotImplementedError(
            "WeWork Smart Robot (Bot mode) does not support sending files. "
            "Stream only supports JPG/PNG images via msg_item."
        )

    # ── Image handling ──

    async def _prepare_image_for_stream(self, image_path: str) -> tuple[str, str]:
        """
        Prepare an image for stream msg_item.

        1. Read the file
        2. Check/convert format (only JPG/PNG supported; others converted to JPG)
        3. Check size (<= 10MB)
        4. Return (base64_str, md5_hex)
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        raw_data = path.read_bytes()
        file_ext = path.suffix.lower()

        # Determine whether format conversion is needed
        is_jpg = file_ext in (".jpg", ".jpeg") or raw_data[:2] == b"\xff\xd8"
        is_png = file_ext == ".png" or raw_data[:4] == b"\x89PNG"

        if not is_jpg and not is_png:
            # Needs conversion to JPG
            raw_data = await self._convert_image_to_jpg(raw_data, path.name)
            logger.info(f"WeWorkBot: Converted {path.name} to JPG ({len(raw_data)} bytes)")

        # Check size (10MB)
        if len(raw_data) > 10 * 1024 * 1024:
            raise ValueError(f"Image too large: {len(raw_data)} bytes (max 10MB)")

        b64_data = base64.b64encode(raw_data).decode("utf-8")
        md5_hash = hashlib.md5(raw_data).hexdigest()

        return b64_data, md5_hash

    async def _convert_image_to_jpg(self, raw_data: bytes, filename: str) -> bytes:
        """
        Convert an image to JPG format.

        Uses Pillow if available; otherwise tries to send the raw data.
        """
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(raw_data))
            # Convert to RGB (strip alpha channel)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=90)
            return output.getvalue()
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("PIL")
            logger.warning(f"WeWorkBot: {hint}; cannot convert {filename}, attempting to send raw data")
            return raw_data
        except Exception as e:
            logger.error(f"WeWorkBot: Image conversion failed for {filename}: {e}")
            raise

    # ==================== Media handling ====================

    async def download_media(self, media: MediaFile) -> Path:
        """
        Download a media file.

        Smart Bot image/file URL content is AES-encrypted;
        after download, decrypt with EncodingAESKey. URLs are valid for 5 minutes.
        """
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.url:
            raise ValueError("Media has no URL to download")

        # Download
        response = await self._http_client.get(media.url, timeout=60.0)
        response.raise_for_status()
        raw_data = response.content

        # If marked as AES-encrypted, decrypt the content
        if media.extra and media.extra.get("aes_encrypted") and self._crypt:
            try:
                raw_data = self._crypt.decrypt_media(raw_data)
                logger.debug(f"WeWorkBot: Decrypted media {media.filename} ({len(raw_data)} bytes)")
            except Exception as e:
                logger.error(f"WeWorkBot: Failed to decrypt media {media.filename}: {e}")
                media.status = MediaStatus.FAILED
                raise ValueError(f"Media decryption failed for {media.filename}") from e

        # Save locally
        from openakita.channels.base import sanitize_filename

        safe_name = sanitize_filename(Path(media.filename).name or "download")
        local_path = self.media_dir / safe_name
        with open(local_path, "wb") as f:
            f.write(raw_data)

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"WeWorkBot: Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        Upload a media file.

        Smart Bot mode does not require media pre-upload.
        Images are sent inline via base64+md5 through stream msg_item.
        """
        raise NotImplementedError(
            "WeWork Smart Robot sends images inline via stream msg_item (base64+md5). "
            "No separate upload API is needed. Use send_image() instead."
        )

    # ==================== Cleanup ====================

    async def _cleanup_expired_urls(self) -> None:
        """Periodically clean up expired stream sessions and response_url caches."""
        while self._running:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)

                now = time.time()

                # ── Clean up timed-out stream sessions ──
                expired_streams = []
                async with self._stream_lock:
                    for sid, session in list(self._stream_sessions.items()):
                        age = now - session.created_at
                        if age > STREAM_TIMEOUT + 60:
                            # Past timeout + 1 minute buffer, force cleanup
                            expired_streams.append(sid)

                for sid in expired_streams:
                    await self._cleanup_stream_session(sid)

                if expired_streams:
                    logger.info(
                        f"WeWorkBot: Cleaned {len(expired_streams)} expired stream sessions"
                    )

                # ── Clean up response_url caches ──
                if len(self._msgid_response_urls) > 200:
                    excess = len(self._msgid_response_urls) - 100
                    keys = list(self._msgid_response_urls.keys())[:excess]
                    for k in keys:
                        self._msgid_response_urls.pop(k, None)
                    logger.info(f"WeWorkBot: Cleaned {excess} expired msgid->url entries")

                if len(self._response_urls) > 100:
                    excess = len(self._response_urls) - 50
                    keys = list(self._response_urls.keys())[:excess]
                    for k in keys:
                        self._response_urls.pop(k, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WeWorkBot: Cleanup error: {e}")
