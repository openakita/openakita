"""
统一消息类型定义

定义跨平台通用的消息格式:
- UnifiedMessage: 接收的消息
- OutgoingMessage: 发送的消息
- MessageContent: 消息内容（文本/媒体）
- MediaFile: 媒体文件
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class MessageType(Enum):
    """消息类型"""

    TEXT = "text"  # 纯文本
    IMAGE = "image"  # 图片
    VOICE = "voice"  # 语音
    FILE = "file"  # 文件
    VIDEO = "video"  # 视频
    LOCATION = "location"  # 位置
    STICKER = "sticker"  # 表情包
    MIXED = "mixed"  # 图文混合
    COMMAND = "command"  # 命令（/xxx）
    UNKNOWN = "unknown"  # 未知类型


class MediaStatus(Enum):
    """媒体状态"""

    PENDING = "pending"  # 待下载
    DOWNLOADING = "downloading"  # 下载中
    READY = "ready"  # 已就绪
    FAILED = "failed"  # 失败
    PROCESSED = "processed"  # 已处理（如语音转文字）


@dataclass
class MediaFile:
    """
    媒体文件

    表示图片、语音、文件等媒体内容
    """

    id: str  # 媒体 ID
    filename: str  # 文件名
    mime_type: str  # MIME 类型
    size: int = 0  # 文件大小（字节）

    # 来源
    url: str | None = None  # 原始 URL（平台提供）
    file_id: str | None = None  # 平台文件 ID

    # 本地
    local_path: str | None = None  # 本地缓存路径
    status: MediaStatus = MediaStatus.PENDING

    # 处理结果
    transcription: str | None = None  # 语音转文字结果
    description: str | None = None  # 图片描述
    extracted_text: str | None = None  # 文件提取文本

    # 元数据
    duration: float | None = None  # 时长（音视频）
    width: int | None = None  # 宽度（图片/视频）
    height: int | None = None  # 高度（图片/视频）
    thumbnail_url: str | None = None  # 缩略图 URL
    extra: dict = None  # 平台特定的额外数据

    def __post_init__(self):
        """初始化后处理"""
        if self.extra is None:
            self.extra = {}

    @classmethod
    def create(
        cls,
        filename: str,
        mime_type: str,
        url: str | None = None,
        file_id: str | None = None,
        size: int = 0,
    ) -> "MediaFile":
        """创建媒体文件"""
        return cls(
            id=f"media_{uuid.uuid4().hex[:12]}",
            filename=filename,
            mime_type=mime_type,
            url=url,
            file_id=file_id,
            size=size,
        )

    @property
    def is_image(self) -> bool:
        return (self.mime_type or "").startswith("image/")

    @property
    def is_audio(self) -> bool:
        return (self.mime_type or "").startswith("audio/")

    @property
    def is_video(self) -> bool:
        return (self.mime_type or "").startswith("video/")

    @property
    def is_document(self) -> bool:
        return not (self.is_image or self.is_audio or self.is_video)

    @property
    def is_ready(self) -> bool:
        return self.status == MediaStatus.READY and self.local_path is not None

    @property
    def extension(self) -> str:
        """获取文件扩展名"""
        if "." in self.filename:
            return self.filename.rsplit(".", 1)[-1].lower()
        # 从 MIME 类型推断
        mime_to_ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "video/mp4": "mp4",
            "application/pdf": "pdf",
        }
        return mime_to_ext.get(self.mime_type, "bin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "url": self.url,
            "file_id": self.file_id,
            "local_path": self.local_path,
            "status": self.status.value,
            "transcription": self.transcription,
            "description": self.description,
            "extracted_text": self.extracted_text,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "thumbnail_url": self.thumbnail_url,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MediaFile":
        try:
            status = MediaStatus(data.get("status", "pending"))
        except (ValueError, KeyError):
            status = MediaStatus.PENDING
        return cls(
            id=data.get("id", f"media_{__import__('uuid').uuid4().hex[:12]}"),
            filename=data.get("filename", "unknown"),
            mime_type=data.get("mime_type") or "application/octet-stream",
            size=data.get("size", 0),
            url=data.get("url"),
            file_id=data.get("file_id"),
            local_path=data.get("local_path"),
            status=status,
            transcription=data.get("transcription"),
            description=data.get("description"),
            extracted_text=data.get("extracted_text"),
            duration=data.get("duration"),
            width=data.get("width"),
            height=data.get("height"),
            thumbnail_url=data.get("thumbnail_url"),
            extra=data.get("extra", {}),
        )


@dataclass
class MessageContent:
    """
    消息内容

    封装文本和媒体内容
    """

    text: str | None = None  # 文本内容
    images: list[MediaFile] = field(default_factory=list)  # 图片列表
    voices: list[MediaFile] = field(default_factory=list)  # 语音列表
    files: list[MediaFile] = field(default_factory=list)  # 文件列表
    videos: list[MediaFile] = field(default_factory=list)  # 视频列表

    # 特殊内容
    location: dict | None = None  # 位置 {lat, lng, name, address}
    sticker: dict | None = None  # 表情包 {id, emoji, set_name}

    @property
    def has_text(self) -> bool:
        return bool(self.text)

    @property
    def has_media(self) -> bool:
        return bool(self.images or self.voices or self.files or self.videos)

    @property
    def all_media(self) -> list[MediaFile]:
        """获取所有媒体文件"""
        return self.images + self.voices + self.files + self.videos

    @property
    def message_type(self) -> MessageType:
        """推断消息类型"""
        if self.has_text and self.has_media:
            return MessageType.MIXED
        if self.images:
            return MessageType.IMAGE
        if self.voices:
            return MessageType.VOICE
        if self.videos:
            return MessageType.VIDEO
        if self.files:
            return MessageType.FILE
        if self.location:
            return MessageType.LOCATION
        if self.sticker:
            return MessageType.STICKER
        if self.text:
            if self.text.startswith("/"):
                return MessageType.COMMAND
            return MessageType.TEXT
        return MessageType.UNKNOWN

    def to_plain_text(self) -> str:
        """
        转换为纯文本

        将媒体内容转换为描述性文本，用于发送给 LLM
        """
        parts = []

        if self.text:
            parts.append(self.text)

        for img in self.images:
            if img.description:
                parts.append(f"[图片: {img.description}]")
            else:
                parts.append(f"[图片: {img.filename}]")

        for voice in self.voices:
            if voice.transcription:
                parts.append(f"[语音转文字: {voice.transcription}]")
            else:
                parts.append(f"[语音: {voice.duration or '未知'}秒]")

        for video in self.videos:
            parts.append(f"[视频: {video.filename}, {video.duration or '未知'}秒]")

        for file in self.files:
            if file.extracted_text:
                parts.append(f"[文件内容: {file.extracted_text}]")
            else:
                parts.append(f"[文件: {file.filename}]")

        if self.location:
            parts.append(f"[位置: {self.location.get('name', '未知')}]")

        if self.sticker:
            parts.append(f"[表情: {self.sticker.get('emoji', '😀')}]")

        return "\n".join(parts) if parts else ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "images": [m.to_dict() for m in self.images],
            "voices": [m.to_dict() for m in self.voices],
            "files": [m.to_dict() for m in self.files],
            "videos": [m.to_dict() for m in self.videos],
            "location": self.location,
            "sticker": self.sticker,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageContent":
        return cls(
            text=data.get("text"),
            images=[MediaFile.from_dict(m) for m in data.get("images", [])],
            voices=[MediaFile.from_dict(m) for m in data.get("voices", [])],
            files=[MediaFile.from_dict(m) for m in data.get("files", [])],
            videos=[MediaFile.from_dict(m) for m in data.get("videos", [])],
            location=data.get("location"),
            sticker=data.get("sticker"),
        )

    @classmethod
    def text_only(cls, text: str) -> "MessageContent":
        """创建纯文本内容"""
        return cls(text=text)

    @classmethod
    def with_image(cls, image: MediaFile, caption: str | None = None) -> "MessageContent":
        """创建图片消息"""
        return cls(text=caption, images=[image])

    @classmethod
    def with_file(cls, file: MediaFile, caption: str | None = None) -> "MessageContent":
        """创建文件消息"""
        return cls(text=caption, files=[file])

    @classmethod
    def with_voice(cls, voice: MediaFile, caption: str | None = None) -> "MessageContent":
        """创建语音消息"""
        return cls(text=caption, voices=[voice])

    @classmethod
    def with_video(cls, video: MediaFile, caption: str | None = None) -> "MessageContent":
        """创建视频消息"""
        return cls(text=caption, videos=[video])


@dataclass
class UnifiedMessage:
    """
    统一消息格式（接收）

    将各平台消息转换为统一格式
    """

    id: str  # 消息 ID
    channel: str  # 来源通道
    channel_message_id: str  # 原始消息 ID

    # 发送者
    user_id: str  # 统一用户 ID
    channel_user_id: str  # 通道用户 ID

    # 聊天
    chat_id: str  # 聊天 ID（私聊/群组）
    chat_type: str = "private"  # 聊天类型: private/group/channel
    thread_id: str | None = None  # 话题/线程 ID

    # 内容
    message_type: MessageType = MessageType.TEXT
    content: MessageContent = field(default_factory=MessageContent)

    # 引用
    reply_to: str | None = None  # 回复的消息 ID
    forward_from: str | None = None  # 转发来源

    # 时间
    timestamp: datetime = field(default_factory=datetime.now)

    # @提及检测
    is_mentioned: bool = False
    is_direct_message: bool = False

    # 原始数据
    raw: dict = field(default_factory=dict)

    # 元数据
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        channel: str,
        channel_message_id: str,
        user_id: str,
        channel_user_id: str,
        chat_id: str,
        content: MessageContent,
        **kwargs,
    ) -> "UnifiedMessage":
        """创建统一消息"""
        return cls(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            channel=channel,
            channel_message_id=channel_message_id,
            user_id=user_id,
            channel_user_id=channel_user_id,
            chat_id=chat_id,
            message_type=content.message_type,
            content=content,
            **kwargs,
        )

    @property
    def text(self) -> str:
        """获取文本内容"""
        return self.content.text or ""

    @property
    def plain_text(self) -> str:
        """获取纯文本（包含媒体描述）"""
        return self.content.to_plain_text()

    @property
    def is_command(self) -> bool:
        """是否为命令"""
        return self.message_type == MessageType.COMMAND

    @property
    def command(self) -> str | None:
        """获取命令（不含 /）"""
        if self.is_command and self.text:
            parts = self.text[1:].split(maxsplit=1)
            return parts[0] if parts else None
        return None

    @property
    def command_args(self) -> str:
        """获取命令参数"""
        if self.is_command and self.text:
            parts = self.text[1:].split(maxsplit=1)
            return parts[1] if len(parts) > 1 else ""
        return ""

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "channel_message_id": self.channel_message_id,
            "user_id": self.user_id,
            "channel_user_id": self.channel_user_id,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "thread_id": self.thread_id,
            "message_type": self.message_type.value,
            "content": self.content.to_dict(),
            "reply_to": self.reply_to,
            "forward_from": self.forward_from,
            "timestamp": self.timestamp.isoformat(),
            "is_mentioned": self.is_mentioned,
            "is_direct_message": self.is_direct_message,
            "raw": self.raw,
            "metadata": self.metadata,
        }


@dataclass
class OutgoingMessage:
    """
    发送消息格式

    Agent 回复转换为此格式发送
    """

    chat_id: str  # 目标聊天 ID
    content: MessageContent  # 消息内容

    # 可选
    reply_to: str | None = None  # 回复消息 ID
    thread_id: str | None = None  # 话题/线程 ID

    # 格式
    parse_mode: str | None = None  # 解析模式: markdown/html
    disable_preview: bool = False  # 禁用链接预览
    silent: bool = False  # 静默发送（不通知）

    # 元数据
    metadata: dict = field(default_factory=dict)

    @classmethod
    def text(cls, chat_id: str, text: str, **kwargs) -> "OutgoingMessage":
        """创建纯文本消息"""
        return cls(
            chat_id=chat_id,
            content=MessageContent.text_only(text),
            **kwargs,
        )

    @classmethod
    def with_image(
        cls,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> "OutgoingMessage":
        """创建图片消息"""
        import mimetypes

        path = Path(image_path)
        mime_type = mimetypes.guess_type(str(path))[0] or f"image/{path.suffix[1:]}"
        media = MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
        media.local_path = str(path)
        media.status = MediaStatus.READY

        return cls(
            chat_id=chat_id,
            content=MessageContent.with_image(media, caption),
            **kwargs,
        )

    @classmethod
    def with_file(
        cls,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> "OutgoingMessage":
        """创建文件消息"""
        import mimetypes

        path = Path(file_path)
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        media = MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
        media.local_path = str(path)
        media.status = MediaStatus.READY

        return cls(
            chat_id=chat_id,
            content=MessageContent.with_file(media, caption),
            **kwargs,
        )

    @classmethod
    def with_voice(
        cls,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> "OutgoingMessage":
        """创建语音消息"""
        import mimetypes

        path = Path(voice_path)
        mime_type = mimetypes.guess_type(str(path))[0] or "audio/ogg"
        media = MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
        media.local_path = str(path)
        media.status = MediaStatus.READY

        return cls(
            chat_id=chat_id,
            content=MessageContent.with_voice(media, caption),
            **kwargs,
        )

    @classmethod
    def with_video(
        cls,
        chat_id: str,
        video_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> "OutgoingMessage":
        """创建视频消息"""
        import mimetypes

        path = Path(video_path)
        mime_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
        media = MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
        media.local_path = str(path)
        media.status = MediaStatus.READY

        return cls(
            chat_id=chat_id,
            content=MessageContent.with_video(media, caption),
            **kwargs,
        )

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "content": self.content.to_dict(),
            "reply_to": self.reply_to,
            "thread_id": self.thread_id,
            "parse_mode": self.parse_mode,
            "disable_preview": self.disable_preview,
            "silent": self.silent,
            "metadata": self.metadata,
        }
