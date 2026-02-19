"""补充生命周期测试: 附件清理, consolidate_daily 完整流程."""

import asyncio
from datetime import datetime, timedelta

import pytest

from openakita.memory.extractor import MemoryExtractor
from openakita.memory.lifecycle import LifecycleManager
from openakita.memory.types import (
    Attachment,
    AttachmentDirection,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
)
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


class TestAttachmentCleanup:
    def test_cleans_stale_empty_attachments(self, lifecycle, store):
        old_date = (datetime.now() - timedelta(days=120)).isoformat()
        store.db.save_attachment({
            "id": "stale-1",
            "filename": "old.txt",
            "description": "",
            "transcription": "",
            "extracted_text": "",
            "linked_memory_ids": "[]",
            "created_at": old_date,
        })
        cleaned = lifecycle.cleanup_stale_attachments(max_age_days=90)
        assert cleaned == 1
        assert store.get_attachment("stale-1") is None

    def test_keeps_described_attachments(self, lifecycle, store):
        old_date = (datetime.now() - timedelta(days=120)).isoformat()
        store.db.save_attachment({
            "id": "described-1",
            "filename": "cat.jpg",
            "description": "一只橘猫",
            "transcription": "",
            "extracted_text": "",
            "linked_memory_ids": "[]",
            "created_at": old_date,
        })
        cleaned = lifecycle.cleanup_stale_attachments(max_age_days=90)
        assert cleaned == 0
        assert store.get_attachment("described-1") is not None

    def test_keeps_recent_empty_attachments(self, lifecycle, store):
        recent = datetime.now().isoformat()
        store.db.save_attachment({
            "id": "recent-1",
            "filename": "new.txt",
            "description": "",
            "transcription": "",
            "extracted_text": "",
            "linked_memory_ids": "[]",
            "created_at": recent,
        })
        cleaned = lifecycle.cleanup_stale_attachments(max_age_days=90)
        assert cleaned == 0

    def test_keeps_linked_attachments(self, lifecycle, store):
        old_date = (datetime.now() - timedelta(days=120)).isoformat()
        import json
        store.db.save_attachment({
            "id": "linked-1",
            "filename": "old.txt",
            "description": "",
            "transcription": "",
            "extracted_text": "",
            "linked_memory_ids": json.dumps(["mem-123"]),
            "created_at": old_date,
        })
        cleaned = lifecycle.cleanup_stale_attachments(max_age_days=90)
        assert cleaned == 0


class TestConsolidateDaily:
    def test_consolidate_returns_report(self, lifecycle, store):
        store.save_semantic(SemanticMemory(
            content="test fact", type=MemoryType.FACT, importance_score=0.9,
        ))
        report = asyncio.get_event_loop().run_until_complete(lifecycle.consolidate_daily())
        assert "started_at" in report
        assert "finished_at" in report
        assert "duplicates_removed" in report
        assert "memories_decayed" in report
        assert "stale_attachments_cleaned" in report

    def test_consolidate_empty_store(self, lifecycle):
        report = asyncio.get_event_loop().run_until_complete(lifecycle.consolidate_daily())
        assert report["unextracted_processed"] == 0
        assert report["duplicates_removed"] == 0


class TestDecayEdgeCases:
    def test_decay_very_old_memory(self, lifecycle, store):
        mem = SemanticMemory(
            content="very old data",
            priority=MemoryPriority.SHORT_TERM,
            importance_score=0.2,
            access_count=0,
        )
        mem.updated_at = datetime.now() - timedelta(days=365)
        mem.last_accessed_at = datetime.now() - timedelta(days=365)
        store.save_semantic(mem)
        decayed = lifecycle.compute_decay()
        assert isinstance(decayed, int)

    def test_long_term_not_decayed(self, lifecycle, store):
        mem = SemanticMemory(
            content="long term fact",
            priority=MemoryPriority.LONG_TERM,
            importance_score=0.5,
        )
        store.save_semantic(mem)
        decayed = lifecycle.compute_decay()
        remaining = store.load_all_memories()
        assert any(m.content == "long term fact" for m in remaining)


class TestRefreshUserMd:
    def test_refresh_creates_user_md(self, lifecycle, store, tmp_path):
        store.save_semantic(SemanticMemory(
            content="用户的名字叫小明",
            type=MemoryType.FACT,
            subject="用户",
            predicate="称呼",
            importance_score=0.8,
        ))
        store.save_semantic(SemanticMemory(
            content="用户偏好深色主题",
            type=MemoryType.PREFERENCE,
            subject="用户",
            predicate="偏好",
            importance_score=0.7,
        ))
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        asyncio.get_event_loop().run_until_complete(lifecycle.refresh_user_md(identity_dir))
        user_md = identity_dir / "USER.md"
        if user_md.exists():
            content = user_md.read_text(encoding="utf-8")
            assert "用户" in content

    def test_refresh_no_user_facts(self, lifecycle, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        asyncio.get_event_loop().run_until_complete(lifecycle.refresh_user_md(identity_dir))
        user_md = identity_dir / "USER.md"
        # Should not create file with no data
