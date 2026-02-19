"""补充 storage.py 单元测试: 连接异常降级, FTS5 rebuild, 边界条件."""

import sqlite3
from datetime import datetime

import pytest

from openakita.memory.storage import MemoryStorage


@pytest.fixture
def storage(tmp_path):
    db = MemoryStorage(tmp_path / "test.db")
    yield db
    db.close()


class TestConnectionDegradation:
    def test_save_after_close(self, storage):
        storage.close()
        storage.save_memory({"id": "m1", "content": "test", "created_at": datetime.now().isoformat()})

    def test_query_after_close(self, storage):
        storage.close()
        result = storage.query(limit=10)
        assert result == []

    def test_get_memory_after_close(self, storage):
        storage.close()
        assert storage.get_memory("nonexistent") is None

    def test_search_fts_after_close(self, storage):
        storage.close()
        assert storage.search_fts("anything") == []

    def test_count_after_close(self, storage):
        storage.close()
        assert storage.count() == 0

    def test_save_attachment_after_close(self, storage):
        storage.close()
        storage.save_attachment({"id": "a1", "filename": "test.txt", "created_at": datetime.now().isoformat()})

    def test_search_attachments_after_close(self, storage):
        storage.close()
        assert storage.search_attachments(query="test") == []

    def test_get_attachment_after_close(self, storage):
        storage.close()
        assert storage.get_attachment("nonexistent") is None

    def test_save_episode_after_close(self, storage):
        storage.close()
        storage.save_episode({"id": "e1", "session_id": "s1", "summary": "test"})

    def test_save_scratchpad_after_close(self, storage):
        storage.close()
        storage.save_scratchpad({"user_id": "u1", "content": "test"})


class TestFTS5Rebuild:
    def test_rebuild_fts_index(self, storage):
        storage.save_memory({
            "id": "m1", "content": "Python 是编程语言",
            "type": "FACT", "created_at": datetime.now().isoformat(),
        })
        storage.rebuild_fts_index()
        results = storage.search_fts("Python")
        assert len(results) >= 1

    def test_rebuild_empty(self, storage):
        storage.rebuild_fts_index()


class TestSanitizeFtsQuery:
    def test_special_chars_cleaned(self, storage):
        result = storage._sanitize_fts_query('hello "world" (test)')
        assert '"' not in result
        assert '(' not in result
        assert ')' not in result

    def test_empty_query(self, storage):
        result = storage._sanitize_fts_query("")
        assert result == '""'

    def test_normal_query(self, storage):
        result = storage._sanitize_fts_query("Python 编程")
        assert "Python" in result
        assert "编程" in result


class TestSchemaVersion:
    def test_creates_all_tables(self, storage):
        tables = storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "memories" in table_names
        assert "episodes" in table_names
        assert "scratchpad" in table_names
        assert "conversation_turns" in table_names
        assert "extraction_queue" in table_names
        assert "embedding_cache" in table_names
        assert "attachments" in table_names

    def test_reinit_is_safe(self, tmp_path):
        db1 = MemoryStorage(tmp_path / "reopen.db")
        db1.save_memory({"id": "m1", "content": "test", "created_at": datetime.now().isoformat()})
        db1.close()

        db2 = MemoryStorage(tmp_path / "reopen.db")
        assert db2.get_memory("m1") is not None
        db2.close()


class TestExpiryCleanup:
    def test_cleanup_expired(self, storage):
        from datetime import timedelta
        past = (datetime.now() - timedelta(days=1)).isoformat()
        storage.save_memory({
            "id": "expired",
            "content": "old stuff",
            "created_at": past,
            "expires_at": past,
        })
        cleaned = storage.cleanup_expired()
        assert cleaned >= 1
        assert storage.get_memory("expired") is None

    def test_cleanup_not_expired(self, storage):
        from datetime import timedelta
        future = (datetime.now() + timedelta(days=30)).isoformat()
        storage.save_memory({
            "id": "fresh",
            "content": "new stuff",
            "created_at": datetime.now().isoformat(),
            "expires_at": future,
        })
        storage.cleanup_expired()
        assert storage.get_memory("fresh") is not None
