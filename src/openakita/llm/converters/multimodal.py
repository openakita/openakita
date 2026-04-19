"""
Multimodal content converter

Converts multimedia content such as images and videos between the internal
format and various external formats.
"""

import base64
import logging as _multimodal_logging
import re

from ..types import (
    AudioBlock,
    AudioContent,
    ContentBlock,
    DocumentBlock,
    DocumentContent,
    ImageBlock,
    ImageContent,
    TextBlock,
    ThinkingBlock,
    VideoBlock,
    VideoContent,
)

_converter_logger = _multimodal_logging.getLogger(__name__)

# Image format detection
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # WebP starts with RIFF
}


def detect_media_type(data: bytes) -> str:
    """
    Detect the media type from binary data.

    Args:
        data: binary data

    Returns:
        Media type string, e.g. "image/jpeg"
    """
    for signature, media_type in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return media_type

    # WebP needs an extra check
    if len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"

    # Video format detection
    if data.startswith(b"\x00\x00\x00") and b"ftyp" in data[:12]:
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"

    # Default to JPEG
    return "image/jpeg"


def detect_media_type_from_base64(data: str) -> str:
    """Detect the media type from base64-encoded data."""
    try:
        decoded = base64.b64decode(data[:100])  # Only decode the first 100 bytes
        return detect_media_type(decoded)
    except Exception:
        return "image/jpeg"


def convert_image_to_openai(image: ImageContent) -> dict:
    """
    Convert the internal image format to OpenAI format.

    Internal format:
    {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "..."
        }
    }

    OpenAI format:
    {
        "type": "image_url",
        "image_url": {
            "url": "data:image/jpeg;base64,..."
        }
    }
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": image.to_data_url(),
        },
    }


def convert_openai_image_to_internal(item: dict) -> ImageContent | None:
    """
    Convert the OpenAI image format to the internal format.

    Supports two input forms:
    1. data URL: "data:image/jpeg;base64,..."
    2. Remote URL: "https://..."
    """
    image_url = item.get("image_url", {})
    url = image_url.get("url", "")

    if not url:
        return None

    if url.startswith("data:"):
        # Parse the data URL
        match = re.match(r"data:([^;]+);base64,(.+)", url)
        if match:
            media_type = match.group(1)
            data = match.group(2)
            return ImageContent(media_type=media_type, data=data)
    else:
        # Remote URL
        return ImageContent.from_url(url)

    return None


_DASHSCOPE_MAX_DATA_URI_BYTES = 10 * 1024 * 1024  # DashScope API limit: 10MB per data-uri
_KIMI_MAX_DATA_URI_BYTES = 10 * 1024 * 1024  # Kimi: conservatively capped at 10MB


def _check_video_data_uri_size(
    video: VideoContent, provider_name: str, max_bytes: int
) -> str | None:
    """Check whether the video data URL exceeds the provider size limit; if so return a degraded text."""
    if video.media_type == "url":
        return None
    data_url = video.to_data_url()
    data_url_bytes = len(data_url.encode("utf-8"))
    if data_url_bytes > max_bytes:
        size_mb = len(video.data) * 3 / 4 / 1024 / 1024
        limit_mb = max_bytes / 1024 / 1024
        _converter_logger.warning(
            f"Video data-uri too large for {provider_name}: "
            f"{data_url_bytes / 1024 / 1024:.1f}MB > {limit_mb:.0f}MB limit. "
            f"Degrading to text."
        )
        return (
            f"[Video content: the video file is about {size_mb:.1f}MB, which after encoding "
            f"exceeds {provider_name}'s {limit_mb:.0f}MB data-uri limit and has been skipped. "
            f"Please send a smaller video.]"
        )
    return None


def convert_video_to_kimi(video: VideoContent) -> dict:
    """
    Convert the internal video format to Kimi format.

    Kimi uses the video_url type (a private extension):
    {
        "type": "video_url",
        "video_url": {
            "url": "data:video/mp4;base64,..."
        }
    }
    """
    degraded = _check_video_data_uri_size(video, "Kimi", _KIMI_MAX_DATA_URI_BYTES)
    if degraded:
        return {"type": "text", "text": degraded}
    return {
        "type": "video_url",
        "video_url": {
            "url": video.to_data_url(),
        },
    }


def convert_video_to_gemini(video: VideoContent) -> dict:
    """
    Convert the internal video format to Gemini format.

    Gemini uses the inline_data format (may be passed through when going via the OpenAI compatibility layer):
    {
        "type": "image_url",
        "image_url": {
            "url": "data:video/mp4;base64,..."
        }
    }

    Note: when calling Gemini through the OpenAI compatibility layer, videos are passed as data URLs.
    Large files should use the Gemini Files API (implemented in gemini_files.py).
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": video.to_data_url(),
        },
    }


def convert_video_to_dashscope(video: VideoContent) -> dict:
    """
    Convert the internal video format to DashScope (Qwen-VL) format.

    DashScope Qwen-VL uses the video_url type (same shape as Kimi):
    {
        "type": "video_url",
        "video_url": {
            "url": "data:video/mp4;base64,..."
        }
    }

    Note: DashScope limits a single data-uri to at most 10MB.
    """
    degraded = _check_video_data_uri_size(video, "DashScope", _DASHSCOPE_MAX_DATA_URI_BYTES)
    if degraded:
        return {"type": "text", "text": degraded}
    return {
        "type": "video_url",
        "video_url": {
            "url": video.to_data_url(),
        },
    }


def convert_audio_to_openai(audio: AudioContent) -> dict:
    """
    Convert the internal audio format to OpenAI's input_audio format.

    OpenAI format:
    {
        "type": "input_audio",
        "input_audio": {
            "data": "<base64>",
            "format": "wav"
        }
    }
    """
    return {
        "type": "input_audio",
        "input_audio": {
            "data": audio.data,
            "format": audio.format or "wav",
        },
    }


def convert_audio_to_gemini(audio: AudioContent) -> dict:
    """
    Convert the internal audio format to Gemini format (via the OpenAI compatibility layer).

    Passed as a data URL, consistent with image/video.
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": audio.to_data_url(),
        },
    }


def convert_audio_to_dashscope(audio: AudioContent) -> dict:
    """
    Convert the internal audio format to DashScope (Qwen-Audio) format.

    DashScope uses audio_url:
    {
        "type": "audio_url",
        "audio_url": {
            "url": "data:audio/wav;base64,..."
        }
    }
    """
    return {
        "type": "audio_url",
        "audio_url": {
            "url": audio.to_data_url(),
        },
    }


def convert_document_to_anthropic(document: DocumentContent) -> dict:
    """
    Convert the internal document format to the Anthropic document format.

    Anthropic format:
    {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "..."
        }
    }
    """
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": document.media_type,
            "data": document.data,
        },
    }


def convert_document_to_gemini(document: DocumentContent) -> dict:
    """
    Convert the internal document format to Gemini format.

    Uses a data URL when going through the OpenAI compatibility layer.
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{document.media_type};base64,{document.data}",
        },
    }


# -- Strategy tables: dispatch multimodal converters by provider --
# Each media type has its own provider -> converter mapping.
# Providers not in the table fall through the degradation chain.

VIDEO_CONVERTERS: dict[str, object] = {
    "moonshot": convert_video_to_kimi,
    "google": convert_video_to_gemini,
    "dashscope": convert_video_to_dashscope,
}

AUDIO_CONVERTERS: dict[str, object] = {
    "openai": convert_audio_to_openai,
    "google": convert_audio_to_gemini,
    "dashscope": convert_audio_to_dashscope,
}

DOCUMENT_CONVERTERS: dict[str, object] = {
    "anthropic": convert_document_to_anthropic,
    "google": convert_document_to_gemini,
}


def _degrade_video(block: VideoBlock) -> dict:
    """Video degradation: endpoints that don't support video -> text description."""
    _converter_logger.warning("Video content degraded to text (provider not supported)")
    return {"type": "text", "text": "[Video content: this endpoint does not support video input; the video has been skipped]"}


def _degrade_audio(block: AudioBlock) -> dict:
    """Audio degradation: endpoints that don't support audio -> text description."""
    _converter_logger.warning("Audio content degraded to text (provider not supported)")
    return {"type": "text", "text": "[Audio content: this endpoint does not support audio input; skipped]"}


def _degrade_document(block: DocumentBlock) -> dict:
    """Document degradation: endpoints that don't support documents -> text description."""
    fname = block.document.filename or "unknown"
    _converter_logger.warning(f"Document '{fname}' degraded to text (provider not supported)")
    return {"type": "text", "text": f"[Document content: this endpoint does not support document input. Filename: {fname}]"}


def convert_content_blocks(
    blocks: list[ContentBlock],
    provider: str = "openai",
) -> str | list[dict]:
    """
    Unified content-block converter (strategy-table dispatch + graceful degradation).

    Selects a converter from the strategy table based on the provider.
    If the provider is not in the strategy table, automatically falls through the degradation chain.

    Degradation chain:
    - Video unsupported -> text description "[Video content: this endpoint does not support video input]"
    - Audio unsupported -> text description "[Audio content: this endpoint does not support audio input]"
    - Document unsupported -> text description "[Document content: this endpoint does not support document input]"

    Args:
        blocks: list of content blocks
        provider: provider identifier

    Returns:
        A string if there is only a single text block; otherwise a list.
    """
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
        return blocks[0].text

    if len(blocks) == 1 and isinstance(blocks[0], dict) and blocks[0].get("type") == "text":
        return blocks[0].get("text", "")

    result = []
    for block in blocks:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})

        elif isinstance(block, ImageBlock):
            result.append(convert_image_to_openai(block.image))

        elif isinstance(block, VideoBlock):
            converter = VIDEO_CONVERTERS.get(provider)
            if converter:
                result.append(converter(block.video))
            else:
                result.append(_degrade_video(block))

        elif isinstance(block, AudioBlock):
            converter = AUDIO_CONVERTERS.get(provider)
            if converter:
                result.append(converter(block.audio))
            else:
                result.append(_degrade_audio(block))

        elif isinstance(block, DocumentBlock):
            converter = DOCUMENT_CONVERTERS.get(provider)
            if converter:
                result.append(converter(block.document))
            else:
                result.append(_degrade_document(block))

        elif isinstance(block, ThinkingBlock):
            pass

        elif isinstance(block, dict):
            result.append(block)

    if not result:
        return ""

    return result


# Backward-compatible alias
convert_content_blocks_to_openai = convert_content_blocks


def has_images(content: str | list[ContentBlock]) -> bool:
    """Check whether the content contains images."""
    if isinstance(content, str):
        return False
    return any(isinstance(block, ImageBlock) for block in content)


def has_videos(content: str | list[ContentBlock]) -> bool:
    """Check whether the content contains videos."""
    if isinstance(content, str):
        return False
    return any(isinstance(block, VideoBlock) for block in content)


def has_audio(content: str | list[ContentBlock]) -> bool:
    """Check whether the content contains audio."""
    if isinstance(content, str):
        return False
    return any(isinstance(block, AudioBlock) for block in content)


def has_documents(content: str | list[ContentBlock]) -> bool:
    """Check whether the content contains documents."""
    if isinstance(content, str):
        return False
    return any(isinstance(block, DocumentBlock) for block in content)


def extract_images(content: list[ContentBlock]) -> list[ImageContent]:
    """Extract all image content."""
    return [block.image for block in content if isinstance(block, ImageBlock)]


def extract_videos(content: list[ContentBlock]) -> list[VideoContent]:
    """Extract all video content."""
    return [block.video for block in content if isinstance(block, VideoBlock)]
