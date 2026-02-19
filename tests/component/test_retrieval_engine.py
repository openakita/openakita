"""L2 Component Tests: RetrievalEngine multi-way recall + reranking."""

import pytest
from datetime import datetime

from openakita.memory.retrieval import RetrievalEngine
from openakita.memory.types import Episode, MemoryType, SemanticMemory
from openakita.memory.unified_store import UnifiedStore


@pytest.fixture
def store(tmp_path):
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def engine(store):
    return RetrievalEngine(store)


@pytest.fixture
def populated_store(store):
    """Store pre-populated with test data."""
    store.save_semantic(SemanticMemory(
        content="用户喜欢深色主题",
        type=MemoryType.PREFERENCE,
        subject="用户",
        predicate="主题偏好",
        importance_score=0.8,
    ))
    store.save_semantic(SemanticMemory(
        content="项目使用 Python 3.12",
        type=MemoryType.FACT,
        subject="项目",
        predicate="Python版本",
        importance_score=0.7,
    ))
    store.save_semantic(SemanticMemory(
        content="git rebase 导致冲突时用 git merge 替代",
        type=MemoryType.SKILL,
        importance_score=0.6,
    ))
    store.save_episode(Episode(
        session_id="s1",
        summary="重构了记忆系统架构",
        goal="记忆系统重构",
        outcome="success",
        entities=["memory", "storage.py"],
        tools_used=["write_file", "read_file"],
    ))
    return store


class TestRetrievalEngine:
    def test_retrieve_empty_store(self, engine):
        result = engine.retrieve("anything")
        assert isinstance(result, str)

    def test_retrieve_finds_relevant(self, populated_store):
        engine = RetrievalEngine(populated_store)
        result = engine.retrieve("Python 版本")
        assert isinstance(result, str)

    def test_retrieve_candidates(self, populated_store):
        engine = RetrievalEngine(populated_store)
        candidates = engine.retrieve_candidates("Python", limit=10)
        assert isinstance(candidates, list)

    def test_retrieve_with_recent_messages(self, populated_store):
        engine = RetrievalEngine(populated_store)
        recent = [
            {"role": "user", "content": "Python 版本是多少?"},
            {"role": "assistant", "content": "3.12"},
        ]
        result = engine.retrieve("版本", recent_messages=recent)
        assert isinstance(result, str)

    def test_token_budget_respected(self, populated_store):
        engine = RetrievalEngine(populated_store)
        result = engine.retrieve("Python", max_tokens=50)
        assert len(result) < 200  # ~50 tokens * ~3 chars/token

    def test_reranking_scoring(self, engine):
        from openakita.memory.retrieval import RetrievalCandidate

        candidates = [
            RetrievalCandidate(
                memory_id="a", content="low", relevance=0.3,
                recency_score=0.1, importance_score=0.2, access_frequency_score=0.1,
            ),
            RetrievalCandidate(
                memory_id="b", content="high", relevance=0.9,
                recency_score=0.8, importance_score=0.9, access_frequency_score=0.5,
            ),
        ]
        ranked = engine._rerank(candidates, "test")
        assert ranked[0].memory_id == "b"
