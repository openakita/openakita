import pytest

from openakita.memory.manager import MemoryManager
from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory


def _manager(tmp_path) -> MemoryManager:
    return MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )


def _memory(content: str, *, subject: str = "", predicate: str = "") -> SemanticMemory:
    return SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content=content,
        subject=subject,
        predicate=predicate,
        importance_score=0.8,
    )


def test_two_users_do_not_see_each_other_long_term_memory(tmp_path):
    manager = _manager(tmp_path)

    manager.start_session("session-a", user_id="user-a")
    manager.add_memory(_memory("用户住在苏州"), scope="global")

    manager.start_session("session-b", user_id="user-b")
    manager.add_memory(_memory("用户住在上海"), scope="global")

    user_b_results = manager.search_memories("用户住在", scope="user")
    assert [m.content for m in user_b_results] == ["用户住在上海"]

    manager.start_session("session-a", user_id="user-a")
    user_a_results = manager.search_memories("用户住在", scope="user")
    assert [m.content for m in user_a_results] == ["用户住在苏州"]


def test_legacy_quarantine_is_not_in_default_retrieval(tmp_path):
    manager = _manager(tmp_path)
    manager.store.save_semantic(
        _memory("用户住在上海"),
        scope="legacy_quarantine",
        user_id="legacy",
    )
    manager.start_session("session-a", user_id="user-a")
    manager.add_memory(_memory("用户住在苏州"), scope="global")

    results = manager.search_visible_semantic("用户住在", limit=5)
    context = "\n".join(m.content for m in results)

    assert "用户住在苏州" in context
    assert "用户住在上海" not in context


@pytest.mark.asyncio
async def test_same_user_subject_predicate_update_replaces_active_fact(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="user-a")

    first_id = await manager._save_extracted_item(
        {
            "type": "FACT",
            "content": "用户年龄是 28 岁",
            "subject": "用户",
            "predicate": "年龄",
            "importance": 0.8,
        }
    )
    second_id = await manager._save_extracted_item(
        {
            "type": "FACT",
            "content": "用户年龄是 29 岁",
            "subject": "用户",
            "predicate": "年龄",
            "importance": 0.8,
        }
    )

    assert first_id != second_id
    old = manager.store.get_semantic(first_id, include_inactive=True)
    saved = manager.store.get_semantic(second_id)
    assert old is not None
    assert old.superseded_by == second_id
    assert saved is not None
    assert saved.content == "用户年龄是 29 岁"

    active = manager.search_memories("用户年龄", scope="user")
    assert [m.content for m in active] == ["用户年龄是 29 岁"]


def test_explicit_none_user_id_does_not_reuse_previous_user(tmp_path):
    manager = _manager(tmp_path)

    manager.start_session("session-a", user_id="user-a")
    manager.add_memory(_memory("用户住在苏州"), scope="global")
    manager.start_session("session-anon", user_id=None)
    manager.add_memory(_memory("匿名用户住在杭州"), scope="global")

    anon_results = manager.search_memories("住在", scope="user")
    assert [m.content for m in anon_results] == ["匿名用户住在杭州"]

    manager.start_session("session-a2", user_id="user-a")
    user_results = manager.search_memories("住在", scope="user")
    assert [m.content for m in user_results] == ["用户住在苏州"]


@pytest.mark.asyncio
async def test_context_compression_quick_facts_are_user_scoped(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="user-a")

    await manager.on_context_compressing(
        [{"role": "user", "content": "我喜欢以后用中文解释复杂问题"}]
    )

    results = manager.search_memories("中文解释", scope="session", scope_owner="session-a")
    assert all(m.user_id == "user-a" for m in results)
