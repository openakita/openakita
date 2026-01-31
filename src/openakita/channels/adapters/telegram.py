"""
Telegram 适配器

基于 python-telegram-bot 库实现:
- Webhook / Long Polling 模式
- 文本/图片/语音/文件收发
- Markdown 格式支持
"""

import asyncio
import logging
from pathlib import Path
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


class TelegramAdapter(ChannelAdapter):
    """
    Telegram 适配器
    
    支持:
    - Long Polling 模式
    - Webhook 模式（需要公网 URL）
    - 文本/图片/语音/文件收发
    - Markdown 格式
    """
    
    channel_name = "telegram"
    
    def __init__(
        self,
        bot_token: str,
        webhook_url: Optional[str] = None,
        media_dir: Optional[Path] = None,
    ):
        """
        Args:
            bot_token: Telegram Bot Token
            webhook_url: Webhook URL（可选，不提供则使用 Long Polling）
            media_dir: 媒体文件存储目录
        """
        super().__init__()
        
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/telegram")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        
        self._app: Optional[Any] = None
        self._bot: Optional[Any] = None
    
    async def start(self) -> None:
        """启动 Telegram Bot"""
        _import_telegram()
        
        from telegram.ext import Defaults
        from telegram.request import HTTPXRequest
        
        # 配置更长的超时时间（默认 5 秒太短）
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0,
        )
        
        # 创建 Application
        self._app = (
            Application.builder()
            .token(self.bot_token)
            .request(request)
            .get_updates_request(HTTPXRequest(
                connection_pool_size=4,
                read_timeout=60.0,  # getUpdates 用更长的超时
            ))
            .build()
        )
        self._bot = self._app.bot
        
        # 注册消息处理器
        from telegram.ext import MessageHandler, filters
        
        self._app.add_handler(
            MessageHandler(
                filters.ALL & ~filters.COMMAND,
                self._handle_message
            )
        )
        
        # 注册命令处理器
        from telegram.ext import CommandHandler
        
        self._app.add_handler(
            CommandHandler("start", self._handle_start)
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
        await update.message.reply_text(
            "👋 你好！我是 OpenAkita，一个全能AI助手。\n\n"
            "发送消息开始对话，我可以帮你：\n"
            "- 回答问题\n"
            "- 执行任务\n"
            "- 处理文件\n"
            "- 更多功能..."
        )
    
    async def _handle_message(self, update: Any, context: Any) -> None:
        """处理收到的消息"""
        try:
            message = update.message or update.edited_message
            if not message:
                return
            
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
    
    def _escape_markdown_v2(self, text: str) -> str:
        """
        转义 Telegram MarkdownV2 全部特殊字符
        
        官方文档规定必须转义的 18 个字符:
        _ * [ ] ( ) ~ ` > # + - = | { } . !
        
        策略: 全部转义，确保消息能正常发送
        """
        escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        for char in escape_chars:
            text = text.replace(char, '\\' + char)
        
        return text
    
    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._bot:
            raise RuntimeError("Telegram bot not started")
        
        chat_id = int(message.chat_id)
        sent_message = None
        
        # 确定解析模式（默认使用 MarkdownV2）
        parse_mode = telegram.constants.ParseMode.MARKDOWN_V2
        text_to_send = message.content.text
        
        if message.parse_mode:
            if message.parse_mode.lower() == "markdown":
                parse_mode = telegram.constants.ParseMode.MARKDOWN_V2
            elif message.parse_mode.lower() == "html":
                parse_mode = telegram.constants.ParseMode.HTML
            elif message.parse_mode.lower() == "none":
                parse_mode = None
        
        # 如果使用 MarkdownV2，转义特殊字符
        if parse_mode == telegram.constants.ParseMode.MARKDOWN_V2 and text_to_send:
            text_to_send = self._escape_markdown_v2(text_to_send)
        
        # 发送文本
        if text_to_send and not message.content.has_media:
            sent_message = await self._bot.send_message(
                chat_id=chat_id,
                text=text_to_send,
                parse_mode=parse_mode,
                reply_to_message_id=int(message.reply_to) if message.reply_to else None,
                disable_web_page_preview=message.disable_preview,
            )
        
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
        
        logger.info(f"Downloaded media: {media.filename}")
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
        
        logger.info(f"Sent photo to {chat_id}: {photo_path}")
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
        
        logger.info(f"Sent file to {chat_id}: {file_path}")
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
        
        logger.info(f"Sent voice to {chat_id}: {voice_path}")
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
