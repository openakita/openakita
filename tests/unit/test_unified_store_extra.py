"""补充 unified_store 测试: find_similar 去重检测."""

import pytest
from datetime import datetime

from openakita.memory.types import MemoryType, SemanticMemory
from openakita.memory.unified_store import UnifiedStore


@pytest.fixture
def store(tmp_path):
    s = UnifiedStore(tmp_path / "test.db")
    yield s
    s.close()


class TestFindSimilar:
    def test_find_by_subject_and_predicate(self, store):
        store.save_semantic(SemanticMemory(
            content="用户喜欢深色主题",
            type=MemoryType.PREFERENCE,
            subject="用户",
            predicate="主题偏好",
        ))
        result = store.find_similar("用户", "主题偏好")
        assert result is not None
        assert "深色主题" in result.content

    def test_find_no_match(self, store):
        store.save_semantic(SemanticMemory(
            content="项目使用 Python",
            type=MemoryType.FACT,
            subject="项目",
            predicate="语言",
        ))
        result = store.find_similar("用户", "名字")
        assert result is None

    def test_find_case_insensitive_predicate(self, store):
        store.save_semantic(SemanticMemory(
            content="时区是 UTC+8",
            type=MemoryType.FACT,
            subject="用户",
            predicate="TimeZone",
        ))
        result = store.find_similar("用户", "timezone")
        assert result is not None

    def test_find_empty_store(self, store):
        result = store.find_similar("any", "thing")
        assert result is None


class TestLoadAllMemories:
    def test_load_all(self, store):
        for i in range(5):
            store.save_semantic(SemanticMemory(
                content=f"Fact number {i}", type=MemoryType.FACT,
            ))
        all_mems = store.load_all_memories()
        assert len(all_mems) == 5

    def test_load_all_empty(self, store):
        assert store.load_all_memories() == []


class TestCountMemories:
    def test_count_total(self, store):
        store.save_semantic(SemanticMemory(content="a", type=MemoryType.FACT))
        store.save_semantic(SemanticMemory(content="b", type=MemoryType.PREFERENCE))
        assert store.count_memories() == 2

    def test_count_by_type(self, store):
        store.save_semantic(SemanticMemory(content="a", type=MemoryType.FACT))
        store.save_semantic(SemanticMemory(content="b", type=MemoryType.PREFERENCE))
        assert store.count_memories("fact") >= 1


class TestGetStats:
    def test_stats_structure(self, store):
        stats = store.get_stats()
        assert "memory_count" in stats
        assert "search_backend" in stats
        assert "search_available" in stats
        assert stats["search_backend"] == "fts5"
        assert stats["search_available"] is True
