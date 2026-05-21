"""
OpenAkita 存储模块
"""

from .database import Database
from .degraded import DegradedRegistry
from .degraded import registry as degraded_registry
from .models import Conversation, MemoryEntry, Message, SkillRecord
from .safe_sqlite import (
    SQLiteUnavailable,
    quick_check_or_raise_async,
    quick_check_or_raise_sync,
    safe_open_async,
    safe_open_async_ctx,
    safe_open_sync,
)

__all__ = [
    "Database",
    "Conversation",
    "Message",
    "SkillRecord",
    "MemoryEntry",
    "SQLiteUnavailable",
    "safe_open_sync",
    "safe_open_async",
    "safe_open_async_ctx",
    "quick_check_or_raise_sync",
    "quick_check_or_raise_async",
    "DegradedRegistry",
    "degraded_registry",
]

