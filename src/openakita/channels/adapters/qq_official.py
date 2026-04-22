"""
QQ 官方机器人适配器

基于 QQ 官方机器人 API v2 实现:
- AppID + AppSecret 鉴权 (OAuth2 Access Token)
- 支持 WebSocket 和 Webhook 两种事件接收模式
- 支持群聊、单聊 (C2C)、频道消息
- 文本/图片/富媒体消息收发

模式说明:
- websocket (默认): 自建 WebSocket 连接到 QQ Gateway，无需公网 IP
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
# 延迟导入 websockets（仅 WebSocket 模式使用）
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
    QQ 官方机器人适配器

    通过 QQ 开放平台官方 API 接入。

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
            footer_elapsed: 回复末尾显示处理耗时（默认 True，可通过 QQBOT_FOOTER_ELAPSED 环境变量控制）
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
        self._retry_delay: int = 5  # 重连延迟（秒），on_ready 时重置
        self._webhook_runner: Any | None = None  # aiohttp web runner
        self._access_token: str | None = None  # OAuth2 access token
        self._token_expires: float = 0

        # ---- WebSocket gateway state ----
        self._ws_session_id: str | None = None
        self._ws_last_seq: int | None = None
        self._ws_heartbeat_ack: bool = True

        # ---- chat_id 路由表 ----
        # {chat_id: "group" | "c2c" | "channel"}
        self._chat_type_map: dict[str, str] = {}
        # {chat_id: 最近一条收到的 msg_id}（被动回复需要）
        self._last_msg_id: dict[str, str] = {}
        # {chat_id: 最近一条收到的 event_id}（msg_id 过期时回退使用）
        self._last_event_id: dict[str, str] = {}
        # {msg_id: msg_seq} — QQ API 要求同一 msg_id 的多条回复递增 msg_seq 避免去重
        self._msg_seq: dict[str, int] = {}
        self._msg_seq_max_entries = 500
        # {chat_id: message_id} — "正在思考中..."提示消息 ID（send_typing 发出，clear_typing 撤回）
        self._typing_msg_ids: dict[str, str] = {}
        # C2C 使用 msg_type=6 输入状态通知，无需撤回，用此集合标记
        self._typing_c2c_active: set[str] = set()
        # {chat_id: start_time} — typing 开始时间，用于计算耗时 footer
        self._typing_start_time: dict[str, float] = {}
        self._footer_elapsed = (
            footer_elapsed
            if footer_elapsed is not None
            else (os.environ.get("QQBOT_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes"))
        )
        # Markdown 能力是否可用（自定义 markdown 需内邀开通，首次失败后自动降级）
        self._markdown_available: bool = True
        # 沙箱环境 2026/03/05 起不受消息频控限制
        self._sandbox_rate_exempt: bool = sandbox

        # 待投递消息队列：QQ 群聊不支持主动发送，缓存后等用户下条消息时投递
        self._pending_messages: dict[str, list[tuple[float, str]]] = {}
        self._pending_max_per_chat = 5

        # 消息去重：Webhook/WebSocket 可能重复投递
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

    def _remember_chat(
        self,
        chat_id: str,
        chat_type: str,
        msg_id: str = "",
        event_id: str = "",
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
            logger.info(f"Local file served as public URL: {url}")
            return url
        except Exception as e:
            logger.warning(f"Failed to make local file publicly accessible: {e}")
            return None

    @staticmethod
    def _is_proactive_limit_error(exc: BaseException) -> bool:
        """检测是否为 QQ 群聊主动消息限制错误（11255 invalid request）。"""
        s = str(exc).lower()
        return "11255" in s or "invalid request" in s

    @staticmethod
    def _is_msg_expired_error(exc: BaseException) -> bool:
        """检测是否为 msg_id/event_id 过期错误。

        QQ API 在被动回复窗口（约 5 分钟）过期后返回特定错误码。
        """
        s = str(exc).lower()
        return any(k in s for k in ("msg_id is invalid", "40003", "msg id is invalid"))

    def _enqueue_pending(self, chat_id: str, text: str) -> None:
        """将无法主动发送的消息缓存，等用户下条消息到达时投递。

        QQ 群聊主动推送已于 2025/04/21 废弃，消息必须在被动回复窗口内发送。
        """
        pending = self._pending_messages.setdefault(chat_id, [])
        if len(pending) >= self._pending_max_per_chat:
            pending.pop(0)
        pending.append((time.time(), text))

    @staticmethod
    def _format_pending_delay(queued_at: float) -> str:
        """将入队到投递的时间差格式化为可读文本。"""
        delta = int(time.time() - queued_at)
        if delta < 60:
            return "刚刚"
        if delta < 3600:
            return f"{delta // 60} 分钟前"
        if delta < 86400:
            h, m = divmod(delta, 3600)
            return f"{h} 小时{f' {m // 60} 分钟' if m // 60 else ''}前"
        return f"{delta // 86400} 天前"

    async def _flush_pending_messages(self, chat_id: str) -> None:
        """当收到用户新消息时，投递该 chat_id 下缓存的待发消息。"""
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

        header = "📬 以下消息因 QQ 群聊限制未能及时发送，现在补发给你：\n"
        combined = header + "\n\n".join(parts)

        chat_type = self._chat_type_map.get(chat_id, "group")
        try:
            await self._send_text_via_http(chat_type, chat_id, combined, msg_id)
            logger.info(f"QQ: delivered {len(pending)} pending message(s) to {chat_id}")
        except Exception as e:
            logger.warning(f"QQ: failed to deliver pending messages to {chat_id}: {e}")

    async def start(self) -> None:
        """启动 QQ 官方机器人"""
        if not self.app_id or not self.app_secret:
            raise ValueError("QQ 机器人 AppID 或 AppSecret 未配置，请在 q.qq.com 开发设置中获取。")

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

    # 不可重试的配置类错误关键词（遇到后大幅延长重试间隔）
    _FATAL_KEYWORDS = ("不在白名单", "invalid appid", "invalid secret", "鉴权失败")
    _FATAL_GIVE_UP_THRESHOLD = 5

    # ==================== WebSocket Gateway ====================

    async def _get_gateway_url(self) -> str:
        """通过 REST API 获取 WebSocket Gateway 连接地址。"""
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
        """定时发送心跳 (op 1)，检测 ACK 超时则关闭连接。"""
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
        """WebSocket 模式：自建 Gateway 连接，带自动重连/Resume。"""
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
                            f"QQ Official Bot 配置错误: {err_msg}\n"
                            f"  → 请检查 QQ 开放平台配置（IP 白名单 / AppID / AppSecret）\n"
                            f"  → 将持续后台重试，修复配置后自动恢复"
                        )
                    elif consecutive_fatal % 5 == 0:
                        logger.warning(
                            f"QQ Official Bot 仍无法连接 (已重试 {consecutive_fatal} 次): {err_msg}"
                        )

                    if consecutive_fatal >= self._FATAL_GIVE_UP_THRESHOLD:
                        reason = (
                            f"连续 {consecutive_fatal} 次认证失败: {err_msg}。"
                            "请检查 QQ 开放平台 AppID / AppSecret / IP 白名单配置"
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

    # ==================== Webhook 模式 ====================

    async def _get_access_token(self) -> str:
        """获取 QQ 官方 API 的 OAuth2 access_token"""
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
        验证 QQ Webhook 回调签名 (ed25519)。

        QQ 官方 Webhook 使用 ed25519 签名验证：
        - 签名内容: timestamp + body
        - 密钥: 由 app_secret + bot_secret seed 派生的 ed25519 密钥
        - 签名值: 在 X-Signature-Ed25519 header 中

        简化实现：使用 HMAC-SHA256 作为备选验签方式（部分旧版本 API 支持）。
        如需完整 ed25519 验签，需安装 PyNaCl。
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
        """启动 Webhook HTTP 回调服务器"""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp not installed. Run: pip install aiohttp")

        async def handle_callback(request: web.Request) -> web.Response:
            """处理 QQ Webhook 回调"""
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

            # op=13: 验证回调 URL (Validation)
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

            # op=0: 事件分发 (Dispatch)
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
        """处理 Webhook/WS 推送的事件"""
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
        """将 Webhook 群聊消息转换为 UnifiedMessage"""
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
        """将 Webhook 单聊消息转换为 UnifiedMessage"""
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
        """将 Webhook 频道消息转换为 UnifiedMessage"""
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
        """从 QQ 语音附件中提取平台特有字段。

        QQ 语音附件提供:
        - voice_wav_url: WAV 格式下载链接（比默认 SILK 更通用）
        - asr_refer_text: QQ 平台侧的 ASR 转写结果
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

    # ==================== REST API 基础设施 ====================

    async def _build_api_headers(self, content_type: str = "application/json") -> dict:
        """构建 QQ API V2 请求头，使用正确的 QQBot {access_token} 格式。"""
        token = await self._get_access_token()
        headers = {"Authorization": f"QQBot {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _api_base_url(self) -> str:
        return "https://sandbox.api.sgroup.qq.com" if self.sandbox else "https://api.sgroup.qq.com"

    # ==================== 富媒体上传 ====================

    async def _upload_rich_media_url(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        url: str,
        srv_send_msg: bool = False,
    ) -> dict:
        """通过公网 URL 上传富媒体到 QQ 服务器 (REST API)。

        Args:
            chat_type: "group" 或 "c2c"
            target_id: group_openid 或 user openid
            file_type: 1=图片, 2=视频, 3=语音, 4=文件
            url: 公网可访问的媒体 URL
            srv_send_msg: True 则服务端直接发送（占主动消息频次）

        Returns:
            API 响应 dict，包含 file_info / file_uuid / ttl 等字段
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
        """通过 file_data (base64) 直接上传富媒体到 QQ 服务器。

        QQ 官方 API 支持 file_data（base64 编码的二进制内容）方式上传。
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
        完整的富媒体发送流程（两步）：上传 + 发消息。

        支持两种上传方式（二选一）：
        - url: 公网可访问的媒体 URL（走 REST API）
        - local_path: 本地文件路径（读取后 base64 编码）

        Args:
            chat_type: "group" 或 "c2c"
            target_id: 目标 openid
            file_type: 1=图片, 2=视频, 3=语音, 4=文件
            url: 公网可访问的媒体 URL
            msg_id: 被动回复的消息 ID（可选）
            local_path: 本地文件路径（可选，与 url 二选一）
            event_id: 被动回复的事件 ID（msg_id 过期时回退使用）

        Returns:
            发送后的消息 ID
        """
        import base64 as b64
        from pathlib import Path as _P

        # Step 1: 上传富媒体资源获取 file_info
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

        # Step 2: 发送消息 msg_type=7 (media)
        return await self._send_media_message_via_http(
            chat_type,
            target_id,
            file_info,
            msg_id,
            event_id=event_id,
        )

    # ==================== REST 消息发送 ====================

    async def _send_media_message_via_http(
        self,
        chat_type: str,
        target_id: str,
        file_info: str,
        msg_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        """通过 HTTP 直接发送媒体消息 (msg_type=7)。"""
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
        """通过 HTTP 发送纯文本消息。"""
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
        """频道消息发送：支持 content + image 在同一条消息中，支持 Markdown。

        QQ 频道 API 与群/C2C 不同，支持文本和图片在同一条 POST 中发送。
        - image_url 使用 JSON body 的 image 字段
        - image_path 使用 multipart form 的 file_image 字段
        """
        import httpx as hx

        base_url = self._api_base_url()
        url = f"{base_url}/channels/{channel_id}/messages"

        # 尝试 Markdown（仅纯文本无图片时）
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
                logger.warning("QQ 频道 Markdown 发送失败，已降级为纯文本: %s", e)

        # 本地图片：multipart form
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

        # JSON body（纯文本 / 文本+图片URL / 纯图片URL）
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

    def _append_elapsed_footer(self, text: str, chat_id: str) -> str:
        """若开启 footer_elapsed，在文本末尾追加耗时信息。"""
        if not self._footer_elapsed or not text:
            return text
        start = self._typing_start_time.pop(chat_id, None)
        if not start:
            return text
        elapsed = time.time() - start
        if elapsed < 1.0:
            return text
        return f"{text}\n\n⏱ 完成 ({elapsed:.1f}s)"

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息

        支持:
        - 文本消息 (msg_type=0)
        - Markdown 消息 (msg_type=2, 需内邀开通，失败自动降级)
        - 图片消息 (频道: content+image/file_image; 群/C2C: 两步富媒体上传)
        - 文件消息 (群/C2C: file_type=4 两步富媒体上传)
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
            # msg_id 过期：尝试使用 event_id 重发（被动回复窗口约 5 分钟）
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
        """通过 HTTP API 发送消息（文本/Markdown/图片/文件），统一用于 WS 和 Webhook 模式。"""
        try:
            import httpx as hx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        text = message.content.text or ""
        target_id = message.chat_id

        # 提取首张图片
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
            # 频道支持 text+image 在同一条消息中
            result_id = await self._send_channel_message_via_http(
                target_id,
                text,
                first_image_url,
                first_image_path,
                msg_id,
                parse_mode,
            )
        else:
            # 群聊/C2C: 文本和图片必须分两条消息
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

            # 发送首张图片（群/C2C 两步富媒体上传）
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

        # 循环发送剩余图片
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

        # 发送文件附件 (file_type=4, 频道不支持)
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
        """发送文件（file_type=4），支持群聊和 C2C。

        优先将本地文件转为公网 URL 上传（QQ API 可从 URL 获取扩展名），
        当 public_api_url 未配置时降级为 base64 上传（QQ 可能无法识别文件类型）。
        """
        chat_type = self._resolve_chat_type(chat_id)
        if chat_type == "channel":
            raise NotImplementedError("QQ 频道暂不支持通过富媒体 API 发送文件")
        msg_id = self._resolve_msg_id(chat_id)

        if caption:
            try:
                await self._send_text_via_http(chat_type, chat_id, caption, msg_id)
            except Exception as e:
                logger.warning(f"QQ: send file caption failed: {e}")

        # 优先走 URL 上传：QQ 从 URL 路径识别扩展名，文件可正常打开
        public_url = self._local_path_to_public_url(file_path)
        if public_url:
            return await self._send_rich_media(
                chat_type,
                chat_id,
                file_type=4,
                url=public_url,
                msg_id=msg_id,
            )

        # 降级：base64 上传（QQ 无法从二进制推断文件扩展名，接收方可能无法打开）
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

    # QQ 官方 API 文档允许直传的语音格式（无需任何转码）
    # 来源：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/send-receive/rich-media.html
    _QQ_VOICE_DIRECT_FORMATS = {".silk", ".slk", ".wav", ".mp3", ".flac"}

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """发送语音消息 (file_type=3, base64 上传)。

        QQ 官方 API 已支持 silk/wav/mp3/flac 直接上传，**不再强制 SILK 转码**。
        - 上述 4 种格式：原文件直接 base64 上传
        - 其他格式（opus/ogg/amr/m4a/...）：用 ffmpeg 转为 mp3 后上传
        - 无 ffmpeg 且格式不支持：抛 RuntimeError 并给出明确提示
        """
        import base64 as b64
        from pathlib import Path as _Path

        src = _Path(voice_path)
        if not src.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")

        chat_type = self._resolve_chat_type(chat_id)
        if chat_type == "channel":
            raise NotImplementedError("QQ 频道暂不支持语音发送")
        msg_id = self._resolve_msg_id(chat_id)

        ext = src.suffix.lower()
        if ext in self._QQ_VOICE_DIRECT_FORMATS:
            voice_bytes = src.read_bytes()
        else:
            voice_bytes = await self._transcode_voice_to_mp3(src)

        file_data = b64.standard_b64encode(voice_bytes).decode("ascii")
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

    @staticmethod
    async def _transcode_voice_to_mp3(src_path: Path) -> bytes:
        """把任意音频转成 mp3 字节流（用于 QQ send_voice 兜底）。

        通过 ffmpeg 转码为 64kbps mono mp3——QQ 接收即可。
        ffmpeg 不可用时抛 RuntimeError，提示用户改传 silk/wav/mp3/flac。
        """
        import asyncio
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                f"音频格式 {src_path.suffix} 不在 QQ 直传列表 "
                f"(silk/wav/mp3/flac) 中，且系统未安装 ffmpeg 用于自动转码。"
                f"请安装 ffmpeg，或将语音转换为受支持格式后再发送。"
            )

        loop = asyncio.get_event_loop()

        def _run_ffmpeg() -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fp:
                tmp_out = fp.name
            try:
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(src_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-b:a",
                    "64k",
                    "-f",
                    "mp3",
                    tmp_out,
                ]
                extra: dict[str, Any] = {}
                if os.name == "nt":
                    extra["creationflags"] = subprocess.CREATE_NO_WINDOW
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=60,
                    check=False,
                    **extra,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"ffmpeg 转 mp3 失败: {err}")
                return Path(tmp_out).read_bytes()
            finally:
                Path(tmp_out).unlink(missing_ok=True)

        return await loop.run_in_executor(None, _run_ffmpeg)

    # ==================== Typing 提示 ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送输入状态提示。

        C2C 单聊先发 msg_type=6 原生输入状态通知（每次调用续期），同时发送
        一条可见的"正在思考中..."占位消息（幂等，只发一次），在 clear_typing 时撤回。
        群聊/频道使用 msg_type=0 文本消息"正在思考中..."（幂等，只发一次）。
        """
        if chat_id not in self._typing_start_time:
            self._typing_start_time[chat_id] = time.time()

        chat_type = self._resolve_chat_type(chat_id)

        # C2C: 使用 msg_type=6 输入状态通知，每 4 秒续期一次
        if chat_type == "c2c":
            self._typing_c2c_active.add(chat_id)
            try:
                await self._send_input_notify(chat_id)
            except Exception as e:
                logger.debug(f"QQ Official Bot: send_typing (input_notify) failed: {e}")

        # 群聊/频道/C2C: 幂等发送文本消息
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
        """C2C 发送 msg_type=6 输入状态通知（QQ 客户端显示"对方正在输入..."）。"""
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
        """通过 HTTP API 发送思考提示"""
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
        """清除输入状态提示。

        仅清理内部状态标记，不撤回"正在思考中..."占位消息。
        QQ IM 不支持折叠思考过程，撤回反而会显示"对方撤回了一条消息"，
        保留占位消息作为思考过程的可见指示更合理。
        C2C 的 msg_type=6 输入状态通知自动过期。
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
        """通过 HTTP API 撤回消息"""
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
        """下载媒体文件。

        语音文件优先使用 voice_wav_url（WAV 格式，STT 兼容性更好）。
        所有请求携带 Bot Token 鉴权头以防 QQ CDN 要求验证。
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
        """上传媒体文件"""
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
