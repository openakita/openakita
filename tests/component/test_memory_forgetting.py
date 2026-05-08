from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from openakita.mcp_server import MCPServer
from openakita.memory import lifecycle as lifecycle_module
from openakita.memory.extractor import MemoryExtractor
from openakita.memory.lifecycle import LifecycleManager
from openakita.memory.relational.store import RelationalMemoryStore
from openakita.memory.relational.types import MemoryNode, NodeType
from openakita.memory.retrieval import RetrievalCandidate
from openakita.memory.types import Episode, MemoryPriority, SemanticMemory
from openakita.memory.unified_store import UnifiedStore
from openakita.scheduler.consolidation_tracker import ConsolidationTracker


@pytest.fixture
def store(tmp_path):
    return UnifiedStore(tmp_path / "memory.db")


def test_lifecycle_extracted_items_receive_ttl(store, tmp_path):
    lifecycle = LifecycleManager(store, MemoryExtractor(brain=None), tmp_path)

    lifecycle._save_extracted_item(
        {
            "type": "FACT",
            "content": "临时项目事实会自动过期",
            "importance": 0.4,
            "subject": "项目",
            "predicate": "临时事实",
        }
    )

    [mem] = store.load_all_memories()
    assert mem.priority == MemoryPriority.SHORT_TERM
    assert mem.expires_at is not None
    assert mem.expires_at > datetime.now()


def test_compute_decay_matches_lowercase_short_term(store, tmp_path):
    lifecycle = LifecycleManager(store, MemoryExtractor(brain=None), tmp_path)
    old_mem = SemanticMemory(
        content="很旧且低价值的短期记忆",
        priority=MemoryPriority.SHORT_TERM,
        importance_score=0.2,
        access_count=0,
    )
    old_mem.last_accessed_at = datetime.now() - timedelta(days=90)
    store.save_semantic(old_mem)

    assert lifecycle.compute_decay() >= 1
    assert store.get_semantic(old_mem.id) is None


def test_active_memory_filters_hide_expired_and_superseded(store):
    active = SemanticMemory(content="活跃事实", importance_score=0.8)
    expired = SemanticMemory(content="过期事实", importance_score=0.8)
    expired.expires_at = datetime.now() - timedelta(days=1)
    superseded = SemanticMemory(content="被替代事实", importance_score=0.8)
    superseded.superseded_by = active.id

    store.save_semantic(active)
    store.save_semantic(expired)
    store.save_semantic(superseded)

    active_contents = {m.content for m in store.load_all_memories()}
    assert active_contents == {"活跃事实"}
    assert store.get_semantic(expired.id) is None
    assert store.get_semantic(superseded.id) is None

    inactive_contents = {m.content for m in store.load_all_memories(include_inactive=True)}
    assert inactive_contents == {"活跃事实", "过期事实", "被替代事实"}


@pytest.mark.asyncio
async def test_llm_review_accepts_control_characters_in_json(store, tmp_path):
    class ReviewBrain:
        async def think(self, prompt, **kwargs):
            [mem_id] = re.findall(r"ID=([^ |]+)", prompt)
            return SimpleNamespace(
                content=f'[{{"id": "{mem_id}", "action": "update", "new_content": "第一行\n第二行"}}]'
            )

    mem = SemanticMemory(content="原始记忆", importance_score=0.8)
    store.save_semantic(mem)
    lifecycle = LifecycleManager(store, MemoryExtractor(brain=ReviewBrain()), tmp_path)

    report = await lifecycle.review_memories_with_llm()

    assert report["errors"] == 0
    assert report["updated"] == 1
    assert store.get_semantic(mem.id).content == "第一行\n第二行"


@pytest.mark.asyncio
async def test_llm_review_continues_after_consecutive_risky_batches(store, tmp_path):
    class ReviewBrain:
        def __init__(self):
            self.calls = 0

        async def think(self, prompt, **kwargs):
            self.calls += 1
            ids = re.findall(r"ID=([^ |]+)", prompt)
            if self.calls <= 3:
                decisions = [{"id": mem_id, "action": "delete"} for mem_id in ids]
            else:
                decisions = [
                    {"id": mem_id, "action": "update", "new_content": f"reviewed {mem_id}"}
                    for mem_id in ids
                ]
            return SimpleNamespace(content=json.dumps(decisions))

    memories = [SemanticMemory(content=f"记忆 {idx}", importance_score=0.8) for idx in range(46)]
    for mem in memories:
        store.save_semantic(mem, skip_dedup=True)
    brain = ReviewBrain()
    lifecycle = LifecycleManager(store, MemoryExtractor(brain=brain), tmp_path)

    report = await lifecycle.review_memories_with_llm()

    assert brain.calls == 4
    assert report["deleted"] == 0
    assert report["updated"] == 1
    assert any(
        (store.get_semantic(mem.id).content or "").startswith("reviewed ")
        for mem in memories
    )


@pytest.mark.asyncio
async def test_llm_review_checkpoint_resumes_remaining_batches(store, tmp_path):
    class ReviewBrain:
        def __init__(self):
            self.calls = 0

        async def think(self, prompt, **kwargs):
            self.calls += 1
            ids = re.findall(r"ID=([^ |]+)", prompt)
            return SimpleNamespace(
                content=json.dumps([{"id": mem_id, "action": "keep"} for mem_id in ids])
            )

    memories = [SemanticMemory(content=f"可保留记忆 {idx}", importance_score=0.8) for idx in range(31)]
    for mem in memories:
        store.save_semantic(mem, skip_dedup=True)

    saved_checkpoint = {}
    brain = ReviewBrain()
    lifecycle = LifecycleManager(store, MemoryExtractor(brain=brain), tmp_path)

    first = await lifecycle.review_memories_with_llm(
        checkpoint_callback=saved_checkpoint.update,
        max_batches=1,
    )

    assert first["partial"] is True
    assert saved_checkpoint["cursor"] == 1
    assert brain.calls == 1

    second = await lifecycle.review_memories_with_llm(
        checkpoint=saved_checkpoint,
        checkpoint_callback=saved_checkpoint.update,
    )

    assert second["partial"] is False
    assert second["kept"] == 31
    assert second["processed_batches"] == 3
    assert brain.calls == 3


def test_consolidation_tracker_checkpoint_does_not_mark_success(tmp_path):
    tracker = ConsolidationTracker(tmp_path)

    tracker.record_memory_consolidation_checkpoint(
        {"phase": "llm_review", "llm_review": {"cursor": 1}}
    )

    assert tracker.last_memory_consolidation is None
    assert tracker.get_memory_consolidation_checkpoint()["phase"] == "llm_review"

    tracker.record_memory_consolidation({"unextracted_processed": 1})

    assert tracker.last_memory_consolidation is not None
    assert tracker.get_memory_consolidation_checkpoint() == {}


@pytest.mark.asyncio
async def test_unextracted_turns_are_marked_after_each_success(store, tmp_path):
    class PartiallyFailingExtractor:
        async def generate_episode(self, _turns, session_id, **kwargs):
            return Episode(session_id=session_id, summary="partial extraction")

        async def extract_from_turn_v2(self, turn):
            if "失败" in turn.content:
                raise RuntimeError("boom")
            return []

    store.save_turn(session_id="s1", turn_index=0, role="user", content="这一轮可以成功")
    store.save_turn(session_id="s1", turn_index=1, role="user", content="这一轮会失败")

    lifecycle = LifecycleManager(store, PartiallyFailingExtractor(), tmp_path)

    processed = await lifecycle.process_unextracted_turns()

    assert processed == 0
    remaining = store.get_unextracted_turns()
    assert [turn["turn_index"] for turn in remaining] == [1]


@pytest.mark.asyncio
async def test_unextracted_turns_pause_before_budget_timeout(store, tmp_path, monkeypatch):
    class BudgetAwareExtractor:
        async def generate_episode(self, _turns, session_id, **kwargs):
            return Episode(session_id=session_id, summary="budgeted extraction")

        async def extract_from_turn_v2(self, _turn):
            return []

    store.save_turn(session_id="s1", turn_index=0, role="user", content="第一轮可以完成")
    store.save_turn(session_id="s1", turn_index=1, role="user", content="第二轮留到下次")
    values = iter([0.0, 0.0, 71.0])
    monkeypatch.setattr(lifecycle_module.time, "monotonic", lambda: next(values, 71.0))
    lifecycle = LifecycleManager(store, BudgetAwareExtractor(), tmp_path)

    result = await lifecycle.process_unextracted_turns(deadline_monotonic=100.0)

    assert result["partial"] is True
    remaining = store.get_unextracted_turns()
    assert [turn["turn_index"] for turn in remaining] == [1]


def test_superseded_memory_does_not_block_new_duplicate(store):
    old = SemanticMemory(content="用户喜欢蓝色主题设置", importance_score=0.8)
    old.superseded_by = "newer-memory"
    store.save_semantic(old, skip_dedup=True)

    fresh = SemanticMemory(content="用户喜欢蓝色主题设置", importance_score=0.8)
    saved_id = store.save_semantic(fresh)

    assert saved_id == fresh.id


@pytest.mark.asyncio
async def test_unextracted_turns_retry_when_episode_generation_fails(store, tmp_path):
    class EmptyEpisodeExtractor:
        async def generate_episode(self, *args, **kwargs):
            return None

        async def extract_from_turn_v2(self, *args, **kwargs):
            return []

    store.save_turn(
        session_id="s1",
        turn_index=0,
        role="user",
        content="这是一条应该等待重试的对话内容",
    )
    lifecycle = LifecycleManager(store, EmptyEpisodeExtractor(), tmp_path)

    processed = await lifecycle.process_unextracted_turns()

    assert processed == 0
    assert len(store.get_unextracted_turns()) == 1


def test_relational_search_filters_expired_nodes(tmp_path):
    store = UnifiedStore(tmp_path / "rel.db")
    relational = RelationalMemoryStore(store.db._conn)
    active = MemoryNode(id="active", content="当前项目记忆", node_type=NodeType.FACT)
    expired = MemoryNode(
        id="expired",
        content="当前项目记忆",
        node_type=NodeType.FACT,
        valid_until=datetime.now() - timedelta(days=1),
    )
    relational.save_nodes_batch([active, expired])

    ids = {n.id for n in relational.search_like("当前项目记忆", limit=10)}

    assert ids == {"active"}


@pytest.mark.asyncio
async def test_mcp_memory_search_uses_retrieval_engine():
    server = MCPServer()
    candidate = RetrievalCandidate(
        memory_id="m1",
        content="记忆内容",
        memory_type="fact",
        source_type="semantic",
        score=0.9,
    )
    retrieval_engine = SimpleNamespace(
        retrieve_candidates=lambda query, limit: [candidate],
    )
    server._agent = SimpleNamespace(
        memory_manager=SimpleNamespace(retrieval_engine=retrieval_engine)
    )

    result = await server._execute_tool(
        "openakita_memory_search",
        {"query": "记忆", "limit": 1},
    )

    assert '"id": "m1"' in result
    assert "记忆内容" in result
