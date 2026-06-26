"""L1 Unit Tests: Storage data models (Message, Conversation, etc.)."""

import pytest
from datetime import datetime

from openakita.storage.models import (
    Message,
    Conversation,
    SkillRecord,
    MemoryEntry,
    TaskRecord,
    UserPreference,
)


class TestMessage:
    def test_default_message(self):
        m = Message()
        assert m.role == "user"
        assert m.content == ""

    def test_custom_message(self):
        m = Message(role="assistant", content="你好", metadata={"tokens": 5})
        assert m.role == "assistant"
        assert m.metadata["tokens"] == 5


class TestConversation:
    def test_default_conversation(self):
        c = Conversation()
        assert c.title == ""
        assert c.messages == []

    def test_with_messages(self):
        c = Conversation(
            title="Test",
            messages=[Message(content="hi"), Message(role="assistant", content="hello")],
        )
        assert len(c.messages) == 2


class TestSkillRecord:
    def test_skill_record(self):
        r = SkillRecord(name="web-search", version="1.0", source="github")
        assert r.name == "web-search"
        assert r.use_count == 0


class TestMemoryEntry:
    def test_memory_entry(self):
        e = MemoryEntry(category="user_preference", content="用户喜欢Python", importance=3)
        assert e.importance == 3
        assert e.tags == []


class TestTaskRecord:
    def test_task_record(self):
        r = TaskRecord(task_id="t1", description="Do something")
        assert r.status == "pending"
        assert r.attempts == 0


class TestUserPreference:
    def test_preference(self):
        p = UserPreference(key="theme", value="dark")
        assert p.key == "theme"
        assert p.value == "dark"
