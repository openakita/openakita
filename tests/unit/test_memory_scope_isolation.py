import pytest

from openakita.memory.manager import MemoryManager
from openakita.memory.types import Episode, MemoryPriority, MemoryType, SemanticMemory


def _manager(tmp_path) -> MemoryManager:
    return MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )


def _save_memory(
    manager: MemoryManager,
    content: str,
    *,
    scope: str,
    scope_owner: str = "",
    memory_type: MemoryType = MemoryType.FACT,
) -> str:
    mem = SemanticMemory(
        type=memory_type,
        priority=MemoryPriority.LONG_TERM,
        content=content,
        importance_score=0.9,
        confidence=0.8,
    )
    return manager.store.save_semantic(mem, scope=scope, scope_owner=scope_owner)


def test_injection_context_only_sees_current_session_and_global_memory(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a")

    _save_memory(manager, "alpha current session detail", scope="session", scope_owner="session-a")
    _save_memory(manager, "alpha other session detail", scope="session", scope_owner="session-b")
    _save_memory(manager, "alpha global preference", scope="global")

    context = manager.get_injection_context("alpha", max_related=5)

    assert "alpha current session detail" in context
    assert "alpha global preference" in context
    assert "alpha other session detail" not in context


def test_episode_retrieval_is_limited_to_current_session(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a")

    manager.store.save_episode(
        Episode(
            session_id="session-a",
            goal="alpha current episode",
            summary="current session episode",
            entities=["alpha"],
            importance_score=0.9,
        )
    )
    manager.store.save_episode(
        Episode(
            session_id="session-b",
            goal="alpha other episode",
            summary="other session episode",
            entities=["alpha"],
            importance_score=0.9,
        )
    )

    candidates = manager.retrieval_engine.retrieve_candidates("alpha")
    content = "\n".join(c.content for c in candidates)

    assert "alpha current episode" in content
    assert "alpha other episode" not in content


def test_legacy_global_search_does_not_leak_session_memories(tmp_path):
    manager = _manager(tmp_path)
    manager.add_memory(
        SemanticMemory(
            content="alpha current session detail",
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
        ),
        scope="session",
        scope_owner="session-a",
    )
    manager.add_memory(
        SemanticMemory(
            content="alpha global preference",
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
        ),
        scope="global",
    )

    global_results = manager.search_memories("alpha", scope="global")
    session_results = manager.search_memories(
        "alpha",
        scope="session",
        scope_owner="session-a",
    )

    assert [m.content for m in global_results] == ["alpha global preference"]
    assert [m.content for m in session_results] == ["alpha current session detail"]


def test_manual_cancel_rule_supersedes_related_old_rule(tmp_path):
    from types import SimpleNamespace

    from openakita.tools.handlers.memory import MemoryHandler

    manager = _manager(tmp_path)
    old_id = manager.add_memory(
        SemanticMemory(
            content="网页操作偏好：所有浏览器操作优先使用 MCP 工具",
            type=MemoryType.RULE,
            priority=MemoryPriority.PERMANENT,
            importance_score=0.9,
        ),
        scope="global",
    )
    handler = MemoryHandler(SimpleNamespace(memory_manager=manager, profile_manager=None))

    result = handler._add_memory(
        {
            "content": "网页操作偏好规则已取消：不再强制使用 MCP 工具，改用内置浏览器",
            "type": "rule",
            "importance": 0.9,
        }
    )

    assert "已替代旧记忆" in result
    old = manager.store.get_semantic(old_id, include_inactive=True)
    assert old is not None
    assert old.superseded_by
    assert manager.store.get_semantic(old_id) is None


@pytest.mark.asyncio
async def test_auto_extracted_memory_is_saved_to_current_session_scope(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a")

    memory_id = await manager._save_extracted_item(
        {
            "type": "FACT",
            "content": "alpha extracted from session a",
            "subject": "alpha",
            "predicate": "context",
            "importance": 0.7,
        }
    )

    assert memory_id is not None
    saved = manager.store.get_semantic(memory_id)
    assert saved is not None
    assert saved.scope == "session"
    assert saved.scope_owner == "session-a"


@pytest.mark.asyncio
async def test_auto_extraction_dedup_does_not_evolve_other_session_memory(tmp_path):
    manager = _manager(tmp_path)
    _save_memory(manager, "alpha existing in session b", scope="session", scope_owner="session-b")

    manager.start_session("session-a")
    memory_id = await manager._save_extracted_item(
        {
            "type": "FACT",
            "content": "alpha existing in session b",
            "subject": "alpha",
            "predicate": "context",
            "importance": 0.7,
        }
    )

    assert memory_id is not None
    saved = manager.store.get_semantic(memory_id)
    assert saved is not None
    assert saved.scope == "session"
    assert saved.scope_owner == "session-a"
    session_a = manager.store.search_semantic(
        "alpha",
        scope="session",
        scope_owner="session-a",
    )
    session_b = manager.store.search_semantic(
        "alpha",
        scope="session",
        scope_owner="session-b",
    )
    assert len(session_a) == 1
    assert len(session_b) == 1
