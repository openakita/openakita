"""L2 Component Tests: MemoryManager add/search/inject operations."""

import pytest
from pathlib import Path

from openakita.memory.types import Memory, MemoryPriority, MemoryType


@pytest.fixture
def memory_dir(tmp_workspace):
    mem_dir = tmp_workspace / "data" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    memory_md = tmp_workspace / "identity" / "MEMORY.md"
    memory_md.parent.mkdir(parents=True, exist_ok=True)
    memory_md.write_text("# Memory\n", encoding="utf-8")
    return mem_dir, memory_md


@pytest.fixture
def memory_manager(memory_dir, mock_brain):
    from openakita.memory.manager import MemoryManager
    mem_dir, memory_md = memory_dir
    return MemoryManager(
        data_dir=mem_dir,
        memory_md_path=memory_md,
        brain=mock_brain,
    )


class TestMemoryManagerAdd:
    def test_add_memory(self, memory_manager):
        mem = Memory(content="User likes Python", type=MemoryType.PREFERENCE)
        mid = memory_manager.add_memory(mem)
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_add_multiple_memories(self, memory_manager):
        ids = []
        for i in range(5):
            mem = Memory(content=f"Fact {i}", type=MemoryType.FACT)
            ids.append(memory_manager.add_memory(mem))
        assert len(set(ids)) == 5

    def test_get_memory_by_id(self, memory_manager):
        mem = Memory(content="retrievable fact", type=MemoryType.FACT)
        mid = memory_manager.add_memory(mem)
        retrieved = memory_manager.get_memory(mid)
        assert retrieved is not None
        assert retrieved.content == "retrievable fact"

    def test_get_nonexistent_memory(self, memory_manager):
        result = memory_manager.get_memory("nonexistent-id")
        assert result is None


class TestMemoryManagerSearch:
    def test_search_by_query(self, memory_manager):
        memory_manager.add_memory(Memory(content="Python is great", type=MemoryType.FACT))
        memory_manager.add_memory(Memory(content="Java is verbose", type=MemoryType.FACT))
        results = memory_manager.search_memories(query="Python")
        assert len(results) >= 1
        assert any("Python" in r.content for r in results)

    def test_search_by_type(self, memory_manager):
        memory_manager.add_memory(Memory(content="likes coffee", type=MemoryType.PREFERENCE))
        memory_manager.add_memory(Memory(content="sky is blue", type=MemoryType.FACT))
        results = memory_manager.search_memories(memory_type=MemoryType.PREFERENCE)
        assert all(r.type == MemoryType.PREFERENCE for r in results)

    def test_search_with_limit(self, memory_manager):
        for i in range(10):
            memory_manager.add_memory(Memory(content=f"item {i}"))
        results = memory_manager.search_memories(limit=3)
        assert len(results) <= 3

    def test_search_empty_store(self, memory_manager):
        results = memory_manager.search_memories(query="anything")
        assert results == []


class TestMemoryManagerDelete:
    def test_delete_memory(self, memory_manager):
        mem = Memory(content="to be deleted")
        mid = memory_manager.add_memory(mem)
        result = memory_manager.delete_memory(mid)
        assert result is True
        assert memory_manager.get_memory(mid) is None

    def test_delete_nonexistent(self, memory_manager):
        result = memory_manager.delete_memory("no-such-id")
        assert result is False


class TestMemoryManagerStats:
    def test_get_stats(self, memory_manager):
        memory_manager.add_memory(Memory(content="a", type=MemoryType.FACT))
        memory_manager.add_memory(Memory(content="b", type=MemoryType.PREFERENCE))
        stats = memory_manager.get_stats()
        assert isinstance(stats, dict)
        assert stats.get("total", 0) >= 2


class TestMemoryManagerInjection:
    def test_get_injection_context(self, memory_manager):
        memory_manager.add_memory(Memory(content="User birthday is March 15"))
        ctx = memory_manager.get_injection_context(task_description="greeting")
        assert isinstance(ctx, str)

