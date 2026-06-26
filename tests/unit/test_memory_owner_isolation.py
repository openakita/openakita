import asyncio
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
    # Backup filename uses target schema version, so v2 dbs upgrade
    # through to the current ``_SCHEMA_VERSION`` (was v4, bumped to v5
    # by v1.27.15 S2 P1-6 for ``conversation_turns.metadata``).
    from openakita.memory.storage import _SCHEMA_VERSION

    assert list(db_path.parent.glob(f"openakita.db.bak.v2_to_v{_SCHEMA_VERSION}.*"))


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


def test_same_user_different_bot_workspaces_do_not_share_memory(tmp_path):
    manager = _manager(tmp_path)

    manager.start_session("writer-session", user_id="user-a", workspace_id="feishu:writer")
    manager.add_memory(_memory("用户喜欢写长文"), scope="global")

    manager.start_session("reviewer-session", user_id="user-a", workspace_id="feishu:reviewer")
    manager.add_memory(_memory("用户喜欢严格审稿"), scope="global")

    reviewer_results = manager.search_memories("用户喜欢", scope="user")
    assert [m.content for m in reviewer_results] == ["用户喜欢严格审稿"]

    manager.start_session("writer-session", user_id="user-a", workspace_id="feishu:writer")
    writer_results = manager.search_memories("用户喜欢", scope="user")
    assert [m.content for m in writer_results] == ["用户喜欢写长文"]


@pytest.mark.asyncio
async def test_memory_scope_context_is_task_local(tmp_path):
    manager = _manager(tmp_path)

    async def run_session(session_id: str, workspace_id: str):
        manager.start_session(session_id, user_id="user-a", workspace_id=workspace_id)
        await asyncio.sleep(0)
        return (
            manager._current_session_id,
            manager._current_user_id,
            manager._current_workspace_id,
        )

    writer, reviewer = await asyncio.gather(
        run_session("writer-session", "feishu:writer"),
        run_session("reviewer-session", "feishu:reviewer"),
    )

    assert writer == ("writer-session", "user-a", "feishu:writer")
    assert reviewer == ("reviewer-session", "user-a", "feishu:reviewer")


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
    assert status.json()["legacy_pending"] == 1
    assert status.json()["has_recoverable_legacy"] is True
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
    status_after = client.get("/api/memories/migration-status").json()
    assert status_after["legacy_pending"] == 0
    assert status_after["has_recoverable_legacy"] is False


def test_migration_status_show_banner_field_phase4(tmp_path):
    """Phase 4：migration-status 应该返回 show_banner（前端唯一信源）+ api_version。

    1. 有真历史 legacy 待 review → show_banner=True
    2. dismiss API 调用后 → show_banner=False（哪怕 legacy 还在）
    3. claim-legacy 成功后 → dismissed sentinel 被清除（show_banner 重新跟随 legacy 数量）
    """
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    manager.store.save_semantic(
        _memory("v1 时期老记忆，需要复核"),
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)

    # 1) 默认状态：有 legacy，banner 应该亮。
    status = client.get("/api/memories/migration-status").json()
    assert status["api_version"] == "v4"
    assert status["show_banner"] is True
    assert status["banner_dismissed"] is False
    assert status["legacy_pending"] == 1

    # 2) 用户点"不再提醒"，sentinel 被写到 _schema_meta。
    dismiss_res = client.post("/api/memories/legacy/dismiss")
    assert dismiss_res.status_code == 200
    assert dismiss_res.json()["dismissed"] is True

    # 幂等：再调一次不报错，行为相同。
    again = client.post("/api/memories/legacy/dismiss")
    assert again.status_code == 200

    status2 = client.get("/api/memories/migration-status").json()
    assert status2["show_banner"] is False
    assert status2["banner_dismissed"] is True
    # 注意：has_recoverable_legacy 是旧字段，反映 "底层还有没有 legacy"，
    # 不受 dismissed 影响 —— 这是有意的，给老前端保留行为。
    assert status2["has_recoverable_legacy"] is True
    assert status2["legacy_pending"] == 1


def test_claim_legacy_clears_banner_dismiss_sentinel(tmp_path):
    """Phase 4：用户主动 claim-legacy 完成后，"不再提醒" sentinel 应被重置。

    场景：如果之后通过任何途径又出现新的 legacy_quarantine（比如导入旧 db 备份），
    banner 应该再次提醒，而不是被旧的 dismiss 永久淹没。
    """
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    manager.store.save_semantic(
        _memory("用户喜欢中文解释"),
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)

    # 先 dismiss
    assert client.post("/api/memories/legacy/dismiss").status_code == 200
    assert client.get("/api/memories/migration-status").json()["show_banner"] is False

    # 然后 claim-legacy
    res = client.post("/api/memories/claim-legacy", json={})
    assert res.status_code == 200

    # 现在没有 pending legacy，show_banner 自然 False；但更关键的是：
    # 模拟未来又出现新 legacy，看 banner 是否会再亮。
    manager.store.save_semantic(
        _memory("以后又导入的旧记忆"),
        scope="legacy_quarantine",
        user_id="legacy",
        workspace_id="default",
        skip_dedup=True,
    )
    status_final = client.get("/api/memories/migration-status").json()
    assert status_final["legacy_pending"] == 1
    assert status_final["banner_dismissed"] is False
    assert status_final["show_banner"] is True


def test_dismiss_endpoint_is_idempotent(tmp_path):
    """Phase 4 回归：dismiss API 反复点不会爆，set_meta 幂等。"""
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    client = _memory_client(manager)

    for _ in range(5):
        res = client.post("/api/memories/legacy/dismiss")
        assert res.status_code == 200
        assert res.json() == {"ok": True, "dismissed": True}

    # 没有任何 legacy 也应能 dismiss，不依赖 legacy 存在。
    status = client.get("/api/memories/migration-status").json()
    assert status["banner_dismissed"] is True
    assert status["show_banner"] is False


def test_dismiss_endpoint_returns_503_when_store_missing():
    """没有 memory store 时端点必须给出明确错误，而不是 500。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openakita.api.routes.memory import router as memory_router

    app = FastAPI()
    app.include_router(memory_router)
    # 不挂 app.state.agent → _get_store 会返回 None
    client = TestClient(app)
    res = client.post("/api/memories/legacy/dismiss")
    assert res.status_code == 503


def test_migration_status_no_banner_when_no_legacy(tmp_path):
    """没有任何 legacy 时，show_banner 必须是 False，
    哪怕 pending_consolidation 桶有东西也不能误亮（那是 DevOps 字段）。"""
    manager = _manager(tmp_path)
    manager.start_session("session-a", user_id="desktop_user")
    # 模拟 lifecycle 后台合成的产物落到 pending_consolidation
    manager.store.save_semantic(
        _memory("后台合成的偏好总结"),
        scope="pending_consolidation",
        user_id="pending",
        workspace_id="default",
        skip_dedup=True,
    )

    client = _memory_client(manager)
    status = client.get("/api/memories/migration-status").json()
    assert status["legacy_pending"] == 0
    assert status["pending_consolidation"] == 1
    assert status["show_banner"] is False
    assert status["has_recoverable_legacy"] is False


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
    status_after = client.get("/api/memories/migration-status").json()
    assert status_after["legacy_pending"] == 0
    assert status_after["legacy_reviewed"] == 1
    assert status_after["has_recoverable_legacy"] is False


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
    status_after = client.get("/api/memories/migration-status").json()
    assert status_after["legacy_pending"] == 0
    assert status_after["legacy_reviewed"] == 1
    assert status_after["has_recoverable_legacy"] is False


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
