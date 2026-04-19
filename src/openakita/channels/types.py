"""
Unified message type definition

Defines cross-platform common message formats:
- UnifiedMessage: Received message
- OutgoingMessage: Sent message
- MessageContent: Message content (text/media)
- MediaFile: Media file
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class MessageType(Enum):
    """Message types"""

    TEXT = "text"  # Plain text
    IMAGE = "image"  # Image
    VOICE = "voice"  # Voice
    FILE = "file"  # File
    VIDEO = "video"  # Video
    LOCATION = "location"  # Location
    STICKER = "sticker"  # Sticker
    MIXED = "mixed"  # Mixed text and image
    COMMAND = "command"  # Command (/xxx)
    UNKNOWN = "unknown"  # Unknown type


VOICE_STT_FAILURES = frozenset({
    "[voice recognition failed]",
    "[voice processing timeout]",
    "[voice processing failed]",
})


def is_voice_stt_failed(transcription: str | None) -> bool:
    """Check if voice transcription result is a failure marker"""
    return not transcription or transcription in VOICE_STT_FAILURES


class MediaStatus(Enum):
    """Media status"""

    PENDING = "pending"  # Pending download
    DOWNLOADING = "downloading"  # Downloading
    READY = "ready"  # Ready
    FAILED = "failed"  # Failed
    PROCESSED = "processed"  # Processed (e.g., voice to text)


@dataclass
class MediaFile:
    """
    Media file

    Represents media content such as images, voice, files, etc.
    """

    id: str  # Media ID
    filename: str  # Filename
    mime_type: str  # MIME type
    size: int = 0  # File size (bytes)

    # Source
    url: str | None = None  # Original URL (provided by platform)
    file_id: str | None = None  # Platform file ID

    # Local
    local_path: str | None = None  # Local cache path
    status: MediaStatus = MediaStatus.PENDING

    # Processing results
    transcription: str | None = None  # Voice to text result
    description: str | None = None  # Image description
    extracted_text: str | None = None  # File extracted text

    # Metadata
    duration: float | None = None  # Duration (audio/video)
    width: int | None = None  # Width (image/video)
    height: int | None = None  # Height (image/video)
    thumbnail_url: str | None = None  # Thumbnail URL
    extra: dict = None  # Platform-specific extra data

    def __post_init__(self):
        """Post-initialization processing"""
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
        """Create media file"""
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
        """Get file extension"""
        if "." in self.filename:
            return self.filename.rsplit(".", 1)[-1].lower()
        # Infer from MIME type
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
    Message content

    Encapsulates text and media content
    """

    text: str | None = None  # Text content
    images: list[MediaFile] = field(default_factory=list)  # Image list
    voices: list[MediaFile] = field(default_factory=list)  # Voice list
    files: list[MediaFile] = field(default_factory=list)  # File list
    videos: list[MediaFile] = field(default_factory=list)  # Video list

    # Special content
    location: dict | None = None  # Location {lat, lng, name, address}
    sticker: dict | None = None  # Sticker {id, emoji, set_name}

    @property
    def has_text(self) -> bool:
        return bool(self.text)

    @property
    def has_media(self) -> bool:
        return bool(self.images or self.voices or self.files or self.videos)

    @property
    def all_media(self) -> list[MediaFile]:
        """Get all media files"""
        return self.images + self.voices + self.files + self.videos

    @property
    def message_type(self) -> MessageType:
        """Infer message type"""
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
        Convert to plain text

        Converts media content to descriptive text for sending to LLM
        """
        parts = []

        if self.text:
            parts.append(self.text)

        for img in self.images:
            if img.description:
                parts.append(f"[Image: {img.description}]")
            else:
                parts.append(f"[Image: {img.filename}]")

        for voice in self.voices:
            if not is_voice_stt_failed(voice.transcription):
                parts.append(f"[Voice to text: {voice.transcription}]")
            else:
                dur = f"{voice.duration}s" if voice.duration else "unknown duration"
                parts.append(f"[Voice message: {dur}, transcription failed]")

        for video in self.videos:
            parts.append(f"[Video: {video.filename}, {video.duration or 'unknown'}s]")

        for file in self.files:
            if file.extracted_text:
                parts.append(f"[File content: {file.extracted_text}]")
            else:
                parts.append(f"[File: {file.filename}]")

        if self.location:
            parts.append(f"[Location: {self.location.get('name', 'unknown')}]")

        if self.sticker:
            parts.append(f"[Sticker: {self.sticker.get('emoji', '😀')}]")

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
        """Create plain text content"""
        return cls(text=text)

    @classmethod
    def with_image(cls, image: MediaFile, caption: str | None = None) -> "MessageContent":
        """Create image message"""
        return cls(text=caption, images=[image])

    @classmethod
    def with_file(cls, file: MediaFile, caption: str | None = None) -> "MessageContent":
        """Create file message"""
        return cls(text=caption, files=[file])

    @classmethod
    def with_voice(cls, voice: MediaFile, caption: str | None = None) -> "MessageContent":
        """Create voice message"""
        return cls(text=caption, voices=[voice])

    @classmethod
    def with_video(cls, video: MediaFile, caption: str | None = None) -> "MessageContent":
        """Create video message"""
        return cls(text=caption, videos=[video])


@dataclass
class UnifiedMessage:
    """
    Unified message format (received)

    Converts messages from various platforms to unified format
    """

    id: str  # Message ID
    channel: str  # Source channel
    channel_message_id: str  # Original message ID

    # Sender
    user_id: str  # Unified user ID
    channel_user_id: str  # Channel user ID

    # Chat
    chat_id: str  # Chat ID (private/group)
    chat_type: str = "private"  # Chat type: private/group/channel
    thread_id: str | None = None  # Topic/thread ID

    # Content
    message_type: MessageType = MessageType.TEXT
    content: MessageContent = field(default_factory=MessageContent)

    # References
    reply_to: str | None = None  # ID of replied message
    forward_from: str | None = None  # Forward source

    # Time
    timestamp: datetime = field(default_factory=datetime.now)

    # @mention detection
    is_mentioned: bool = False
    is_direct_message: bool = False

    # Raw data
    raw: dict = field(default_factory=dict)

    # Metadata
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
        """Create unified message"""
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
        """Get text content"""
        return self.content.text or ""

    @property
    def plain_text(self) -> str:
        """Get plain text (including media descriptions)"""
        return self.content.to_plain_text()

    @property
    def is_command(self) -> bool:
        """Is it a command"""
        return self.message_type == MessageType.COMMAND

    @property
    def command(self) -> str | None:
        """Get command (without /)"""
        if self.is_command and self.text:
            parts = self.text[1:].split(maxsplit=1)
            return parts[0] if parts else None
        return None

    @property
    def command_args(self) -> str:
        """Get command arguments"""
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
    Outgoing message format

    Agent replies converted to this format for sending
    """

    chat_id: str  # Target chat ID
    content: MessageContent  # Message content

    # Optional
    reply_to: str | None = None  # Reply message ID
    thread_id: str | None = None  # Topic/thread ID

    # Format
    parse_mode: str | None = None  # Parse mode: markdown/html
    disable_preview: bool = False  # Disable link preview
    silent: bool = False  # Silent send (no notification)

    # Metadata
    metadata: dict = field(default_factory=dict)

    @classmethod
    def text(cls, chat_id: str, text: str, **kwargs) -> "OutgoingMessage":
        """Create plain text message"""
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
        """Create image message"""
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
        """Create file message"""
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
        """Create voice message"""
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
        """Create video message"""
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
