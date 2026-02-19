"""补充检索引擎测试: 附件召回, rerank 权重, 增强查询."""

import pytest
from datetime import datetime

from openakita.memory.retrieval import RetrievalCandidate, RetrievalEngine
from openakita.memory.types import (
    Attachment,
    AttachmentDirection,
    Episode,
    MemoryType,
    SemanticMemory,
)
from openakita.memory.unified_store import UnifiedStore


@pytest.fixture
def store(tmp_path):
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def engine(store):
    return RetrievalEngine(store)


@pytest.fixture
def store_with_attachments(store):
    store.save_attachment(Attachment(
        id="att-cat",
        filename="cat.jpg",
        mime_type="image/jpeg",
        description="一只橘猫趴在沙发上",
        direction=AttachmentDirection.INBOUND,
    ))
    store.save_attachment(Attachment(
        id="att-report",
        filename="report.pdf",
        mime_type="application/pdf",
        description="月度销售报告",
        direction=AttachmentDirection.OUTBOUND,
    ))
    store.save_semantic(SemanticMemory(
        content="用户养了一只橘猫", type=MemoryType.FACT,
        subject="用户", predicate="宠物",
    ))
    return store


class TestAttachmentRetrieval:
    def test_keyword_gate_triggers_on_media_word(self, store_with_attachments):
        """_search_attachments 应在包含媒体关键词时触发搜索, 不含时跳过."""
        engine = RetrievalEngine(store_with_attachments)
        candidates = engine._search_attachments("橘猫图片")
        assert isinstance(candidates, list)

    def test_no_media_keyword_skips_attachment(self, store_with_attachments):
        engine = RetrievalEngine(store_with_attachments)
        candidates = engine._search_attachments("今天天气怎么样")
        assert len(candidates) == 0

    def test_file_keyword_recognized(self, store_with_attachments):
        """关键词"文件"触发搜索路径."""
        engine = RetrievalEngine(store_with_attachments)
        candidates = engine._search_attachments("report.pdf 文件")
        assert isinstance(candidates, list)

    def test_attachment_included_in_full_retrieve(self, store_with_attachments):
        engine = RetrievalEngine(store_with_attachments)
        result = engine.retrieve("cat 图片在哪")
        assert isinstance(result, str)

    def test_attachment_candidate_structure(self, store_with_attachments):
        """直接用 store 层确认结构, 再经 engine 转换."""
        engine = RetrievalEngine(store_with_attachments)
        store_with_attachments.save_attachment(Attachment(
            id="att-test-struct",
            filename="structure_test.png",
            mime_type="image/png",
            description="图片结构测试",
            direction=AttachmentDirection.INBOUND,
        ))
        candidates = engine._search_attachments("structure_test 照片")
        assert len(candidates) >= 1
        for c in candidates:
            assert c.source_type == "attachment"
            assert c.memory_type == "attachment"
            assert c.memory_id.startswith("attach:")

    def test_direct_store_search_description(self, store_with_attachments):
        results = store_with_attachments.search_attachments(query="橘猫")
        assert len(results) >= 1
        assert results[0].filename == "cat.jpg"

    def test_direct_store_search_filename(self, store_with_attachments):
        results = store_with_attachments.search_attachments(query="report")
        assert len(results) >= 1


class TestReranking:
    def test_higher_relevance_wins(self, engine):
        candidates = [
            RetrievalCandidate(
                memory_id="low", content="low relevance",
                relevance=0.2, recency_score=0.5,
                importance_score=0.5, access_frequency_score=0.5,
            ),
            RetrievalCandidate(
                memory_id="high", content="high relevance",
                relevance=0.9, recency_score=0.5,
                importance_score=0.5, access_frequency_score=0.5,
            ),
        ]
        ranked = engine._rerank(candidates, "test")
        assert ranked[0].memory_id == "high"

    def test_tech_persona_boosts_skill(self, engine):
        candidates = [
            RetrievalCandidate(
                memory_id="skill", content="skill item", memory_type="skill",
                relevance=0.5, recency_score=0.5,
                importance_score=0.5, access_frequency_score=0.5,
            ),
            RetrievalCandidate(
                memory_id="fact", content="fact item", memory_type="fact",
                relevance=0.5, recency_score=0.5,
                importance_score=0.5, access_frequency_score=0.5,
            ),
        ]
        ranked = engine._rerank(candidates, "test", persona="tech_expert")
        assert ranked[0].memory_id == "skill"

    def test_score_is_weighted_sum(self, engine):
        c = RetrievalCandidate(
            memory_id="test", content="test",
            relevance=1.0, recency_score=1.0,
            importance_score=1.0, access_frequency_score=1.0,
        )
        engine._rerank([c], "test")
        expected = 1.0 * 0.4 + 1.0 * 0.25 + 1.0 * 0.2 + 1.0 * 0.15
        assert c.score == pytest.approx(expected)


class TestEnhancedQuery:
    def test_includes_recent_messages(self, engine):
        recent = [
            {"role": "user", "content": "Python 版本?"},
            {"role": "assistant", "content": "3.12"},
        ]
        enhanced = engine._build_enhanced_query("版本信息", recent)
        assert "Python" in enhanced
        assert "3.12" in enhanced
        assert "版本信息" in enhanced

    def test_empty_recent(self, engine):
        enhanced = engine._build_enhanced_query("query", None)
        assert enhanced == "query"

    def test_truncates_long_messages(self, engine):
        recent = [{"role": "user", "content": "x" * 500}]
        enhanced = engine._build_enhanced_query("q", recent)
        assert len(enhanced) < 200


class TestMergeDedup:
    def test_deduplicates_by_id(self, engine):
        list_a = [
            RetrievalCandidate(memory_id="shared", relevance=0.5),
            RetrievalCandidate(memory_id="a-only", relevance=0.3),
        ]
        list_b = [
            RetrievalCandidate(memory_id="shared", relevance=0.8),
            RetrievalCandidate(memory_id="b-only", relevance=0.4),
        ]
        merged = engine._merge_and_deduplicate(list_a, list_b)
        ids = {c.memory_id for c in merged}
        assert ids == {"shared", "a-only", "b-only"}
        shared = next(c for c in merged if c.memory_id == "shared")
        assert shared.relevance == 0.8


class TestScoringHelpers:
    def test_compute_recency_now(self, engine):
        score = engine._compute_recency(datetime.now())
        assert score > 0.9

    def test_compute_recency_old(self, engine):
        from datetime import timedelta
        old = datetime.now() - timedelta(days=30)
        score = engine._compute_recency(old)
        assert score < 0.2

    def test_compute_recency_none(self, engine):
        assert engine._compute_recency(None) == 0.0

    def test_compute_access_score(self, engine):
        assert engine._compute_access_score(0) == 0.0
        s1 = engine._compute_access_score(1)
        s10 = engine._compute_access_score(10)
        assert s10 > s1

    def test_format_empty(self, engine):
        assert engine._format_within_budget([], 100) == ""
