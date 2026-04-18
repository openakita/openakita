"""
Media processing module

Provides media file processing capabilities:
- Speech-to-text
- Image understanding
- File content extraction
"""

from .handler import MediaHandler
from .storage import MediaStorage

__all__ = [
    "MediaHandler",
    "MediaStorage",
]
