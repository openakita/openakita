"""L2 Component Tests: RetrievalEngine multi-way recall + reranking."""

import pytest

from openakita.memory.retrieval import MemoryQueryPreprocessor, RetrievalEngine
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

    def test_preprocessor_strips_injected_memory_blocks(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "当前问题\n## 相关记忆（自动检索）\n- 用户喜欢旧方案\n## 其他\n这个文件继续改"
        )
        assert "用户喜欢旧方案" not in prepared.query
        assert not prepared.skip

    def test_retrieval_gate_skips_control_only(self):
        prepared = MemoryQueryPreprocessor.prepare("好的")
        assert prepared.skip

    def test_retrieval_gate_keeps_short_reference_with_context(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "这个文件",
            recent_messages=[{"role": "user", "content": "src/openakita/memory/retrieval.py"}],
        )
        assert not prepared.skip

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

    def test_focus_terms_boost_sorting_only(self, engine):
        from openakita.memory.retrieval import RetrievalCandidate

        engine.set_focus_terms(["retrieval.py"])
        candidates = [
            RetrievalCandidate(
                memory_id="a", content="unrelated", relevance=0.7,
                recency_score=0.3, importance_score=0.3, access_frequency_score=0.1,
            ),
            RetrievalCandidate(
                memory_id="b", content="retrieval.py current task", relevance=0.68,
                recency_score=0.3, importance_score=0.3, access_frequency_score=0.1,
            ),
        ]
        ranked = engine._rerank(candidates, "test")
        assert ranked[0].memory_id == "b"

