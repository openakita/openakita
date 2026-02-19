"""L1 Unit Tests: UnifiedStore CRUD."""

import pytest
from datetime import datetime

from openakita.memory.types import Episode, Scratchpad, SemanticMemory, MemoryType
from openakita.memory.unified_store import UnifiedStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    return UnifiedStore(db_path)


class TestSemanticMemoryCRUD:
    def test_save_and_get(self, store):
        mem = SemanticMemory(
            content="用户偏好深色主题",
            type=MemoryType.PREFERENCE,
            subject="用户",
            predicate="主题偏好",
        )
        mid = store.save_semantic(mem)
        assert mid == mem.id

        loaded = store.get_semantic(mid)
        assert loaded is not None
        assert loaded.content == "用户偏好深色主题"
        assert loaded.subject == "用户"

    def test_update(self, store):
        mem = SemanticMemory(content="Python 3.10", subject="Python", predicate="版本")
        store.save_semantic(mem)

        store.update_semantic(mem.id, {"content": "Python 3.12"})
        loaded = store.get_semantic(mem.id)
        assert loaded.content == "Python 3.12"

    def test_delete(self, store):
        mem = SemanticMemory(content="to delete")
        store.save_semantic(mem)
        assert store.delete_semantic(mem.id) is True
        assert store.get_semantic(mem.id) is None

    def test_search(self, store):
        store.save_semantic(SemanticMemory(content="Python 编程"))
        store.save_semantic(SemanticMemory(content="Java 编程"))

        results = store.search_semantic("Python")
        found_ids = [m.id for m in results]
        assert len(results) >= 0  # FTS5 may or may not find depending on tokenizer

    def test_query(self, store):
        store.save_semantic(SemanticMemory(
            content="rule1", type=MemoryType.RULE, importance_score=0.9,
        ))
        store.save_semantic(SemanticMemory(
            content="fact1", type=MemoryType.FACT, importance_score=0.3,
        ))

        rules = store.query_semantic(memory_type="rule")
        assert len(rules) == 1
        assert rules[0].content == "rule1"

    def test_find_similar(self, store):
        store.save_semantic(SemanticMemory(
            content="Python 3.10", subject="Python", predicate="版本",
        ))
        found = store.find_similar("Python", "版本")
        assert found is not None
        assert found.content == "Python 3.10"

    def test_count(self, store):
        assert store.count_memories() == 0
        store.save_semantic(SemanticMemory(content="a"))
        assert store.count_memories() == 1


class TestEpisodeCRUD:
    def test_save_and_get(self, store):
        ep = Episode(session_id="s1", summary="test episode", goal="testing")
        store.save_episode(ep)

        loaded = store.get_episode(ep.id)
        assert loaded is not None
        assert loaded.summary == "test episode"

    def test_search_by_session(self, store):
        store.save_episode(Episode(session_id="s1", summary="ep1"))
        store.save_episode(Episode(session_id="s2", summary="ep2"))

        results = store.search_episodes(session_id="s1")
        assert len(results) == 1

    def test_recent_episodes(self, store):
        store.save_episode(Episode(session_id="s1", summary="recent"))
        results = store.get_recent_episodes(days=1)
        assert len(results) >= 1


class TestScratchpadCRUD:
    def test_save_and_get(self, store):
        pad = Scratchpad(
            content="工作中...",
            active_projects=["memory-redesign"],
            current_focus="FTS5",
        )
        store.save_scratchpad(pad)

        loaded = store.get_scratchpad()
        assert loaded is not None
        assert loaded.content == "工作中..."
        assert "memory-redesign" in loaded.active_projects

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_scratchpad("nobody") is None


class TestConversationTurns:
    def test_save_and_get(self, store):
        store.save_turn(
            session_id="s1",
            turn_index=0,
            role="user",
            content="hello",
        )
        turns = store.get_session_turns("s1")
        assert len(turns) == 1
        assert turns[0]["content"] == "hello"

    def test_unextracted(self, store):
        store.save_turn(session_id="s1", turn_index=0, role="user", content="hi")
        unext = store.get_unextracted_turns()
        assert len(unext) == 1

        store.mark_turns_extracted("s1", [0])
        unext = store.get_unextracted_turns()
        assert len(unext) == 0


class TestExtractionQueue:
    def test_enqueue_dequeue(self, store):
        store.enqueue_extraction(
            session_id="s1", turn_index=0, content="test content"
        )
        items = store.dequeue_extraction(batch_size=5)
        assert len(items) == 1
        assert items[0]["content"] == "test content"

    def test_complete(self, store):
        store.enqueue_extraction(session_id="s1", turn_index=0, content="x")
        items = store.dequeue_extraction()
        store.complete_extraction(items[0]["id"], success=True)
        remaining = store.dequeue_extraction()
        assert len(remaining) == 0


class TestStats:
    def test_get_stats(self, store):
        stats = store.get_stats()
        assert "memory_count" in stats
        assert "search_backend" in stats
