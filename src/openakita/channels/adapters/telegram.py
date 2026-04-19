"""
Telegram adapter

Built on the python-telegram-bot library:
- Webhook / Long Polling modes
- Text/image/voice/file send and receive
- Markdown format support
- Pairing verification (prevents unauthorized access)
- Automatic proxy detection (supports config, environment variables, Windows system proxy)
"""

import asyncio
import contextlib
import html as _html
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from datetime import datetime
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

# Lazy import of telegram library
telegram = None
Application = None
Update = None
ContextTypes = None


def _import_telegram():
    """Lazy import of the telegram library"""
    global telegram, Application, Update, ContextTypes
    if telegram is None:
        try:
            import telegram as tg
            from telegram import Update as Upd
            from telegram.ext import Application as App
            from telegram.ext import ContextTypes as TelegramContextTypes

            telegram = tg
            Application = App
            Update = Upd
            ContextTypes = TelegramContextTypes
        except ImportError:
            raise ImportError(
                "python-telegram-bot not installed. Run: pip install python-telegram-bot"
            )


def _get_proxy(config_proxy: str | None = None) -> str | None:
    """
    Get proxy settings (from config file or environment variables only)

    Args:
        config_proxy: Proxy address specified in the config file

    Returns:
        Proxy URL or None
    """
    # 1. Prefer the proxy from the config file
    if config_proxy:
        logger.info(f"[Telegram] Using proxy from config: {config_proxy}")
        return config_proxy

    # 2. Check environment variables (only used when explicitly set by the user)
    for env_var in ["TELEGRAM_PROXY", "ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"]:
        proxy = os.environ.get(env_var)
        if proxy:
            logger.info(f"[Telegram] Using proxy from environment variable {env_var}: {proxy}")
            return proxy

    # Do not auto-detect system proxy; supports TUN passthrough mode
    return None


class TelegramPairingManager:
    """
    Telegram pairing manager

    Manages paired users/chats to prevent unauthorized access
    """

    def __init__(self, data_dir: Path, pairing_code: str | None = None):
        """
        Args:
            data_dir: Data storage directory
            pairing_code: Pairing code (auto-generated if empty)
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.paired_file = self.data_dir / "paired_users.json"
        self.code_file = self.data_dir / "pairing_code.txt"

        # Load paired users
        self.paired_users: dict = self._load_paired_users()

        # Set pairing code
        if pairing_code:
            self.pairing_code = pairing_code
            # Write to file to keep file contents in sync with the active pairing code
            try:
                self.code_file.write_text(pairing_code, encoding="utf-8")
                logger.info(f"Pairing code from config saved to {self.code_file}")
            except Exception as e:
                logger.error(f"Failed to save pairing code to file: {e}")
        else:
            self.pairing_code = self._load_or_generate_code()

        # Users waiting for pairing {chat_id: timestamp}
        self._pending_pairing: dict[str, float] = {}

        logger.info(f"TelegramPairingManager initialized, {len(self.paired_users)} paired users")
        logger.info(f"Pairing code file: {self.code_file}")

    def _load_paired_users(self) -> dict:
        """Load paired users"""
        if self.paired_file.exists():
            try:
                with open(self.paired_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load paired users: {e}")
        return {}

    def _save_paired_users(self) -> None:
        """Save paired users"""
        try:
            with open(self.paired_file, "w", encoding="utf-8") as f:
                json.dump(self.paired_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paired users: {e}")

    def _load_or_generate_code(self) -> str:
        """Load or generate a pairing code"""
        if self.code_file.exists():
            try:
                code = self.code_file.read_text(encoding="utf-8").strip()
                if code:
                    return code
            except Exception:
                pass

        # Generate a new pairing code (6 digits)
        code = str(secrets.randbelow(900000) + 100000)

        try:
            self.code_file.write_text(code, encoding="utf-8")
            logger.info(f"Generated new pairing code: {code}")
        except Exception as e:
            logger.error(f"Failed to save pairing code: {e}")

        return code

    def regenerate_code(self) -> str:
        """Regenerate the pairing code"""
        code = str(secrets.randbelow(900000) + 100000)

        try:
            self.code_file.write_text(code, encoding="utf-8")
            self.pairing_code = code
            logger.info(f"Regenerated pairing code: {code}")
        except Exception as e:
            logger.error(f"Failed to save pairing code: {e}")

        return code

    def is_paired(self, chat_id: str) -> bool:
        """Check whether the chat is paired"""
        return chat_id in self.paired_users

    def start_pairing(self, chat_id: str) -> None:
        """Start the pairing flow"""
        import time

        now = time.time()
        stale = [k for k, ts in self._pending_pairing.items() if now - ts > 300]
        for k in stale:
            del self._pending_pairing[k]
        self._pending_pairing[chat_id] = now

    def is_pending_pairing(self, chat_id: str) -> bool:
        """Check whether pairing is pending"""
        import time

        if chat_id not in self._pending_pairing:
            return False

        # 5-minute timeout
        if time.time() - self._pending_pairing[chat_id] > 300:
            del self._pending_pairing[chat_id]
            return False

        return True

    def verify_code(self, chat_id: str, code: str, user_info: dict = None) -> bool:
        """
        Verify a pairing code

        Args:
            chat_id: Chat ID
            code: Pairing code entered by the user
            user_info: User information (for recording)

        Returns:
            Whether pairing succeeded
        """
        if code.strip() == self.pairing_code:
            # Pairing successful
            self.paired_users[chat_id] = {
                "paired_at": datetime.now().isoformat(),
                "user_info": user_info or {},
            }
            self._save_paired_users()

            # Clear pending state
            if chat_id in self._pending_pairing:
                del self._pending_pairing[chat_id]

            logger.info(f"Chat {chat_id} paired successfully")
            return True

        return False

    def unpair(self, chat_id: str) -> bool:
        """Unpair"""
        if chat_id in self.paired_users:
            del self.paired_users[chat_id]
            self._save_paired_users()
            logger.info(f"Chat {chat_id} unpaired")
            return True
        return False

    def get_paired_list(self) -> list[dict]:
        """Get the list of paired users"""
        result = []
        for chat_id, info in self.paired_users.items():
            result.append(
                {
                    "chat_id": chat_id,
                    **info,
                }
            )
        return result


class TelegramAdapter(ChannelAdapter):
    """
    Telegram adapter

    Supports:
    - Long Polling mode
    - Webhook mode (requires a public URL)
    - Text/image/voice/file send and receive
    - Markdown format
    - Pairing verification (prevents unauthorized access)
    """

    channel_name = "telegram"

    capabilities = {
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": True,
        "edit_message": True,
        "get_chat_info": True,
        "get_user_info": False,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": True,
    }

    def __init__(
        self,
        bot_token: str,
        webhook_url: str | None = None,
        media_dir: Path | None = None,
        pairing_code: str | None = None,
        require_pairing: bool = True,
        proxy: str | None = None,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
        footer_elapsed: bool | None = None,
        footer_status: bool | None = None,
    ):
        """
        Args:
            bot_token: Telegram Bot Token
            webhook_url: Webhook URL (optional; uses Long Polling if not provided)
            media_dir: Directory for storing media files
            pairing_code: Pairing code (optional; auto-generated if not provided)
            require_pairing: Whether pairing verification is required (default True)
            proxy: Proxy address (optional; auto-detected if not provided)
            channel_name: Channel name (used to distinguish instances when running multiple bots)
            bot_id: Unique identifier for the bot instance
            agent_profile_id: Bound agent profile ID
            footer_elapsed: Show processing time on thinking cards (default True, can be controlled via TELEGRAM_FOOTER_ELAPSED env var)
            footer_status: Show processing status on thinking cards (default True, can be controlled via TELEGRAM_FOOTER_STATUS env var)
        """
        super().__init__(
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id
        )

        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/telegram")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # Proxy settings (from config or environment variables only; system proxy is not auto-detected)
        self.proxy = _get_proxy(proxy)

        self._app: Any | None = None
        self._bot: Any | None = None
        self._watchdog_task: asyncio.Task | None = None

        # Pairing management
        self.require_pairing = require_pairing
        self.pairing_manager = TelegramPairingManager(
            data_dir=Path("data/telegram/pairing"),
            pairing_code=pairing_code,
        )

        # Webhook secret_token (used to verify that requests come from Telegram)
        import secrets

        self._webhook_secret = secrets.token_urlsafe(32)

        # Message deduplication (prevents duplicate processing from webhook retries or network jitter)
        self._seen_update_ids: OrderedDict[int, None] = OrderedDict()
        self._seen_update_ids_max = 500

        # Thinking placeholder messages: session_key -> (chat_id_int, message_id)
        self._thinking_cards: dict[str, tuple[int, int]] = {}
        # Streaming output state
        self._streaming_buffers: dict[str, str] = {}
        self._streaming_thinking: dict[str, str] = {}
        self._streaming_thinking_ms: dict[str, int] = {}
        self._streaming_chain: dict[str, list[str]] = {}
        self._streaming_last_patch: dict[str, float] = {}
        self._streaming_finalized: set[str] = set()
        self._streaming_throttle_ms: int = 1500

        # Footer config (elapsed time / status display)
        self._typing_start_time: dict[str, float] = {}
        self._typing_status: dict[str, str] = {}
        self._footer_elapsed: bool = (
            footer_elapsed
            if footer_elapsed is not None
            else (os.environ.get("TELEGRAM_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes"))
        )
        self._footer_status: bool = (
            footer_status
            if footer_status is not None
            else (os.environ.get("TELEGRAM_FOOTER_STATUS", "true").lower() in ("true", "1", "yes"))
        )

    async def start(self) -> None:
        """Start the Telegram bot"""
        _import_telegram()

        from telegram.request import HTTPXRequest

        # Configure a longer timeout (the default 5 seconds is too short)
        # Use a proxy automatically if one is detected
        request_kwargs = {
            "connection_pool_size": 8,
            "connect_timeout": 30.0,
            "read_timeout": 30.0,
            "write_timeout": 30.0,
            "pool_timeout": 30.0,
        }

        get_updates_kwargs = {
            "connection_pool_size": 4,
            "connect_timeout": 30.0,
            "read_timeout": 60.0,
            "write_timeout": 30.0,
            "pool_timeout": 10.0,
        }

        if self.proxy:
            request_kwargs["proxy"] = self.proxy
            get_updates_kwargs["proxy"] = self.proxy
            logger.info(f"[Telegram] HTTPXRequest configured with proxy: {self.proxy}")

        request = HTTPXRequest(**request_kwargs)

        # Create Application
        self._app = (
            Application.builder()
            .token(self.bot_token)
            .request(request)
            .get_updates_request(HTTPXRequest(**get_updates_kwargs))
            .build()
        )
        self._bot = self._app.bot

        # Register error handler (catches all exceptions during update processing to prevent silent losses)
        self._app.add_error_handler(self._on_error)

        # Register command handlers (built-in Telegram commands, handled with priority)
        from telegram.ext import CommandHandler, MessageHandler, filters

        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("unpair", self._handle_unpair))
        self._app.add_handler(CommandHandler("status", self._handle_status))

        # Register message handler (handles all messages, including system commands like /model)
        # Note: registered CommandHandlers match first; this handles other commands and regular messages
        self._app.add_handler(
            MessageHandler(
                filters.ALL,  # Accept all messages; let the Gateway handle system commands
                self._handle_message,
            )
        )

        # Bot API 8.0+ reaction events (reserved; currently only logged)
        try:
            from telegram.ext import CallbackQueryHandler, MessageReactionHandler

            self._app.add_handler(MessageReactionHandler(self._handle_reaction))
            self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))
        except (ImportError, AttributeError):
            pass

        # Initialize (connect to Telegram API)
        try:
            await self._app.initialize()
        except Exception as e:
            err_str = str(e)
            err_type = type(e).__name__
            if "ConnectError" in err_type or "ConnectError" in err_str:
                proxy_hint = (
                    "Unable to connect to the Telegram API (api.telegram.org). "
                    "In regions where Telegram is restricted, a proxy is required to use the Telegram Bot.\n"
                    "Configuration options (choose one):\n"
                    "  1. Add a proxy field in the IM channel config, e.g. socks5://127.0.0.1:7890\n"
                    "  2. Set the environment variable TELEGRAM_PROXY=socks5://127.0.0.1:7890\n"
                    "  3. Use a proxy tool that supports TUN mode (e.g. Clash TUN)"
                )
                logger.error(f"[Telegram] {proxy_hint}")
                raise ConnectionError(proxy_hint) from e
            if "InvalidToken" in err_type or "Not Found" in err_str or "Unauthorized" in err_str:
                raise ConnectionError(
                    "Telegram Bot Token is invalid or expired. Please check the Token in @BotFather."
                ) from e
            raise

        # Automatically register the bot command menu (Telegram's / command hints)
        try:
            from telegram import BotCommand

            bot_commands = [
                BotCommand("start", "Start / pairing verification"),
                BotCommand("status", "Check pairing status"),
                BotCommand("unpair", "Unpair this chat"),
                BotCommand("model", "Show current model"),
                BotCommand("switch", "Temporarily switch model"),
                BotCommand("priority", "Adjust model priority"),
                BotCommand("restore", "Restore default model"),
                BotCommand("thinking", "Deep thinking mode (on/off/auto)"),
                BotCommand("thinking_depth", "Thinking depth (low/medium/high)"),
                BotCommand("chain", "Reasoning chain push (on/off)"),
                BotCommand("cancel", "Cancel current operation"),
                BotCommand("restart", "Restart the service"),
                BotCommand("cancel_restart", "Cancel restart"),
            ]
            await self._bot.set_my_commands(bot_commands)
            logger.info(f"[Telegram] Registered {len(bot_commands)} bot commands to the menu")
        except Exception as e:
            logger.warning(f"[Telegram] Failed to register command menu (does not affect usage): {e}")

        # Start
        if self.webhook_url:
            # Webhook mode
            await self._app.start()
            await self._bot.set_webhook(
                self.webhook_url,
                secret_token=self._webhook_secret,
                allowed_updates=["message", "edited_message", "callback_query", "message_reaction"],
            )
            logger.info(f"Telegram bot started with webhook: {self.webhook_url}")
        else:
            # Long Polling mode - use updater.start_polling
            # Clear any leftover webhook/polling connections first to avoid Conflict errors
            try:
                await self._bot.delete_webhook(drop_pending_updates=True)
                logger.info("Cleared previous webhook/polling connections before starting")
            except Exception as e:
                logger.warning(f"Failed to delete webhook before polling: {e}")

            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message", "callback_query", "message_reaction"],
                error_callback=self._on_polling_error,
            )
            logger.info("Telegram bot started with long polling")

        self._running = True

        # Start polling health-check watchdog
        if not self.webhook_url:
            self._watchdog_task = asyncio.create_task(self._polling_watchdog())

        # Print pairing info (using logger instead of print to avoid GBK encoding issues)
        if self.require_pairing:
            paired_count = len(self.pairing_manager.paired_users)
            logger.info("=" * 50)
            logger.info("[Telegram] Pairing verification enabled")
            logger.info(f"  Paired users: {paired_count}")
            logger.info(f"  Pairing code: {self.pairing_manager.pairing_code}")
            logger.info(f"  Pairing code file: {self.pairing_manager.code_file}")
            logger.info("=" * 50)

    async def stop(self) -> None:
        """Stop the Telegram bot"""
        self._running = False

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None

        if self._app:
            # In webhook mode, delete the webhook first
            if self.webhook_url and self._bot:
                with contextlib.suppress(Exception):
                    await self._bot.delete_webhook()

            # Stop the updater first
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            # Then stop the application
            await self._app.stop()
            await self._app.shutdown()

        logger.info("Telegram bot stopped")

    # ==================== Error Handling and Health Monitoring ====================

    async def _on_error(self, update: Any, context: Any) -> None:
        """Handle exceptions during update processing to prevent silent message loss"""
        logger.error(
            f"[Telegram] Error handling update: {context.error}",
            exc_info=context.error,
        )

    def _on_polling_error(self, error: Exception) -> None:
        """Handle polling network errors (disconnects, timeouts, etc.); the library will auto-retry.

        Note: python-telegram-bot requires error_callback to be a synchronous function, not a coroutine.
        """
        logger.warning(f"[Telegram] Polling network error (will auto-retry): {error}")

    async def _polling_watchdog(self) -> None:
        """Monitor whether polling is alive; automatically restart if it stops"""
        await asyncio.sleep(60)
        while self._running:
            await asyncio.sleep(120)
            if not self._app or not self._app.updater:
                continue
            if not self._app.updater.running:
                logger.warning("[Telegram] Polling stopped unexpectedly, restarting...")
                try:
                    await self._app.updater.start_polling(
                        drop_pending_updates=False,
                        allowed_updates=[
                            "message",
                            "edited_message",
                            "callback_query",
                            "message_reaction",
                        ],
                        error_callback=self._on_polling_error,
                    )
                    logger.info("[Telegram] Polling restarted successfully")
                except Exception as e:
                    logger.error(f"[Telegram] Failed to restart polling: {e}")

    # ==================== Command Handling ====================

    async def _handle_start(self, update: Any, context: Any) -> None:
        """Handle the /start command"""
        message = update.message
        chat_id = str(message.chat.id)

        # Check pairing status
        if self.require_pairing and not self.pairing_manager.is_paired(chat_id):
            # Not paired: begin pairing flow
            self.pairing_manager.start_pairing(chat_id)
            code_file = self.pairing_manager.code_file.absolute()
            await message.reply_text(
                "🔐 Welcome to OpenAkita!\n\n"
                "For security, first-time use requires pairing.\n"
                "Please enter the **pairing code** to verify:\n\n"
                f"📁 Pairing code file:\n`{code_file}`"
            )
            return

        # Already paired or pairing not required
        await message.reply_text(
            "👋 Hi! I'm OpenAkita, your all-in-one AI assistant.\n\n"
            "Send a message to start. I can help you:\n"
            "- Answer questions\n"
            "- Execute tasks\n"
            "- Set reminders\n"
            "- Handle files\n"
            "- And much more...\n\n"
            "How can I help you?"
        )

    async def _handle_unpair(self, update: Any, context: Any) -> None:
        """Handle the /unpair command - unpair"""
        message = update.message
        chat_id = str(message.chat.id)

        if self.pairing_manager.unpair(chat_id):
            await message.reply_text(
                "🔓 Unpaired successfully.\n\nSend /start and enter the pairing code to use again."
            )
        else:
            await message.reply_text("This chat is not paired.")

    async def _handle_status(self, update: Any, context: Any) -> None:
        """Handle the /status command - view pairing status"""
        message = update.message
        chat_id = str(message.chat.id)

        if self.pairing_manager.is_paired(chat_id):
            info = self.pairing_manager.paired_users.get(chat_id, {})
            paired_at = info.get("paired_at", "unknown")
            await message.reply_text(
                f"✅ Paired\n📅 Paired at: {paired_at}\n\nSend /unpair to unpair"
            )
        else:
            await message.reply_text("❌ Not paired\n\nSend /start to begin pairing")

    async def _handle_reaction(self, update: Any, context: Any) -> None:
        """Bot API 8.0+ reaction events (reserved; logged only)"""
        reaction = getattr(update, "message_reaction", None)
        if reaction:
            logger.debug(
                f"Telegram reaction: chat={reaction.chat.id}, "
                f"user={getattr(reaction.user, 'id', '?')}, "
                f"new={reaction.new_reaction}"
            )

    async def _handle_callback_query(self, update: Any, context: Any) -> None:
        """Inline keyboard callback; handles security confirmation buttons and similar."""
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        with contextlib.suppress(Exception):
            await query.answer()

        # Security confirmation callbacks: sec_<decision>_<confirm_id>
        if data.startswith("sec_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                decision_key = parts[1]
                confirm_id = parts[2]
                decision_map = {
                    "allow": "allow_once",
                    "session": "allow_session",
                    "always": "allow_always",
                    "deny": "deny",
                    "sandbox": "sandbox",
                }
                decision = decision_map.get(decision_key, "deny")
                try:
                    from openakita.core.policy import get_policy_engine

                    get_policy_engine().resolve_ui_confirm(confirm_id, decision)
                    logger.info(f"[Telegram] Security decision: {decision} for {confirm_id[:8]}")
                except Exception as e:
                    logger.warning(f"[Telegram] Security callback failed: {e}")
                return

        logger.debug(f"Telegram callback_query: data={data}")

    async def _handle_message(self, update: Any, context: Any) -> None:
        """Handle incoming messages"""
        try:
            # Deduplicate: prevents the same update being processed multiple times due to webhook retries / network jitter
            uid = update.update_id
            if uid in self._seen_update_ids:
                logger.debug(f"Duplicate update_id={uid}, skipping")
                return
            self._seen_update_ids[uid] = None
            if len(self._seen_update_ids) > self._seen_update_ids_max:
                self._seen_update_ids.popitem(last=False)

            message = update.message or update.edited_message
            if not message:
                logger.debug("Received update without message")
                return

            chat_id = str(message.chat.id)
            _fu = message.from_user
            user_id = _fu.id if _fu else "unknown"
            logger.debug(f"Received message from user {user_id} in chat {chat_id}: {message.text}")

            # Skip pairing for anonymous users (channel signatures / anonymous admins)
            if not _fu:
                logger.debug(f"Skipping pairing for anonymous message in chat {chat_id}")
            elif self.require_pairing:
                # Check whether paired
                if not self.pairing_manager.is_paired(chat_id):
                    logger.debug(f"Chat {chat_id} is not paired, checking pairing status...")
                    # Check whether waiting for pairing
                    if self.pairing_manager.is_pending_pairing(chat_id):
                        # Try to verify the pairing code
                        code = message.text.strip() if message.text else ""
                        user_info = {
                            "user_id": _fu.id,
                            "username": _fu.username,
                            "first_name": _fu.first_name,
                            "last_name": _fu.last_name,
                        }

                        if self.pairing_manager.verify_code(chat_id, code, user_info):
                            # Pairing successful
                            await message.reply_text(
                                "✅ Paired successfully!\n\n"
                                "You can now use OpenAkita.\n"
                                "Send a message to start. I can help you:\n"
                                "- Answer questions\n"
                                "- Execute tasks\n"
                                "- Set reminders\n"
                                "- Handle files\n"
                                "- And much more..."
                            )
                            logger.info(f"Chat {chat_id} paired: {user_info}")
                        else:
                            # Wrong pairing code
                            code_file = self.pairing_manager.code_file.absolute()
                            await message.reply_text(
                                f"❌ Wrong pairing code. Please try again.\n\n📁 Pairing code file:\n`{code_file}`"
                            )
                        return
                    else:
                        # Pairing flow not started; prompt the user
                        self.pairing_manager.start_pairing(chat_id)
                        code_file = self.pairing_manager.code_file.absolute()
                        await message.reply_text(
                            "🔐 First-time use requires pairing.\n\n"
                            "Please enter the **pairing code** to verify:\n\n"
                            f"📁 Pairing code file:\n`{code_file}`"
                        )
                        return

            # Paired; handle the message normally
            # Convert to unified message format
            unified = await self._convert_message(message)

            # Log
            self._log_message(unified)

            # Trigger callback
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    @staticmethod
    def _duration_secs(d: Any) -> float:
        """Convert a PTB duration to seconds (compatible with int and v22.2+ timedelta)."""
        if d is None:
            return 0.0
        if hasattr(d, "total_seconds"):
            return d.total_seconds()
        return float(d)

    async def _convert_message(self, message: Any) -> UnifiedMessage:
        """Convert a Telegram message to the unified format"""
        content = MessageContent()

        # Text
        if message.text:
            content.text = message.text
            if message.text.startswith("/"):
                pass

        # Image
        if message.photo:
            # Use the largest-sized image
            photo = message.photo[-1]
            media = await self._create_media_from_file(
                photo.file_id,
                f"photo_{photo.file_id}.jpg",
                "image/jpeg",
                photo.file_size or 0,
            )
            media.width = photo.width
            media.height = photo.height
            content.images.append(media)

        # Voice
        if message.voice:
            voice = message.voice
            media = await self._create_media_from_file(
                voice.file_id,
                f"voice_{voice.file_id}.ogg",
                voice.mime_type or "audio/ogg",
                voice.file_size or 0,
            )
            media.duration = self._duration_secs(voice.duration)
            content.voices.append(media)

        # Audio file (not a voice note; handled as an attachment to avoid the STT transcription flow)
        if message.audio:
            audio = message.audio
            media = await self._create_media_from_file(
                audio.file_id,
                audio.file_name or f"audio_{audio.file_id}.mp3",
                audio.mime_type or "audio/mpeg",
                audio.file_size or 0,
            )
            media.duration = self._duration_secs(audio.duration)
            content.files.append(media)

        # Video
        if message.video:
            video = message.video
            media = await self._create_media_from_file(
                video.file_id,
                video.file_name or f"video_{video.file_id}.mp4",
                video.mime_type or "video/mp4",
                video.file_size or 0,
            )
            media.duration = self._duration_secs(video.duration)
            media.width = video.width
            media.height = video.height
            content.videos.append(media)

        # Document
        if message.document:
            doc = message.document
            media = await self._create_media_from_file(
                doc.file_id,
                doc.file_name or f"document_{doc.file_id}",
                doc.mime_type or "application/octet-stream",
                doc.file_size or 0,
            )
            content.files.append(media)

        # video_note (round short video)
        if message.video_note:
            vn = message.video_note
            media = await self._create_media_from_file(
                vn.file_id,
                f"video_note_{vn.file_id}.mp4",
                "video/mp4",
                vn.file_size or 0,
            )
            media.duration = self._duration_secs(vn.duration)
            content.videos.append(media)

        # animation (GIF)
        if message.animation:
            anim = message.animation
            media = await self._create_media_from_file(
                anim.file_id,
                anim.file_name or f"animation_{anim.file_id}.mp4",
                anim.mime_type or "video/mp4",
                anim.file_size or 0,
            )
            content.videos.append(media)

        # Unified caption extraction (applies to all media types)
        if message.caption and not content.text:
            content.text = message.caption

        # Location
        if message.location:
            loc = message.location
            content.location = {
                "lat": loc.latitude,
                "lng": loc.longitude,
            }

        # Sticker
        if message.sticker:
            sticker = message.sticker
            content.sticker = {
                "id": sticker.file_id,
                "emoji": sticker.emoji,
                "set_name": sticker.set_name,
            }

        # Determine the chat type
        chat = message.chat
        chat_type = "private"
        if chat.type == "group" or chat.type == "supergroup":
            chat_type = "group"
        elif chat.type == "channel":
            chat_type = "channel"

        is_direct_message = chat_type == "private"

        # Detect @bot mentions
        is_mentioned = False
        bot_username = getattr(self._bot, "username", None) if self._bot else None
        if bot_username:
            for entities in [message.entities, message.caption_entities]:
                if not entities:
                    continue
                for entity in entities:
                    if entity.type == "mention":
                        mention = message.parse_entity(entity)
                        if mention.lower() == f"@{bot_username.lower()}":
                            is_mentioned = True
                            break
                if is_mentioned:
                    break

        # Implicit mention: a reply to a bot message counts as a mention
        if not is_mentioned and chat_type == "group" and message.reply_to_message:
            reply_from = message.reply_to_message.from_user
            bot_id = getattr(self._bot, "id", None) if self._bot else None
            if reply_from and bot_id and reply_from.id == bot_id:
                is_mentioned = True
                logger.info(
                    f"Telegram: implicit mention detected "
                    f"(reply to bot message {message.reply_to_message.message_id})"
                )

        from_user = message.from_user
        user_id_val = from_user.id if from_user else 0
        username_val = (from_user.username if from_user else None) or ""
        first_name_val = (from_user.first_name if from_user else None) or ""

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=str(message.message_id),
            user_id=f"tg_{user_id_val}" if user_id_val else "tg_anonymous",
            channel_user_id=str(user_id_val) if user_id_val else "anonymous",
            chat_id=str(chat.id),
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            reply_to=str(message.reply_to_message.message_id) if message.reply_to_message else None,
            raw={
                "message_id": message.message_id,
                "chat_id": chat.id,
                "user_id": user_id_val,
                "username": username_val,
                "first_name": first_name_val,
            },
            metadata={
                "is_group": chat_type == "group",
                "sender_name": first_name_val or username_val,
                "chat_name": chat.title or chat.first_name or "",
            },
        )

    async def _create_media_from_file(
        self,
        file_id: str,
        filename: str,
        mime_type: str,
        size: int,
    ) -> MediaFile:
        """Create a media file object"""
        return MediaFile.create(
            filename=filename,
            mime_type=mime_type,
            file_id=file_id,
            size=size,
        )

    # ==================== RetryAfter Generic Retry ====================

    async def _api_retry(self, fn, *args, **kwargs):
        """Execute a Telegram API call; on 429 RetryAfter, wait and retry once."""
        _import_telegram()
        try:
            return await fn(*args, **kwargs)
        except telegram.error.RetryAfter as e:
            logger.warning(f"Telegram rate limit, retrying after {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await fn(*args, **kwargs)

    # ==================== Streaming Thinking / Reply ====================

    async def stream_thinking(
        self,
        chat_id: str,
        thinking_text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
        duration_ms: int = 0,
    ) -> None:
        """Receive thinking content and update the thinking placeholder message in-place."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_thinking[sk] = thinking_text
        self._typing_status[sk] = "deep thinking"
        if duration_ms:
            self._streaming_thinking_ms[sk] = duration_ms

        card_ref = self._thinking_cards.get(sk)
        if not card_ref:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        if now - last_t < self._streaming_throttle_ms / 1000.0:
            return

        display = self._compose_thinking_display(sk)
        try:
            await self._bot.edit_message_text(
                chat_id=card_ref[0],
                message_id=card_ref[1],
                text=display,
                parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Telegram: stream_thinking edit failed: {e}")

    async def stream_chain_text(
        self,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Append chain text (tool call descriptions, result summaries, etc.) to the thinking placeholder message."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_chain.setdefault(sk, []).append(text)
        self._typing_status[sk] = "running tools"

        card_ref = self._thinking_cards.get(sk)
        if not card_ref:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        if now - last_t < self._streaming_throttle_ms / 1000.0:
            return

        display = self._compose_thinking_display(sk)
        try:
            await self._bot.edit_message_text(
                chat_id=card_ref[0],
                message_id=card_ref[1],
                text=display,
                parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Telegram: stream_chain_text edit failed: {e}")

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Accumulate reply tokens; periodically refresh the placeholder message when thinking/chain content exists."""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_buffers[sk] = self._streaming_buffers.get(sk, "") + token
        self._typing_status[sk] = "generating"

        card_ref = self._thinking_cards.get(sk)
        if not card_ref:
            return
        has_thinking = sk in self._streaming_thinking or sk in self._streaming_chain
        if not has_thinking:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        if now - last_t < self._streaming_throttle_ms / 1000.0:
            return

        display = self._compose_thinking_display(sk)
        try:
            await self._bot.edit_message_text(
                chat_id=card_ref[0],
                message_id=card_ref[1],
                text=display,
                parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Telegram: stream_token edit failed: {e}")

    def _compose_thinking_display(self, sk: str) -> str:
        """Build the real-time display text for the thinking process (plain text for editing the placeholder message)."""
        thinking = self._streaming_thinking.get(sk, "")
        reply = self._streaming_buffers.get(sk, "")
        dur_ms = self._streaming_thinking_ms.get(sk, 0)
        chain_lines = self._streaming_chain.get(sk, [])

        parts: list[str] = []
        if thinking:
            dur_str = f" ({dur_ms / 1000:.1f}s)" if dur_ms else ""
            preview = thinking.strip()
            if len(preview) > 600:
                preview = preview[:600] + "..."
            parts.append(f"💭 Thinking{dur_str}\n> " + preview.replace("\n", "\n> "))

        if chain_lines:
            visible = chain_lines[-8:]
            parts.append("\n".join(visible))

        if reply:
            if parts:
                parts.append("─" * 16)
            parts.append(reply[:300] + " ▍" if len(reply) > 300 else reply + " ▍")
        elif not thinking and not chain_lines:
            parts.append("💭 Thinking...")

        text = "\n".join(parts)

        # footer: elapsed time + status
        footer_parts: list[str] = []
        start = self._typing_start_time.get(sk)
        if self._footer_elapsed and start:
            elapsed = time.time() - start
            footer_parts.append(f"⏱ {elapsed:.1f}s")
        if self._footer_status:
            status = self._typing_status.get(sk, "")
            if status:
                footer_parts.append(status)
        if footer_parts:
            text = text + "\n" + " · ".join(footer_parts)

        if len(text) > 4000:
            text = text[:4000] + "\n..."
        return text

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """End of stream: collapse the thinking content into an Expandable Blockquote and send the reply separately.

        Returns:
            True — the thinking placeholder message was replaced with the full reply (send_message not needed).
            False — the thinking placeholder message was edited into a collapsed summary (the reply is sent normally by send_message).
        """
        sk = self._make_session_key(chat_id, thread_id)
        card_ref = self._thinking_cards.get(sk)

        thinking = self._streaming_thinking.pop(sk, "")
        dur_ms = self._streaming_thinking_ms.pop(sk, 0)
        chain_lines = self._streaming_chain.pop(sk, [])
        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)

        if not card_ref:
            return False

        has_progress = bool(thinking or chain_lines)

        if has_progress:
            # Has thinking/chain → edit into an Expandable Blockquote summary and send the reply separately
            summary_html = self._build_thinking_summary_html(thinking, dur_ms, chain_lines, sk=sk)
            try:
                await self._bot.edit_message_text(
                    chat_id=card_ref[0],
                    message_id=card_ref[1],
                    text=summary_html,
                    parse_mode=telegram.constants.ParseMode.HTML,
                )
            except Exception as e:
                logger.debug(f"Telegram: finalize thinking summary failed: {e}")
                with contextlib.suppress(Exception):
                    await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])
            self._thinking_cards.pop(sk, None)
            return False

        # No thinking/chain → replace the placeholder message directly with the reply
        elapsed_suffix = ""
        start = self._typing_start_time.get(sk)
        if self._footer_elapsed and start:
            elapsed_suffix = f"\n\n⏱ Done ({time.time() - start:.1f}s)"

        if final_text and len(final_text + elapsed_suffix) <= 4000:
            text_to_send = self._convert_to_telegram_html(final_text + elapsed_suffix)
            try:
                await self._bot.edit_message_text(
                    chat_id=card_ref[0],
                    message_id=card_ref[1],
                    text=text_to_send,
                    parse_mode=telegram.constants.ParseMode.HTML,
                )
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                return True
            except telegram.error.BadRequest:
                with contextlib.suppress(Exception):
                    await self._bot.edit_message_text(
                        chat_id=card_ref[0],
                        message_id=card_ref[1],
                        text=final_text + elapsed_suffix,
                        parse_mode=None,
                    )
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                return True
            except Exception:
                pass

        # Fallback: delete the placeholder message and go through the normal send_message path
        with contextlib.suppress(Exception):
            await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])
        self._thinking_cards.pop(sk, None)
        return False

    def _build_thinking_summary_html(
        self,
        thinking: str,
        dur_ms: int,
        chain_lines: list[str],
        sk: str = "",
    ) -> str:
        """Build the Expandable Blockquote HTML (collapsed display of the thinking summary)."""
        parts: list[str] = []
        if thinking:
            dur_str = f" ({dur_ms / 1000:.1f}s)" if dur_ms else ""
            header = f"💭 Thinking{dur_str}"
            preview = thinking.strip()
            if len(preview) > 2500:
                preview = preview[:2500] + "..."
            parts.append(f"{_html.escape(header)}\n\n{_html.escape(preview)}")

        if chain_lines:
            visible = chain_lines[-12:]
            parts.append("\n".join(_html.escape(ln) for ln in visible))

        inner = "\n\n".join(parts) if parts else "💭 Thinking complete"
        html = f"<blockquote expandable>{inner}</blockquote>"

        start = self._typing_start_time.get(sk) if sk else None
        if self._footer_elapsed and start:
            elapsed = time.time() - start
            html += f"\n⏱ Done ({elapsed:.1f}s)"

        return html

    def _convert_to_telegram_html(self, text: str) -> str:
        """Convert standard Markdown to Telegram HTML format.

        HTML mode is more reliable than legacy Markdown / MarkdownV2:
        - Special characters are handled uniformly via html.escape(), avoiding accidental format parsing
        - Code blocks / inline code are extracted and protected first to avoid double-escaping their contents
        """
        import re

        if not text:
            return text

        # Step 1: extract fenced code blocks
        code_blocks: list[tuple[str, str]] = []

        def _save_fenced(m: re.Match) -> str:
            lang = m.group(1) or ""
            code = m.group(2)
            idx = len(code_blocks)
            code_blocks.append((lang, code))
            return f"\x00CODEBLOCK{idx}\x00"

        text = re.sub(r"```(\w*)\n(.*?)```", _save_fenced, text, flags=re.DOTALL)

        # Step 2: extract inline code
        inline_codes: list[str] = []

        def _save_inline(m: re.Match) -> str:
            idx = len(inline_codes)
            inline_codes.append(m.group(1))
            return f"\x00INLINE{idx}\x00"

        text = re.sub(r"`([^`\n]+)`", _save_inline, text)

        # Step 3: HTML-escape (does not affect Markdown syntax characters like *, _, ~, [, #, |)
        text = _html.escape(text)

        # Step 4: Markdown -> HTML inline formatting
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
        text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
        text = re.sub(r"\[([^\]]+)]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

        # Step 5: headings -> bold
        text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

        # Step 6: simplified tables
        lines = text.split("\n")
        new_lines: list[str] = []
        in_table = False
        table_rows: list[str] = []
        for line in lines:
            stripped = line.strip()
            if re.match(r"^\|.*\|$", stripped):
                if re.match(r"^\|[-:\s|]+\|$", stripped):
                    continue
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if not in_table:
                    in_table = True
                    table_rows.append(" | ".join(f"<b>{c}</b>" for c in cells if c))
                else:
                    table_rows.append(" | ".join(cells))
            else:
                if in_table:
                    new_lines.extend(table_rows)
                    table_rows = []
                    in_table = False
                new_lines.append(line)
        if table_rows:
            new_lines.extend(table_rows)
        text = "\n".join(new_lines)

        # Step 7: horizontal rules
        text = re.sub(r"^---+$", "─" * 20, text, flags=re.MULTILINE)

        # Step 8: restore code blocks
        for i, (lang, code) in enumerate(code_blocks):
            escaped = _html.escape(code)
            if lang:
                repl = f'<pre><code class="language-{_html.escape(lang)}">{escaped}</code></pre>'
            else:
                repl = f"<pre>{escaped}</pre>"
            text = text.replace(f"\x00CODEBLOCK{i}\x00", repl)

        # Step 9: restore inline code
        for i, code in enumerate(inline_codes):
            text = text.replace(f"\x00INLINE{i}\x00", f"<code>{_html.escape(code)}</code>")

        return text

    async def send_message(self, message: OutgoingMessage) -> str:
        """Send a message"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        # ── Thinking placeholder message handling ──
        sk = self._make_session_key(message.chat_id, message.thread_id)
        if sk in self._streaming_finalized:
            card_ref = self._thinking_cards.pop(sk, None)
            self._streaming_finalized.discard(sk)
            self._streaming_buffers.pop(sk, None)
            self._streaming_last_patch.pop(sk, None)
            self._typing_start_time.pop(sk, None)
            self._typing_status.pop(sk, None)
            return str(card_ref[1]) if card_ref else sk
        if sk not in self._streaming_buffers:
            card_ref = self._thinking_cards.pop(sk, None)
            if card_ref:
                text = message.content.text or ""
                elapsed_suffix = ""
                start = self._typing_start_time.get(sk)
                if self._footer_elapsed and start:
                    elapsed_suffix = f"\n\n⏱ Done ({time.time() - start:.1f}s)"
                if text and not message.content.has_media and len(text + elapsed_suffix) <= 4000:
                    try:
                        t = self._convert_to_telegram_html(text + elapsed_suffix)
                        await self._bot.edit_message_text(
                            chat_id=card_ref[0],
                            message_id=card_ref[1],
                            text=t,
                            parse_mode=telegram.constants.ParseMode.HTML,
                        )
                        self._typing_start_time.pop(sk, None)
                        self._typing_status.pop(sk, None)
                        return str(card_ref[1])
                    except Exception:
                        pass
                with contextlib.suppress(Exception):
                    await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])
                self._typing_start_time.pop(sk, None)
                self._typing_status.pop(sk, None)

        chat_id = int(message.chat_id)
        sent_message = None

        parse_mode = telegram.constants.ParseMode.HTML
        text_to_send = message.content.text

        if message.parse_mode:
            if message.parse_mode.lower() in ("markdown", "html"):
                parse_mode = telegram.constants.ParseMode.HTML
            elif message.parse_mode.lower() == "none":
                parse_mode = None

        if parse_mode == telegram.constants.ParseMode.HTML and text_to_send:
            text_to_send = self._convert_to_telegram_html(text_to_send)

        # Caption is attached only to the first media item to avoid duplicate sends
        caption_used = False
        reply_to_id = int(message.reply_to) if message.reply_to else None
        _thread_id = (
            int(message.thread_id) if message.thread_id and str(message.thread_id).strip() else None
        )

        def _next_caption() -> str | None:
            nonlocal caption_used
            if caption_used or not text_to_send:
                return None
            caption_used = True
            return text_to_send

        # Send text (only when there is no media, or when media exists but text must be sent first)
        if text_to_send and not message.content.has_media:
            try:
                sent_message = await self._api_retry(
                    self._bot.send_message,
                    chat_id=chat_id,
                    text=text_to_send,
                    parse_mode=parse_mode,
                    reply_to_message_id=reply_to_id,
                    message_thread_id=_thread_id,
                    disable_web_page_preview=message.disable_preview,
                )
            except telegram.error.BadRequest as e:
                if "Can't parse entities" in str(e) and parse_mode:
                    logger.warning(f"Markdown parse failed, falling back to plain text: {e}")
                    sent_message = await self._bot.send_message(
                        chat_id=chat_id,
                        text=message.content.text,
                        parse_mode=None,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                        disable_web_page_preview=message.disable_preview,
                    )
                else:
                    raise

        async def _send_media_with_retry(coro_factory):
            """Execute a media send, handling RetryAfter uniformly"""
            try:
                return await coro_factory()
            except telegram.error.RetryAfter as e:
                logger.warning(f"Telegram rate limit on media, retrying after {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
                return await coro_factory()

        # Send images
        for img in message.content.images:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if img.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=img.local_path, _c=cap, _pm=pm: self._bot.send_photo(
                        chat_id=chat_id,
                        photo=_p,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            elif img.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=img.url, _c=cap, _pm=pm: self._bot.send_photo(
                        chat_id=chat_id,
                        photo=_u,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            else:
                logger.warning(f"Telegram: image has no local_path or url, skipped: {img.filename}")

        # Send videos
        for vid in message.content.videos:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if vid.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=vid.local_path, _c=cap, _pm=pm: self._bot.send_video(
                        chat_id=chat_id,
                        video=_p,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            elif vid.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=vid.url, _c=cap, _pm=pm: self._bot.send_video(
                        chat_id=chat_id,
                        video=_u,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            else:
                logger.warning(f"Telegram: video has no local_path or url, skipped: {vid.filename}")

        # Send documents
        for file in message.content.files:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if file.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=file.local_path, _c=cap, _pm=pm, _fn=file.filename: (
                        self._bot.send_document(
                            chat_id=chat_id,
                            document=_p,
                            filename=_fn,
                            caption=_c,
                            parse_mode=_pm,
                            reply_to_message_id=reply_to_id,
                            message_thread_id=_thread_id,
                        )
                    )
                )
            elif file.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=file.url, _c=cap, _pm=pm, _fn=file.filename: self._bot.send_document(
                        chat_id=chat_id,
                        document=_u,
                        filename=_fn,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            else:
                logger.warning(f"Telegram: file has no local_path or url, skipped: {file.filename}")

        # Send voice messages
        for voice in message.content.voices:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if voice.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=voice.local_path, _c=cap, _pm=pm: self._bot.send_voice(
                        chat_id=chat_id,
                        voice=_p,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            elif voice.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=voice.url, _c=cap, _pm=pm: self._bot.send_voice(
                        chat_id=chat_id,
                        voice=_u,
                        caption=_c,
                        parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    )
                )
            else:
                logger.warning(
                    f"Telegram: voice has no local_path or url, skipped: {voice.filename}"
                )

        # text+media scenario: if text exists but no media can carry a caption, send the text separately
        if text_to_send and message.content.has_media and not caption_used:
            try:
                sent_message = await self._bot.send_message(
                    chat_id=chat_id,
                    text=text_to_send,
                    parse_mode=parse_mode,
                    reply_to_message_id=reply_to_id,
                    message_thread_id=_thread_id,
                )
            except Exception as e:
                logger.warning(f"Telegram: fallback text send failed: {e}")

        if not sent_message:
            raise RuntimeError("Telegram: no message was sent")
        return str(sent_message.message_id)

    async def download_media(self, media: MediaFile) -> Path:
        """Download a media file"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        if media.local_path and media.local_path.strip() and Path(media.local_path).is_file():
            return Path(media.local_path)

        if not media.file_id:
            media.status = MediaStatus.FAILED
            raise ValueError("Media has no file_id")

        try:
            file = await self._bot.get_file(media.file_id)
            local_path = self.media_dir / media.filename
            await file.download_to_drive(local_path)
        except Exception:
            media.status = MediaStatus.FAILED
            raise

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.debug(f"Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """Upload a media file (Telegram does not require pre-upload)"""
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )

    async def get_user_info(self, user_id: str) -> dict | None:
        """Get user information"""
        if not self._bot:
            return None

        try:
            # Telegram does not support fetching user info directly
            # It can only be obtained from messages
            return None
        except Exception:
            return None

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """Get chat information"""
        if not self._bot:
            return None

        try:
            chat = await self._bot.get_chat(int(chat_id))
            return {
                "id": str(chat.id),
                "type": chat.type,
                "title": chat.title or chat.first_name,
                "username": chat.username,
            }
        except Exception as e:
            logger.error(f"Failed to get chat info: {e}")
            return None

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message"""
        if not self._bot:
            return False

        try:
            await self._api_retry(
                self._bot.delete_message,
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_content: str,
        parse_mode: str | None = "markdown",
    ) -> bool:
        """Edit a message"""
        if not self._bot:
            return False

        tg_parse_mode = None
        raw_content = new_content
        if parse_mode:
            if parse_mode.lower() in ("markdown", "html"):
                tg_parse_mode = telegram.constants.ParseMode.HTML
                new_content = self._convert_to_telegram_html(new_content)

        try:
            await self._bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=new_content,
                parse_mode=tg_parse_mode,
            )
            return True
        except telegram.error.BadRequest as e:
            if "Can't parse entities" in str(e) and tg_parse_mode:
                with contextlib.suppress(Exception):
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=raw_content,
                        parse_mode=None,
                    )
                    return True
            logger.error(f"Failed to edit message: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
            return False

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> str:
        """Send an image"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        with open(photo_path, "rb") as f:
            sent = await self._bot.send_photo(
                chat_id=int(chat_id),
                photo=f,
                caption=caption if caption else None,
            )

        logger.debug(f"Sent photo to {chat_id}: {photo_path}")
        return str(sent.message_id)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "") -> str:
        """Send a file"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        from pathlib import Path

        filename = Path(file_path).name

        with open(file_path, "rb") as f:
            sent = await self._bot.send_document(
                chat_id=int(chat_id),
                document=f,
                filename=filename,
                caption=caption if caption else None,
            )

        logger.debug(f"Sent file to {chat_id}: {file_path}")
        return str(sent.message_id)

    async def send_voice(self, chat_id: str, voice_path: str, caption: str = "") -> str:
        """Send a voice message"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        with open(voice_path, "rb") as f:
            sent = await self._bot.send_voice(
                chat_id=int(chat_id),
                voice=f,
                caption=caption if caption else None,
            )

        logger.debug(f"Sent voice to {chat_id}: {voice_path}")
        return str(sent.message_id)

    # ==================== Session-level key / streaming helpers ====================

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        return f"{chat_id}:{thread_id}" if thread_id else chat_id

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        return self._bot is not None

    # ==================== Thinking Status Indicators ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send typing status; on the first call, also creates the thinking placeholder message."""
        if not self._bot:
            return

        _tid = int(thread_id) if thread_id and str(thread_id).strip() else None

        with contextlib.suppress(Exception):
            await self._bot.send_chat_action(
                chat_id=int(chat_id),
                action=telegram.constants.ChatAction.TYPING,
                message_thread_id=_tid,
            )

        sk = self._make_session_key(chat_id, thread_id)
        if sk in self._thinking_cards:
            # Subsequent calls: periodically refresh the elapsed-time display on the thinking card
            if self._footer_elapsed or self._footer_status:
                now = time.time()
                last_t = self._streaming_last_patch.get(sk, 0.0)
                if now - last_t >= 3.5:
                    display = self._compose_thinking_display(sk)
                    card_ref = self._thinking_cards[sk]
                    with contextlib.suppress(Exception):
                        await self._bot.edit_message_text(
                            chat_id=card_ref[0],
                            message_id=card_ref[1],
                            text=display,
                            parse_mode=None,
                        )
                        self._streaming_last_patch[sk] = now
            return

        self._streaming_finalized.discard(sk)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)
        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)

        try:
            sent = await self._bot.send_message(
                chat_id=int(chat_id),
                text="💭 Thinking...",
                message_thread_id=_tid,
            )
            self._thinking_cards[sk] = (int(chat_id), sent.message_id)
            self._typing_start_time[sk] = time.time()
            self._typing_status[sk] = "thinking"
        except Exception as e:
            logger.debug(f"Telegram: create thinking placeholder failed: {e}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Clean up any leftover thinking placeholder messages (safety net)."""
        sk = self._make_session_key(chat_id, thread_id)
        card_ref = self._thinking_cards.pop(sk, None)
        self._streaming_finalized.discard(sk)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)
        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)
        self._typing_start_time.pop(sk, None)
        self._typing_status.pop(sk, None)
        if card_ref and self._bot:
            with contextlib.suppress(Exception):
                await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])

    async def _patch_card_content(self, card_ref: tuple[int, int], text: str) -> bool:
        """Edit the contents of the thinking placeholder message (for gateway _try_patch_progress_to_card to call)."""
        if not self._bot or not card_ref:
            return False
        _chat_id, _msg_id = card_ref
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        try:
            await self._api_retry(
                self._bot.edit_message_text,
                chat_id=_chat_id,
                message_id=_msg_id,
                text=text,
                parse_mode=None,
            )
            return True
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                return True
            logger.debug(f"Telegram: _patch_card_content failed: {e}")
            return False
        except Exception as e:
            logger.debug(f"Telegram: _patch_card_content failed: {e}")
            return False
