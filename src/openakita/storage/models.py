"""
Data models
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    """A chat message."""

    id: int | None = None
    conversation_id: int | None = None
    role: str = "user"  # user, assistant, system
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


@dataclass
class Conversation:
    """A conversation thread."""

    id: int | None = None
    title: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    messages: list[Message] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class SkillRecord:
    """A skill installation record."""

    id: int | None = None
    name: str = ""
    version: str = ""
    source: str = ""  # github url, pip, local
    installed_at: datetime = field(default_factory=datetime.now)
    last_used: datetime | None = None
    use_count: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class MemoryEntry:
    """A memory entry."""

    id: int | None = None
    category: str = ""  # task, experience, discovery, error
    content: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    importance: int = 0  # 0-10
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class TaskRecord:
    """A task record."""

    id: int | None = None
    task_id: str = ""
    description: str = ""
    status: str = "pending"  # pending, in_progress, completed, failed
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class UserPreference:
    """A user preference."""

    key: str
    value: Any
    updated_at: datetime = field(default_factory=datetime.now)
