"""
格式转换器

负责在内部格式（Anthropic-like）和各种外部格式之间转换。
"""

from .messages import (
    convert_messages_to_openai,
    convert_messages_from_openai,
    convert_system_to_openai,
)
from .tools import (
    convert_tools_to_openai,
    convert_tool_calls_from_openai,
    convert_tool_result_to_openai,
)
from .multimodal import (
    convert_image_to_openai,
    convert_video_to_kimi,
    convert_content_blocks_to_openai,
    detect_media_type,
)

__all__ = [
    # Messages
    "convert_messages_to_openai",
    "convert_messages_from_openai",
    "convert_system_to_openai",
    # Tools
    "convert_tools_to_openai",
    "convert_tool_calls_from_openai",
    "convert_tool_result_to_openai",
    # Multimodal
    "convert_image_to_openai",
    "convert_video_to_kimi",
    "convert_content_blocks_to_openai",
    "detect_media_type",
]
