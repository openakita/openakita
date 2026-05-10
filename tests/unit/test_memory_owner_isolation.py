import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.memory import router as memory_router
from openakita.memory.manager import MemoryManager
from openakita.memory.relational.types import MemoryNode, NodeType
from openakita.memory.storage import MemoryStorage
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


def _memory_client(manager: MemoryManager) -> TestClient:
    app = FastAPI()
    app.include_router(memory_router)
    app.state.agent = SimpleNamespace(memory_manager=manager)
    return TestClient(app)


def test_v3_migration_backs_up_and_quarantines_legacy_desktop_memory(tmp_path):
    db_path = tmp_path / "old" / "openakita.db"
    db_path.parent.mkdir(parents=True)
    now = datetime.now().isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE _schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _schema_meta VALUES ('version', '2')")
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'fact',
                priority TEXT NOT NULL DEFAULT 'long_term',
                source TEXT DEFAULT '',
                importance_score REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                metadata TEXT DEFAULT '{}',
                subject TEXT DEFAULT '',
                predicate TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5,
                decay_rate REAL DEFAULT 0.1,
                last_accessed_at TEXT,
                superseded_by TEXT,
                source_episode_id TEXT,
                scope TEXT DEFAULT 'global',
                scope_owner TEXT DEFAULT '',
                agent_id TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memories
            (id, content, type, priority, created_at, updated_at, scope, scope_owner)
            VALUES ('legacy-1', 'legacy desktop memory', 'fact', 'long_term', ?, ?, 'global', '')
            """,
            (now, now),
        )
        conn.commit()

    storage = MemoryStorage(db_path)

    rows = storage.load_all(scope="legacy_quarantine", scope_owner="", user_id="legacy")
    assert [row["content"] for row in rows] == ["legacy desktop memory"]
    assert list(db_path.parent.glob("openakita.db.bak.v2_to_v3.*"))


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


def test_memory_migration_status_and_claim_legacy(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    legacy = _memory("用户喜欢中文解释")
    manager.store.save_semantic(
        legacy,
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)
    status = client.get("/api/memories/migration-status")
    assert status.status_code == 200
    assert status.json()["legacy_quarantine"] == 1
    assert status.json()["current_visible"] == 0

    claimed = client.post("/api/memories/claim-legacy", json={})
    assert claimed.status_code == 200
    assert claimed.json()["claimed"] == 1
    assert claimed.json()["rejected"] == 0

    listing = client.get("/api/memories")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["memories"][0]["content"] == "用户喜欢中文解释"
    assert body["memories"][0]["importance_score"] <= 0.65


def test_claim_legacy_keeps_unstructured_task_logs_quarantined(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    manager.store.save_semantic(
        _memory("本轮调用了 read_file 并生成测试报告"),
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)
    claimed = client.post("/api/memories/claim-legacy", json={})
    assert claimed.status_code == 200
    assert claimed.json()["claimed"] == 0
    assert claimed.json()["rejected"] == 1
    assert client.get("/api/memories").json()["total"] == 0


def test_claim_legacy_does_not_override_current_identity_slot(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    manager.add_memory(_memory("用户名字是小红", subject="用户", predicate="姓名"), scope="user")
    manager.store.save_semantic(
        _memory("用户名字是张三", subject="用户", predicate="姓名"),
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)
    claimed = client.post("/api/memories/claim-legacy", json={})
    assert claimed.status_code == 200
    assert claimed.json()["claimed"] == 0
    assert claimed.json()["conflict_skipped"] == 1

    body = client.get("/api/memories").json()
    assert body["total"] == 1
    assert body["memories"][0]["content"] == "用户名字是小红"


def test_memory_graph_is_filtered_by_current_owner(tmp_path):
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="user-a")
    assert manager._ensure_relational()
    manager.relational_store.save_nodes_batch(
        [
            MemoryNode(
                id="node-a",
                content="user a graph memory",
                node_type=NodeType.FACT,
                user_id="user-a",
                workspace_id="default",
                importance=0.9,
            ),
            MemoryNode(
                id="node-b",
                content="user b graph memory",
                node_type=NodeType.FACT,
                user_id="user-b",
                workspace_id="default",
                importance=0.9,
            ),
        ]
    )

    client = _memory_client(manager)
    graph = client.get("/api/memories/graph?limit=10")
    assert graph.status_code == 200
    nodes = graph.json()["nodes"]
    assert [n["id"] for n in nodes] == ["node-a"]
