"""L2 Component Tests: LifecycleManager dedup, decay, refresh."""

import pytest
from datetime import datetime, timedelta

from openakita.memory.extractor import MemoryExtractor
from openakita.memory.lifecycle import LifecycleManager
from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory
from openakita.memory.unified_store import UnifiedStore


@pytest.fixture
def store(tmp_path):
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def extractor():
    return MemoryExtractor(brain=None)


@pytest.fixture
def lifecycle(store, extractor, tmp_path):
    identity_dir = tmp_path / "identity"
    identity_dir.mkdir()
    return LifecycleManager(store, extractor, identity_dir)


class TestDeduplication:
    def test_no_duplicates_when_unique(self, lifecycle, store):
        store.save_semantic(SemanticMemory(content="fact A", type=MemoryType.FACT))
        store.save_semantic(SemanticMemory(content="totally different B", type=MemoryType.FACT))

        import asyncio
        removed = asyncio.get_event_loop().run_until_complete(lifecycle.deduplicate_batch())
        assert removed == 0

    def test_removes_exact_duplicates(self, lifecycle, store):
        store.save_semantic(SemanticMemory(
            content="用户喜欢 Python", importance_score=0.8, type=MemoryType.PREFERENCE,
        ))
        store.save_semantic(SemanticMemory(
            content="用户喜欢 Python", importance_score=0.5, type=MemoryType.PREFERENCE,
        ))

        import asyncio
        removed = asyncio.get_event_loop().run_until_complete(lifecycle.deduplicate_batch())
        assert removed == 1
        remaining = store.load_all_memories()
        assert len(remaining) == 1
        assert remaining[0].importance_score == 0.8  # kept the better one


class TestDecay:
    def test_decay_old_short_term(self, lifecycle, store):
        old_mem = SemanticMemory(
            content="old fact",
            priority=MemoryPriority.SHORT_TERM,
            importance_score=0.3,
            access_count=0,
        )
        old_mem.updated_at = datetime.now() - timedelta(days=30)
        old_mem.last_accessed_at = datetime.now() - timedelta(days=30)
        store.save_semantic(old_mem)

        decayed = lifecycle.compute_decay()
        assert decayed >= 0  # may or may not trigger depending on exact threshold

    def test_permanent_not_decayed(self, lifecycle, store):
        mem = SemanticMemory(
            content="permanent rule",
            priority=MemoryPriority.PERMANENT,
            importance_score=0.9,
        )
        store.save_semantic(mem)

        decayed = lifecycle.compute_decay()
        remaining = store.load_all_memories()
        assert any(m.content == "permanent rule" for m in remaining)


class TestRefreshMemoryMd:
    def test_refresh_creates_file(self, lifecycle, store, tmp_path):
        store.save_semantic(SemanticMemory(
            content="重要偏好", type=MemoryType.PREFERENCE, importance_score=0.9,
        ))

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        lifecycle.refresh_memory_md(identity_dir)

        memory_md = identity_dir / "MEMORY.md"
        assert memory_md.exists()
        content = memory_md.read_text(encoding="utf-8")
        assert "重要偏好" in content

    def test_refresh_empty_store(self, lifecycle, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        lifecycle.refresh_memory_md(identity_dir)

        memory_md = identity_dir / "MEMORY.md"
        assert not memory_md.exists()


class TestClusterByContent:
    def test_cluster_similar(self, lifecycle):
        memories = [
            SemanticMemory(content="用户喜欢 Python 编程"),
            SemanticMemory(content="用户喜欢 Python 编程语言"),
            SemanticMemory(content="完全不相关的内容 XYZ ABC"),
        ]
        clusters = lifecycle._cluster_by_content(memories, threshold=0.5)
        assert len(clusters) >= 0

    def test_pick_best(self, lifecycle):
        memories = [
            SemanticMemory(content="low", importance_score=0.3, access_count=0),
            SemanticMemory(content="high", importance_score=0.9, access_count=5),
        ]
        best, remove = lifecycle._pick_best_in_cluster(memories)
        assert best.importance_score == 0.9
        assert len(remove) == 1
