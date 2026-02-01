"""
多模态内容转换器

负责在内部格式和各种外部格式之间转换图片、视频等多媒体内容。
"""

import base64
import re
from typing import Optional, Union

from ..types import (
    ContentBlock,
    TextBlock,
    ImageBlock,
    VideoBlock,
    ImageContent,
    VideoContent,
    UnsupportedMediaError,
)


# 图片格式检测
IMAGE_SIGNATURES = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
    b'RIFF': 'image/webp',  # WebP 以 RIFF 开头
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
    if len(data) > 12 and data[8:12] == b'WEBP':
        return 'image/webp'
    
    # 视频格式检测
    if data.startswith(b'\x00\x00\x00') and b'ftyp' in data[:12]:
        return 'video/mp4'
    if data.startswith(b'\x1a\x45\xdf\xa3'):
        return 'video/webm'
    
    # 默认为 JPEG
    return 'image/jpeg'


def detect_media_type_from_base64(data: str) -> str:
    """从 base64 数据检测媒体类型"""
    try:
        decoded = base64.b64decode(data[:100])  # 只解码前 100 字节
        return detect_media_type(decoded)
    except Exception:
        return 'image/jpeg'


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
        }
    }


def convert_openai_image_to_internal(item: dict) -> Optional[ImageContent]:
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
        match = re.match(r'data:([^;]+);base64,(.+)', url)
        if match:
            media_type = match.group(1)
            data = match.group(2)
            return ImageContent(media_type=media_type, data=data)
    else:
        # 远程 URL
        return ImageContent.from_url(url)
    
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
    return {
        "type": "video_url",
        "video_url": {
            "url": video.to_data_url(),
        }
    }


def convert_video_to_gemini(video: VideoContent) -> dict:
    """
    将内部视频格式转换为 Gemini 格式
    
    注意：Gemini 的视频格式可能与 Kimi 不同，需要根据实际 API 文档调整
    """
    # TODO: 根据 Gemini API 文档实现
    return convert_video_to_kimi(video)


def convert_content_blocks_to_openai(
    blocks: list[ContentBlock],
    provider: str = "openai",
) -> Union[str, list[dict]]:
    """
    将内容块列表转换为 OpenAI 格式
    
    Args:
        blocks: 内容块列表
        provider: 服务商（影响视频处理方式）
        
    Returns:
        如果只有一个文本块，返回字符串；否则返回列表
    """
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
        return blocks[0].text
    
    result = []
    for block in blocks:
        if isinstance(block, TextBlock):
            result.append({
                "type": "text",
                "text": block.text,
            })
        elif isinstance(block, ImageBlock):
            result.append(convert_image_to_openai(block.image))
        elif isinstance(block, VideoBlock):
            if provider == "moonshot":
                result.append(convert_video_to_kimi(block.video))
            elif provider == "google":
                result.append(convert_video_to_gemini(block.video))
            else:
                raise UnsupportedMediaError(
                    f"Provider '{provider}' does not support video content"
                )
    
    return result


def has_images(content: Union[str, list[ContentBlock]]) -> bool:
    """检查内容是否包含图片"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, ImageBlock) for block in content)


def has_videos(content: Union[str, list[ContentBlock]]) -> bool:
    """检查内容是否包含视频"""
    if isinstance(content, str):
        return False
    return any(isinstance(block, VideoBlock) for block in content)


def extract_images(content: list[ContentBlock]) -> list[ImageContent]:
    """提取所有图片内容"""
    return [
        block.image for block in content
        if isinstance(block, ImageBlock)
    ]


def extract_videos(content: list[ContentBlock]) -> list[VideoContent]:
    """提取所有视频内容"""
    return [
        block.video for block in content
        if isinstance(block, VideoBlock)
    ]
