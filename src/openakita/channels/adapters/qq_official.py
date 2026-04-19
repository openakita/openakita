"""
QQ Official Bot adapter

Based on QQ Official Bot API v2:
- AppID + AppSecret authentication (OAuth2 Access Token)
- Supports both WebSocket and Webhook event delivery modes
- Supports group, direct (C2C), and channel messages
- Text/image/rich media message send and receive

Mode notes:
- websocket (default): self-initiated WebSocket connection to the QQ Gateway, no public IP needed
- webhook: QQ server pushes events to an HTTP callback endpoint, requires a public IP/domain

Official docs: https://bot.q.qq.com/wiki/develop/api-v2/
"""

import asyncio
import collections
import contextlib
import hashlib
import hmac
import json
import logging
import os
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

# ---------------------------------------------------------------------------
# Lazy-import websockets (only used in WebSocket mode)
# ---------------------------------------------------------------------------
websockets: Any = None


def _import_websockets():
    global websockets
    if websockets is None:
        try:
            import websockets as ws

            websockets = ws
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("websockets"))


class QQBotAdapter(ChannelAdapter):
    """
    QQ Official Bot adapter

    Integrates via the QQ Open Platform official API.

    Supports:
    - Group @bot messages (GROUP_AT_MESSAGE_CREATE)
    - Direct messages (C2C_MESSAGE_CREATE)
    - Channel @messages (AT_MESSAGE_CREATE)
    - Text message send and receive
    """

    channel_name = "qqbot"

    capabilities = {
        "streaming": False,
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
        "proactive_send": False,
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
        footer_elapsed: bool | None = None,
    ):
        """
        Args:
            app_id: QQ Bot AppID (obtained from developer settings on q.qq.com)
            app_secret: QQ Bot AppSecret
            sandbox: whether to use the sandbox environment
            mode: access mode, "websocket" or "webhook"
            webhook_port: webhook callback server port (webhook mode only)
            webhook_path: webhook callback path (webhook mode only)
            media_dir: media file storage directory
            channel_name: channel name (used to distinguish instances when running multiple bots)
            bot_id: unique identifier for this bot instance
            agent_profile_id: bound agent profile ID
            public_api_url: public URL of the OpenAkita API (e.g. https://example.com),
                used to convert local images into public URLs that QQ can access. Without it,
                local images cannot be sent to groups/C2C.
            footer_elapsed: append processing elapsed time at the end of replies (default True,
                can be controlled via the QQBOT_FOOTER_ELAPSED env var)
        """
        super().__init__(
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id
        )

        self.app_id = app_id
        self.app_secret = app_secret
        self.sandbox = sandbox
        self.mode = mode.lower().strip()
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path
        self.public_api_url = public_api_url.rstrip("/") if public_api_url else ""
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/qqbot")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._task: asyncio.Task | None = None
        self._retry_delay: int = 5  # reconnect delay in seconds, reset on on_ready
        self._webhook_runner: Any | None = None  # aiohttp web runner
        self._access_token: str | None = None  # OAuth2 access token
        self._token_expires: float = 0

        # ---- WebSocket gateway state ----
        self._ws_session_id: str | None = None
        self._ws_last_seq: int | None = None
        self._ws_heartbeat_ack: bool = True

        # ---- chat_id routing tables ----
        # {chat_id: "group" | "c2c" | "channel"}
        self._chat_type_map: dict[str, str] = {}
        # {chat_id: most recent msg_id received} (required for passive replies)
        self._last_msg_id: dict[str, str] = {}
        # {chat_id: most recent event_id received} (fallback when msg_id has expired)
        self._last_event_id: dict[str, str] = {}
        # {msg_id: msg_seq} — QQ API requires incrementing msg_seq for each reply sharing a msg_id to avoid deduplication
        self._msg_seq: dict[str, int] = {}
        self._msg_seq_max_entries = 500
        # {chat_id: message_id} — "Thinking..." hint message ID (sent by send_typing, recalled by clear_typing)
        self._typing_msg_ids: dict[str, str] = {}
        # C2C uses msg_type=6 input-status notifications that don't need to be recalled; tracked via this set
        self._typing_c2c_active: set[str] = set()
        # {chat_id: start_time} — typing start time, used to compute the elapsed footer
        self._typing_start_time: dict[str, float] = {}
        self._footer_elapsed = (
            footer_elapsed
            if footer_elapsed is not None
            else (os.environ.get("QQBOT_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes"))
        )
        # Whether Markdown is available (custom markdown requires invite-based approval; auto-downgrade after first failure)
        self._markdown_available: bool = True
        # As of 2026/03/05 the sandbox environment is exempt from message rate limits
        self._sandbox_rate_exempt: bool = sandbox

        # Pending message queue: QQ groups don't allow proactive sending, buffer messages and deliver on the next user message
        self._pending_messages: dict[str, list[tuple[float, str]]] = {}
        self._pending_max_per_chat = 5

        # Message deduplication: Webhook/WebSocket may deliver duplicates
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

    def _remember_chat(
        self,
        chat_id: str,
        chat_type: str,
        msg_id: str = "",
        event_id: str = "",
    ) -> None:
        """Record routing info for a chat_id (called when a message is received)."""
        self._chat_type_map[chat_id] = chat_type
        if msg_id:
            self._last_msg_id[chat_id] = msg_id
        if event_id:
            self._last_event_id[chat_id] = event_id

    def _next_msg_seq(self, seq_key: str) -> int:
        """Fetch and increment msg_seq (required by QQ API dedup).

        seq_key should be the msg_id being replied to (passive reply) or chat_id (proactive send).
        """
        seq = self._msg_seq.get(seq_key, 0) + 1
        self._msg_seq[seq_key] = seq
        if len(self._msg_seq) > self._msg_seq_max_entries:
            keys = list(self._msg_seq.keys())
            for k in keys[: len(keys) // 2]:
                self._msg_seq.pop(k, None)
        return seq

    def _resolve_chat_type(self, chat_id: str, metadata: dict | None = None) -> str:
        """
        Resolve chat_type, in priority order:
        1. chat_type in OutgoingMessage.metadata
        2. Routing table _chat_type_map (recorded on receive)
        3. Default "group"
        """
        if metadata:
            ct = metadata.get("chat_type")
            if ct:
                return ct
        return self._chat_type_map.get(chat_id, "group")

    def _resolve_msg_id(self, chat_id: str, metadata: dict | None = None) -> str | None:
        """
        Resolve msg_id (required for passive replies), in priority order:
        1. msg_id in OutgoingMessage.metadata
        2. Routing table _last_msg_id (most recently received message ID)
        """
        if metadata:
            mid = metadata.get("msg_id")
            if mid:
                return mid
        return self._last_msg_id.get(chat_id)

    def _local_path_to_public_url(self, local_path: str) -> str | None:
        """Copy a local file to the uploads directory and return a publicly accessible URL.

        Requires public_api_url to be configured.
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
            logger.info(f"Local file served as public URL: {url}")
            return url
        except Exception as e:
            logger.warning(f"Failed to make local file publicly accessible: {e}")
            return None

    @staticmethod
    def _is_proactive_limit_error(exc: BaseException) -> bool:
        """Detect QQ group proactive-message limit errors (11255 invalid request)."""
        s = str(exc).lower()
        return "11255" in s or "invalid request" in s

    @staticmethod
    def _is_msg_expired_error(exc: BaseException) -> bool:
        """Detect msg_id/event_id expiration errors.

        QQ API returns specific error codes once the passive reply window (roughly 5 minutes) has expired.
        """
        s = str(exc).lower()
        return any(k in s for k in ("msg_id is invalid", "40003", "msg id is invalid"))

    def _enqueue_pending(self, chat_id: str, text: str) -> None:
        """Buffer messages that cannot be sent proactively and deliver them on the next user message.

        QQ group proactive push has been deprecated since 2025/04/21; messages must be sent within the passive reply window.
        """
        pending = self._pending_messages.setdefault(chat_id, [])
        if len(pending) >= self._pending_max_per_chat:
            pending.pop(0)
        pending.append((time.time(), text))

    @staticmethod
    def _format_pending_delay(queued_at: float) -> str:
        """Format the delay between queue time and delivery as human-readable text."""
        delta = int(time.time() - queued_at)
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{delta // 60} min ago"
        if delta < 86400:
            h, m = divmod(delta, 3600)
            return f"{h}h{f' {m // 60}m' if m // 60 else ''} ago"
        return f"{delta // 86400}d ago"

    async def _flush_pending_messages(self, chat_id: str) -> None:
        """When a new user message arrives, deliver the pending messages queued for that chat_id."""
        pending = self._pending_messages.pop(chat_id, [])
        if not pending:
            return
        msg_id = self._last_msg_id.get(chat_id)
        if not msg_id:
            self._pending_messages[chat_id] = pending
            return

        parts: list[str] = []
        for queued_at, text in pending:
            delay = self._format_pending_delay(queued_at)
            parts.append(f"[⏰ {delay}] {text}")

        header = "📬 The following messages could not be delivered in time due to QQ group restrictions, resending now:\n"
        combined = header + "\n\n".join(parts)

        chat_type = self._chat_type_map.get(chat_id, "group")
        try:
            await self._send_text_via_http(chat_type, chat_id, combined, msg_id)
            logger.info(f"QQ: delivered {len(pending)} pending message(s) to {chat_id}")
        except Exception as e:
            logger.warning(f"QQ: failed to deliver pending messages to {chat_id}: {e}")

    async def start(self) -> None:
        """Start the QQ Official Bot."""
        if not self.app_id or not self.app_secret:
            raise ValueError("QQ Bot AppID or AppSecret is not configured; obtain them from the developer settings on q.qq.com.")

        self._running = True

        if self.mode == "webhook":
            try:
                from aiohttp import web  # noqa: F401
            except ImportError:
                raise ImportError("aiohttp not installed. Run: pip install aiohttp")

            self._task = asyncio.create_task(self._run_webhook_server())
            logger.info(
                f"QQ Official Bot adapter starting in WEBHOOK mode "
                f"(AppID: {self.app_id}, port: {self.webhook_port}, "
                f"path: {self.webhook_path})"
            )
        else:
            _import_websockets()
            self._task = asyncio.create_task(self._run_ws_client())
            logger.info(
                f"QQ Official Bot adapter starting in WEBSOCKET mode "
                f"(AppID: {self.app_id}, sandbox: {self.sandbox})"
            )

    # Non-retryable configuration-error keywords (dramatically extend retry interval when seen)
    _FATAL_KEYWORDS = ("不在白名单", "invalid appid", "invalid secret", "鉴权失败")
    _FATAL_GIVE_UP_THRESHOLD = 5

    # ==================== WebSocket Gateway ====================

    async def _get_gateway_url(self) -> str:
        """Fetch the WebSocket Gateway connection URL via the REST API."""
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        async with hx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/gateway/bot", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            url = data.get("url")
            if not url:
                raise RuntimeError(f"Gateway response missing 'url': {data}")
            logger.info(f"QQ Gateway URL: {url}")
            return url

    async def _ws_heartbeat_loop(self, ws: Any, interval: float) -> None:
        """Send periodic heartbeats (op 1); close the connection on ACK timeout."""
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._ws_heartbeat_ack:
                    logger.warning("QQ WS: heartbeat ACK not received, closing connection")
                    await ws.close()
                    return
                self._ws_heartbeat_ack = False
                await ws.send(json.dumps({"op": 1, "d": self._ws_last_seq}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"QQ WS heartbeat error: {e}")

    async def _run_ws_client(self) -> None:
        """WebSocket mode: self-managed Gateway connection with automatic reconnect/Resume."""
        _import_websockets()

        max_delay = 120
        fatal_max_delay = 600
        consecutive_fatal = 0
        # QQ Gateway intents: PUBLIC_GUILD_MESSAGES (1<<25) | PUBLIC_MESSAGES (1<<30)
        intents = (1 << 25) | (1 << 30)

        self._ws_session_id = None
        self._ws_last_seq = None

        while self._running:
            try:
                gateway_url = await self._get_gateway_url()

                async with websockets.connect(
                    gateway_url,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                ) as ws:
                    # ---- Op 10 Hello ----
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    hello = json.loads(raw)
                    if hello.get("op") != 10:
                        raise RuntimeError(
                            f"Expected Hello (op 10), got op {hello.get('op')}"
                        )
                    heartbeat_interval_ms = hello.get("d", {}).get(
                        "heartbeat_interval", 41250
                    )
                    heartbeat_interval = heartbeat_interval_ms / 1000.0

                    # ---- Start heartbeat ----
                    self._ws_heartbeat_ack = True
                    heartbeat_task = asyncio.create_task(
                        self._ws_heartbeat_loop(ws, heartbeat_interval)
                    )

                    try:
                        # ---- Identify (op 2) or Resume (op 6) ----
                        if (
                            self._ws_session_id is not None
                            and self._ws_last_seq is not None
                        ):
                            token = await self._get_access_token()
                            await ws.send(
                                json.dumps(
                                    {
                                        "op": 6,
                                        "d": {
                                            "token": f"QQBot {token}",
                                            "session_id": self._ws_session_id,
                                            "seq": self._ws_last_seq,
                                        },
                                    }
                                )
                            )
                        else:
                            token = await self._get_access_token()
                            await ws.send(
                                json.dumps(
                                    {
                                        "op": 2,
                                        "d": {
                                            "token": f"QQBot {token}",
                                            "intents": intents,
                                            "shard": [0, 1],
                                        },
                                    }
                                )
                            )

                        # ---- Receive loop ----
                        async for raw_msg in ws:
                            msg = json.loads(raw_msg)
                            op = msg.get("op")

                            if op == 0:  # Dispatch
                                s = msg.get("s")
                                if s is not None:
                                    self._ws_last_seq = s
                                t = msg.get("t", "")
                                d = msg.get("d", {})

                                if t == "READY":
                                    self._ws_session_id = d.get("session_id")
                                    user = d.get("user", {})
                                    logger.info(
                                        f"QQ Official Bot ready "
                                        f"(user: {user.get('username', '?')})"
                                    )
                                    self._retry_delay = 5
                                    consecutive_fatal = 0
                                elif t == "RESUMED":
                                    logger.info("QQ WS session resumed successfully")
                                    self._retry_delay = 5
                                else:
                                    asyncio.create_task(
                                        self._handle_webhook_event(t, d)
                                    )

                            elif op == 11:  # Heartbeat ACK
                                self._ws_heartbeat_ack = True

                            elif op == 1:  # Server heartbeat request
                                await ws.send(
                                    json.dumps({"op": 1, "d": self._ws_last_seq})
                                )

                            elif op == 7:  # Reconnect
                                logger.info(
                                    "QQ WS: server requested reconnect (op 7)"
                                )
                                break

                            elif op == 9:  # Invalid Session
                                can_resume = msg.get("d", False)
                                if not can_resume:
                                    self._ws_session_id = None
                                    self._ws_last_seq = None
                                logger.warning(
                                    f"QQ WS: invalid session "
                                    f"(can_resume={can_resume})"
                                )
                                await asyncio.sleep(2)
                                break

                    finally:
                        heartbeat_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat_task

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
                    if consecutive_fatal == 1:
                        logger.error(
                            f"QQ Official Bot configuration error: {err_msg}\n"
                            f"  -> Check your QQ Open Platform configuration (IP allowlist / AppID / AppSecret)\n"
                            f"  -> Will keep retrying in the background and recover automatically after the config is fixed"
                        )
                    elif consecutive_fatal % 5 == 0:
                        logger.warning(
                            f"QQ Official Bot still cannot connect (retried {consecutive_fatal} times): {err_msg}"
                        )

                    if consecutive_fatal >= self._FATAL_GIVE_UP_THRESHOLD:
                        reason = (
                            f"{consecutive_fatal} consecutive authentication failures: {err_msg}. "
                            "Check the QQ Open Platform AppID / AppSecret / IP allowlist configuration"
                        )
                        logger.error(f"QQ Official Bot: {reason}")
                        self._running = False
                        self._report_failure(reason)
                        return
                else:
                    consecutive_fatal = 0
                    cap = max_delay
                    logger.error(f"QQ Official Bot error: {err_msg}")

                logger.info(f"QQ Official Bot: reconnecting in {self._retry_delay}s...")
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, cap)

    # ==================== Webhook mode ====================

    async def _get_access_token(self) -> str:
        """Fetch the OAuth2 access_token for the QQ Official API."""
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
            _do_fetch,
            max_retries=2,
            base_delay=1.0,
            operation_name="QQ._get_access_token",
        )
        self._access_token = data["access_token"]
        self._token_expires = now + int(data.get("expires_in", 7200))
        logger.info("QQ Bot access_token refreshed")
        return self._access_token

    def _verify_signature(self, body: bytes, signature: str, timestamp: str) -> bool:
        """
        Verify the QQ Webhook callback signature (ed25519).

        QQ Official Webhook uses ed25519 signature verification:
        - Signed content: timestamp + body
        - Key: an ed25519 key derived from the app_secret + bot_secret seed
        - Signature value: in the X-Signature-Ed25519 header

        Simplified implementation: HMAC-SHA256 is used as a fallback verification (supported by some older API versions).
        For full ed25519 verification, install PyNaCl.
        """
        try:
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey

            seed = self.app_secret.encode("utf-8")
            msg = timestamp.encode("utf-8") + body
            sig_bytes = bytes.fromhex(signature)

            verify_key = VerifyKey(seed[:32].ljust(32, b"\x00"))
            try:
                verify_key.verify(msg, sig_bytes)
                return True
            except BadSignatureError:
                pass
        except ImportError:
            pass

        msg = timestamp.encode("utf-8") + body
        expected = hmac.new(self.app_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _run_webhook_server(self) -> None:
        """Start the Webhook HTTP callback server."""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp not installed. Run: pip install aiohttp")

        async def handle_callback(request: web.Request) -> web.Response:
            """Handle QQ Webhook callbacks."""
            body = await request.read()

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

            # op=13: callback URL validation
            if op == 13:
                d = payload.get("d", {})
                plain_token = d.get("plain_token", "")
                event_ts = d.get("event_ts", "")
                msg = event_ts.encode("utf-8") + plain_token.encode("utf-8")
                sig = hmac.new(self.app_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
                return web.json_response(
                    {
                        "plain_token": plain_token,
                        "signature": sig,
                    }
                )

            # op=0: event dispatch
            if op == 0:
                event_type = payload.get("t", "")
                event_data = payload.get("d", {})
                asyncio.create_task(self._handle_webhook_event(event_type, event_data))
                return web.json_response({"status": "ok"})

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

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check whether a message is a duplicate and record it in the LRU cache."""
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
        """Handle events pushed by Webhook/WS."""
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
                            f"QQ: stale message discarded "
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
                logger.debug(f"QQ: unhandled event type {event_type}")
                return

            if self._is_duplicate(unified.channel_message_id):
                return

            self._log_message(unified)
            await self._emit_message(unified)
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                await self._flush_pending_messages(unified.chat_id)
        except Exception as e:
            logger.error(f"Error handling QQ event {event_type}: {e}")

    def _convert_webhook_group_message(self, data: dict) -> UnifiedMessage:
        """Convert a Webhook group message to a UnifiedMessage."""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_openid = author.get("member_openid", "")
        group_openid = data.get("group_openid", "")

        self._remember_chat(
            group_openid,
            "group",
            data.get("id", ""),
            data.get("event_id", ""),
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
        """Convert a Webhook direct (C2C) message to a UnifiedMessage."""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_openid = author.get("user_openid", "")

        self._remember_chat(
            user_openid,
            "c2c",
            data.get("id", ""),
            data.get("event_id", ""),
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
        """Convert a Webhook channel message to a UnifiedMessage."""
        content = MessageContent()
        content.text = (data.get("content") or "").strip()

        self._parse_webhook_attachments(data.get("attachments"), content)

        author = data.get("author", {})
        user_id = author.get("id", "")
        channel_id = data.get("channel_id", "")
        guild_id = data.get("guild_id", "")

        self._remember_chat(
            channel_id,
            "channel",
            data.get("id", ""),
            data.get("event_id", ""),
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
        """Parse attachments from a Webhook callback (falling back to _guess_media_type via extension)."""
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

            if media_type == "audio":
                QQBotAdapter._enrich_voice_media(att, media)
                content.voices.append(media)
            elif media_type == "image":
                content.images.append(media)
            elif media_type == "video":
                content.videos.append(media)
            else:
                content.files.append(media)

    async def stop(self) -> None:
        """Stop the QQ Official Bot."""
        self._running = False

        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        logger.info(f"QQ Official Bot adapter stopped (mode: {self.mode})")

    # File extension -> media type fallback mapping (QQ attachment content_type is often empty)
    _EXT_AUDIO = {".amr", ".silk", ".slk", ".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac"}
    _EXT_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    _EXT_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}

    @staticmethod
    def _guess_media_type(content_type: str, filename: str) -> str:
        """
        Infer media category from content_type and file extension.

        QQ attachment content_type is frequently empty or non-standard, so the extension is used as fallback.
        Returns: "image" | "audio" | "video" | "file"
        """
        ct = content_type.lower()
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("audio/"):
            return "audio"
        if ct.startswith("video/"):
            return "video"

        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in QQBotAdapter._EXT_AUDIO:
            return "audio"
        if ext in QQBotAdapter._EXT_IMAGE:
            return "image"
        if ext in QQBotAdapter._EXT_VIDEO:
            return "video"

        return "file"

    @staticmethod
    def _enrich_voice_media(att: dict, media: "MediaFile") -> None:
        """Extract platform-specific fields from a QQ voice attachment.

        QQ voice attachments provide:
        - voice_wav_url: WAV download link (more broadly compatible than the default SILK)
        - asr_refer_text: server-side ASR transcription from the QQ platform
        """
        wav_url = att.get("voice_wav_url")
        if wav_url:
            media.extra["voice_wav_url"] = wav_url

        asr_text = (att.get("asr_refer_text") or "").strip()
        if asr_text:
            media.transcription = asr_text
            logger.info(f"QQ voice ASR (platform): {asr_text[:80]}")

        size = att.get("size")
        if size:
            media.extra["size"] = size

    # ==================== REST API infrastructure ====================

    async def _build_api_headers(self, content_type: str = "application/json") -> dict:
        """Build QQ API V2 request headers using the correct QQBot {access_token} format."""
        token = await self._get_access_token()
        headers = {"Authorization": f"QQBot {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _api_base_url(self) -> str:
        return "https://sandbox.api.sgroup.qq.com" if self.sandbox else "https://api.sgroup.qq.com"

    # ==================== Rich media upload ====================

    async def _upload_rich_media_url(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str,
        srv_send_msg: bool = False,
    ) -> dict:
        """Upload rich media to the QQ server via a public URL (REST API).

        Args:
            chat_type: "group" or "c2c"
            target_id: group_openid or user openid
            file_type: 1=image, 2=video, 3=voice, 4=file
            url: publicly accessible media URL
            srv_send_msg: if True, the server sends the message directly (consumes the proactive-message quota)

        Returns:
            API response dict containing file_info / file_uuid / ttl, etc.
        """
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            api_url = f"{base_url}/v2/groups/{target_id}/files"
        else:
            api_url = f"{base_url}/v2/users/{target_id}/files"

        payload: dict[str, Any] = {
            "file_type": file_type,
            "url": url,
            "srv_send_msg": srv_send_msg,
        }

        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            logger.debug(
                f"QQ: rich_media_url upload result: file_type={file_type}, "
                f"keys={list(result.keys()) if isinstance(result, dict) else type(result)}"
            )
            return result

    async def _upload_rich_media_base64(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        file_data: str,
        srv_send_msg: bool = False,
        file_name: str | None = None,
    ) -> dict:
        """Upload rich media directly to the QQ server using file_data (base64).

        The QQ Official API supports uploading via file_data (base64-encoded binary content).
        """
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"{base_url}/v2/groups/{target_id}/files"
        else:
            url = f"{base_url}/v2/users/{target_id}/files"

        payload: dict[str, Any] = {
            "file_type": file_type,
            "file_data": file_data,
            "srv_send_msg": srv_send_msg,
        }
        if file_name:
            payload["file_name"] = file_name
        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            logger.debug(
                f"QQ: rich_media_base64 upload result: "
                f"file_type={file_type}, file_name={file_name}, "
                f"keys={list(result.keys()) if isinstance(result, dict) else type(result)}"
            )
            return result

    async def _send_rich_media(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str | None = None,
        msg_id: str | None = None,
        local_path: str | None = None,
        event_id: str | None = None,
    ) -> str:
        """
        Complete two-step rich-media send flow: upload + send message.

        Two upload modes (choose one):
        - url: publicly accessible media URL (via REST API)
        - local_path: local file path (read and base64-encoded)

        Args:
            chat_type: "group" or "c2c"
            target_id: target openid
            file_type: 1=image, 2=video, 3=voice, 4=file
            url: publicly accessible media URL
            msg_id: message ID to passively reply to (optional)
            local_path: local file path (optional, mutually exclusive with url)
            event_id: event ID to passively reply to (fallback when msg_id has expired)

        Returns:
            The ID of the sent message.
        """
        import base64 as b64
        from pathlib import Path as _P

        # Step 1: upload the rich media resource to obtain file_info
        if local_path:
            with open(local_path, "rb") as f:
                file_data = b64.standard_b64encode(f.read()).decode("ascii")
            _fname = _P(local_path).name if file_type == 4 else None
            upload_result = await self._upload_rich_media_base64(
                chat_type,
                target_id,
                file_type=file_type,
                file_data=file_data,
                srv_send_msg=False,
                file_name=_fname,
            )
        elif url:
            upload_result = await self._upload_rich_media_url(
                chat_type,
                target_id,
                file_type=file_type,
                url=url,
                srv_send_msg=False,
            )
        else:
            raise ValueError("_send_rich_media requires either url or local_path")

        file_info = upload_result.get("file_info") if isinstance(upload_result, dict) else None
        if not file_info:
            raise RuntimeError(f"Rich media upload did not return file_info: {upload_result}")

        # Step 2: send the message with msg_type=7 (media)
        return await self._send_media_message_via_http(
            chat_type,
            target_id,
            file_info,
            msg_id,
            event_id=event_id,
        )

    # ==================== REST message send ====================

    async def _send_media_message_via_http(
        self,
        chat_type: str,
        target_id: str,
        file_info: str,
        msg_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        """Send a media message directly over HTTP (msg_type=7)."""
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"{base_url}/v2/groups/{target_id}/messages"
        elif chat_type == "channel":
            url = f"{base_url}/channels/{target_id}/messages"
        else:
            url = f"{base_url}/v2/users/{target_id}/messages"

        seq_key = msg_id or event_id or target_id
        payload: dict[str, Any] = {
            "msg_type": 7,
            "media": {"file_info": file_info},
            "msg_seq": self._next_msg_seq(seq_key),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id

        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))

    async def _send_text_via_http(
        self,
        chat_type: str,
        target_id: str,
        text: str,
        msg_id: str | None = None,
        is_wakeup: bool = False,
    ) -> str:
        """Send a plain text message over HTTP."""
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        if chat_type == "group":
            url = f"{base_url}/v2/groups/{target_id}/messages"
        elif chat_type == "channel":
            url = f"{base_url}/channels/{target_id}/messages"
        else:
            url = f"{base_url}/v2/users/{target_id}/messages"

        seq_key = msg_id or target_id
        payload: dict[str, Any] = {
            "msg_type": 0,
            "content": text,
            "msg_seq": self._next_msg_seq(seq_key),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        if is_wakeup:
            payload["is_wakeup"] = True

        async with hx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", ""))

    async def _send_channel_message_via_http(
        self,
        channel_id: str,
        text: str,
        image_url: str | None,
        image_path: str | None,
        msg_id: str | None,
        parse_mode: str | None = None,
    ) -> str:
        """Send a channel message: supports content + image in the same message, and Markdown.

        Unlike the group/C2C APIs, the QQ channel API allows text and image to be sent in a single POST.
        - image_url uses the JSON body's image field
        - image_path uses the multipart form's file_image field
        """
        import httpx as hx

        base_url = self._api_base_url()
        url = f"{base_url}/channels/{channel_id}/messages"

        # Try Markdown (plain text without images only)
        if self._should_try_markdown(parse_mode, text) and not image_url and not image_path:
            headers = await self._build_api_headers()
            md_body: dict[str, Any] = {
                "msg_type": 2,
                "markdown": {"content": text},
            }
            if msg_id:
                md_body["msg_id"] = msg_id
            try:
                async with hx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, json=md_body, headers=headers)
                    resp.raise_for_status()
                    return str(resp.json().get("id", ""))
            except Exception as e:
                self._markdown_available = False
                logger.warning("QQ channel Markdown send failed, falling back to plain text: %s", e)

        # Local image: multipart form
        if image_path and not image_url:
            auth_headers = await self._build_api_headers(content_type="")
            form_data: dict[str, str] = {}
            if text:
                form_data["content"] = text
            if msg_id:
                form_data["msg_id"] = msg_id

            async with hx.AsyncClient(timeout=30.0) as client:
                with open(image_path, "rb") as f:
                    files = {"file_image": (Path(image_path).name, f, "image/png")}
                    resp = await client.post(
                        url, data=form_data, files=files, headers=auth_headers
                    )
                resp.raise_for_status()
                return str(resp.json().get("id", ""))

        # JSON body (plain text / text + image URL / image URL only)
        headers = await self._build_api_headers()
        body: dict[str, Any] = {}
        if text:
            body["content"] = text
        if image_url:
            body["image"] = image_url
        if msg_id:
            body["msg_id"] = msg_id

        if not body or (not text and not image_url):
            return ""

        async with hx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return str(resp.json().get("id", ""))

    # ==================== Message send ====================

    @staticmethod
    def _has_markdown_features(text: str) -> bool:
        """Detect whether the text contains Markdown formatting features."""
        markers = ("**", "##", "- ", "```", "~~", "[", "](", "> ", "---")
        return any(m in text for m in markers)

    def _should_try_markdown(self, parse_mode: str | None, text: str) -> bool:
        """Decide whether to attempt sending in Markdown format."""
        if not self._markdown_available:
            return False
        if not text:
            return False
        return parse_mode == "markdown" and self._has_markdown_features(text)

    def _append_elapsed_footer(self, text: str, chat_id: str) -> str:
        """When footer_elapsed is enabled, append elapsed-time info to the end of the text."""
        if not self._footer_elapsed or not text:
            return text
        start = self._typing_start_time.pop(chat_id, None)
        if not start:
            return text
        elapsed = time.time() - start
        if elapsed < 1.0:
            return text
        return f"{text}\n\n⏱ done ({elapsed:.1f}s)"

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        Send a message.

        Supports:
        - Text messages (msg_type=0)
        - Markdown messages (msg_type=2, requires invite-based approval, auto-downgrades on failure)
        - Image messages (channel: content+image/file_image; group/C2C: two-step rich media upload)
        - File messages (group/C2C: file_type=4 two-step rich media upload)
        """
        chat_type = self._resolve_chat_type(message.chat_id, message.metadata)
        msg_id = self._resolve_msg_id(message.chat_id, message.metadata)
        parse_mode = message.parse_mode

        if message.content.text:
            message.content.text = self._append_elapsed_footer(
                message.content.text,
                message.chat_id,
            )

        try:
            return await self._send_message_via_http(
                message,
                chat_type,
                msg_id,
                parse_mode,
            )
        except Exception as e:
            if chat_type == "group" and self._is_proactive_limit_error(e):
                queued_text = message.content.text or ""
                if queued_text:
                    self._enqueue_pending(message.chat_id, queued_text)
                    logger.info(
                        f"QQ: proactive group message queued for {message.chat_id} "
                        f"(will deliver on next user message)"
                    )
                    return ""
            # msg_id expired: try resending with event_id (passive reply window is about 5 minutes)
            if msg_id and self._is_msg_expired_error(e):
                event_id = self._last_event_id.get(message.chat_id)
                if event_id:
                    logger.info(
                        f"QQ: msg_id expired for {message.chat_id}, "
                        f"retrying with event_id"
                    )
                    try:
                        return await self._send_message_via_http(
                            message, chat_type, None, parse_mode,
                            event_id=event_id,
                        )
                    except Exception as retry_exc:
                        logger.warning(
                            f"QQ: event_id retry also failed for "
                            f"{message.chat_id}: {retry_exc}"
                        )
            raise

    async def _send_message_via_http(
        self,
        message: OutgoingMessage,
        chat_type: str,
        msg_id: str | None,
        parse_mode: str | None = None,
        event_id: str | None = None,
    ) -> str:
        """Send a message via the HTTP API (text/Markdown/image/file); used for both WS and Webhook modes."""
        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        text = message.content.text or ""
        target_id = message.chat_id

        # Extract the first image
        first_image_url: str | None = None
        first_image_path: str | None = None
        if message.content.images:
            img = message.content.images[0]
            if img.url:
                first_image_url = img.url
            elif img.local_path:
                first_image_path = img.local_path

        result_id = ""

        if chat_type == "channel":
            # Channels support text + image in the same message
            result_id = await self._send_channel_message_via_http(
                target_id,
                text,
                first_image_url,
                first_image_path,
                msg_id,
                parse_mode,
            )
        else:
            # Group/C2C: text and image must be sent as two separate messages
            if chat_type == "group":
                url = f"/v2/groups/{target_id}/messages"
            elif chat_type == "c2c":
                url = f"/v2/users/{target_id}/messages"
            else:
                url = f"/v2/groups/{target_id}/messages"

            seq_key = msg_id or event_id or target_id
            headers = await self._build_api_headers()
            base_url = self._api_base_url()

            if text:
                async with hx.AsyncClient(base_url=base_url, headers=headers) as client:
                    sent_as_md = False
                    if self._should_try_markdown(parse_mode, text):
                        md_body: dict[str, Any] = {
                            "msg_type": 2,
                            "markdown": {"content": text},
                            "msg_seq": self._next_msg_seq(seq_key),
                        }
                        if msg_id:
                            md_body["msg_id"] = msg_id
                        elif event_id:
                            md_body["event_id"] = event_id
                        try:
                            resp = await client.post(url, json=md_body)
                            resp.raise_for_status()
                            data = resp.json()
                            result_id = str(data.get("id", ""))
                            sent_as_md = True
                        except Exception as e:
                            self._markdown_available = False
                            logger.warning(
                                "QQ Markdown send failed, falling back to plain text (Markdown will be skipped for subsequent messages): %s",
                                e,
                            )

                    # Plain text send (with 40054005 dedup retry, up to 2 attempts)
                    if not sent_as_md:
                        for attempt in range(2):
                            body: dict[str, Any] = {
                                "msg_type": 0,
                                "content": text,
                                "msg_seq": self._next_msg_seq(seq_key),
                            }
                            if msg_id:
                                body["msg_id"] = msg_id
                            elif event_id:
                                body["event_id"] = event_id

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

            # Send the first image (group/C2C two-step rich media upload)
            if first_image_url or first_image_path:
                media_id = await self._send_rich_media(
                    chat_type,
                    target_id,
                    file_type=1,
                    url=first_image_url,
                    msg_id=msg_id,
                    local_path=first_image_path if not first_image_url else None,
                    event_id=event_id,
                )
                result_id = result_id or media_id

        # Loop through the remaining images
        for extra_img in message.content.images[1:]:
            extra_url = extra_img.url if extra_img.url else None
            extra_path = extra_img.local_path if not extra_url and extra_img.local_path else None
            if not extra_url and not extra_path:
                continue
            try:
                if chat_type == "channel":
                    await self._send_channel_message_via_http(
                        target_id, "", extra_url, extra_path, msg_id, None
                    )
                else:
                    await self._send_rich_media(
                        chat_type,
                        target_id,
                        file_type=1,
                        url=extra_url,
                        msg_id=msg_id,
                        local_path=extra_path if not extra_url else None,
                        event_id=event_id,
                    )
            except Exception as e:
                logger.warning(f"QQ: send extra image failed: {e}")

        # Send file attachments (file_type=4, not supported by channels)
        if chat_type != "channel":
            for file_media in message.content.files:
                file_url = file_media.url if file_media.url else None
                file_path = (
                    file_media.local_path if not file_url and file_media.local_path else None
                )
                if not file_url and not file_path:
                    continue
                try:
                    await self._send_rich_media(
                        chat_type,
                        target_id,
                        file_type=4,
                        url=file_url,
                        msg_id=msg_id,
                        local_path=file_path if not file_url else None,
                        event_id=event_id,
                    )
                except Exception as e:
                    logger.warning(f"QQ: send file failed: {e}")

        return result_id

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> str:
        """Send a file (file_type=4), supported for groups and C2C.

        Prefers converting the local file into a public URL for upload (QQ API can derive the extension from the URL);
        falls back to base64 upload when public_api_url is not configured (QQ may fail to recognize the file type).
        """
        chat_type = self._resolve_chat_type(chat_id)
        if chat_type == "channel":
            raise NotImplementedError("Sending files via the rich-media API is not currently supported for QQ channels")
        msg_id = self._resolve_msg_id(chat_id)

        if caption:
            try:
                await self._send_text_via_http(chat_type, chat_id, caption, msg_id)
            except Exception as e:
                logger.warning(f"QQ: send file caption failed: {e}")

        # Prefer URL upload: QQ derives the extension from the URL path so the file opens properly
        public_url = self._local_path_to_public_url(file_path)
        if public_url:
            return await self._send_rich_media(
                chat_type,
                chat_id,
                file_type=4,
                url=public_url,
                msg_id=msg_id,
            )

        # Fallback: base64 upload (QQ cannot infer the extension from binary data, so the recipient may be unable to open it)
        if not self.public_api_url:
            logger.warning(
                "QQ: send_file falling back to base64 upload — "
                "file may be unopenable without extension. "
                "Configure public_api_url for reliable file delivery."
            )
        return await self._send_rich_media(
            chat_type,
            chat_id,
            file_type=4,
            msg_id=msg_id,
            local_path=file_path,
        )

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """Send a voice message (file_type=3, SILK format + base64 upload).

        The QQ Official API requires SILK format for voice. Input format is auto-detected:
        - .silk/.slk files are uploaded directly
        - Other formats are transcoded to SILK via pilk
        """
        import base64 as b64
        from pathlib import Path as _Path

        src = _Path(voice_path)
        if not src.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")

        chat_type = self._resolve_chat_type(chat_id)
        if chat_type == "channel":
            raise NotImplementedError("Sending voice is not currently supported for QQ channels")
        msg_id = self._resolve_msg_id(chat_id)

        ext = src.suffix.lower()
        silk_data: bytes | None = None

        if ext in (".silk", ".slk"):
            silk_data = src.read_bytes()
        else:
            try:
                import io
                import tempfile
                import wave

                import pilk

                raw_bytes = src.read_bytes()
                pcm_data: bytes
                sample_rate = 24000
                try:
                    with wave.open(io.BytesIO(raw_bytes)) as wf:
                        sample_rate = wf.getframerate()
                        pcm_data = wf.readframes(wf.getnframes())
                except wave.Error:
                    pcm_data = raw_bytes

                tmp_pcm = None
                tmp_silk = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as fp:
                        tmp_pcm = fp.name
                        fp.write(pcm_data)
                    with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as fp:
                        tmp_silk = fp.name
                    pilk.encode(tmp_pcm, tmp_silk, pcm_rate=sample_rate, tencent=True)
                    silk_data = _Path(tmp_silk).read_bytes()
                finally:
                    if tmp_pcm:
                        _Path(tmp_pcm).unlink(missing_ok=True)
                    if tmp_silk:
                        _Path(tmp_silk).unlink(missing_ok=True)
            except ImportError:
                raise ImportError("pilk is not installed, unable to transcode audio to SILK format. Run: pip install pilk")

        if not silk_data:
            raise RuntimeError("Failed to prepare SILK voice data")

        file_data = b64.standard_b64encode(silk_data).decode("ascii")
        upload_result = await self._upload_rich_media_base64(
            chat_type,
            chat_id,
            file_type=3,
            file_data=file_data,
            srv_send_msg=False,
        )
        file_info = (
            upload_result.get("file_info")
            if isinstance(upload_result, dict)
            else None
        )
        if not file_info:
            raise RuntimeError(f"Voice upload did not return file_info: {upload_result}")

        return await self._send_media_message_via_http(
            chat_type,
            chat_id,
            file_info,
            msg_id,
        )

    # ==================== Typing indicator ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send a typing-status indicator.

        C2C direct chats first send a native msg_type=6 typing notification (renewed on each call) and also
        send a visible "Thinking..." placeholder message (idempotent, sent once) that is recalled on clear_typing.
        Group chats/channels use an msg_type=0 "Thinking..." text message (idempotent, sent once).
        """
        if chat_id not in self._typing_start_time:
            self._typing_start_time[chat_id] = time.time()

        chat_type = self._resolve_chat_type(chat_id)

        # C2C: use msg_type=6 typing notification, renewed every 4 seconds
        if chat_type == "c2c":
            self._typing_c2c_active.add(chat_id)
            try:
                await self._send_input_notify(chat_id)
            except Exception as e:
                logger.debug(f"QQ Official Bot: send_typing (input_notify) failed: {e}")

        # Group/channel/C2C: idempotent text message send
        if chat_id in self._typing_msg_ids:
            return

        self._typing_msg_ids[chat_id] = ""
        msg_id = self._resolve_msg_id(chat_id)

        try:
            sent_id = await self._send_typing_via_http(chat_id, chat_type, msg_id)
            if sent_id:
                self._typing_msg_ids[chat_id] = sent_id
        except Exception as e:
            logger.debug(f"QQ Official Bot: send_typing failed: {e}")

    async def _send_input_notify(self, chat_id: str) -> None:
        """Send a C2C msg_type=6 typing notification (QQ client shows "The other party is typing...")."""
        import httpx as hx

        headers = await self._build_api_headers()
        base_url = self._api_base_url()
        msg_id = self._resolve_msg_id(chat_id)
        seq_key = msg_id or chat_id

        body: dict[str, Any] = {
            "msg_type": 6,
            "input_notify": {"input_type": 1, "input_second": 10},
            "msg_seq": self._next_msg_seq(seq_key),
        }
        if msg_id:
            body["msg_id"] = msg_id

        url = f"{base_url}/v2/users/{chat_id}/messages"
        async with hx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()

    async def _send_typing_via_http(
        self,
        chat_id: str,
        chat_type: str,
        msg_id: str | None,
    ) -> str:
        """Send a thinking indicator via the HTTP API."""
        try:
            import httpx as hx
        except ImportError:
            return ""

        headers = await self._build_api_headers()
        base_url = self._api_base_url()

        body: dict[str, Any] = {"msg_type": 0, "content": "Thinking..."}
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
        """Clear the typing-status indicator.

        Only clears internal state flags; the "Thinking..." placeholder message is not recalled.
        QQ IM doesn't support collapsing the thinking process, and recalling the message would display
        "The other party recalled a message," so keeping the placeholder as a visible indication of the
        thinking process is preferable.
        C2C's msg_type=6 typing notification expires automatically.
        """
        self._typing_c2c_active.discard(chat_id)
        self._typing_start_time.pop(chat_id, None)
        self._typing_msg_ids.pop(chat_id, None)

    async def _recall_message_via_http(
        self,
        chat_id: str,
        chat_type: str,
        message_id: str,
    ) -> None:
        """Recall a message via the HTTP API."""
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

    # ==================== Media download/upload ====================

    async def download_media(self, media: MediaFile) -> Path:
        """Download a media file.

        For voice, voice_wav_url is preferred (WAV format offers better STT compatibility).
        All requests carry the Bot Token auth header in case the QQ CDN requires verification.
        """
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        download_url = media.extra.get("voice_wav_url") or media.url
        if not download_url:
            raise ValueError("Media has no url")

        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        headers = await self._build_api_headers(content_type="")

        async with hx.AsyncClient(timeout=60.0) as client:
            response = await client.get(download_url, headers=headers)
            if response.status_code in (401, 403) and download_url != media.url:
                logger.debug("QQ: voice_wav_url auth failed, retrying with original url")
                response = await client.get(media.url, headers=headers)
            if response.status_code in (401, 403):
                logger.debug("QQ: retrying media download without auth headers")
                response = await client.get(download_url)
            response.raise_for_status()

            from openakita.channels.base import sanitize_filename

            fname = Path(media.filename).name or "download"
            if download_url != media.url and not fname.endswith(".wav"):
                fname = Path(fname).stem + ".wav"
            safe_name = sanitize_filename(fname)
            local_path = self.media_dir / safe_name
            with open(local_path, "wb") as f:
                f.write(response.content)

            media.local_path = str(local_path)
            media.status = MediaStatus.READY
            return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """Upload a media file."""
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
