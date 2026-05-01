from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from openakita.mcp_server import MCPServer
from openakita.memory.extractor import MemoryExtractor
from openakita.memory.lifecycle import LifecycleManager
from openakita.memory.relational.store import RelationalMemoryStore
from openakita.memory.relational.types import MemoryNode, NodeType
from openakita.memory.retrieval import RetrievalCandidate
from openakita.memory.types import MemoryPriority, SemanticMemory
from openakita.memory.unified_store import UnifiedStore


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
