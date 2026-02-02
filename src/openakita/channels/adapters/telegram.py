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
import json
import logging
import os
import secrets
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

from ..base import ChannelAdapter
from ..types import (
    UnifiedMessage,
    OutgoingMessage,
    MessageContent,
    MediaFile,
    MediaStatus,
    MessageType,
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
            from telegram.ext import Application as App, ContextTypes as CT
            from telegram import Update as Upd
            
            telegram = tg
            Application = App
            Update = Upd
            ContextTypes = CT
        except ImportError:
            raise ImportError(
                "python-telegram-bot not installed. "
                "Run: pip install python-telegram-bot"
            )


def _get_proxy(config_proxy: Optional[str] = None) -> Optional[str]:
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
    for env_var in ['TELEGRAM_PROXY', 'ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY']:
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
    
    def __init__(self, data_dir: Path, pairing_code: Optional[str] = None):
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
        self.pairing_code = pairing_code or self._load_or_generate_code()
        
        # 等待配对的用户 {chat_id: timestamp}
        self._pending_pairing: dict[str, float] = {}
        
        logger.info(f"TelegramPairingManager initialized, {len(self.paired_users)} paired users")
        logger.info(f"Pairing code file: {self.code_file}")
    
    def _load_paired_users(self) -> dict:
        """加载已配对用户"""
        if self.paired_file.exists():
            try:
                with open(self.paired_file, "r", encoding="utf-8") as f:
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
        self._pending_pairing[chat_id] = time.time()
    
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
            result.append({
                "chat_id": chat_id,
                **info,
            })
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
    
    def __init__(
        self,
        bot_token: str,
        webhook_url: Optional[str] = None,
        media_dir: Optional[Path] = None,
        pairing_code: Optional[str] = None,
        require_pairing: bool = True,
        proxy: Optional[str] = None,
    ):
        """
        Args:
            bot_token: Telegram Bot Token
            webhook_url: Webhook URL（可选，不提供则使用 Long Polling）
            media_dir: 媒体文件存储目录
            pairing_code: 配对码（可选，不提供则自动生成）
            require_pairing: 是否需要配对验证（默认 True）
            proxy: 代理地址（可选，不提供则自动检测）
        """
        super().__init__()
        
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/telegram")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        
        # 代理设置（仅从配置或环境变量获取，不自动检测系统代理）
        self.proxy = _get_proxy(proxy)
        
        self._app: Optional[Any] = None
        self._bot: Optional[Any] = None
        
        # 配对管理
        self.require_pairing = require_pairing
        self.pairing_manager = TelegramPairingManager(
            data_dir=Path("data/telegram/pairing"),
            pairing_code=pairing_code,
        )
    
    async def start(self) -> None:
        """启动 Telegram Bot"""
        _import_telegram()
        
        from telegram.ext import Defaults
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
            "read_timeout": 60.0,  # getUpdates 用更长的超时
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
        
        # 注册命令处理器（Telegram 内置命令，优先处理）
        from telegram.ext import MessageHandler, CommandHandler, filters
        
        self._app.add_handler(
            CommandHandler("start", self._handle_start)
        )
        self._app.add_handler(
            CommandHandler("unpair", self._handle_unpair)
        )
        self._app.add_handler(
            CommandHandler("status", self._handle_status)
        )
        
        # 注册消息处理器（处理所有消息，包括系统命令如 /model）
        # 注意：已注册的 CommandHandler 会优先匹配，其他命令和普通消息由此处理
        self._app.add_handler(
            MessageHandler(
                filters.ALL,  # 接受所有消息，让 Gateway 处理系统命令
                self._handle_message
            )
        )
        
        # 初始化
        await self._app.initialize()
        
        # 启动
        if self.webhook_url:
            # Webhook 模式
            await self._app.start()
            await self._bot.set_webhook(self.webhook_url)
            logger.info(f"Telegram bot started with webhook: {self.webhook_url}")
        else:
            # Long Polling 模式 - 使用 updater.start_polling
            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message"],
            )
            logger.info("Telegram bot started with long polling")
        
        self._running = True
        
        # 打印配对信息
        if self.require_pairing:
            paired_count = len(self.pairing_manager.paired_users)
            print("\n" + "=" * 50)
            print("🔐 Telegram 配对验证已启用")
            print(f"   已配对用户: {paired_count}")
            print(f"   配对码: {self.pairing_manager.pairing_code}")
            print(f"   配对码文件: {self.pairing_manager.code_file}")
            print("=" * 50 + "\n")
    
    async def stop(self) -> None:
        """停止 Telegram Bot"""
        self._running = False
        
        if self._app:
            # 先停止 updater
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            # 再停止 application
            await self._app.stop()
            await self._app.shutdown()
        
        logger.info("Telegram bot stopped")
    
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
                "🔓 已取消配对。\n\n"
                "如需重新使用，请发送 /start 并输入配对码。"
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
                f"✅ 配对状态：已配对\n"
                f"📅 配对时间：{paired_at}\n\n"
                f"发送 /unpair 可取消配对"
            )
        else:
            await message.reply_text(
                "❌ 配对状态：未配对\n\n"
                "发送 /start 开始配对"
            )
    
    async def _handle_message(self, update: Any, context: Any) -> None:
        """处理收到的消息"""
        try:
            message = update.message or update.edited_message
            if not message:
                logger.debug("Received update without message")
                return
            
            chat_id = str(message.chat.id)
            user_id = message.from_user.id if message.from_user else "unknown"
            text_preview = (message.text[:30] + "...") if message.text and len(message.text) > 30 else message.text
            logger.debug(f"Received message from user {user_id} in chat {chat_id}: {text_preview}")
            
            # 配对验证
            if self.require_pairing:
                # 检查是否已配对
                if not self.pairing_manager.is_paired(chat_id):
                    logger.debug(f"Chat {chat_id} is not paired, checking pairing status...")
                    # 检查是否在等待配对
                    if self.pairing_manager.is_pending_pairing(chat_id):
                        # 尝试验证配对码
                        code = message.text.strip() if message.text else ""
                        user_info = {
                            "user_id": message.from_user.id,
                            "username": message.from_user.username,
                            "first_name": message.from_user.first_name,
                            "last_name": message.from_user.last_name,
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
                                "❌ 配对码错误，请重新输入。\n\n"
                                f"📁 配对码文件：\n`{code_file}`"
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
        message_type = MessageType.TEXT
        
        # 文本
        if message.text:
            content.text = message.text
            if message.text.startswith("/"):
                message_type = MessageType.COMMAND
        
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
            message_type = MessageType.IMAGE
            
            # 图片说明
            if message.caption:
                content.text = message.caption
                message_type = MessageType.MIXED
        
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
            message_type = MessageType.VOICE
        
        # 音频
        if message.audio:
            audio = message.audio
            media = await self._create_media_from_file(
                audio.file_id,
                audio.file_name or f"audio_{audio.file_id}.mp3",
                audio.mime_type or "audio/mpeg",
                audio.file_size or 0,
            )
            media.duration = audio.duration
            content.voices.append(media)
            message_type = MessageType.VOICE
        
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
            message_type = MessageType.VIDEO
        
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
            message_type = MessageType.FILE
        
        # 位置
        if message.location:
            loc = message.location
            content.location = {
                "lat": loc.latitude,
                "lng": loc.longitude,
            }
            message_type = MessageType.LOCATION
        
        # 表情包
        if message.sticker:
            sticker = message.sticker
            content.sticker = {
                "id": sticker.file_id,
                "emoji": sticker.emoji,
                "set_name": sticker.set_name,
            }
            message_type = MessageType.STICKER
        
        # 确定聊天类型
        chat = message.chat
        chat_type = "private"
        if chat.type == "group":
            chat_type = "group"
        elif chat.type == "supergroup":
            chat_type = "group"
        elif chat.type == "channel":
            chat_type = "channel"
        
        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=str(message.message_id),
            user_id=f"tg_{message.from_user.id}",
            channel_user_id=str(message.from_user.id),
            chat_id=str(chat.id),
            content=content,
            chat_type=chat_type,
            reply_to=str(message.reply_to_message.message_id) if message.reply_to_message else None,
            raw={
                "message_id": message.message_id,
                "chat_id": chat.id,
                "user_id": message.from_user.id,
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
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
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # 2. 将表格转换为简单格式
        lines = text.split('\n')
        new_lines = []
        in_table = False
        table_rows = []
        
        for line in lines:
            stripped = line.strip()
            
            # 检测表格行
            if re.match(r'^\|.*\|$', stripped):
                # 跳过分隔行 (|---|---|)
                if re.match(r'^\|[-:\s|]+\|$', stripped):
                    continue
                
                # 提取单元格内容
                cells = [c.strip() for c in stripped.strip('|').split('|')]
                
                if not in_table:
                    in_table = True
                    # 第一行是表头，用粗体
                    header = ' | '.join(f"*{c}*" for c in cells if c)
                    table_rows.append(header)
                else:
                    # 数据行
                    row = ' | '.join(cells)
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
        
        text = '\n'.join(new_lines)
        
        # 3. 将水平线转换为分隔符
        text = re.sub(r'^---+$', '─' * 20, text, flags=re.MULTILINE)
        
        return text
    
    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")
        
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
        
        # 发送文本
        if text_to_send and not message.content.has_media:
            try:
                sent_message = await self._bot.send_message(
                    chat_id=chat_id,
                    text=text_to_send,
                    parse_mode=parse_mode,
                    reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                    disable_web_page_preview=message.disable_preview,
                )
            except telegram.error.BadRequest as e:
                # MarkdownV2 解析失败，回退到纯文本
                if "Can't parse entities" in str(e) and parse_mode:
                    logger.warning(f"MarkdownV2 parse failed, falling back to plain text: {e}")
                    sent_message = await self._bot.send_message(
                        chat_id=chat_id,
                        text=message.content.text,  # 使用原始文本
                        parse_mode=None,
                        reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                        disable_web_page_preview=message.disable_preview,
                    )
                else:
                    raise
        
        # 发送图片
        for img in message.content.images:
            if img.local_path:
                with open(img.local_path, "rb") as f:
                    sent_message = await self._bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=message.content.text,
                        parse_mode=parse_mode,
                        reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                    )
            elif img.url:
                sent_message = await self._bot.send_photo(
                    chat_id=chat_id,
                    photo=img.url,
                    caption=message.content.text,
                    parse_mode=parse_mode,
                    reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                )
        
        # 发送文档
        for file in message.content.files:
            if file.local_path:
                with open(file.local_path, "rb") as f:
                    sent_message = await self._bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=file.filename,
                        caption=message.content.text,
                        reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                    )
        
        # 发送语音
        for voice in message.content.voices:
            if voice.local_path:
                with open(voice.local_path, "rb") as f:
                    sent_message = await self._bot.send_voice(
                        chat_id=chat_id,
                        voice=f,
                        caption=message.content.text,
                        reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                    )
        
        return str(sent_message.message_id) if sent_message else ""
    
    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")
        
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)
        
        if not media.file_id:
            raise ValueError("Media has no file_id")
        
        # 获取文件
        file = await self._bot.get_file(media.file_id)
        
        # 下载
        local_path = self.media_dir / media.filename
        await file.download_to_drive(local_path)
        
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
    
    async def get_user_info(self, user_id: str) -> Optional[dict]:
        """获取用户信息"""
        if not self._bot:
            return None
        
        try:
            # Telegram 不支持直接获取用户信息
            # 只能从消息中获取
            return None
        except Exception:
            return None
    
    async def get_chat_info(self, chat_id: str) -> Optional[dict]:
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
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_content: str,
    ) -> bool:
        """编辑消息"""
        if not self._bot:
            return False
        
        try:
            await self._bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=new_content,
            )
            return True
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
    
    async def send_typing(self, chat_id: str) -> None:
        """发送正在输入状态"""
        if self._bot:
            try:
                await self._bot.send_chat_action(
                    chat_id=int(chat_id),
                    action=telegram.constants.ChatAction.TYPING,
                )
            except Exception:
                pass
