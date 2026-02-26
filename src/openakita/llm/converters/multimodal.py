"""
多模态内容转换器

负责在内部格式和各种外部格式之间转换图片、视频等多媒体内容。
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
    VideoBlock,
    VideoContent,
)

_converter_logger = _multimodal_logging.getLogger(__name__)

# 图片格式检测
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # WebP 以 RIFF 开头
}


def detect_media_type(data: bytes) -> str:
    """
    从二进制数据检测媒体类型

    Args:
        data: 二进制数据

    Returns:
        媒体类型字符串，如 "image/jpeg"
    """
    for signature, media_type in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return media_type

    # WebP 需要额外检查
    if len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"

    # 视频格式检测
    if data.startswith(b"\x00\x00\x00") and b"ftyp" in data[:12]:
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"

    # 默认为 JPEG
    return "image/jpeg"


def detect_media_type_from_base64(data: str) -> str:
    """从 base64 数据检测媒体类型"""
    try:
        decoded = base64.b64decode(data[:100])  # 只解码前 100 字节
        return detect_media_type(decoded)
    except Exception:
        return "image/jpeg"


def convert_image_to_openai(image: ImageContent) -> dict:
    """
    将内部图片格式转换为 OpenAI 格式

    内部格式:
    {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "..."
        }
    }

    OpenAI 格式:
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
    将 OpenAI 图片格式转换为内部格式

    支持两种输入:
    1. data URL: "data:image/jpeg;base64,..."
    2. 远程 URL: "https://..."
    """
    image_url = item.get("image_url", {})
    url = image_url.get("url", "")

    if not url:
        return None

    if url.startswith("data:"):
        # 解析 data URL
        match = re.match(r"data:([^;]+);base64,(.+)", url)
        if match:
            media_type = match.group(1)
            data = match.group(2)
            return ImageContent(media_type=media_type, data=data)
    else:
        # 远程 URL
        return ImageContent.from_url(url)

    return None


_DASHSCOPE_MAX_DATA_URI_BYTES = 10 * 1024 * 1024  # DashScope API 限制 10MB per data-uri
_KIMI_MAX_DATA_URI_BYTES = 10 * 1024 * 1024  # Kimi 保守按 10MB 限制


def _check_video_data_uri_size(video: VideoContent, provider_name: str, max_bytes: int) -> str | None:
    """检查视频 data URL 是否超过 provider 大小限制，超过则返回降级文本"""
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
            f"[视频内容：视频文件约 {size_mb:.1f}MB，编码后超过 {provider_name} "
            f"的 {limit_mb:.0f}MB data-uri 限制，已跳过。请发送更小的视频。]"
        )
    return None


def convert_video_to_kimi(video: VideoContent) -> dict:
    """
    将内部视频格式转换为 Kimi 格式

    Kimi 使用 video_url 类型（私有扩展）:
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
    将内部视频格式转换为 Gemini 格式

    Gemini 使用 inline_data 格式（通过 OpenAI 兼容层时可能透传）:
    {
        "type": "image_url",
        "image_url": {
            "url": "data:video/mp4;base64,..."
        }
    }

    注意: 通过 OpenAI 兼容层调用 Gemini 时，视频作为 data URL 传递
    大文件应使用 Gemini Files API（在 gemini_files.py 中实现）
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": video.to_data_url(),
        },
    }


def convert_video_to_dashscope(video: VideoContent) -> dict:
    """
    将内部视频格式转换为 DashScope (Qwen-VL) 格式

    DashScope Qwen-VL 使用 video_url 类型（同 Kimi 格式）:
    {
        "type": "video_url",
        "video_url": {
            "url": "data:video/mp4;base64,..."
        }
    }

    注意: DashScope 限制单个 data-uri 不超过 10MB
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
    将内部音频格式转换为 OpenAI input_audio 格式

    OpenAI 格式:
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
    将内部音频格式转换为 Gemini 格式（通过 OpenAI 兼容层）

    使用 data URL 传递，与图片/视频一致
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": audio.to_data_url(),
        },
    }


def convert_audio_to_dashscope(audio: AudioContent) -> dict:
    """
    将内部音频格式转换为 DashScope (Qwen-Audio) 格式

    DashScope 使用 audio_url:
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
    将内部文档格式转换为 Anthropic document 格式

    Anthropic 格式:
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
    将内部文档格式转换为 Gemini 格式

    通过 OpenAI 兼容层时使用 data URL
    """
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{document.media_type};base64,{document.data}",
        },
    }


# ── 策略表：按服务商分发多模态转换器 ──
# 每种媒体类型对应一个 provider -> converter 映射
# 不在表中的 provider 将走降级链

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
    """视频降级: 不支持视频的端点 → 文本描述"""
    _converter_logger.warning("Video content degraded to text (provider not supported)")
    return {"type": "text", "text": "[视频内容：该端点不支持视频输入，视频已被跳过]"}


def _degrade_audio(block: AudioBlock) -> dict:
    """音频降级: 不支持音频的端点 → 文本描述"""
    _converter_logger.warning("Audio content degraded to text (provider not supported)")
    return {"type": "text", "text": "[音频内容：该端点不支持音频输入，已跳过]"}


def _degrade_document(block: DocumentBlock) -> dict:
    """文档降级: 不支持文档的端点 → 文本描述"""
    fname = block.document.filename or "unknown"
    _converter_logger.warning(f"Document '{fname}' degraded to text (provider not supported)")
    return {"type": "text", "text": f"[文档内容：该端点不支持文档输入。文件名: {fname}]"}


def convert_content_blocks(
    blocks: list[ContentBlock],
    provider: str = "openai",
) -> str | list[dict]:
    """
    统一内容块转换器（策略表分发 + 优雅降级）

    根据 provider 从策略表中选择对应的转换器。
    如果 provider 不在策略表中，自动走降级链。

    降级链:
    - 视频不支持 → 文本描述 "[视频内容：该端点不支持视频输入]"
    - 音频不支持 → 文本描述 "[音频内容：该端点不支持音频输入]"
    - 文档不支持 → 文本描述 "[文档内容：该端点不支持文档输入]"

    Args:
        blocks: 内容块列表
        provider: 服务商标识

    Returns:
        如果只有一个文本块，返回字符串；否则返回列表
    """
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
        return blocks[0].text

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

    return result


# 向后兼容别名
convert_content_blocks_to_openai = convert_content_blocks


def has_images(content: str | list[ContentBlock]) -> bool:
    """检查内容是否包含图片"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, ImageBlock) for block in content)


def has_videos(content: str | list[ContentBlock]) -> bool:
    """检查内容是否包含视频"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, VideoBlock) for block in content)


def has_audio(content: str | list[ContentBlock]) -> bool:
    """检查内容是否包含音频"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, AudioBlock) for block in content)


def has_documents(content: str | list[ContentBlock]) -> bool:
    """检查内容是否包含文档"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, DocumentBlock) for block in content)


def extract_images(content: list[ContentBlock]) -> list[ImageContent]:
    """提取所有图片内容"""
    return [block.image for block in content if isinstance(block, ImageBlock)]


def extract_videos(content: list[ContentBlock]) -> list[VideoContent]:
    """提取所有视频内容"""
    return [block.video for block in content if isinstance(block, VideoBlock)]
