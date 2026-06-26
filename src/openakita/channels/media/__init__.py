"""
媒体处理模块

提供媒体文件处理能力:
- 语音转文字
- 图片理解
- 文件内容提取
"""

from .handler import MediaHandler
from .storage import MediaStorage

__all__ = [
    "MediaHandler",
    "MediaStorage",
]
