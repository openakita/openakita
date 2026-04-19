"""
Feishu adapter

Built on the lark-oapi library:
- Event subscription (supports both long-lived WebSocket and Webhook)
- Card messages
- Text/image/file send and receive

Reference docs:
- Bot overview: https://open.feishu.cn/document/client-docs/bot-v3/bot-overview
- Python SDK: https://github.com/larksuite/oapi-sdk-python
- Event subscription: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events
"""

import asyncio
import collections
import contextlib
import importlib.util
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita.python_compat import patch_simplejson_jsondecodeerror

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)


def _drain_loop_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 3.0) -> None:
    """Cancel all pending tasks on *loop* and run them to completion.

    Lark SDK spawns internal asyncio tasks (ExpiringCache cron, ping loop,
    receive loop) that are never explicitly cancelled.  If we just close the
    loop, Python emits "Task was destroyed but it is pending!" for each one,
    and — more importantly — the thread may hang waiting for those tasks,
    blocking process shutdown.

    A *timeout* guard prevents indefinite blocking in case any task swallows
    ``CancelledError`` and keeps running (as some third-party SDKs do).
    """
    try:
        pending = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        )
    except Exception:
        pass


# Lazy import
lark_oapi = None


def _import_lark():
    """Lazily import the lark-oapi library"""
    global lark_oapi
    if lark_oapi is None:
        try:
            patch_simplejson_jsondecodeerror(logger=logger)
            import lark_oapi as lark

            lark_oapi = lark
        except ImportError as exc:
            logger.error("lark_oapi import failed: %s", exc, exc_info=True)
            if "JSONDecodeError" in str(exc) and "simplejson" in str(exc):
                raise ImportError(
                    "Feishu SDK dependency conflict: simplejson is missing JSONDecodeError. "
                    "Please go to 'Setup Center → Python Environment' and run the one-click fix, then restart."
                ) from exc
            from openakita.tools._import_helper import import_or_hint

            raise ImportError(import_or_hint("lark_oapi")) from exc


@dataclass
class FeishuConfig:
    """Feishu / Lark configuration"""

    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None
    log_level: str = "INFO"
    domain: str = "feishu"

    def __post_init__(self) -> None:
        if not self.app_id or not self.app_id.strip():
            raise ValueError("FeishuConfig: app_id is required")
        if not self.app_secret or not self.app_secret.strip():
            raise ValueError("FeishuConfig: app_secret is required")
        self.log_level = self.log_level.upper()
        if self.log_level not in ("DEBUG", "INFO", "WARN", "ERROR"):
            raise ValueError(f"FeishuConfig: invalid log_level '{self.log_level}'")
        if self.domain not in ("feishu", "lark"):
            raise ValueError(
                f"FeishuConfig: domain must be 'feishu' or 'lark', got {self.domain!r}"
            )

    @property
    def is_lark(self) -> bool:
        return self.domain == "lark"

    @property
    def api_domain(self) -> str:
        """REST / Open API base, e.g. https://open.feishu.cn"""
        return "https://open.larksuite.com" if self.is_lark else "https://open.feishu.cn"

    @property
    def accounts_domain(self) -> str:
        """OAuth / Accounts base"""
        return "https://accounts.larksuite.com" if self.is_lark else "https://accounts.feishu.cn"

    @property
    def platform_label(self) -> str:
        return "Lark" if self.is_lark else "Feishu"


class FeishuAdapter(ChannelAdapter):
    """
    Feishu adapter

    Supports:
    - Event subscription (long-lived WebSocket or Webhook)
    - Text / rich text messages
    - Images / files
    - Card messages

    Usage:
    1. Long-connection mode (recommended): start() automatically launches the WebSocket connection
    2. Webhook mode: use handle_event() to process HTTP callbacks
    """

    channel_name = "feishu"

    capabilities = {
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": True,
        "get_user_info": True,
        "get_chat_members": True,
        "get_recent_messages": True,
        "markdown": True,
        "add_reaction": True,
    }

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        media_dir: Path | None = None,
        log_level: str = "INFO",
        domain: str = "feishu",
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
        streaming_enabled: bool | None = None,
        group_streaming: bool | None = None,
        streaming_throttle_ms: int | None = None,
        group_response_mode: str | None = None,
        footer_elapsed: bool | None = None,
        footer_status: bool | None = None,
    ):
        """
        Args:
            app_id: Feishu application App ID (obtained from the developer console)
            app_secret: Feishu application App Secret (obtained from the developer console)
            verification_token: Event subscription verification token (required in Webhook mode)
            encrypt_key: Event encryption key (required if encryption is configured)
            media_dir: Media file storage directory
            log_level: Log level (DEBUG, INFO, WARN, ERROR)
            channel_name: Channel name (used to distinguish instances in multi-bot setups)
            bot_id: Unique identifier for the bot instance
            agent_profile_id: Bound agent profile ID
        """
        if channel_name is None:
            channel_name = "lark" if domain == "lark" else "feishu"
        super().__init__(
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id
        )

        self.config = FeishuConfig(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            log_level=log_level,
            domain=domain,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/feishu")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._ws_client: Any | None = None
        self._event_dispatcher: Any | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_watchdog_task: asyncio.Task | None = None
        self._ws_restart_count: int = 0
        self._bot_open_id: str | None = None
        self._capabilities: list[str] = []

        # Message deduplication: WebSocket reconnects may cause duplicate delivery
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

        # User name cache: open_id -> display name (avoids repeated Contact API calls)
        self._user_name_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._user_name_cache_max = 200

        # Group name cache: chat_id -> group name (avoids repeated im.v1.chat.get calls)
        self._chat_name_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._chat_name_cache_max = 200

        # "Thinking..." placeholder card: session_key -> card message_id
        # session_key = chat_id or chat_id:thread_id (thread mode)
        self._thinking_cards: dict[str, str] = {}
        # CardKit streaming state: sk -> (card_id, element_id)
        # If present, use CardKit API to update the card (no edit-count limit)
        self._cardkit_cards: dict[str, tuple[str, str]] = {}
        self._cardkit_available: bool | None = None  # None = not probed
        self._tenant_token: str | None = None
        self._tenant_token_expires: float = 0
        self._tenant_token_lock: asyncio.Lock = asyncio.Lock()
        # Most recent user message ID: session_key -> user_msg_id (for send_typing to target the reply)
        self._last_user_msg: dict[str, str] = {}
        # Set of session_keys whose thinking card has been consumed; prevents _keep_typing from rebuilding it
        self._typing_suppressed: set[str] = set()

        # Streaming output state (constructor arg takes priority; falls back to env when None)
        self._streaming_enabled = (
            streaming_enabled
            if streaming_enabled is not None
            else (
                os.environ.get("FEISHU_STREAMING_ENABLED", "false").lower() in ("true", "1", "yes")
            )
        )
        self._group_streaming = (
            group_streaming
            if group_streaming is not None
            else (os.environ.get("FEISHU_GROUP_STREAMING", "false").lower() in ("true", "1", "yes"))
        )
        self._streaming_throttle_ms = (
            streaming_throttle_ms
            if streaming_throttle_ms is not None
            else (int(os.environ.get("FEISHU_STREAMING_THROTTLE_MS", "800")))
        )
        # session_key -> accumulated streaming text
        self._streaming_buffers: dict[str, str] = {}
        # session_key -> last PATCH timestamp (seconds)
        self._streaming_last_patch: dict[str, float] = {}
        # session_key -> whether finalized
        self._streaming_finalized: set[str] = set()
        # session_key -> thinking content (buffered during streaming, cleared after finalize)
        self._streaming_thinking: dict[str, str] = {}
        # session_key -> thinking duration (ms)
        self._streaming_thinking_ms: dict[str, int] = {}
        # session_key -> chain text lines (tool calls/results, appended during streaming, cleared after finalize)
        self._streaming_chain: dict[str, list[str]] = {}

        # Bot-sent message ID tracking: used to identify "reply to bot message" as an implicit mention
        self._bot_sent_msg_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._bot_sent_msg_ids_max = 500

        # Per-bot group chat response mode (constructor arg > env var > global config)
        self._group_response_mode: str | None = group_response_mode or (
            os.environ.get("FEISHU_GROUP_RESPONSE_MODE") or None
        )

        # Card footer config (show elapsed time / status)
        self._footer_elapsed = (
            footer_elapsed
            if footer_elapsed is not None
            else (os.environ.get("FEISHU_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes"))
        )
        self._footer_status = (
            footer_status
            if footer_status is not None
            else (os.environ.get("FEISHU_FOOTER_STATUS", "true").lower() in ("true", "1", "yes"))
        )
        self._typing_start_time: dict[str, float] = {}
        self._typing_status: dict[str, str] = {}

        # Key event buffer (per-chat_id, up to _MAX_EVENTS_PER_CHAT entries)
        self._important_events: dict[str, list[dict]] = {}
        self._events_lock = threading.Lock()
        self._MAX_EVENTS_PER_CHAT = 10

    async def start(self) -> None:
        """
        Start the Feishu client and automatically establish a WebSocket long connection

        Automatically starts a WebSocket long connection (non-blocking mode) to receive messages.
        The SDK manages access_token automatically; no manual refresh needed.
        """
        _import_lark()

        # Create client
        log_level = getattr(lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO)

        sdk_domain = lark_oapi.LARK_DOMAIN if self.config.is_lark else lark_oapi.FEISHU_DOMAIN
        self._client = (
            lark_oapi.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .domain(sdk_domain)
            .log_level(log_level)
            .build()
        )

        # Record the main event loop, used to dispatch coroutines from the WebSocket thread
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
        logger.info("Feishu adapter: client initialized")

        # Try to obtain the bot's open_id (used for precise @mention matching).
        # lark_oapi.api.bot submodule may be missing in some packaged builds;
        # import failure should not block adapter startup — only affects group @mention detection.
        _bot_info_error: str | None = None
        try:
            import lark_oapi.api.bot.v3 as bot_v3

            for attempt in range(3):
                try:
                    req = bot_v3.GetBotInfoRequest.builder().build()
                    resp = await asyncio.get_running_loop().run_in_executor(
                        None, lambda _r=req: self._client.bot.v3.bot_info.get(_r)
                    )
                    if resp.success() and resp.data and resp.data.bot:
                        self._bot_open_id = getattr(resp.data.bot, "open_id", None)
                        logger.info(f"Feishu bot open_id: {self._bot_open_id}")
                        _bot_info_error = None
                        break
                    else:
                        _bot_info_error = getattr(resp, "msg", "unknown")
                        logger.warning(
                            f"Feishu: GetBotInfo attempt {attempt + 1}/3 failed: {_bot_info_error}"
                        )
                except Exception as e:
                    _bot_info_error = str(e)
                    logger.warning(f"Feishu: GetBotInfo attempt {attempt + 1}/3 error: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        except ImportError:
            logger.warning("lark_oapi.api.bot module not available, trying raw HTTP fallback...")
            try:
                raw_req = (
                    lark_oapi.BaseRequest.builder()
                    .http_method(lark_oapi.HttpMethod.GET)
                    .uri("/open-apis/bot/v3/info")
                    .token_types({lark_oapi.AccessTokenType.TENANT})
                    .build()
                )
                raw_resp = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.request(raw_req)
                )
                if raw_resp.success() and raw_resp.raw:
                    _body = json.loads(raw_resp.raw.content)
                    _bot = _body.get("bot") or _body.get("data", {}).get("bot") or {}
                    self._bot_open_id = _bot.get("open_id")
                    if self._bot_open_id:
                        logger.info(f"Feishu bot open_id (raw HTTP): {self._bot_open_id}")
                        _bot_info_error = None
                    else:
                        _bot_info_error = "raw HTTP response did not include bot open_id"
                else:
                    _bot_info_error = getattr(raw_resp, "msg", "raw HTTP fallback failed")
            except Exception as e:
                _bot_info_error = str(e)
                logger.warning(f"Feishu: raw HTTP bot info fallback failed: {e}")

        if not self._bot_open_id and _bot_info_error:
            _err_lower = (_bot_info_error or "").lower()
            if any(
                kw in _err_lower for kw in ("invalid", "app_id", "secret", "token", "auth", "10003")
            ):
                raise ConnectionError(
                    f"{self.config.platform_label} App ID or App Secret is invalid; please check the application credentials. "
                    f"(Error details: {_bot_info_error})"
                )
            if "connect" in _err_lower or "timeout" in _err_lower or "resolve" in _err_lower:
                raise ConnectionError(
                    f"Unable to connect to {self.config.platform_label} API ({self.config.api_domain}); please check network connectivity. "
                    f"(Error details: {_bot_info_error})"
                )
            logger.warning(
                "Feishu: bot open_id not available. "
                "@mention detection will be disabled (bot will NOT respond to any @mention in groups)."
            )

        # Mark as running before launching WS:
        # - must happen after client creation + successful lark import (so the green dot isn't misleading)
        # - must happen before start_websocket (the WS thread relies on _running to decide whether to log errors)
        self._running = True

        # Automatically start the WebSocket long connection (non-blocking mode)
        try:
            self.start_websocket(blocking=False)
            logger.info("Feishu adapter: WebSocket started in background")
        except Exception as e:
            logger.warning(f"Feishu adapter: WebSocket startup failed: {e}")
            logger.warning("Feishu adapter: falling back to webhook-only mode")

        if self._group_response_mode and self._group_response_mode != "mention_only":
            logger.info(
                f"Feishu[{self.channel_name}]: group_response_mode={self._group_response_mode}, "
                f"please ensure 'Receive all group chat messages' is enabled in the Feishu console"
            )

        # Probe available permissions / capabilities
        await self._probe_capabilities()

        # Start the WebSocket watchdog (background task that periodically checks WS thread liveness)
        if self._ws_thread is not None:
            self._ws_watchdog_task = asyncio.create_task(self._ws_watchdog_loop())

    # ==================== WebSocket watchdog ====================

    _WS_WATCHDOG_INTERVAL = 15  # Check interval (seconds)
    _WS_WATCHDOG_INITIAL_DELAY = 30  # Initial wait before first check (seconds)
    _WS_RECONNECT_MIN_INTERVAL = 10  # Minimum reconnect interval (seconds)
    _WS_RECONNECT_MAX_DELAY = 120  # Maximum backoff delay (seconds)

    _WS_STABLE_THRESHOLD = 300  # Reset restart counter after 5 minutes of stable connection
    _WS_FATAL_RESTART_THRESHOLD = 5  # More than this many consecutive restarts without stability = fatal failure

    async def _ws_watchdog_loop(self) -> None:
        """Periodically check whether the WebSocket thread is alive; restart automatically after exit."""
        await asyncio.sleep(self._WS_WATCHDOG_INITIAL_DELAY)
        last_restart_time = 0.0
        stable_since = asyncio.get_running_loop().time()

        while self._running:
            await asyncio.sleep(self._WS_WATCHDOG_INTERVAL)
            if not self._running:
                break

            ws_thread = self._ws_thread
            if ws_thread is not None and ws_thread.is_alive():
                now = asyncio.get_running_loop().time()
                if self._ws_restart_count > 0 and (now - stable_since) >= self._WS_STABLE_THRESHOLD:
                    logger.info("Feishu WS watchdog: connection stable, resetting restart count")
                    self._ws_restart_count = 0
                continue

            # WS thread has exited; compute backoff delay then restart
            now = asyncio.get_running_loop().time()
            since_last = now - last_restart_time
            if since_last < self._WS_RECONNECT_MIN_INTERVAL:
                continue

            self._ws_restart_count += 1

            if self._ws_restart_count >= self._WS_FATAL_RESTART_THRESHOLD:
                reason = (
                    f"WebSocket failed to restart {self._ws_restart_count} times in a row; "
                    f"please check whether the {self.config.platform_label} App ID / App Secret is valid"
                )
                logger.error(f"Feishu WS watchdog: {reason}")
                self._running = False
                self._report_failure(reason)
                return

            backoff = min(
                self._WS_RECONNECT_MIN_INTERVAL * (2 ** min(self._ws_restart_count - 1, 6)),
                self._WS_RECONNECT_MAX_DELAY,
            )
            logger.warning(
                f"Feishu WS watchdog: thread exited (restart #{self._ws_restart_count}), "
                f"reconnecting in {backoff:.0f}s"
            )
            await asyncio.sleep(backoff)
            if not self._running:
                break

            try:
                self.start_websocket(blocking=False)
                last_restart_time = asyncio.get_running_loop().time()
                stable_since = last_restart_time
                logger.info(f"Feishu WS watchdog: reconnected (restart #{self._ws_restart_count})")
            except Exception as e:
                logger.error(f"Feishu WS watchdog: reconnect failed: {e}")

    async def _probe_capabilities(self) -> None:
        """Probe which permissions implemented by the Feishu adapter are available.

        Determines permission status by calling the API and inspecting the response code:
        - Insufficient permission: response messages usually contain "permission" / "access denied" / "scope".
        - Invalid argument / resource not found: indicates the permission itself is granted.
        """
        self._capabilities = ["send_message", "send_file", "reply_message"]
        if not self._client:
            return

        try:
            import lark_oapi.api.contact.v3 as contact_v3
            import lark_oapi.api.im.v1 as im_v1
        except ImportError:
            logger.warning("lark_oapi submodules not available for capability probing")
            return

        try:
            req = im_v1.GetChatRequest.builder().chat_id("probe_test").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat.get(req)
            )
            if not self._is_token_error(resp):
                self._capabilities.append("get_chat_info")
        except Exception:
            pass

        try:
            req = (
                contact_v3.GetUserRequest.builder()
                .user_id("probe_test")
                .user_id_type("open_id")
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.contact.v3.user.get(req)
            )
            if not self._is_token_error(resp):
                self._capabilities.append("get_user_info")
        except Exception:
            pass

        try:
            req = (
                im_v1.GetChatMembersRequest.builder()
                .chat_id("probe_test")
                .member_id_type("open_id")
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat_members.get(req)
            )
            if not self._is_token_error(resp):
                self._capabilities.append("get_chat_members")
        except Exception:
            pass

        try:
            req = (
                im_v1.ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id("probe_test")
                .page_size(1)
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.list(req)
            )
            if not self._is_token_error(resp):
                self._capabilities.append("get_message_history")
        except Exception:
            pass

        # Probe image upload permission (im:resource:upload)
        # Sending an invalid PNG header does not create any resource on the Feishu side:
        # - Permission OK -> returns "unsupported image format" (not a permission error)
        # - Missing permission -> returns "Access denied...scope"
        try:
            import io

            req = (
                im_v1.CreateImageRequest.builder()
                .request_body(
                    im_v1.CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(io.BytesIO(b"\x89PNG\r\n"))
                    .build()
                )
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.create(req)
            )
            if not self._is_token_error(resp):
                self._capabilities.append("upload_image")
            else:
                logger.warning(
                    "Feishu: missing im:resource:upload permission; image/sticker sending will be unavailable. "
                    "Please grant this permission to the bot in the Feishu Open Platform."
                )
        except Exception:
            pass

        # Probe CardKit streaming card permission (cardkit:card:write)
        try:
            result = await self._cardkit_api(
                "POST",
                "/open-apis/cardkit/v1/cards",
                body={"type": "card_json", "data": "{}", "settings": {}},
            )
            code = result.get("code", -1)
            if code == 0 or "card_id" in result.get("data", {}):
                self._cardkit_available = True
                self._capabilities.append("cardkit_streaming_card")
            elif self._is_permission_error(result.get("msg", "")):
                self._cardkit_available = False
                logger.info(
                    "Feishu: CardKit permission unavailable; streaming output will fall back to PatchMessage (20-30 edit limit). "
                    "Consider granting cardkit:card:write on the Feishu Open Platform."
                )
            else:
                self._cardkit_available = True
                self._capabilities.append("cardkit_streaming_card")
        except Exception:
            self._cardkit_available = False

        logger.info(f"Feishu capabilities: {self._capabilities}")
        _stream_detail = (
            f"streaming={self._streaming_enabled}"
            f", group_streaming={self._group_streaming}"
            f", cardkit={self._cardkit_available}"
            f", throttle={self._streaming_throttle_ms}ms"
        )
        if self._streaming_enabled:
            logger.info(f"Feishu: streaming enabled ({_stream_detail})")
        else:
            logger.info(
                f"Feishu: streaming disabled ({_stream_detail}). "
                "To enable, add streaming_enabled=true to the bot config or set FEISHU_STREAMING_ENABLED=true"
            )

    def start_websocket(self, blocking: bool = True) -> None:
        """
        Start the WebSocket long connection to receive events (recommended).

        Notes:
        - Only self-built enterprise apps are supported
        - Each app can establish up to 50 connections
        - Message delivery is cluster-mode: for multiple clients of the same app, only a random one receives each message

        Args:
            blocking: Whether to block the main thread (default True)
        """
        _import_lark()

        if not self._event_dispatcher:
            self._setup_event_dispatcher()

        logger.info("Starting Feishu WebSocket connection...")

        # lark_oapi.ws.client stores a module-level global `loop` variable that
        # Client.start / _connect / _receive_message_loop all reference directly.
        # Multiple FeishuAdapter instances starting on different threads would
        # overwrite this loop, causing create_task to dispatch to the wrong event
        # loop at runtime and silently dropping messages.
        #
        # Solution: use importlib.util to create a **separate copy** of the
        # lark_oapi.ws.client module for each thread (without modifying sys.modules).
        # Each copy's Client methods reference their own loop variable via
        # __globals__, which eliminates cross-instance pollution at the root.

        def _run_ws_in_thread() -> None:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            self._ws_loop = new_loop

            try:
                spec = importlib.util.find_spec("lark_oapi.ws.client")
                ws_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ws_mod)

                ws_client = ws_mod.Client(
                    self.config.app_id,
                    self.config.app_secret,
                    event_handler=self._event_dispatcher,
                    log_level=getattr(
                        lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO
                    ),
                    domain=self.config.api_domain,
                )
                self._ws_client = ws_client

                ws_client.start()
            except Exception as e:
                if self._running:
                    logger.error(f"Feishu WebSocket error: {e}", exc_info=True)
            finally:
                _drain_loop_tasks(new_loop)
                self._ws_loop = None
                with contextlib.suppress(Exception):
                    new_loop.close()

        if blocking:
            _run_ws_in_thread()
        else:
            self._ws_thread = threading.Thread(
                target=_run_ws_in_thread,
                daemon=True,
                name=f"FeishuWS-{self.channel_name}",
            )
            self._ws_thread.start()
            logger.info(
                f"Feishu WebSocket client started in background thread ({self.channel_name})"
            )

    def _setup_event_dispatcher(self) -> None:
        """Set up the event dispatcher"""
        _import_lark()

        # Create event dispatcher
        # verification_token and encrypt_key must be empty strings in long-connection mode
        builder = lark_oapi.EventDispatcherHandler.builder(
            verification_token="",  # Long-connection mode does not need verification
            encrypt_key="",  # Long-connection mode does not need encryption
        ).register_p2_im_message_receive_v1(self._on_message_receive)
        # Register message-read event to avoid SDK logging "processor not found" ERROR
        try:
            builder = builder.register_p2_im_message_read_v1(self._on_message_read)
        except AttributeError:
            pass
        # Register bot-enter-chat event
        try:
            builder = builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(
                self._on_bot_chat_entered
            )
        except AttributeError:
            pass
        # Register chat-updated event (group announcement changes, etc.)
        try:
            builder = builder.register_p2_im_chat_updated_v1(self._on_chat_updated)
        except AttributeError:
            pass
        # Register bot added/removed from chat events
        try:
            builder = builder.register_p2_im_chat_member_bot_added_v1(self._on_bot_chat_added)
        except AttributeError:
            pass
        try:
            builder = builder.register_p2_im_chat_member_bot_deleted_v1(self._on_bot_chat_deleted)
        except AttributeError:
            pass
        # Register reaction events to avoid SDK logging "processor not found" ERROR
        try:
            builder = builder.register_p2_im_message_reaction_created_v1(self._on_reaction_created)
        except AttributeError:
            pass
        try:
            builder = builder.register_p2_im_message_reaction_deleted_v1(self._on_reaction_deleted)
        except AttributeError:
            pass
        # Register card action callback (card.action.trigger); requires lark-oapi >= 1.3.0
        try:
            builder = builder.register_p2_card_action_trigger(self._on_card_action)
        except AttributeError:
            logger.warning(
                "Feishu: register_p2_card_action_trigger not available, "
                "card button interactions will not work. "
                "Upgrade lark-oapi to >= 1.3.0."
            )
        self._event_dispatcher = builder.build()

    def _on_message_receive(self, data: Any) -> None:
        """
        Handle an incoming message event (im.message.receive_v1)

        Note: this method is called synchronously from the WebSocket thread.
        """
        try:
            event = data.event
            message = event.message
            sender = event.sender

            logger.info(
                f"Feishu[{self.channel_name}]: received message from {sender.sender_id.open_id}"
            )

            # Extract the mentions list (used for is_mentioned detection)
            mentions_raw = []
            if hasattr(message, "mentions") and message.mentions:
                for m in message.mentions:
                    mid = getattr(m, "id", None)
                    mentions_raw.append(
                        {
                            "key": getattr(m, "key", ""),
                            "name": getattr(m, "name", ""),
                            "id": {
                                "open_id": getattr(mid, "open_id", "") if mid else "",
                                "user_id": getattr(mid, "user_id", "") if mid else "",
                            },
                        }
                    )

            # Build message dict
            msg_dict = {
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "message_type": message.message_type,
                "content": message.content,
                "root_id": getattr(message, "root_id", None),
                "parent_id": getattr(message, "parent_id", None),
                "mentions": mentions_raw,
                "create_time": getattr(message, "create_time", None),
            }

            sender_dict = {
                "sender_id": {
                    "user_id": getattr(sender.sender_id, "user_id", ""),
                    "open_id": getattr(sender.sender_id, "open_id", ""),
                },
            }

            # Safely dispatch the coroutine from the WebSocket thread to the main event loop.
            # Must use run_coroutine_threadsafe: the current thread already has a running event
            # loop (the SDK's ws loop); asyncio.run() would raise
            # "asyncio.run() cannot be called from a running event loop" and drop messages.
            if self._main_loop is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._handle_message_async(msg_dict, sender_dict),
                    self._main_loop,
                )

                # Add a callback to capture cross-thread dispatch exceptions and avoid silent message loss
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
        """Message-read event (im.message.message_read_v1); silently consumed to avoid SDK errors"""
        pass

    def _on_reaction_created(self, data: Any) -> None:
        """Message reaction created event (im.message.reaction.created_v1); silently consumed to avoid SDK errors"""
        pass

    def _on_reaction_deleted(self, data: Any) -> None:
        """Message reaction removed event (im.message.reaction.deleted_v1); silently consumed to avoid SDK errors"""
        pass

    def _on_bot_chat_entered(self, data: Any) -> None:
        """Bot-enter-chat event; silently consumed to avoid SDK errors"""
        pass

    def _on_chat_updated(self, data: Any) -> None:
        """Chat-updated event (im.chat.updated_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if not chat_id:
                return
            after = getattr(event, "after", None)
            changes = {}
            if after:
                name = getattr(after, "name", None)
                if name:
                    changes["name"] = name
                description = getattr(after, "description", None)
                if description is not None:
                    changes["description"] = description
            if changes:
                self._buffer_event(
                    chat_id,
                    {
                        "type": "chat_updated",
                        "chat_id": chat_id,
                        "changes": changes,
                    },
                )
        except Exception as e:
            logger.debug(f"Feishu: failed to handle chat_updated event: {e}")

    def _on_bot_chat_added(self, data: Any) -> None:
        """Bot-added-to-chat event (im.chat.member.bot.added_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if chat_id:
                self._buffer_event(
                    chat_id,
                    {
                        "type": "bot_added",
                        "chat_id": chat_id,
                    },
                )
                logger.info(f"Feishu: bot added to chat {chat_id}")
        except Exception as e:
            logger.debug(f"Feishu: failed to handle bot_added event: {e}")

    def _on_bot_chat_deleted(self, data: Any) -> None:
        """Bot-removed-from-chat event (im.chat.member.bot.deleted_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if chat_id:
                self._buffer_event(
                    chat_id,
                    {
                        "type": "bot_removed",
                        "chat_id": chat_id,
                    },
                )
                logger.info(f"Feishu: bot removed from chat {chat_id}")
        except Exception as e:
            logger.debug(f"Feishu: failed to handle bot_deleted event: {e}")

    # ==================== Card interaction callbacks ====================

    def _on_card_action(self, data: Any) -> Any:
        """Card callback action (card.action.trigger) — WebSocket long-connection mode.

        Called synchronously from the WS thread; must return P2CardActionTriggerResponse within 3 seconds.
        """
        try:
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                P2CardActionTriggerResponse,
            )
        except ImportError:
            logger.error("P2CardActionTriggerResponse not available, card action ignored")
            return None

        try:
            event = data.event
            action = event.action
            value = action.value
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    value = {"action": value}

            resp_dict = self._dispatch_card_action(value or {})
            return P2CardActionTriggerResponse(resp_dict)

        except Exception as e:
            logger.error(f"Feishu: card action callback error: {e}", exc_info=True)
            return P2CardActionTriggerResponse(
                {
                    "toast": {"type": "error", "content": "Processing failed, please try again later"},
                }
            )

    def _handle_card_action_webhook(self, body: dict) -> dict:
        """Card callback action (card.action.trigger) — Webhook mode.

        Returns a response dict directly as the HTTP response body.
        """
        try:
            event = body.get("event", {})
            action = event.get("action", {})
            value = action.get("value", {})
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    value = {"action": value}

            return self._dispatch_card_action(value or {})

        except Exception as e:
            logger.error(f"Feishu: card action webhook error: {e}", exc_info=True)
            return {
                "toast": {"type": "error", "content": "Processing failed, please try again later"},
            }

    def _dispatch_card_action(self, value: dict) -> dict:
        """Dispatch to the appropriate handler based on the button value's action field.

        Returns a Feishu card callback response dict (with optional toast / card fields).
        """
        action_type = value.get("action", "")

        if action_type == "expand_folder":
            return self._handle_expand_folder(value.get("path", ""))

        if action_type == "collapse_folder":
            return self._handle_collapse_folder(value)

        if action_type in (
            "security_allow",
            "security_deny",
            "security_sandbox",
            "security_allow_session",
            "security_allow_always",
        ):
            return self._handle_security_decision(value)

        logger.debug(f"Feishu: unknown card action: {action_type}")
        return {}

    def _handle_security_decision(self, value: dict) -> dict:
        """Handle security confirmation card button clicks."""
        action = value.get("action", "")
        confirm_id = value.get("confirm_id", "")
        decision_map = {
            "security_allow": "allow_once",
            "security_deny": "deny",
            "security_sandbox": "sandbox",
            "security_allow_session": "allow_session",
            "security_allow_always": "allow_always",
        }
        decision = decision_map.get(action, "deny")
        try:
            from openakita.core.policy import get_policy_engine

            get_policy_engine().resolve_ui_confirm(confirm_id, decision)
            labels = {
                "allow_once": "✅ Allowed",
                "deny": "❌ Denied",
                "sandbox": "🔒 Sandboxed",
                "allow_session": "✅ Session allowed",
                "allow_always": "✅ Always allowed",
            }
            return {"toast": {"type": "success", "content": labels.get(decision, decision)}}
        except Exception as e:
            logger.warning(f"Feishu: security decision failed: {e}")
            return {"toast": {"type": "error", "content": "Processing failed"}}

    def _handle_expand_folder(self, path: str) -> dict:
        """Read directory contents and return an updated card with a file tree and expand buttons."""
        if not path:
            return {"toast": {"type": "error", "content": "Empty path"}}

        norm = os.path.normpath(path)
        if ".." in norm.split(os.sep):
            return {"toast": {"type": "error", "content": "Path not allowed"}}

        if not os.path.isdir(norm):
            return {
                "toast": {"type": "warning", "content": f"Directory not found: {os.path.basename(norm)}"}
            }

        try:
            entries = os.listdir(norm)
        except PermissionError:
            return {"toast": {"type": "error", "content": "No permission to access this directory"}}
        except OSError as e:
            return {"toast": {"type": "error", "content": f"Read failed: {e}"}}

        card = self._build_folder_card(norm, entries)
        return {"card": {"type": "raw", "data": card}}

    def _handle_collapse_folder(self, value: dict) -> dict:
        """Collapse a directory: return a compact card with only the directory name and an expand button."""
        path = value.get("path", "")
        parent = value.get("parent", "")
        if not path:
            return {}

        folder_name = os.path.basename(path) or path
        elements = [
            {"tag": "markdown", "content": f"📁 **{folder_name}**"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"📂 Expand {folder_name}"},
                        "type": "default",
                        "value": {"action": "expand_folder", "path": path},
                    },
                ],
            },
        ]

        title = os.path.basename(parent) if parent else folder_name
        return {
            "card": {
                "type": "raw",
                "data": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": f"📁 {title}"},
                        "template": "blue",
                    },
                    "elements": elements,
                },
            },
        }

    @staticmethod
    def _build_folder_card(dir_path: str, entries: list[str]) -> dict:
        """Build a Feishu interactive card (JSON 1.0) containing a file list and subdirectory expand buttons."""
        folder_name = os.path.basename(dir_path) or dir_path

        dirs: list[str] = []
        files: list[str] = []
        for entry in sorted(entries):
            if entry.startswith("."):
                continue
            full = os.path.join(dir_path, entry)
            if os.path.isdir(full):
                dirs.append(entry)
            else:
                files.append(entry)

        _ICON = {
            "dir": "📁",
            "md": "📝",
            "txt": "📄",
            "pdf": "📕",
            "png": "🖼️",
            "jpg": "🖼️",
            "jpeg": "🖼️",
            "gif": "🖼️",
            "mp3": "🎵",
            "wav": "🎵",
            "mp4": "🎬",
            "py": "🐍",
            "js": "📜",
            "json": "📋",
            "csv": "📊",
        }

        md_lines: list[str] = []
        for d in dirs:
            md_lines.append(f"📁 **{d}/**")
        for f in files:
            ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
            icon = _ICON.get(ext, "📄")
            md_lines.append(f"{icon} {f}")

        elements: list[dict] = []

        if md_lines:
            MAX_DISPLAY = 50
            if len(md_lines) > MAX_DISPLAY:
                shown = md_lines[:MAX_DISPLAY]
                shown.append(f"\n*...{len(md_lines)} items total, showing first {MAX_DISPLAY}*")
                md_lines = shown
            elements.append({"tag": "markdown", "content": "\n".join(md_lines)})
        else:
            elements.append({"tag": "markdown", "content": "*(empty directory)*"})

        if dirs:
            MAX_BUTTONS_PER_ROW = 5
            for i in range(0, min(len(dirs), 20), MAX_BUTTONS_PER_ROW):
                chunk = dirs[i : i + MAX_BUTTONS_PER_ROW]
                actions = []
                for d in chunk:
                    actions.append(
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": f"📂 Expand {d}"},
                            "type": "default",
                            "value": {
                                "action": "expand_folder",
                                "path": os.path.join(dir_path, d),
                            },
                        }
                    )
                elements.append({"tag": "action", "actions": actions})

        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📁 Collapse"},
                        "type": "default",
                        "value": {
                            "action": "collapse_folder",
                            "path": dir_path,
                            "parent": str(Path(dir_path).parent),
                        },
                    },
                ],
            }
        )

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📁 {folder_name}"},
                "template": "blue",
            },
            "elements": elements,
        }

    def _buffer_event(self, chat_id: str, event: dict) -> None:
        """Buffer an event in a thread-safe way"""
        with self._events_lock:
            events = self._important_events.setdefault(chat_id, [])
            if len(events) >= self._MAX_EVENTS_PER_CHAT:
                events.pop(0)
            events.append(event)

    def get_pending_events(self, chat_id: str) -> list[dict]:
        """Pop and clear pending events for the given chat (thread-safe)"""
        with self._events_lock:
            return self._important_events.pop(chat_id, [])

    # ==================== Token / permission helpers ====================

    @staticmethod
    def _is_token_error(resp: Any) -> bool:
        """Determine whether an API response indicates a token/permission error"""
        if resp.success():
            return False
        msg = (getattr(resp, "msg", "") or "").lower()
        return any(
            kw in msg
            for kw in (
                "permission",
                "tenant_access_token",
                "app_access_token",
                "forbidden",
                "access denied",
                "scope",
            )
        )

    @staticmethod
    def _is_permission_error(msg: str) -> bool:
        """Determine whether an API response message indicates insufficient permission."""
        m = msg.lower()
        return any(
            kw in m for kw in ("permission", "forbidden", "access denied", "scope", "not allowed")
        )

    # ==================== CardKit streaming card API ====================

    async def _get_tenant_access_token(self) -> str:
        """Fetch the Feishu tenant_access_token (cached, used by the CardKit API)."""
        if self._tenant_token and time.time() < self._tenant_token_expires:
            return self._tenant_token
        async with self._tenant_token_lock:
            if self._tenant_token and time.time() < self._tenant_token_expires:
                return self._tenant_token
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.config.api_domain}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
                )
                data = resp.json()
            if data.get("code", -1) != 0 and "tenant_access_token" not in data:
                raise RuntimeError(f"Failed to get tenant_access_token: {data}")
            self._tenant_token = data["tenant_access_token"]
            self._tenant_token_expires = time.time() + data.get("expire", 7200) - 300
            return self._tenant_token

    async def _cardkit_api(self, method: str, path: str, body: dict | None = None) -> dict:
        """Call the Feishu CardKit REST API."""
        token = await self._get_tenant_access_token()
        import httpx

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.config.api_domain}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method.upper() == "POST":
                resp = await client.post(url, json=body, headers=headers)
            elif method.upper() == "PUT":
                resp = await client.put(url, json=body, headers=headers)
            elif method.upper() == "PATCH":
                resp = await client.patch(url, json=body, headers=headers)
            else:
                resp = await client.get(url, headers=headers)
        return resp.json()

    async def _create_cardkit_card(self, content: str) -> tuple[str, str]:
        """Create a CardKit streaming card and return (card_id, element_id)."""
        element_id = "streaming_content"
        card_json = json.dumps(
            {
                "schema": "2.0",
                "body": {
                    "direction": "vertical",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": content,
                            "text_size": "normal",
                            "element_id": element_id,
                        }
                    ],
                },
            }
        )
        result = await self._cardkit_api(
            "POST",
            "/open-apis/cardkit/v1/cards",
            body={
                "type": "card_json",
                "data": card_json,
                "settings": {"config": {"streaming_mode": True}},
            },
        )
        data = result.get("data", {})
        card_id = data.get("card_id", "")
        if not card_id:
            raise RuntimeError(f"CardKit create failed: {result}")
        return card_id, element_id

    async def _update_cardkit_element(self, card_id: str, element_id: str, content: str) -> None:
        """Update the content of a CardKit card element (no edit-count limit)."""
        await self._cardkit_api(
            "PUT",
            f"/open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content",
            body={"content": json.dumps({"tag": "markdown", "content": content})},
        )

    async def _finish_cardkit_card(self, card_id: str) -> None:
        """End the streaming state of a CardKit card."""
        await self._cardkit_api(
            "PATCH",
            f"/open-apis/cardkit/v1/cards/{card_id}",
            body={"settings": {"config": {"streaming_mode": False}}},
        )

    def _invalidate_token_cache(self) -> None:
        """Mark the cached tenant_access_token as expired, forcing the next request to refetch it.

        The lark-oapi SDK's ICache has no delete method, but set(key, "", 0) is equivalent:
        expire=0 < time.time(), so the next get will be treated as expired and return empty,
        triggering a fresh token request.
        """
        try:
            from lark_oapi.core.token.manager import TokenManager

            cache_key = f"self_tenant_token:{self.config.app_id}"
            TokenManager.cache.set(cache_key, "", 0)
            logger.info(f"Feishu: token cache invalidated ({cache_key})")
        except Exception as e:
            logger.debug(f"Feishu: failed to invalidate token cache: {e}")

    async def add_reaction(self, message_id: str, emoji_type: str = "Get") -> None:
        """Add an emoji reaction to a message (fire-and-forget)."""
        if not self._client:
            return
        try:
            request = (
                lark_oapi.api.im.v1.CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageReactionRequestBody.builder()
                    .reaction_type(
                        lark_oapi.api.im.v1.Emoji.builder().emoji_type(emoji_type).build()
                    )
                    .build()
                )
                .build()
            )
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message_reaction.create(request)
            )
        except Exception as e:
            logger.debug(f"Feishu: add_reaction failed (non-critical): {e}")

    # ==================== Session-level key helpers ====================

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        """Generate a session-level key for dicts like _thinking_cards / _last_user_msg / streaming"""
        return f"{chat_id}:{thread_id}" if thread_id else chat_id

    # ==================== Thinking status indicator ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send a "Thinking..." placeholder card (only on the first call; subsequent calls are skipped).

        The Gateway's _keep_typing is called every 4 seconds; only the first call generates a card.
        If the card already exists, returns immediately and does not clear streaming state
        (consistent with adapters such as dingtalk).
        """
        sk = self._make_session_key(chat_id, thread_id)
        if sk in self._typing_suppressed:
            return
        if sk in self._thinking_cards:
            return
        if not self._client:
            return
        # Only initialize streaming state when actually creating a new card
        self._streaming_finalized.discard(sk)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)
        self._typing_start_time[sk] = time.time()
        self._typing_status[sk] = "Processing"
        reply_to = self._last_user_msg.pop(sk, None) or thread_id
        card_msg_id = await self._send_thinking_card(chat_id, reply_to=reply_to, sk=sk)
        if card_msg_id:
            self._thinking_cards[sk] = card_msg_id

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Clean up any leftover "Thinking..." placeholder card (safety net).

        On the normal path, send_message / finalize_stream already consumes the card,
        and this method is a no-op. It only runs on abnormal paths or when _keep_typing
        rebuilt a card that was never consumed.
        """
        sk = self._make_session_key(chat_id, thread_id)
        self._typing_start_time.pop(sk, None)
        self._typing_status.pop(sk, None)
        self._typing_suppressed.discard(sk)
        ck = self._cardkit_cards.pop(sk, None)
        if ck:
            with contextlib.suppress(Exception):
                await self._finish_cardkit_card(ck[0])
        card_id = self._thinking_cards.pop(sk, None)
        if card_id:
            logger.debug(f"Feishu: clear_typing removing leftover card {card_id}")
            with contextlib.suppress(Exception):
                await self._delete_feishu_message(card_id)

    def _build_footer_note(self, sk: str, *, final: bool = False) -> dict | None:
        """Build the card footer note element (shows elapsed time and/or status)."""
        if not self._footer_elapsed and not self._footer_status:
            return None

        start = self._typing_start_time.get(sk)
        elapsed_s = (time.time() - start) if start else 0.0
        status = self._typing_status.get(sk, "")

        parts: list[str] = []
        if final:
            if self._footer_elapsed:
                parts.append(f"⏱ Done ({elapsed_s:.1f}s)")
            else:
                parts.append("✅ Done")
        else:
            if self._footer_elapsed and elapsed_s > 0:
                parts.append(f"⏱ {elapsed_s:.1f}s")
            if self._footer_status and status:
                parts.append(status)

        if not parts:
            return None

        return {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": " · ".join(parts)},
            ],
        }

    def _build_card_json(
        self,
        content: str,
        sk: str | None = None,
        *,
        final: bool = False,
    ) -> dict:
        """Build a Feishu card JSON 1.0 structure, including an optional footer note."""
        elements: list[dict] = [{"tag": "markdown", "content": content}]
        if sk:
            note = self._build_footer_note(sk, final=final)
            if note:
                elements.append(note)
        return {"config": {"wide_screen_mode": True}, "elements": elements}

    async def _send_thinking_card(
        self,
        chat_id: str,
        reply_to: str | None = None,
        sk: str | None = None,
    ) -> str | None:
        """Send a "Thinking..." interactive card and return its message_id.

        Prefers CardKit streaming (no edit-count limit);
        falls back to PatchMessage (20-30 edit limit) if the permission is unavailable.
        """
        # --- CardKit path ---
        if self._cardkit_available and sk:
            try:
                card_id, element_id = await self._create_cardkit_card("💭 **Thinking...**")
                card_content = json.dumps({"type": "card", "data": {"card_id": card_id}})
                if reply_to:
                    request = (
                        lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                        .message_id(reply_to)
                        .request_body(
                            lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                            .msg_type("interactive")
                            .content(card_content)
                            .build()
                        )
                        .build()
                    )
                    response = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: self._client.im.v1.message.reply(request)
                    )
                else:
                    request = (
                        lark_oapi.api.im.v1.CreateMessageRequest.builder()
                        .receive_id_type("chat_id")
                        .request_body(
                            lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                            .receive_id(chat_id)
                            .msg_type("interactive")
                            .content(card_content)
                            .build()
                        )
                        .build()
                    )
                    response = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: self._client.im.v1.message.create(request)
                    )
                if response.success():
                    mid = response.data.message_id if response.data else ""
                    self._record_bot_msg_id(mid)
                    self._cardkit_cards[sk] = (card_id, element_id)
                    logger.debug(f"Feishu: CardKit thinking card sent to {chat_id}")
                    return mid
                logger.debug(f"Feishu: CardKit card send failed: {response.msg}")
            except Exception as e:
                logger.info(f"Feishu: CardKit path failed, falling back to PatchMessage: {e}")

        # --- PatchMessage fallback path ---
        card = self._build_card_json("💭 **Thinking...**", sk)
        content = json.dumps(card)
        try:
            if reply_to:
                request = (
                    lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                    .message_id(reply_to)
                    .request_body(
                        lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.im.v1.message.reply(request)
                )
            else:
                request = (
                    lark_oapi.api.im.v1.CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("interactive")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.im.v1.message.create(request)
                )
            if response.success():
                logger.debug(f"Feishu: thinking card sent to {chat_id}")
                mid = response.data.message_id if response.data else ""
                self._record_bot_msg_id(mid)
                return mid
            logger.debug(f"Feishu: thinking card failed: {response.msg}")
        except Exception as e:
            logger.debug(f"Feishu: _send_thinking_card error: {e}")
        return None

    async def _patch_card_content(
        self,
        message_id: str,
        new_content: str,
        sk: str | None = None,
        *,
        final: bool = False,
    ) -> bool:
        """Update placeholder card content. Prefers CardKit element update (no count limit);
        falls back to im.v1.message.patch (20-30 edit limit).

        Note: cards created via CardKit (schemaV2) cannot be updated via PatchMessage (schemaV1),
        so CardKit cards do not fall back to PatchMessage on failure.
        """
        # --- CardKit path ---
        ck = self._cardkit_cards.get(sk) if sk else None
        if ck:
            card_id, element_id = ck
            try:
                await self._update_cardkit_element(card_id, element_id, new_content)
            except Exception as e:
                logger.warning(f"Feishu: CardKit element update failed: {e}")
                return False
            if final:
                try:
                    await self._finish_cardkit_card(card_id)
                except Exception as e:
                    logger.info(
                        f"Feishu: CardKit finish failed (content already updated): {e}"
                    )
            return True

        # --- PatchMessage fallback (only for schemaV1 cards not created via CardKit) ---
        card = self._build_card_json(new_content, sk, final=final)
        request = (
            lark_oapi.api.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark_oapi.api.im.v1.PatchMessageRequestBody.builder()
                .content(json.dumps(card))
                .build()
            )
            .build()
        )
        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.patch(request)
        )
        if response.success():
            logger.debug(f"Feishu: thinking card patched: {message_id}")
            return True
        logger.warning(f"Feishu: patch card failed ({message_id}): {response.msg}")
        return False

    async def _delete_feishu_message(self, message_id: str) -> None:
        """Delete a Feishu message (fallback when PATCH fails; errors are silently ignored)."""
        try:
            request = (
                lark_oapi.api.im.v1.DeleteMessageRequest.builder().message_id(message_id).build()
            )
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.delete(request)
            )
        except Exception as e:
            logger.debug(f"Feishu: delete message failed (non-critical): {e}")

    # ==================== Streaming card output ====================

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        """Check whether streaming output is currently enabled"""
        if not self._streaming_enabled:
            return False
        if is_group and not self._group_streaming:
            return False
        return True

    async def stream_thinking(
        self,
        chat_id: str,
        thinking_text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
        duration_ms: int = 0,
    ) -> None:
        """Receive thinking content, then PATCH it to the card (throttled) to show the thinking process.

        When duration_ms > 0, the thinking phase has ended and a flush is forced without throttling.
        """
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_thinking[sk] = thinking_text
        self._typing_status[sk] = "Deep thinking"
        if duration_ms:
            self._streaming_thinking_ms[sk] = duration_ms

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        now = time.time()
        is_final = duration_ms > 0
        if not is_final:
            last_t = self._streaming_last_patch.get(sk, 0.0)
            throttle_s = self._streaming_throttle_ms / 1000.0
            if now - last_t < throttle_s:
                return

        display = self._compose_thinking_display(sk)
        try:
            await self._patch_card_content(card_id, display, sk)
            self._streaming_last_patch[sk] = now
        except Exception as e:
            logger.info(f"Feishu: stream_thinking patch failed (non-fatal): {e}")

    async def stream_chain_text(
        self,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Append chain text (tool call descriptions, result summaries, etc.) to the streaming card."""
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_chain.setdefault(sk, []).append(text)
        self._typing_status[sk] = "Calling tool"

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        throttle_s = self._streaming_throttle_ms / 1000.0
        if now - last_t >= throttle_s:
            display = self._compose_thinking_display(sk)
            try:
                await self._patch_card_content(card_id, display, sk)
                self._streaming_last_patch[sk] = now
            except Exception as e:
                logger.info(f"Feishu: stream_chain_text patch failed (non-fatal): {e}")

    _THINKING_DISPLAY_MAX = 800

    def _compose_thinking_display(self, sk: str) -> str:
        """Build the card display content from the current thinking + chain + reply buffer"""
        thinking = self._streaming_thinking.get(sk, "")
        reply = self._streaming_buffers.get(sk, "")
        dur_ms = self._streaming_thinking_ms.get(sk, 0)
        chain_lines = self._streaming_chain.get(sk, [])

        parts: list[str] = []
        if thinking:
            dur_str = f" ({dur_ms / 1000:.1f}s)" if dur_ms else ""
            preview = thinking.strip()
            limit = self._THINKING_DISPLAY_MAX
            if len(preview) > limit:
                preview = "..." + preview[-limit:]
            parts.append(f"💭 **Thinking process**{dur_str}\n> {preview.replace(chr(10), chr(10) + '> ')}")

        if chain_lines:
            visible = chain_lines[-8:]
            parts.append("\n".join(visible))

        if reply:
            if parts:
                parts.append("---")
            parts.append(reply + " ▍")
        elif not thinking and not chain_lines:
            parts.append("Thinking...")

        return "\n".join(parts)

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Receive a streaming token, accumulate it, and throttle-PATCH to update the card.

        Silently ignored if no thinking card exists or streaming is disabled.
        """
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._typing_status[sk] = "Generating reply"

        buf = self._streaming_buffers.get(sk, "")
        buf += token
        self._streaming_buffers[sk] = buf

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        throttle_s = self._streaming_throttle_ms / 1000.0

        if now - last_t >= throttle_s:
            has_thinking = sk in self._streaming_thinking
            display_text = self._compose_thinking_display(sk) if has_thinking else (buf + " ▍")
            try:
                await self._patch_card_content(card_id, display_text, sk)
                self._streaming_last_patch[sk] = now
            except Exception as e:
                logger.info(f"Feishu: streaming patch failed (non-fatal): {e}")

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """End of stream: do a final PATCH with the complete text.

        Returns:
            True if PATCH succeeded (send_message should skip the duplicate send);
            False if it failed (send_message should take the normal send path).
        """
        sk = self._make_session_key(chat_id, thread_id)
        card_id = self._thinking_cards.get(sk)

        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)

        if not card_id:
            self._cardkit_cards.pop(sk, None)
            return False

        try:
            success = await self._patch_card_content(card_id, final_text, sk, final=True)
            if success:
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                self._cardkit_cards.pop(sk, None)
                self._typing_suppressed.add(sk)
                self._typing_start_time.pop(sk, None)
                self._typing_status.pop(sk, None)
                return True
        except Exception as e:
            logger.warning(f"Feishu: finalize_stream patch failed: {e}")

        # PATCH failed: delete the placeholder card so send_message takes the normal path
        with contextlib.suppress(Exception):
            await self._delete_feishu_message(card_id)
        self._thinking_cards.pop(sk, None)
        self._cardkit_cards.pop(sk, None)
        self._typing_suppressed.add(sk)
        self._typing_start_time.pop(sk, None)
        self._typing_status.pop(sk, None)
        return False

    # ── /feishu command helpers ─────────────────────────────────────────

    def get_status_info(self) -> dict:
        """Return adapter status dict for ``/feishu start``."""
        try:
            from openakita import __version__
        except Exception:
            __version__ = "unknown"
        return {
            "version": __version__,
            "app_id": self.config.app_id,
            "connected": self._client is not None,
            "streaming_enabled": self._streaming_enabled,
            "group_streaming": self._group_streaming,
            "group_response_mode": self._group_response_mode or "global",
        }

    def get_auth_url(self, redirect_uri: str = "") -> str:
        """Build Feishu OAuth2 user authorization URL.

        When *redirect_uri* is empty the parameter is omitted so that the
        Feishu platform automatically uses the redirect URI registered in the
        developer console, avoiding error 20029 (redirect URL mismatch).
        """
        base = f"{self.config.api_domain}/open-apis/authen/v1/authorize"
        url = f"{base}?app_id={self.config.app_id}&response_type=code"
        if redirect_uri:
            url += f"&redirect_uri={redirect_uri}"
        return url

    _STALE_MESSAGE_THRESHOLD = 120  # Redelivered messages older than this many seconds are considered stale

    async def _handle_message_async(self, msg_dict: dict, sender_dict: dict) -> None:
        """Asynchronously process a message (with dedup + stale-message guard + read receipt)"""
        try:
            msg_id = msg_dict.get("message_id")

            # Message deduplication (WebSocket reconnects may cause redeliveries)
            if msg_id:
                if msg_id in self._seen_message_ids:
                    logger.debug(f"Feishu: duplicate message ignored: {msg_id}")
                    return
                self._seen_message_ids[msg_id] = None
                while len(self._seen_message_ids) > self._seen_message_ids_max:
                    self._seen_message_ids.popitem(last=False)

            # Stale-message guard: after a restart the dedup dict is empty, and Feishu's WebSocket
            # may redeliver unacknowledged messages from before the disconnection. Detect via create_time and drop.
            create_time_ms = msg_dict.get("create_time")
            if create_time_ms:
                try:
                    age = time.time() - int(create_time_ms) / 1000
                    if age > self._STALE_MESSAGE_THRESHOLD:
                        logger.warning(
                            f"Feishu[{self.channel_name}]: stale message dropped "
                            f"(age={age:.0f}s > {self._STALE_MESSAGE_THRESHOLD}s): "
                            f"{msg_id}"
                        )
                        return
                except (ValueError, TypeError):
                    pass

            if msg_id:
                asyncio.create_task(self.add_reaction(msg_id))

            chat_id = msg_dict.get("chat_id")

            # Record the most recent user message ID so send_typing can target the reply (at session_key granularity)
            root_id = msg_dict.get("root_id")
            if chat_id and msg_id:
                sk = self._make_session_key(chat_id, root_id or None)
                self._last_user_msg[sk] = msg_id

            unified = await self._convert_message(msg_dict, sender_dict)
            self._log_message(unified)
            await self._emit_message(unified)
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)

    async def stop(self) -> None:
        """Stop the Feishu client, ensuring the old WebSocket connection is fully closed.

        Leaving the old connection open causes Feishu to randomly deliver messages between the
        old and new connections; messages sent to the old connection are silently dropped because
        _main_loop is already invalid.
        """
        self._running = False

        # 0) Cancel watchdog task
        if self._ws_watchdog_task is not None:
            self._ws_watchdog_task.cancel()
            self._ws_watchdog_task = None

        # 1) On the WS thread's loop, schedule task cancellation and then stop the loop.
        #    Cancelling tasks before stop lets _drain_loop_tasks in _run_ws_in_thread's finally
        #    block complete faster (most tasks will already be in cancelled state).
        ws_loop = self._ws_loop
        if ws_loop is not None:
            try:

                def _cancel_and_stop() -> None:
                    for task in asyncio.all_tasks(ws_loop):
                        task.cancel()
                    ws_loop.stop()

                ws_loop.call_soon_threadsafe(_cancel_and_stop)
            except Exception:
                # loop may already be closed
                with contextlib.suppress(Exception):
                    ws_loop.call_soon_threadsafe(ws_loop.stop)

        # 2) Wait for the WS thread to exit (5-second timeout)
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
        Handle Feishu event callbacks (Webhook mode)

        Used in HTTP server mode to receive events pushed by Feishu.

        Args:
            body: request body
            headers: request headers

        Returns:
            response body
        """
        # URL verification
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        # Verify signature
        if self.config.verification_token:
            token = body.get("token")
            if token != self.config.verification_token:
                logger.warning("Invalid verification token")
                return {"error": "invalid token"}

        # Handle event
        event_type = body.get("header", {}).get("event_type")
        event = body.get("event", {})

        if event_type == "im.message.receive_v1":
            asyncio.create_task(self._handle_message_event(event))
        elif event_type == "card.action.trigger":
            return self._handle_card_action_webhook(body)

        return {"success": True}

    async def _handle_message_event(self, event: dict) -> None:
        """Handle message event (Webhook mode, includes dedup + stale-message guard + read receipt)"""
        try:
            message = event.get("message", {})
            sender = event.get("sender", {})

            msg_id = message.get("message_id")
            if msg_id:
                if msg_id in self._seen_message_ids:
                    logger.debug(f"Feishu: duplicate message ignored: {msg_id}")
                    return
                self._seen_message_ids[msg_id] = None
                while len(self._seen_message_ids) > self._seen_message_ids_max:
                    self._seen_message_ids.popitem(last=False)

            create_time_ms = message.get("create_time")
            if create_time_ms:
                try:
                    age = time.time() - int(create_time_ms) / 1000
                    if age > self._STALE_MESSAGE_THRESHOLD:
                        logger.warning(
                            f"Feishu[{self.channel_name}]: stale message dropped "
                            f"(age={age:.0f}s): {msg_id}"
                        )
                        return
                except (ValueError, TypeError):
                    pass

            if msg_id:
                asyncio.create_task(self.add_reaction(msg_id))

            chat_id = message.get("chat_id")
            root_id = message.get("root_id")

            if chat_id and msg_id:
                sk = self._make_session_key(chat_id, root_id or None)
                self._last_user_msg[sk] = msg_id

            unified = await self._convert_message(message, sender)
            self._log_message(unified)
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message event: {e}")

    async def _convert_message(self, message: dict, sender: dict) -> UnifiedMessage:
        """Convert a Feishu message to the unified format"""
        content = MessageContent()

        msg_type = message.get("message_type")
        raw_content = message.get("content", "{}")
        if isinstance(raw_content, dict):
            msg_content = raw_content
        else:
            try:
                msg_content = json.loads(raw_content) if raw_content else {}
            except (json.JSONDecodeError, TypeError):
                msg_content = {}

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
            # Video message
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
            # Sticker
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
            # Rich text (also extract image/video MediaFile)
            msg_id = message.get("message_id", "")
            content.text = self._parse_post_content_with_media(
                msg_content,
                content,
                msg_id,
            )

        else:
            # Unknown type
            content.text = f"[Unsupported message type: {msg_type}]"

        # Determine chat type
        raw_chat_type = message.get("chat_type", "p2p")
        is_direct_message = raw_chat_type == "p2p"

        chat_type = raw_chat_type
        if chat_type == "p2p":
            chat_type = "private"
        elif chat_type == "group":
            chat_type = "group"

        # Detect @bot mention: check whether the mentions list contains the bot
        is_mentioned = False
        mentions = message.get("mentions") or []
        if mentions:
            bot_open_id = getattr(self, "_bot_open_id", None)
            if bot_open_id:
                for m in mentions:
                    m_id = m.get("id", {}) if isinstance(m, dict) else {}
                    if m_id.get("open_id") == bot_open_id:
                        is_mentioned = True
                        break
            else:
                # Fallback detection when _bot_open_id is missing:
                # collect candidate mentions after excluding the sender
                sender_open_id = sender.get("sender_id", {}).get("open_id", "")
                candidates = []
                for m in mentions:
                    m_id = m.get("id", {}) if isinstance(m, dict) else {}
                    m_open_id = m_id.get("open_id", "")
                    if m_open_id and m_open_id != sender_open_id:
                        candidates.append(m_open_id)
                if len(candidates) == 1:
                    # Only one non-sender mention -> highly likely the bot, safe to cache
                    is_mentioned = True
                    self._bot_open_id = candidates[0]
                    logger.info(
                        f"Feishu: auto-discovered bot open_id from mention: {candidates[0]}"
                    )
                elif candidates:
                    # Multiple non-sender mentions -> respond but do not cache to avoid false-positive cache
                    is_mentioned = True
                    logger.info(
                        f"Feishu: multiple non-sender mentions ({len(candidates)}), "
                        "responding without caching bot_open_id"
                    )
                else:
                    logger.warning(
                        "Feishu: _bot_open_id is None, mention detection fallback inconclusive"
                    )

        # Implicit mention: replying to a bot message counts as a mention (in groups users
        # replying to a bot message have no explicit @).
        # Only check parent_id (direct reply target), not root_id (topic root message),
        # to avoid false positives when replying to other users inside a topic whose root message is the bot's.
        if not is_mentioned and chat_type == "group":
            parent_id = message.get("parent_id")
            if parent_id and parent_id in self._bot_sent_msg_ids:
                is_mentioned = True
                logger.info(
                    f"Feishu: implicit mention detected (reply to bot message {parent_id[:20]})"
                )

        # Clean up @_user_N placeholders: replace with the actual name or remove
        if content.text and mentions:
            for m in mentions:
                key = m.get("key", "") if isinstance(m, dict) else ""
                name = m.get("name", "") if isinstance(m, dict) else ""
                if key and key in content.text:
                    content.text = content.text.replace(key, f"@{name}" if name else "")
            content.text = content.text.strip()

        # Detect @all -- dual detection strategy (key == "@_all" or key present but open_id empty)
        metadata: dict[str, Any] = {}
        if mentions:
            for m in mentions:
                m_dict = m if isinstance(m, dict) else {}
                key = m_dict.get("key", "")
                m_id = m_dict.get("id", {})
                open_id = m_id.get("open_id", "") if isinstance(m_id, dict) else ""
                if key == "@_all" or (key and not open_id):
                    chat_id = message.get("chat_id", "")
                    metadata["at_all"] = True
                    logger.info(f"Feishu: detected @all mention in chat {chat_id}: {m_dict}")
                    self._buffer_event(
                        chat_id,
                        {
                            "type": "at_all",
                            "chat_id": chat_id,
                            "message_id": message.get("message_id", ""),
                            "text": (content.text or "")[:200],
                        },
                    )
                    break

        sender_id = sender.get("sender_id", {})
        user_id = sender_id.get("user_id") or sender_id.get("open_id", "")

        metadata["is_group"] = chat_type == "group"
        metadata["sender_name"] = await self._resolve_user_name(sender_id.get("open_id", ""))
        if chat_type == "group":
            metadata["chat_name"] = await self._resolve_chat_name(message.get("chat_id", ""))

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.get("message_id", ""),
            user_id=f"fs_{user_id}",
            channel_user_id=user_id,
            chat_id=message.get("chat_id", ""),
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            thread_id=message.get("root_id"),
            reply_to=message.get("root_id"),
            raw={"message": message, "sender": sender},
            metadata=metadata,
        )

    async def _resolve_user_name(self, open_id: str) -> str:
        """Get the user's display name from cache or the Contact API; silently return empty string on failure."""
        if not open_id:
            return ""

        if open_id in self._user_name_cache:
            self._user_name_cache.move_to_end(open_id)
            return self._user_name_cache[open_id]

        if "get_user_info" not in self._capabilities:
            return ""

        try:
            info = await self.get_user_info(open_id)
            name = (info or {}).get("name", "") if info else ""
        except Exception:
            name = ""

        self._user_name_cache[open_id] = name
        while len(self._user_name_cache) > self._user_name_cache_max:
            self._user_name_cache.popitem(last=False)

        return name

    async def _resolve_chat_name(self, chat_id: str) -> str:
        """Get the chat name from cache or the im.v1.chat.get API; silently return empty string on failure."""
        if not chat_id:
            return ""

        if chat_id in self._chat_name_cache:
            self._chat_name_cache.move_to_end(chat_id)
            return self._chat_name_cache[chat_id]

        try:
            info = await self.get_chat_info(chat_id)
            name = (info or {}).get("name") or "" if info else ""
        except Exception:
            name = ""

        if name:
            self._chat_name_cache[chat_id] = name
            while len(self._chat_name_cache) > self._chat_name_cache_max:
                self._chat_name_cache.popitem(last=False)

        return name

    def _parse_post_content(self, post: dict) -> str:
        """Parse rich-text content (plain text, does not extract MediaFile).

        The content JSON format of a Feishu post message is:
        {"post": {"zh_cn": {"title": "...", "content": [[...]]}}}
        The language layer must be extracted first before parsing the actual content.
        """
        body = self._extract_post_body(post)
        if not isinstance(body, dict):
            return str(body) if body else ""
        return self._render_post_body(body)

    def _parse_post_content_with_media(
        self,
        post: dict,
        content: MessageContent,
        message_id: str = "",
    ) -> str:
        """Parse rich-text content while also extracting images/videos as MediaFile.

        Compared with _parse_post_content, img/media tags create MediaFile instances
        and append them to content.images / content.videos, ensuring multimodal data is not lost.
        """
        body = self._extract_post_body(post)
        if not isinstance(body, dict):
            return str(body) if body else ""
        return self._render_post_body(body, content=content, message_id=message_id)

    @staticmethod
    def _extract_post_body(post: dict) -> dict | str:
        """Extract the language-layer body (zh_cn / en_us / first available language) from the post JSON."""
        body = post
        if "post" in post:
            lang_map = post["post"]
            body = lang_map.get("zh_cn") or lang_map.get("en_us") or {}
            if not body and lang_map:
                body = next(iter(lang_map.values()), {})
        elif "title" not in post and "content" not in post:
            for v in post.values():
                if isinstance(v, dict) and ("title" in v or "content" in v):
                    body = v
                    break
        return body

    @staticmethod
    def _render_post_body(
        body: dict,
        content: MessageContent | None = None,
        message_id: str = "",
    ) -> str:
        """Render the post body as plain text, optionally also extracting media into content."""
        result: list[str] = []

        title = body.get("title", "")
        if title:
            result.append(title)

        for paragraph in body.get("content", []):
            line_parts: list[str] = []
            for item in paragraph:
                tag = item.get("tag", "")
                if tag == "text":
                    line_parts.append(item.get("text", ""))
                elif tag == "a":
                    line_parts.append(f"[{item.get('text', '')}]({item.get('href', '')})")
                elif tag == "at":
                    line_parts.append(f"@{item.get('user_name', item.get('user_id', ''))}")
                elif tag == "img":
                    image_key = item.get("image_key", "")
                    line_parts.append(f"[Image:{image_key}]" if image_key else "[Image]")
                    if image_key and content is not None:
                        media = MediaFile.create(
                            filename=f"{image_key}.png",
                            mime_type="image/png",
                            file_id=image_key,
                        )
                        media.extra["message_id"] = message_id
                        content.images.append(media)
                elif tag == "media":
                    file_key = item.get("file_key", "")
                    line_parts.append(f"[Video:{file_key}]")
                    if file_key and content is not None:
                        media = MediaFile.create(
                            filename=f"{file_key}.mp4",
                            mime_type="video/mp4",
                            file_id=file_key,
                        )
                        media.extra["message_id"] = message_id
                        content.videos.append(media)
                elif tag == "emotion":
                    line_parts.append(item.get("emoji_type", ""))
            if line_parts:
                result.append("".join(line_parts))

        return "\n".join(result)

    def _record_bot_msg_id(self, msg_id: str) -> None:
        """Record a bot-sent message_id for implicit mention detection in group replies."""
        if not msg_id:
            return
        self._bot_sent_msg_ids[msg_id] = None
        while len(self._bot_sent_msg_ids) > self._bot_sent_msg_ids_max:
            self._bot_sent_msg_ids.popitem(last=False)

    async def send_message(self, message: OutgoingMessage) -> str:
        """Send a message"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # ---- Thinking card handling: try to PATCH the placeholder card into the final reply ----
        sk = self._make_session_key(message.chat_id, message.thread_id)

        # Interim messages (ask_user questions, reminders, feedback, etc.) do not participate in card state management;
        # the thinking card is reserved for the final reply.
        _is_interim = message.metadata.get("_interim", False)

        if not _is_interim:
            # If streaming has already been finalized, skip the duplicate PATCH
            if sk in self._streaming_finalized:
                card_id = self._thinking_cards.get(sk)
                self._streaming_finalized.discard(sk)
                self._thinking_cards.pop(sk, None)
                self._typing_suppressed.add(sk)
                self._streaming_buffers.pop(sk, None)
                self._streaming_last_patch.pop(sk, None)
                self._typing_start_time.pop(sk, None)
                self._typing_status.pop(sk, None)
                return card_id or sk
            if sk not in self._streaming_buffers:
                text = message.content.text or ""
                if text and not message.content.has_media:
                    thinking_card_id = self._thinking_cards.pop(sk, None)
                    if thinking_card_id:
                        self._typing_suppressed.add(sk)
                        try:
                            if await self._patch_card_content(
                                thinking_card_id, text, sk, final=True
                            ):
                                self._cardkit_cards.pop(sk, None)
                                self._typing_start_time.pop(sk, None)
                                self._typing_status.pop(sk, None)
                                return thinking_card_id
                        except Exception as e:
                            logger.warning(f"Feishu: patch thinking card failed: {e}")
                        self._cardkit_cards.pop(sk, None)
                        with contextlib.suppress(Exception):
                            await self._delete_feishu_message(thinking_card_id)
                        self._typing_start_time.pop(sk, None)
                        self._typing_status.pop(sk, None)

        reply_target = message.reply_to or message.thread_id

        # Voice / file / video: send all items in a loop (first item carries caption and reply_to)
        if message.content.voices:
            first_msg_id = None
            for i, voice in enumerate(message.content.voices):
                if voice.local_path:
                    try:
                        mid = await self.send_voice(
                            message.chat_id,
                            voice.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send voice [{i}] failed: {e}")
            return first_msg_id or ""
        if message.content.files:
            first_msg_id = None
            for i, file in enumerate(message.content.files):
                if file.local_path:
                    try:
                        mid = await self.send_file(
                            message.chat_id,
                            file.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send file [{i}] failed: {e}")
            return first_msg_id or ""
        if message.content.videos:
            first_msg_id = None
            for i, video in enumerate(message.content.videos):
                if video.local_path:
                    try:
                        mid = await self.send_file(
                            message.chat_id,
                            video.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send video [{i}] failed: {e}")
            return first_msg_id or ""

        # Build message content
        _pending_caption = None
        if message.content.text and not message.content.has_media:
            text = message.content.text
            # Detect whether it contains markdown formatting
            if self._contains_markdown(text):
                # Use an interactive card to support markdown rendering
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
                _pending_caption = message.content.text or None
            else:
                msg_type = "text"
                content = json.dumps({"text": message.content.text or "[Image]"})
                _pending_caption = None
        else:
            msg_type = "text"
            content = json.dumps({"text": message.content.text or ""})
            _pending_caption = None

        # Topic reply: when reply_to or thread_id is present, use ReplyMessageRequest to reply within the same topic
        if reply_target:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_target)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
            if not response.success():
                raise RuntimeError(f"Failed to reply message: {response.msg}")
            if _pending_caption:
                await self._send_text(message.chat_id, _pending_caption, reply_to=reply_target)
            for extra_img in message.content.images[1:]:
                if extra_img.local_path:
                    try:
                        await self.send_image(
                            message.chat_id, extra_img.local_path, reply_to=reply_target
                        )
                    except Exception as e:
                        logger.warning(f"Feishu: send extra image failed: {e}")
            mid = response.data.message_id if response.data else ""
            self._record_bot_msg_id(mid)
            return mid

        # Normal send (executes synchronous call in a thread pool)
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

        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send message: {response.msg}")

        if _pending_caption:
            await self._send_text(message.chat_id, _pending_caption, reply_to=reply_target)

        for extra_img in message.content.images[1:]:
            if extra_img.local_path:
                try:
                    await self.send_image(message.chat_id, extra_img.local_path)
                except Exception as e:
                    logger.warning(f"Feishu: send extra image failed: {e}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    # ==================== IM query helper methods ====================

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """Get chat info (name, member count, owner, etc.)"""
        if not self._client:
            return None
        try:
            import lark_oapi.api.im.v1 as im_v1

            req = im_v1.GetChatRequest.builder().chat_id(chat_id).build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_chat_info failed: {resp.msg}")
                return None
            chat = resp.data.chat
            return {
                "id": chat_id,
                "name": getattr(chat, "name", ""),
                "type": "group",
                "description": getattr(chat, "description", ""),
                "owner_id": getattr(chat, "owner_id", ""),
                "members_count": getattr(chat, "user_count", 0),
            }
        except Exception as e:
            logger.debug(f"Feishu get_chat_info error: {e}")
            return None

    async def get_user_info(self, user_id: str) -> dict | None:
        """Get user info (name, avatar, etc.)"""
        if not self._client:
            return None
        try:
            import lark_oapi.api.contact.v3 as contact_v3

            req = (
                contact_v3.GetUserRequest.builder().user_id(user_id).user_id_type("open_id").build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.contact.v3.user.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_user_info failed: {resp.msg}")
                return None
            user = resp.data.user
            avatar = getattr(user, "avatar", None)
            avatar_url = ""
            if avatar and isinstance(avatar, dict):
                avatar_url = avatar.get("avatar_origin", "")
            elif avatar:
                avatar_url = getattr(avatar, "avatar_origin", "")
            return {
                "id": user_id,
                "name": getattr(user, "name", ""),
                "avatar_url": avatar_url,
            }
        except Exception as e:
            logger.debug(f"Feishu get_user_info error: {e}")
            return None

    async def get_chat_members(self, chat_id: str) -> list[dict]:
        """Get the list of chat members"""
        if not self._client:
            return []
        try:
            import lark_oapi.api.im.v1 as im_v1

            req = (
                im_v1.GetChatMembersRequest.builder()
                .chat_id(chat_id)
                .member_id_type("open_id")
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat_members.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_chat_members failed: {resp.msg}")
                return []
            return [
                {"id": getattr(m, "member_id", ""), "name": getattr(m, "name", "")}
                for m in (resp.data.items or [])
            ]
        except Exception as e:
            logger.debug(f"Feishu get_chat_members error: {e}")
            return []

    async def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        """Get recent chat messages (second layer of the topic tiering strategy)"""
        if not self._client:
            return []
        try:
            import lark_oapi.api.im.v1 as im_v1

            req = (
                im_v1.ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id(chat_id)
                .page_size(limit)
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.list(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_recent_messages failed: {resp.msg}")
                return []
            return [
                {
                    "id": getattr(m, "message_id", ""),
                    "sender": getattr(m, "sender", {}),
                    "content": (
                        lambda b: (
                            b.get("content", "")
                            if isinstance(b, dict)
                            else getattr(b, "content", "")
                            if b
                            else ""
                        )
                    )(getattr(m, "body", None)),
                    "type": getattr(m, "msg_type", ""),
                    "time": getattr(m, "create_time", ""),
                }
                for m in (resp.data.items or [])
            ]
        except Exception as e:
            logger.debug(f"Feishu get_recent_messages error: {e}")
            return []

    def _contains_markdown(self, text: str) -> bool:
        """Detect whether the text contains markdown formatting"""
        import re

        # Common markdown syntax patterns
        patterns = [
            r"\*\*[^*]+\*\*",  # **bold**
            r"__[^_]+__",  # __bold__
            r"(?<!\*)\*[^*]+\*(?!\*)",  # *italic* (not **)
            r"(?<!_)_[^_]+_(?!_)",  # _italic_ (not __)
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
        """Upload an image (with automatic retry on token expiration).

        The lark-oapi SDK does not refresh the token on 401 / permission errors,
        so on a first failure judged to be a token/permission error we proactively
        invalidate the cache and retry once.
        Each retry must reopen the file because the previous request consumed the file handle.
        """
        for attempt in range(2):
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
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda _r=request: self._client.im.v1.image.create(_r)
                )

            if response.success():
                return response.data.image_key if response.data else ""

            if attempt == 0 and self._is_token_error(response):
                logger.warning(
                    f"Feishu: image upload permission error ({response.msg}), "
                    "invalidating token cache and retrying..."
                )
                self._invalidate_token_cache()
                await asyncio.sleep(1)
                continue

            raise RuntimeError(f"Failed to upload image: {response.msg}")

    async def download_media(self, media: MediaFile) -> Path:
        """Download a media file"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.file_id:
            raise ValueError("Media has no file_id")

        # Pick the download API based on media type
        message_id = media.extra.get("message_id", "")
        if media.is_image and not message_id:
            # Used only for downloading bot-uploaded images (no message_id)
            request = lark_oapi.api.im.v1.GetImageRequest.builder().image_key(media.file_id).build()

            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.get(request)
            )
        else:
            # Images/audio/video/files from user messages all go through the MessageResource API
            resource_type = "image" if media.is_image else "file"
            request = (
                lark_oapi.api.im.v1.GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(media.file_id)
                .type(resource_type)
                .build()
            )

            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message_resource.get(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to download media: {response.msg}")

        if not getattr(response, "file", None):
            raise RuntimeError(f"Download succeeded but response.file is empty for {media.file_id}")

        # Save file (filter Windows-illegal characters like : * ? )
        from openakita.channels.base import sanitize_filename

        safe_name = sanitize_filename(Path(media.filename).name or "download")
        local_path = self.media_dir / safe_name
        with open(local_path, "wb") as f:
            f.write(response.file.read())

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """Upload a media file"""
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

    async def send_card(
        self,
        chat_id: str,
        card: dict,
        *,
        reply_to: str | None = None,
    ) -> str:
        """
        Send a card message.

        Args:
            chat_id: chat ID
            card: card content (Feishu card JSON)
            reply_to: target message ID to reply to (used for in-topic replies)

        Returns:
            message ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        content = json.dumps(card)

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send card: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def reply_message(self, message_id: str, text: str, msg_type: str = "text") -> str:
        """
        Reply to a message.

        Args:
            message_id: ID of the message to reply to
            text: reply content
            msg_type: message type

        Returns:
            new message ID
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

        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.reply(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to reply message: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def send_photo(
        self,
        chat_id: str,
        photo_path: str,
        caption: str | None = None,
        *,
        reply_to: str | None = None,
    ) -> str:
        """
        Send an image.

        Args:
            chat_id: chat ID
            photo_path: image file path
            caption: image caption
            reply_to: target message ID to reply to (used for in-topic replies)

        Returns:
            message ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        image_key = await self._upload_image(photo_path)
        content = json.dumps({"image_key": image_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send photo: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent photo to {chat_id}: {photo_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        *,
        reply_to: str | None = None,
    ) -> str:
        """
        Send a file.

        Args:
            chat_id: chat ID
            file_path: file path
            caption: file caption
            reply_to: target message ID to reply to (used for in-topic replies)

        Returns:
            message ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        file_key = await self._upload_file(file_path)
        content = json.dumps({"file_key": file_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("file")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("file")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send file: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent file to {chat_id}: {file_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
        *,
        reply_to: str | None = None,
    ) -> str:
        """
        Send a voice message.

        Args:
            chat_id: chat ID
            voice_path: voice file path
            caption: voice caption
            reply_to: target message ID to reply to (used for in-topic replies)

        Returns:
            message ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        file_key = await self._upload_file(voice_path)
        content = json.dumps({"file_key": file_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("audio")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("audio")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send voice: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent voice to {chat_id}: {voice_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def _send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> str:
        """Send a plain-text message"""
        content = json.dumps({"text": text})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send text: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def _upload_file(self, path: str) -> str:
        """Upload a file to Feishu (with automatic retry on token expiration)"""
        file_name = Path(path).name

        for attempt in range(2):
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
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda _r=request: self._client.im.v1.file.create(_r)
                )

            if response.success():
                return response.data.file_key if response.data else ""

            if attempt == 0 and self._is_token_error(response):
                logger.warning(
                    f"Feishu: file upload permission error ({response.msg}), "
                    "invalidating token cache and retrying..."
                )
                self._invalidate_token_cache()
                await asyncio.sleep(1)
                continue

            raise RuntimeError(f"Failed to upload file: {response.msg}")

    def build_simple_card(
        self,
        title: str,
        content: str,
        buttons: list[dict] | None = None,
    ) -> dict:
        """Build a simple card.

        Args:
            title: card title
            content: Markdown content
            buttons: button list; each item supports two formats:
                - ``{"text": "button label", "value": "string callback value"}``
                - ``{"text": "button label", "value": {"action": "xxx", ...}}``
                  When passing a dict, the whole dict is used as the button value,
                  which is convenient for card callback dispatch.

        Returns:
            Feishu card JSON (1.0 structure)
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
                raw_value = btn.get("value", btn["text"])
                btn_value = raw_value if isinstance(raw_value, dict) else {"action": raw_value}
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": btn["text"]},
                        "type": btn.get("type", "primary"),
                        "value": btn_value,
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
