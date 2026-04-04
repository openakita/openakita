"""
Telegram 适配器

基于 python-telegram-bot 库实现:
- Webhook / Long Polling 模式
- 文本/图片/语音/文件收发
- Markdown 格式支持
- 配对验证（防止未授权访问）
- 自动代理检测（支持配置、环境变量、Windows 系统代理）
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

# 延迟导入 telegram 库
telegram = None
Application = None
Update = None
ContextTypes = None


def _import_telegram():
    """延迟导入 telegram 库"""
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
    获取代理设置（仅从配置文件或环境变量）

    Args:
        config_proxy: 配置文件中指定的代理地址

    Returns:
        代理 URL 或 None
    """
    # 1. 优先使用配置文件中的代理
    if config_proxy:
        logger.info(f"[Telegram] Using proxy from config: {config_proxy}")
        return config_proxy

    # 2. 检查环境变量（仅当用户明确设置时才使用）
    for env_var in ["TELEGRAM_PROXY", "ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"]:
        proxy = os.environ.get(env_var)
        if proxy:
            logger.info(f"[Telegram] Using proxy from environment variable {env_var}: {proxy}")
            return proxy

    # 不自动读取系统代理，支持 TUN 透传模式
    return None


class TelegramPairingManager:
    """
    Telegram 配对管理器

    管理已配对的用户/聊天，防止未授权访问
    """

    def __init__(self, data_dir: Path, pairing_code: str | None = None):
        """
        Args:
            data_dir: 数据存储目录
            pairing_code: 配对码（如果为空，自动生成）
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.paired_file = self.data_dir / "paired_users.json"
        self.code_file = self.data_dir / "pairing_code.txt"

        # 加载已配对用户
        self.paired_users: dict = self._load_paired_users()

        # 设置配对码
        if pairing_code:
            self.pairing_code = pairing_code
            # 同步写入文件，保证文件内容与实际使用的配对码一致
            try:
                self.code_file.write_text(pairing_code, encoding="utf-8")
                logger.info(f"Pairing code from config saved to {self.code_file}")
            except Exception as e:
                logger.error(f"Failed to save pairing code to file: {e}")
        else:
            self.pairing_code = self._load_or_generate_code()

        # 等待配对的用户 {chat_id: timestamp}
        self._pending_pairing: dict[str, float] = {}

        logger.info(f"TelegramPairingManager initialized, {len(self.paired_users)} paired users")
        logger.info(f"Pairing code file: {self.code_file}")

    def _load_paired_users(self) -> dict:
        """加载已配对用户"""
        if self.paired_file.exists():
            try:
                with open(self.paired_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load paired users: {e}")
        return {}

    def _save_paired_users(self) -> None:
        """保存已配对用户"""
        try:
            with open(self.paired_file, "w", encoding="utf-8") as f:
                json.dump(self.paired_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paired users: {e}")

    def _load_or_generate_code(self) -> str:
        """加载或生成配对码"""
        if self.code_file.exists():
            try:
                code = self.code_file.read_text(encoding="utf-8").strip()
                if code:
                    return code
            except Exception:
                pass

        # 生成新的配对码（6位数字）
        code = str(secrets.randbelow(900000) + 100000)

        try:
            self.code_file.write_text(code, encoding="utf-8")
            logger.info(f"Generated new pairing code: {code}")
        except Exception as e:
            logger.error(f"Failed to save pairing code: {e}")

        return code

    def regenerate_code(self) -> str:
        """重新生成配对码"""
        code = str(secrets.randbelow(900000) + 100000)

        try:
            self.code_file.write_text(code, encoding="utf-8")
            self.pairing_code = code
            logger.info(f"Regenerated pairing code: {code}")
        except Exception as e:
            logger.error(f"Failed to save pairing code: {e}")

        return code

    def is_paired(self, chat_id: str) -> bool:
        """检查聊天是否已配对"""
        return chat_id in self.paired_users

    def start_pairing(self, chat_id: str) -> None:
        """开始配对流程"""
        import time

        now = time.time()
        stale = [k for k, ts in self._pending_pairing.items() if now - ts > 300]
        for k in stale:
            del self._pending_pairing[k]
        self._pending_pairing[chat_id] = now

    def is_pending_pairing(self, chat_id: str) -> bool:
        """检查是否在等待配对"""
        import time

        if chat_id not in self._pending_pairing:
            return False

        # 5分钟超时
        if time.time() - self._pending_pairing[chat_id] > 300:
            del self._pending_pairing[chat_id]
            return False

        return True

    def verify_code(self, chat_id: str, code: str, user_info: dict = None) -> bool:
        """
        验证配对码

        Args:
            chat_id: 聊天 ID
            code: 用户输入的配对码
            user_info: 用户信息（用于记录）

        Returns:
            配对是否成功
        """
        if code.strip() == self.pairing_code:
            # 配对成功
            self.paired_users[chat_id] = {
                "paired_at": datetime.now().isoformat(),
                "user_info": user_info or {},
            }
            self._save_paired_users()

            # 清除等待状态
            if chat_id in self._pending_pairing:
                del self._pending_pairing[chat_id]

            logger.info(f"Chat {chat_id} paired successfully")
            return True

        return False

    def unpair(self, chat_id: str) -> bool:
        """取消配对"""
        if chat_id in self.paired_users:
            del self.paired_users[chat_id]
            self._save_paired_users()
            logger.info(f"Chat {chat_id} unpaired")
            return True
        return False

    def get_paired_list(self) -> list[dict]:
        """获取已配对用户列表"""
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
    Telegram 适配器

    支持:
    - Long Polling 模式
    - Webhook 模式（需要公网 URL）
    - 文本/图片/语音/文件收发
    - Markdown 格式
    - 配对验证（防止未授权访问）
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
        "add_reaction": True,
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
            webhook_url: Webhook URL（可选，不提供则使用 Long Polling）
            media_dir: 媒体文件存储目录
            pairing_code: 配对码（可选，不提供则自动生成）
            require_pairing: 是否需要配对验证（默认 True）
            proxy: 代理地址（可选，不提供则自动检测）
            channel_name: 通道名称（多Bot时用于区分实例）
            bot_id: Bot 实例唯一标识
            agent_profile_id: 绑定的 agent profile ID
            footer_elapsed: 思考卡片显示处理耗时（默认 True，可通过 TELEGRAM_FOOTER_ELAPSED 环境变量控制）
            footer_status: 思考卡片显示处理状态（默认 True，可通过 TELEGRAM_FOOTER_STATUS 环境变量控制）
        """
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)

        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/telegram")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # 代理设置（仅从配置或环境变量获取，不自动检测系统代理）
        self.proxy = _get_proxy(proxy)

        self._app: Any | None = None
        self._bot: Any | None = None
        self._watchdog_task: asyncio.Task | None = None

        # 配对管理
        self.require_pairing = require_pairing
        self.pairing_manager = TelegramPairingManager(
            data_dir=Path("data/telegram/pairing"),
            pairing_code=pairing_code,
        )

        # Webhook secret_token（用于验证来源是 Telegram 的请求）
        import secrets
        self._webhook_secret = secrets.token_urlsafe(32)

        # 消息去重（防止 webhook 重试或网络抖动导致重复处理）
        self._seen_update_ids: OrderedDict[int, None] = OrderedDict()
        self._seen_update_ids_max = 500

        # 思考占位消息：session_key -> (chat_id_int, message_id)
        self._thinking_cards: dict[str, tuple[int, int]] = {}
        # 流式输出状态
        self._streaming_buffers: dict[str, str] = {}
        self._streaming_thinking: dict[str, str] = {}
        self._streaming_thinking_ms: dict[str, int] = {}
        self._streaming_chain: dict[str, list[str]] = {}
        self._streaming_last_patch: dict[str, float] = {}
        self._streaming_finalized: set[str] = set()
        self._streaming_throttle_ms: int = 1500

        # Footer 配置（耗时 / 状态显示）
        self._typing_start_time: dict[str, float] = {}
        self._typing_status: dict[str, str] = {}
        self._footer_elapsed: bool = footer_elapsed if footer_elapsed is not None else (
            os.environ.get("TELEGRAM_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes")
        )
        self._footer_status: bool = footer_status if footer_status is not None else (
            os.environ.get("TELEGRAM_FOOTER_STATUS", "true").lower() in ("true", "1", "yes")
        )

    async def start(self) -> None:
        """启动 Telegram Bot"""
        _import_telegram()

        from telegram.request import HTTPXRequest

        # 配置更长的超时时间（默认 5 秒太短）
        # 如果检测到代理，自动使用
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

        # 创建 Application
        self._app = (
            Application.builder()
            .token(self.bot_token)
            .request(request)
            .get_updates_request(HTTPXRequest(**get_updates_kwargs))
            .build()
        )
        self._bot = self._app.bot

        # 注册错误处理器（捕获 update 处理过程中的所有异常，防止静默丢失）
        self._app.add_error_handler(self._on_error)

        # 注册命令处理器（Telegram 内置命令，优先处理）
        from telegram.ext import CommandHandler, MessageHandler, filters

        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("unpair", self._handle_unpair))
        self._app.add_handler(CommandHandler("status", self._handle_status))

        # 注册消息处理器（处理所有消息，包括系统命令如 /model）
        # 注意：已注册的 CommandHandler 会优先匹配，其他命令和普通消息由此处理
        self._app.add_handler(
            MessageHandler(
                filters.ALL,  # 接受所有消息，让 Gateway 处理系统命令
                self._handle_message,
            )
        )

        # 初始化（连接 Telegram API）
        try:
            await self._app.initialize()
        except Exception as e:
            err_str = str(e)
            err_type = type(e).__name__
            if "ConnectError" in err_type or "ConnectError" in err_str:
                proxy_hint = (
                    "Telegram API (api.telegram.org) 无法连接。"
                    "如果你在中国大陆，需要配置代理才能使用 Telegram Bot。\n"
                    "配置方式（任选其一）：\n"
                    "  1. 在 IM 通道配置中添加 proxy 字段，如 socks5://127.0.0.1:7890\n"
                    "  2. 设置环境变量 TELEGRAM_PROXY=socks5://127.0.0.1:7890\n"
                    "  3. 使用支持 TUN 模式的代理工具（如 Clash TUN）"
                )
                logger.error(f"[Telegram] {proxy_hint}")
                raise ConnectionError(proxy_hint) from e
            if "InvalidToken" in err_type or "Not Found" in err_str or "Unauthorized" in err_str:
                raise ConnectionError(
                    "Telegram Bot Token 无效或已过期，请在 @BotFather 检查 Token 是否正确。"
                ) from e
            raise

        # 自动注册机器人命令菜单（Telegram 的 / 命令提示）
        try:
            from telegram import BotCommand

            bot_commands = [
                BotCommand("start", "开始使用 / 配对验证"),
                BotCommand("status", "查看配对状态"),
                BotCommand("unpair", "取消配对"),
                BotCommand("model", "查看当前模型"),
                BotCommand("switch", "临时切换模型"),
                BotCommand("priority", "调整模型优先级"),
                BotCommand("restore", "恢复默认模型"),
                BotCommand("thinking", "深度思考模式 (on/off/auto)"),
                BotCommand("thinking_depth", "思考深度 (low/medium/high)"),
                BotCommand("chain", "思维链进度推送 (on/off)"),
                BotCommand("cancel", "取消当前操作"),
                BotCommand("restart", "终极重启服务"),
                BotCommand("cancel_restart", "取消重启"),
            ]
            await self._bot.set_my_commands(bot_commands)
            logger.info(
                f"[Telegram] 已注册 {len(bot_commands)} 个机器人命令到菜单"
            )
        except Exception as e:
            logger.warning(f"[Telegram] 注册命令菜单失败（不影响使用）: {e}")

        # 启动
        if self.webhook_url:
            # Webhook 模式
            await self._app.start()
            await self._bot.set_webhook(
                self.webhook_url, secret_token=self._webhook_secret,
                allowed_updates=["message", "edited_message"],
            )
            logger.info(f"Telegram bot started with webhook: {self.webhook_url}")
        else:
            # Long Polling 模式 - 使用 updater.start_polling
            # 先清除可能残留的旧 webhook/polling 连接，避免 Conflict 错误
            try:
                await self._bot.delete_webhook(drop_pending_updates=True)
                logger.info("Cleared previous webhook/polling connections before starting")
            except Exception as e:
                logger.warning(f"Failed to delete webhook before polling: {e}")

            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message"],
                error_callback=self._on_polling_error,
            )
            logger.info("Telegram bot started with long polling")

        self._running = True

        # 启动 polling 健康监测 watchdog
        if not self.webhook_url:
            self._watchdog_task = asyncio.create_task(self._polling_watchdog())

        # 打印配对信息（使用 logger 代替 print 避免 GBK 编码问题）
        if self.require_pairing:
            paired_count = len(self.pairing_manager.paired_users)
            logger.info("=" * 50)
            logger.info("[Telegram] Pairing verification enabled")
            logger.info(f"  Paired users: {paired_count}")
            logger.info(f"  Pairing code: {self.pairing_manager.pairing_code}")
            logger.info(f"  Pairing code file: {self.pairing_manager.code_file}")
            logger.info("=" * 50)

    async def stop(self) -> None:
        """停止 Telegram Bot"""
        self._running = False

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None

        if self._app:
            # Webhook 模式下先删除 webhook
            if self.webhook_url and self._bot:
                with contextlib.suppress(Exception):
                    await self._bot.delete_webhook()

            # 先停止 updater
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            # 再停止 application
            await self._app.stop()
            await self._app.shutdown()

        logger.info("Telegram bot stopped")

    # ==================== 错误处理与健康监测 ====================

    async def _on_error(self, update: Any, context: Any) -> None:
        """处理 update 处理过程中的异常，防止消息静默丢失"""
        logger.error(
            f"[Telegram] Error handling update: {context.error}",
            exc_info=context.error,
        )

    def _on_polling_error(self, error: Exception) -> None:
        """处理 polling 网络错误（连接断开、超时等），库会自动重试。

        注意：python-telegram-bot 要求 error_callback 必须是同步函数，不能是 coroutine。
        """
        logger.warning(f"[Telegram] Polling network error (will auto-retry): {error}")

    async def _polling_watchdog(self) -> None:
        """监测 polling 是否存活，停止则自动重启"""
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
                        allowed_updates=["message", "edited_message"],
                        error_callback=self._on_polling_error,
                    )
                    logger.info("[Telegram] Polling restarted successfully")
                except Exception as e:
                    logger.error(f"[Telegram] Failed to restart polling: {e}")

    # ==================== 命令处理 ====================

    async def _handle_start(self, update: Any, context: Any) -> None:
        """处理 /start 命令"""
        message = update.message
        chat_id = str(message.chat.id)

        # 检查配对状态
        if self.require_pairing and not self.pairing_manager.is_paired(chat_id):
            # 未配对，开始配对流程
            self.pairing_manager.start_pairing(chat_id)
            code_file = self.pairing_manager.code_file.absolute()
            await message.reply_text(
                "🔐 欢迎使用 OpenAkita！\n\n"
                "为了安全，首次使用需要配对验证。\n"
                "请输入 **配对码** 完成验证：\n\n"
                f"📁 配对码文件：\n`{code_file}`"
            )
            return

        # 已配对或不需要配对
        await message.reply_text(
            "👋 你好！我是 OpenAkita，一个全能AI助手。\n\n"
            "发送消息开始对话，我可以帮你：\n"
            "- 回答问题\n"
            "- 执行任务\n"
            "- 设置提醒\n"
            "- 处理文件\n"
            "- 更多功能...\n\n"
            "有什么可以帮你的？"
        )

    async def _handle_unpair(self, update: Any, context: Any) -> None:
        """处理 /unpair 命令 - 取消配对"""
        message = update.message
        chat_id = str(message.chat.id)

        if self.pairing_manager.unpair(chat_id):
            await message.reply_text(
                "🔓 已取消配对。\n\n如需重新使用，请发送 /start 并输入配对码。"
            )
        else:
            await message.reply_text("当前聊天未配对。")

    async def _handle_status(self, update: Any, context: Any) -> None:
        """处理 /status 命令 - 查看配对状态"""
        message = update.message
        chat_id = str(message.chat.id)

        if self.pairing_manager.is_paired(chat_id):
            info = self.pairing_manager.paired_users.get(chat_id, {})
            paired_at = info.get("paired_at", "未知")
            await message.reply_text(
                f"✅ 配对状态：已配对\n📅 配对时间：{paired_at}\n\n发送 /unpair 可取消配对"
            )
        else:
            await message.reply_text("❌ 配对状态：未配对\n\n发送 /start 开始配对")

    async def _handle_message(self, update: Any, context: Any) -> None:
        """处理收到的消息"""
        try:
            # 去重：防止 webhook 重试 / 网络抖动导致同一 update 被处理多次
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

            # 匿名用户（频道签名/匿名管理员）跳过配对
            if not _fu:
                logger.debug(f"Skipping pairing for anonymous message in chat {chat_id}")
            elif self.require_pairing:
                # 检查是否已配对
                if not self.pairing_manager.is_paired(chat_id):
                    logger.debug(f"Chat {chat_id} is not paired, checking pairing status...")
                    # 检查是否在等待配对
                    if self.pairing_manager.is_pending_pairing(chat_id):
                        # 尝试验证配对码
                        code = message.text.strip() if message.text else ""
                        user_info = {
                            "user_id": _fu.id,
                            "username": _fu.username,
                            "first_name": _fu.first_name,
                            "last_name": _fu.last_name,
                        }

                        if self.pairing_manager.verify_code(chat_id, code, user_info):
                            # 配对成功
                            await message.reply_text(
                                "✅ 配对成功！\n\n"
                                "现在你可以开始使用 OpenAkita 了。\n"
                                "发送消息开始对话，我可以帮你：\n"
                                "- 回答问题\n"
                                "- 执行任务\n"
                                "- 设置提醒\n"
                                "- 处理文件\n"
                                "- 更多功能..."
                            )
                            logger.info(f"Chat {chat_id} paired: {user_info}")
                        else:
                            # 配对码错误
                            code_file = self.pairing_manager.code_file.absolute()
                            await message.reply_text(
                                f"❌ 配对码错误，请重新输入。\n\n📁 配对码文件：\n`{code_file}`"
                            )
                        return
                    else:
                        # 未开始配对流程，提示用户
                        self.pairing_manager.start_pairing(chat_id)
                        code_file = self.pairing_manager.code_file.absolute()
                        await message.reply_text(
                            "🔐 首次使用需要配对验证。\n\n"
                            "请输入 **配对码** 完成验证：\n\n"
                            f"📁 配对码文件：\n`{code_file}`"
                        )
                        return

            # 已配对，正常处理消息
            # 转换为统一消息格式
            unified = await self._convert_message(message)

            # 记录日志
            self._log_message(unified)

            # 触发回调
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _convert_message(self, message: Any) -> UnifiedMessage:
        """将 Telegram 消息转换为统一格式"""
        content = MessageContent()

        # 文本
        if message.text:
            content.text = message.text
            if message.text.startswith("/"):
                pass

        # 图片
        if message.photo:
            # 获取最大尺寸的图片
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

        # 语音
        if message.voice:
            voice = message.voice
            media = await self._create_media_from_file(
                voice.file_id,
                f"voice_{voice.file_id}.ogg",
                voice.mime_type or "audio/ogg",
                voice.file_size or 0,
            )
            media.duration = voice.duration
            content.voices.append(media)

        # 音频文件（非语音条，作为附件处理，避免走 STT 转写流程）
        if message.audio:
            audio = message.audio
            media = await self._create_media_from_file(
                audio.file_id,
                audio.file_name or f"audio_{audio.file_id}.mp3",
                audio.mime_type or "audio/mpeg",
                audio.file_size or 0,
            )
            media.duration = audio.duration
            content.files.append(media)

        # 视频
        if message.video:
            video = message.video
            media = await self._create_media_from_file(
                video.file_id,
                video.file_name or f"video_{video.file_id}.mp4",
                video.mime_type or "video/mp4",
                video.file_size or 0,
            )
            media.duration = video.duration
            media.width = video.width
            media.height = video.height
            content.videos.append(media)

        # 文档
        if message.document:
            doc = message.document
            media = await self._create_media_from_file(
                doc.file_id,
                doc.file_name or f"document_{doc.file_id}",
                doc.mime_type or "application/octet-stream",
                doc.file_size or 0,
            )
            content.files.append(media)

        # video_note (圆形短视频)
        if message.video_note:
            vn = message.video_note
            media = await self._create_media_from_file(
                vn.file_id,
                f"video_note_{vn.file_id}.mp4",
                "video/mp4",
                vn.file_size or 0,
            )
            media.duration = vn.duration
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

        # 统一提取 caption（对所有媒体类型生效）
        if message.caption and not content.text:
            content.text = message.caption

        # 位置
        if message.location:
            loc = message.location
            content.location = {
                "lat": loc.latitude,
                "lng": loc.longitude,
            }

        # 表情包
        if message.sticker:
            sticker = message.sticker
            content.sticker = {
                "id": sticker.file_id,
                "emoji": sticker.emoji,
                "set_name": sticker.set_name,
            }

        # 确定聊天类型
        chat = message.chat
        chat_type = "private"
        if chat.type == "group" or chat.type == "supergroup":
            chat_type = "group"
        elif chat.type == "channel":
            chat_type = "channel"

        is_direct_message = chat_type == "private"

        # 检测 @机器人 提及
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

        # 隐式 mention：回复机器人消息视为提及
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
        """创建媒体文件对象"""
        return MediaFile.create(
            filename=filename,
            mime_type=mime_type,
            file_id=file_id,
            size=size,
        )

    # ==================== 流式思考 / 回复 ====================

    async def stream_thinking(
        self,
        chat_id: str,
        thinking_text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
        duration_ms: int = 0,
    ) -> None:
        """接收思考内容，Edit-in-Place 更新思考占位消息。"""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_thinking[sk] = thinking_text
        self._typing_status[sk] = "深度思考"
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
                chat_id=card_ref[0], message_id=card_ref[1],
                text=display, parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
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
        """将工具调用描述/结果摘要等 chain 文本追加到思考占位消息。"""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_chain.setdefault(sk, []).append(text)
        self._typing_status[sk] = "调用工具"

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
                chat_id=card_ref[0], message_id=card_ref[1],
                text=display, parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
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
        """累积回复 token；有思考/chain 内容时定期刷新占位消息。"""
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_buffers[sk] = self._streaming_buffers.get(sk, "") + token
        self._typing_status[sk] = "生成回复"

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
                chat_id=card_ref[0], message_id=card_ref[1],
                text=display, parse_mode=None,
            )
            self._streaming_last_patch[sk] = now
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Telegram: stream_token edit failed: {e}")

    def _compose_thinking_display(self, sk: str) -> str:
        """构建思考过程的实时显示文本（纯文本，用于编辑占位消息）。"""
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
            parts.append(f"💭 思考过程{dur_str}\n> " + preview.replace("\n", "\n> "))

        if chain_lines:
            visible = chain_lines[-8:]
            parts.append("\n".join(visible))

        if reply:
            if parts:
                parts.append("─" * 16)
            parts.append(reply[:300] + " ▍" if len(reply) > 300 else reply + " ▍")
        elif not thinking and not chain_lines:
            parts.append("💭 思考中...")

        text = "\n".join(parts)

        # footer: 耗时 + 状态
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
        """流式结束：将思考内容折叠为 Expandable Blockquote，回复另发。

        Returns:
            True — 思考占位消息已被替换为完整回复（无需 send_message）。
            False — 思考占位消息已编辑为折叠摘要（回复由 send_message 正常发送）。
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
            # 有思考/chain → 编辑为 Expandable Blockquote 摘要，回复另发
            summary_html = self._build_thinking_summary_html(thinking, dur_ms, chain_lines, sk=sk)
            try:
                await self._bot.edit_message_text(
                    chat_id=card_ref[0], message_id=card_ref[1],
                    text=summary_html,
                    parse_mode=telegram.constants.ParseMode.HTML,
                )
            except Exception as e:
                logger.debug(f"Telegram: finalize thinking summary failed: {e}")
                with contextlib.suppress(Exception):
                    await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])
            self._thinking_cards.pop(sk, None)
            return False

        # 无思考/chain → 直接用回复替换占位消息
        elapsed_suffix = ""
        start = self._typing_start_time.get(sk)
        if self._footer_elapsed and start:
            elapsed_suffix = f"\n\n⏱ 完成 ({time.time() - start:.1f}s)"

        if final_text and len(final_text + elapsed_suffix) <= 4000:
            text_to_send = self._convert_to_telegram_markdown(final_text + elapsed_suffix)
            try:
                await self._bot.edit_message_text(
                    chat_id=card_ref[0], message_id=card_ref[1],
                    text=text_to_send,
                    parse_mode=telegram.constants.ParseMode.MARKDOWN,
                )
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                return True
            except telegram.error.BadRequest:
                with contextlib.suppress(Exception):
                    await self._bot.edit_message_text(
                        chat_id=card_ref[0], message_id=card_ref[1],
                        text=final_text + elapsed_suffix, parse_mode=None,
                    )
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                return True
            except Exception:
                pass

        # 回退：删除占位消息，走正常 send_message
        with contextlib.suppress(Exception):
            await self._bot.delete_message(chat_id=card_ref[0], message_id=card_ref[1])
        self._thinking_cards.pop(sk, None)
        return False

    def _build_thinking_summary_html(
        self, thinking: str, dur_ms: int, chain_lines: list[str],
        sk: str = "",
    ) -> str:
        """构建 Expandable Blockquote HTML（思考摘要折叠展示）。"""
        parts: list[str] = []
        if thinking:
            dur_str = f" ({dur_ms / 1000:.1f}s)" if dur_ms else ""
            header = f"💭 思考过程{dur_str}"
            preview = thinking.strip()
            if len(preview) > 2500:
                preview = preview[:2500] + "..."
            parts.append(f"{_html.escape(header)}\n\n{_html.escape(preview)}")

        if chain_lines:
            visible = chain_lines[-12:]
            parts.append("\n".join(_html.escape(ln) for ln in visible))

        inner = "\n\n".join(parts) if parts else "💭 思考完成"
        html = f"<blockquote expandable>{inner}</blockquote>"

        start = self._typing_start_time.get(sk) if sk else None
        if self._footer_elapsed and start:
            elapsed = time.time() - start
            html += f"\n⏱ 完成 ({elapsed:.1f}s)"

        return html

    def _convert_to_telegram_markdown(self, text: str) -> str:
        """
        将标准 Markdown 转换为 Telegram 兼容格式

        Telegram 的 Markdown 模式支持：
        - *bold* 或 **bold** → 粗体
        - _italic_ → 斜体
        - `code` → 代码
        - ```code block``` → 代码块
        - [link](url) → 链接

        不支持（需要转换或移除）：
        - 表格（| 格式）→ 转为简单列表
        - 标题（# 格式）→ 移除 # 符号
        - 水平线 (---) → 转为分隔符
        """
        import re

        if not text:
            return text

        # 1. 移除标题符号（保留文字）
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

        # 2. 将表格转换为简单格式
        lines = text.split("\n")
        new_lines = []
        in_table = False
        table_rows = []

        for line in lines:
            stripped = line.strip()

            # 检测表格行
            if re.match(r"^\|.*\|$", stripped):
                # 跳过分隔行 (|---|---|)
                if re.match(r"^\|[-:\s|]+\|$", stripped):
                    continue

                # 提取单元格内容
                cells = [c.strip() for c in stripped.strip("|").split("|")]

                if not in_table:
                    in_table = True
                    # 第一行是表头，用粗体
                    header = " | ".join(f"*{c}*" for c in cells if c)
                    table_rows.append(header)
                else:
                    # 数据行
                    row = " | ".join(cells)
                    table_rows.append(row)
            else:
                # 非表格行
                if in_table:
                    # 表格结束，添加表格内容
                    new_lines.extend(table_rows)
                    table_rows = []
                    in_table = False
                new_lines.append(line)

        # 处理文件末尾的表格
        if table_rows:
            new_lines.extend(table_rows)

        text = "\n".join(new_lines)

        # 3. 将水平线转换为分隔符
        text = re.sub(r"^---+$", "─" * 20, text, flags=re.MULTILINE)

        return text

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")

        # ── 思考占位消息处理 ──
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
                    elapsed_suffix = f"\n\n⏱ 完成 ({time.time() - start:.1f}s)"
                if text and not message.content.has_media and len(text + elapsed_suffix) <= 4000:
                    try:
                        t = self._convert_to_telegram_markdown(text + elapsed_suffix)
                        await self._bot.edit_message_text(
                            chat_id=card_ref[0], message_id=card_ref[1],
                            text=t, parse_mode=telegram.constants.ParseMode.MARKDOWN,
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

        # 确定解析模式（默认使用普通 Markdown，更宽容）
        parse_mode = telegram.constants.ParseMode.MARKDOWN
        text_to_send = message.content.text

        if message.parse_mode:
            if message.parse_mode.lower() == "markdown":
                parse_mode = telegram.constants.ParseMode.MARKDOWN
            elif message.parse_mode.lower() == "html":
                parse_mode = telegram.constants.ParseMode.HTML
            elif message.parse_mode.lower() == "none":
                parse_mode = None

        # 转换 Markdown 为 Telegram 兼容格式
        if parse_mode == telegram.constants.ParseMode.MARKDOWN and text_to_send:
            text_to_send = self._convert_to_telegram_markdown(text_to_send)

        # caption 只附在第一个媒体上，避免重复发送
        caption_used = False
        reply_to_id = int(message.reply_to) if message.reply_to else None
        _thread_id = int(message.thread_id) if message.thread_id and str(message.thread_id).strip() else None

        def _next_caption() -> str | None:
            nonlocal caption_used
            if caption_used or not text_to_send:
                return None
            caption_used = True
            return text_to_send

        # 发送文本（仅在无媒体时，或有媒体但需要先发文本时）
        if text_to_send and not message.content.has_media:
            try:
                sent_message = await self._bot.send_message(
                    chat_id=chat_id,
                    text=text_to_send,
                    parse_mode=parse_mode,
                    reply_to_message_id=reply_to_id,
                    message_thread_id=_thread_id,
                    disable_web_page_preview=message.disable_preview,
                )
            except telegram.error.RetryAfter as e:
                logger.warning(f"Telegram rate limit, retrying after {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
                sent_message = await self._bot.send_message(
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
            """执行媒体发送，统一处理 RetryAfter"""
            try:
                return await coro_factory()
            except telegram.error.RetryAfter as e:
                logger.warning(f"Telegram rate limit on media, retrying after {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
                return await coro_factory()

        # 发送图片
        for img in message.content.images:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if img.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=img.local_path, _c=cap, _pm=pm: self._bot.send_photo(
                        chat_id=chat_id, photo=_p, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            elif img.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=img.url, _c=cap, _pm=pm: self._bot.send_photo(
                        chat_id=chat_id, photo=_u, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            else:
                logger.warning(f"Telegram: image has no local_path or url, skipped: {img.filename}")

        # 发送视频
        for vid in message.content.videos:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if vid.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=vid.local_path, _c=cap, _pm=pm: self._bot.send_video(
                        chat_id=chat_id, video=_p, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            elif vid.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=vid.url, _c=cap, _pm=pm: self._bot.send_video(
                        chat_id=chat_id, video=_u, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            else:
                logger.warning(f"Telegram: video has no local_path or url, skipped: {vid.filename}")

        # 发送文档
        for file in message.content.files:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if file.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=file.local_path, _c=cap, _pm=pm, _fn=file.filename: self._bot.send_document(
                        chat_id=chat_id, document=_p, filename=_fn,
                        caption=_c, parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            elif file.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=file.url, _c=cap, _pm=pm, _fn=file.filename: self._bot.send_document(
                        chat_id=chat_id, document=_u, filename=_fn,
                        caption=_c, parse_mode=_pm,
                        reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            else:
                logger.warning(f"Telegram: file has no local_path or url, skipped: {file.filename}")

        # 发送语音
        for voice in message.content.voices:
            cap = _next_caption()
            pm = parse_mode if cap else None
            if voice.local_path:
                sent_message = await _send_media_with_retry(
                    lambda _p=voice.local_path, _c=cap, _pm=pm: self._bot.send_voice(
                        chat_id=chat_id, voice=_p, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            elif voice.url:
                sent_message = await _send_media_with_retry(
                    lambda _u=voice.url, _c=cap, _pm=pm: self._bot.send_voice(
                        chat_id=chat_id, voice=_u, caption=_c,
                        parse_mode=_pm, reply_to_message_id=reply_to_id,
                        message_thread_id=_thread_id,
                    ))
            else:
                logger.warning(f"Telegram: voice has no local_path or url, skipped: {voice.filename}")

        # text+media 场景：如果有文本但所有媒体都无法附带 caption，单独发送文本
        if text_to_send and message.content.has_media and not caption_used:
            try:
                sent_message = await self._bot.send_message(
                    chat_id=chat_id, text=text_to_send, parse_mode=parse_mode,
                    reply_to_message_id=reply_to_id,
                    message_thread_id=_thread_id,
                )
            except Exception as e:
                logger.warning(f"Telegram: fallback text send failed: {e}")

        if not sent_message:
            raise RuntimeError("Telegram: no message was sent")
        return str(sent_message.message_id)

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
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
        """上传媒体文件（Telegram 不需要预上传）"""
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )

    async def get_user_info(self, user_id: str) -> dict | None:
        """获取用户信息"""
        if not self._bot:
            return None

        try:
            # Telegram 不支持直接获取用户信息
            # 只能从消息中获取
            return None
        except Exception:
            return None

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """获取聊天信息"""
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
        """删除消息"""
        if not self._bot:
            return False

        try:
            await self._bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return False

    async def add_reaction(
        self,
        chat_id: str,
        message_id: str,
        emoji: str = "👀",
    ) -> bool:
        """Telegram Bot API setMessageReaction (API 7.0+)."""
        if not self._bot:
            return False
        try:
            from telegram import ReactionTypeEmoji
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
            return True
        except Exception as e:
            logger.debug(f"Failed to add reaction: {e}")
            return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_content: str,
        parse_mode: str | None = "markdown",
    ) -> bool:
        """编辑消息"""
        if not self._bot:
            return False

        tg_parse_mode = None
        raw_content = new_content
        if parse_mode:
            if parse_mode.lower() == "markdown":
                tg_parse_mode = telegram.constants.ParseMode.MARKDOWN
                new_content = self._convert_to_telegram_markdown(new_content)
            elif parse_mode.lower() == "html":
                tg_parse_mode = telegram.constants.ParseMode.HTML

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
        """发送图片"""
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
        """发送文件"""
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
        """发送语音"""
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

    # ==================== 会话级 key / 流式辅助 ====================

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        return f"{chat_id}:{thread_id}" if thread_id else chat_id

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        return self._bot is not None

    # ==================== 思考状态指示器 ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送 typing 状态；首次调用还会创建思考占位消息。"""
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
            # 后续调用：定期更新思考卡片的耗时显示
            if self._footer_elapsed or self._footer_status:
                now = time.time()
                last_t = self._streaming_last_patch.get(sk, 0.0)
                if now - last_t >= 3.5:
                    display = self._compose_thinking_display(sk)
                    card_ref = self._thinking_cards[sk]
                    with contextlib.suppress(Exception):
                        await self._bot.edit_message_text(
                            chat_id=card_ref[0], message_id=card_ref[1],
                            text=display, parse_mode=None,
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
                text="💭 思考中...",
                message_thread_id=_tid,
            )
            self._thinking_cards[sk] = (int(chat_id), sent.message_id)
            self._typing_start_time[sk] = time.time()
            self._typing_status[sk] = "思考中"
        except Exception as e:
            logger.debug(f"Telegram: create thinking placeholder failed: {e}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """清理残留的思考占位消息（安全网）。"""
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
        """编辑思考占位消息内容（供 gateway _try_patch_progress_to_card 调用）。"""
        if not self._bot or not card_ref:
            return False
        _chat_id, _msg_id = card_ref
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        try:
            await self._bot.edit_message_text(
                chat_id=_chat_id, message_id=_msg_id,
                text=text, parse_mode=None,
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
