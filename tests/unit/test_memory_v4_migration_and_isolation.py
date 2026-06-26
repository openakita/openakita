"""Phase 0 + Phase 2b.1 单测。

覆盖：
1. ``test_v3_to_v4_split_legacy_to_pending``:
   v3 升 v4 时，lifecycle 后台合成（``source IN ('daily_consolidation',
   'experience_synthesis')``）的旧记忆从 ``legacy_quarantine`` 迁到
   ``pending_consolidation``，并写入 ``_memory_scope_audit``。
   真历史 v1/v2 旧数据（其他 source）继续留在 ``legacy_quarantine``。
2. ``test_lifecycle_extracted_item_lands_in_pending_when_tenant_unknown``:
   ``_save_extracted_item`` 拿不到 tenant 时落 ``pending_consolidation``，
   不再污染 ``legacy_quarantine``。
3. ``test_lifecycle_extracted_item_lands_in_user_when_tenant_known``:
   ``_save_extracted_item`` 拿到 tenant 时直接进对应租户的 ``user`` scope。
4. ``test_global_store_source_blocks_cross_user``:
   Phase 2b.1 — ``_GlobalStoreSource`` 必须按 owner_provider 透传的
   (user_id, workspace_id) 过滤，不能跨用户返回结果。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from openakita.memory.lifecycle import LifecycleManager
from openakita.memory.storage import _SCHEMA_VERSION, MemoryStorage
from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory
from openakita.memory.unified_store import UnifiedStore

# ----------------------------------------------------------------------
# 用 raw sqlite 造一个 v3 库，再让 MemoryStorage 读它触发 v3→v4 迁移
# ----------------------------------------------------------------------


def _build_v3_db_with_legacy_rows(
    db_path: Path,
    *,
    extra_turn_sessions: list[str] | None = None,
) -> dict[str, str]:
    """直接造一个 v3 schema 的 sqlite 数据库，预置两条 legacy_quarantine 记忆：

    - mem-lifecycle：``source='daily_consolidation'``，应被 v4 迁出。
    - mem-true-legacy：``source='manual'``，应继续留在 legacy_quarantine。

    可选 ``extra_turn_sessions`` 用于在 conversation_turns 里插入若干 session_id，
    模拟 v3 库中已有未抽取对话，验证 v4 backfill session_tenants 行为。

    返回 {"lifecycle_id": ..., "true_legacy_id": ...}。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE _schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _schema_meta(key, value) VALUES ('version', '3')")
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                priority TEXT NOT NULL,
                content TEXT NOT NULL,
                subject TEXT DEFAULT '',
                predicate TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                source_episode_id TEXT DEFAULT '',
                importance_score REAL DEFAULT 0.5,
                confidence REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                superseded_by TEXT,
                scope TEXT DEFAULT 'user',
                scope_owner TEXT DEFAULT '',
                user_id TEXT DEFAULT 'default',
                workspace_id TEXT DEFAULT 'default',
                agent_id TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_results TEXT,
                has_tool_calls BOOLEAN DEFAULT FALSE,
                timestamp TEXT NOT NULL,
                token_estimate INTEGER,
                episode_id TEXT,
                extracted BOOLEAN DEFAULT FALSE,
                UNIQUE(session_id, turn_index)
            )
            """
        )
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO memories
                (id, type, priority, content, source, importance_score, confidence,
                 created_at, updated_at, scope, scope_owner, user_id, workspace_id)
            VALUES (?, 'fact', 'long_term', 'lifecycle synthesized fact',
                    'daily_consolidation', 0.6, 0.7, ?, ?, 'legacy_quarantine', '',
                    'legacy', 'default')
            """,
            ("mem-lifecycle", now, now),
        )
        conn.execute(
            """
            INSERT INTO memories
                (id, type, priority, content, source, importance_score, confidence,
                 created_at, updated_at, scope, scope_owner, user_id, workspace_id)
            VALUES (?, 'fact', 'long_term', 'true legacy fact from v1 export',
                    'manual', 0.7, 0.8, ?, ?, 'legacy_quarantine', '',
                    'legacy', 'default')
            """,
            ("mem-true-legacy", now, now),
        )
        for sess in extra_turn_sessions or []:
            conn.execute(
                """
                INSERT INTO conversation_turns
                    (session_id, turn_index, role, content, timestamp, extracted)
                VALUES (?, 0, 'user', 'hello', ?, FALSE)
                """,
                (sess, now),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "lifecycle_id": "mem-lifecycle",
        "true_legacy_id": "mem-true-legacy",
    }


def test_v3_to_v4_split_legacy_to_pending(tmp_path: Path):
    db_path = tmp_path / "openakita.db"
    ids = _build_v3_db_with_legacy_rows(db_path)

    storage = MemoryStorage(db_path, _register=False)
    assert storage._get_schema_version() == _SCHEMA_VERSION

    lifecycle_row = storage.get_memory(ids["lifecycle_id"])
    legacy_row = storage.get_memory(ids["true_legacy_id"])
    assert lifecycle_row is not None
    assert legacy_row is not None

    # daily_consolidation 产物迁出到 pending_consolidation
    assert lifecycle_row["scope"] == "pending_consolidation"
    # 真历史 legacy 继续留在 legacy_quarantine
    assert legacy_row["scope"] == "legacy_quarantine"

    audit_rows = storage._conn.execute(
        "SELECT memory_id, old_scope, new_scope, migration_version, reason "
        "FROM _memory_scope_audit ORDER BY memory_id"
    ).fetchall()
    audit = {row[0]: row for row in audit_rows}
    # 只有 lifecycle 那条要审计
    assert ids["lifecycle_id"] in audit
    assert ids["true_legacy_id"] not in audit
    moved = audit[ids["lifecycle_id"]]
    assert moved[1] == "legacy_quarantine"
    assert moved[2] == "pending_consolidation"
    assert moved[3] == "v3_to_v4"
    assert moved[4] == "v3_to_v4_source_lifecycle"

    # session_tenants 表存在但为空（本测试没插任何 conversation_turns）
    cnt = storage._conn.execute("SELECT COUNT(*) FROM session_tenants").fetchone()[0]
    assert cnt == 0


def test_v3_to_v4_backfills_session_tenants_from_conversation_turns(tmp_path: Path):
    """v3→v4 升级时，conversation_turns 里出现过的 session_id 都应在
    session_tenants 中得到登记，避免老 unextracted turn 升级后被误落
    pending_consolidation。

    解析规则：
    - IM 通道 conversation_safe_id 形如 ``ns__chat__user[__thread]`` →
      取第 3 段作 user_id；
    - 桌面 / CLI 形如 ``YYYYMMDD_HHMMSS_xxx`` 单段 → default。
    """
    db_path = tmp_path / "openakita.db"
    _build_v3_db_with_legacy_rows(
        db_path,
        extra_turn_sessions=[
            "telegram__chat-100__alice",
            "telegram__chat-200__bob__thread-1",
            "20251115_120000_abc12345",  # desktop CLI 单段
            "feishu__group-7__default",  # IM 但 user 段是 default
            "feishu__group-9__anonymous",  # 占位身份
            "",  # 空 session_id（不应入表）
        ],
    )

    storage = MemoryStorage(db_path, _register=False)

    rows = storage._conn.execute(
        "SELECT session_id, user_id, workspace_id FROM session_tenants ORDER BY session_id"
    ).fetchall()
    mapping = {sid: (u, w) for sid, u, w in rows}

    # IM 通道里 user 段是真实身份的 → 取出来
    assert mapping["telegram__chat-100__alice"] == ("alice", "default")
    assert mapping["telegram__chat-200__bob__thread-1"] == ("bob", "default")
    # IM 但 user 段是 default / anonymous → 降级为 default
    assert mapping["feishu__group-7__default"] == ("default", "default")
    assert mapping["feishu__group-9__anonymous"] == ("default", "default")
    # desktop CLI 单段 → default
    assert mapping["20251115_120000_abc12345"] == ("default", "default")
    # 空 session_id 不入表
    assert "" not in mapping


def test_workspace_resolver_default_behavior(monkeypatch):
    """Phase 2a：默认情况（无 opt-in）resolver 返回与历史一致的字符串。"""
    from openakita.memory.workspace_resolver import (
        LEGACY_DEFAULT_WORKSPACE_ID,
        resolve_memory_workspace_id,
    )

    monkeypatch.delenv("OPENAKITA_DESKTOP_PROJECT_WORKSPACE", raising=False)

    class _S:
        def __init__(self, channel="desktop", metadata=None, bot_instance_id=None):
            self.channel = channel
            self.metadata = metadata or {}
            self.bot_instance_id = bot_instance_id

    # session=None → "default"
    assert resolve_memory_workspace_id(None) == LEGACY_DEFAULT_WORKSPACE_ID

    # desktop / api / cli / web → 仍是 "default"
    for ch in ("desktop", "api", "cli", "web"):
        assert resolve_memory_workspace_id(_S(channel=ch)) == LEGACY_DEFAULT_WORKSPACE_ID

    # IM 通道：用 bot_instance_id 或 channel 名
    assert resolve_memory_workspace_id(_S(channel="telegram", bot_instance_id="bot-1")) == "bot-1"
    assert resolve_memory_workspace_id(_S(channel="feishu")) == "feishu"

    # metadata 显式覆盖一切
    assert (
        resolve_memory_workspace_id(
            _S(channel="desktop", metadata={"memory_workspace_id": "explicit-ws"})
        )
        == "explicit-ws"
    )


def test_workspace_resolver_opt_in_project_mode(monkeypatch, tmp_path):
    """Phase 2a：opt-in（env or session metadata）后 desktop 改用项目哈希。"""
    from openakita.memory.workspace_resolver import (
        is_project_workspace,
        resolve_desktop_workspace_id,
        resolve_memory_workspace_id,
    )

    class _S:
        def __init__(self, channel="desktop", metadata=None):
            self.channel = channel
            self.metadata = metadata or {}

    # 路径相同时 → workspace_id 相同；不同路径 → 不同
    p1 = tmp_path / "proj-a"
    p1.mkdir()
    p2 = tmp_path / "proj-b"
    p2.mkdir()
    ws_a = resolve_desktop_workspace_id(p1)
    ws_b = resolve_desktop_workspace_id(p2)
    assert ws_a != ws_b
    assert is_project_workspace(ws_a)
    assert resolve_desktop_workspace_id(p1) == ws_a  # 稳定

    # 通过 metadata 切到 project 模式
    monkeypatch.chdir(p1)
    via_meta = resolve_memory_workspace_id(
        _S(channel="desktop", metadata={"memory_workspace_mode": "project"})
    )
    assert is_project_workspace(via_meta)

    # 通过 env 切到 project 模式
    monkeypatch.setenv("OPENAKITA_DESKTOP_PROJECT_WORKSPACE", "1")
    via_env = resolve_memory_workspace_id(_S(channel="desktop"))
    assert is_project_workspace(via_env)


def test_search_memories_workspace_fallback(tmp_path):
    """Phase 2a：search_memories 显式传 fallback_workspace_id 时，主 workspace
    命中不足 limit 会从 fallback 桶补齐；命中充足则不补。"""
    from openakita.memory.manager import MemoryManager

    manager = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    manager.start_session("sess-1", user_id="alice", workspace_id="proj-a")

    # alice 在 proj-a workspace 下有 1 条记忆，在 default 下有 2 条
    for content, ws in [
        ("alice in proj-a", "proj-a"),
        ("alice fallback A", "default"),
        ("alice fallback B", "default"),
    ]:
        mem = SemanticMemory(
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            content=content,
        )
        manager.store.save_semantic(
            mem, scope="user", scope_owner="", user_id="alice", workspace_id=ws
        )
    manager._reload_from_sqlite()

    # 不带 fallback：只看主 workspace
    primary = manager.search_memories(
        query="alice",
        scope="user",
        user_id="alice",
        workspace_id="proj-a",
        limit=10,
    )
    assert {m.content for m in primary} == {"alice in proj-a"}

    # 带 fallback：主不足时补 fallback 内容
    with_fallback = manager.search_memories(
        query="alice",
        scope="user",
        user_id="alice",
        workspace_id="proj-a",
        fallback_workspace_id="default",
        limit=10,
    )
    contents = {m.content for m in with_fallback}
    assert contents == {"alice in proj-a", "alice fallback A", "alice fallback B"}


def test_storage_migrate_workspace_id(tmp_path):
    """Phase 2a：storage.migrate_workspace_id 只移目标 (scope, user_id,
    workspace_id) 组合的行，其他用户 / 其他 scope / 其他 workspace 不动。"""
    from openakita.memory.unified_store import UnifiedStore

    store = UnifiedStore(db_path=tmp_path / "memory.db", backend_type="fts5")

    # 用 alice (default) + bob (default) + alice (proj-a) 三种组合
    for content, user_id, ws in [
        ("alice default 1", "alice", "default"),
        ("alice default 2", "alice", "default"),
        ("bob default keep", "bob", "default"),
        ("alice proj-a keep", "alice", "proj-a"),
    ]:
        mem = SemanticMemory(
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            content=content,
        )
        store.save_semantic(mem, scope="user", scope_owner="", user_id=user_id, workspace_id=ws)

    moved = store.migrate_workspace_id(
        from_workspace_id="default", to_workspace_id="proj-a", user_id="alice"
    )
    assert moved == 2

    # alice 的 default 桶清空，proj-a 桶变成 3 条
    alice_default = store.load_all_memories(
        scope="user", scope_owner="", user_id="alice", workspace_id="default"
    )
    alice_proj_a = store.load_all_memories(
        scope="user", scope_owner="", user_id="alice", workspace_id="proj-a"
    )
    assert alice_default == []
    assert len(alice_proj_a) == 3

    # bob 完全没动
    bob_default = store.load_all_memories(
        scope="user", scope_owner="", user_id="bob", workspace_id="default"
    )
    assert len(bob_default) == 1
    assert bob_default[0].content == "bob default keep"

    # audit 表记录两条 workspace_migrate
    rows = store.db._conn.execute(
        "SELECT memory_id, reason, migration_version FROM _memory_scope_audit "
        "WHERE migration_version = 'workspace_migrate'"
    ).fetchall()
    assert len(rows) == 2
    for _mid, reason, ver in rows:
        assert reason == "workspace_migrate:default->proj-a"
        assert ver == "workspace_migrate"


def test_iter_cached_excludes_isolated_buckets_by_default(tmp_path: Path):
    """Phase 1B：iter_cached 默认排除 legacy_quarantine / pending_consolidation；
    显式 include_isolated=True 才能看到。"""
    from openakita.memory.manager import MemoryManager

    manager = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    # 直接走 store 写入三种 scope
    for scope, user_id, content in [
        ("user", "alice", "alice cached visible note"),
        ("legacy_quarantine", "legacy", "legacy hidden note"),
        ("pending_consolidation", "pending", "pending hidden note"),
    ]:
        mem = SemanticMemory(
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            content=content,
        )
        manager.store.save_semantic(
            mem, scope=scope, scope_owner="", user_id=user_id, workspace_id="default"
        )
    # 重新读 SQLite 到缓存
    manager._reload_from_sqlite()

    visible = [m.content for m in manager.iter_cached()]
    assert "alice cached visible note" in visible
    assert "legacy hidden note" not in visible
    assert "pending hidden note" not in visible

    all_seen = [m.content for m in manager.iter_cached(include_isolated=True)]
    assert "legacy hidden note" in all_seen
    assert "pending hidden note" in all_seen


def test_keyword_search_never_returns_isolated(tmp_path: Path):
    """Phase 1B：_keyword_search fallback 不能返回 legacy_quarantine /
    pending_consolidation 桶内容，防止搜索后端失败时泄漏到提示词。"""
    from openakita.memory.manager import MemoryManager

    manager = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    for scope, user_id, content in [
        ("user", "alice", "watermelon visible to keyword search"),
        ("legacy_quarantine", "legacy", "watermelon should stay isolated"),
        ("pending_consolidation", "pending", "watermelon also isolated"),
    ]:
        mem = SemanticMemory(
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            content=content,
        )
        manager.store.save_semantic(
            mem, scope=scope, scope_owner="", user_id=user_id, workspace_id="default"
        )
    manager._reload_from_sqlite()

    hits = manager._keyword_search("watermelon visible", limit=10)
    contents = [m.content for m in hits]
    assert "watermelon visible to keyword search" in contents
    assert all("isolated" not in c for c in contents)


def test_dual_write_is_no_op_after_v4(tmp_path: Path):
    """Phase 1A：_save_memories 不再 dual-write 到 memories.json。"""
    from openakita.memory.manager import MemoryManager

    manager = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    json_file = manager.memories_file
    # 触发显式调用，模拟旧代码路径
    manager._save_memories()
    # backfill 时如果没有 memories.json 应留下 sentinel 但不创建新 json
    assert not json_file.exists()
    assert manager.store.get_meta(manager._LEGACY_JSON_BACKFILL_SENTINEL) is not None


def test_backfill_sentinel_archives_legacy_json_once(tmp_path: Path):
    """Phase 1A：第一次启动看到 memories.json 时执行 backfill，归档文件，写
    sentinel；第二次启动不再读取原文件，也不需要重做 backfill。"""
    import json as _json

    from openakita.memory.manager import MemoryManager

    data_dir = tmp_path / "memory"
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy = [
        {
            "id": "leg-1",
            "content": "user prefers concise replies",
            "type": "preference",
            "priority": "long_term",
            "source": "manual",
            "importance_score": 0.7,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    ]
    (data_dir / "memories.json").write_text(
        _json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )

    manager = MemoryManager(
        data_dir=data_dir,
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )

    # 原始 memories.json 应被改名归档
    archived = sorted(data_dir.glob("memories.json.archived.*"))
    assert len(archived) == 1
    assert not (data_dir / "memories.json").exists()

    # sentinel 设置完成
    sentinel = manager.store.get_meta(manager._LEGACY_JSON_BACKFILL_SENTINEL)
    assert sentinel is not None
    assert "backfilled" in sentinel or sentinel == "no_legacy_file"

    # 第二次启动：放回一份新 memories.json（模拟用户复制旧库回来）—— 不应再次读取或归档
    second_json = [
        {
            "id": "leg-2",
            "content": "ghost",
            "type": "fact",
            "priority": "short_term",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    ]
    (data_dir / "memories.json").write_text(
        _json.dumps(second_json, ensure_ascii=False), encoding="utf-8"
    )
    # 强制重建 manager 以模拟新进程启动
    from openakita.memory.storage import _instance_registry

    _instance_registry.clear()
    manager2 = MemoryManager(
        data_dir=data_dir,
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    # 第二次启动后 memories.json 仍然存在（未被归档，因为 sentinel 已存在 → 直接跳过）
    assert (data_dir / "memories.json").exists()
    # 不应导入到 SQLite
    assert manager2.store.get_meta(manager2._LEGACY_JSON_BACKFILL_SENTINEL) is not None
    leg2_visible = manager2.store.load_all_memories(
        scope="legacy_quarantine",
        scope_owner="",
        user_id="legacy",
        workspace_id=None,
        include_inactive=True,
    )
    assert all(m.content != "ghost" for m in leg2_visible)


def test_v3_to_v4_backfill_is_idempotent(tmp_path: Path):
    """重复打开同一个已迁移到 v4 的库不应再次 backfill 或动数据。"""
    db_path = tmp_path / "openakita.db"
    _build_v3_db_with_legacy_rows(db_path, extra_turn_sessions=["telegram__a__alice"])

    s1 = MemoryStorage(db_path, _register=False)
    rows_before = s1._conn.execute(
        "SELECT session_id, user_id, last_updated_at FROM session_tenants"
    ).fetchall()
    s1.close()

    # 再次打开（已经是 v4，不会再走 migration）
    s2 = MemoryStorage(db_path, _register=False)
    rows_after = s2._conn.execute(
        "SELECT session_id, user_id, last_updated_at FROM session_tenants"
    ).fetchall()
    assert rows_before == rows_after


# ----------------------------------------------------------------------
# Lifecycle 抽取产物落桶测试（不真正调 LLM，直接走 _save_extracted_item）
# ----------------------------------------------------------------------


def _make_lifecycle(tmp_path: Path) -> tuple[LifecycleManager, UnifiedStore]:
    store = UnifiedStore(db_path=tmp_path / "memory.db", backend_type="fts5")
    lifecycle = LifecycleManager(
        store=store,
        extractor=None,
        identity_dir=tmp_path / "identity",
    )
    return lifecycle, store


def test_lifecycle_extracted_item_lands_in_pending_when_tenant_unknown(tmp_path: Path):
    lifecycle, store = _make_lifecycle(tmp_path)

    item = {
        "type": "FACT",
        "content": "User prefers dark mode for the dashboard",
        "subject": "user",
        "predicate": "theme",
        "importance": 0.55,
    }
    lifecycle._save_extracted_item(item, tenant=None)

    legacy = store.load_all_memories(
        scope="legacy_quarantine",
        scope_owner="",
        user_id="legacy",
        workspace_id=None,
        include_inactive=True,
    )
    pending = store.load_all_memories(
        scope="pending_consolidation",
        scope_owner="",
        user_id=None,
        workspace_id=None,
        include_inactive=True,
    )
    assert legacy == []
    assert len(pending) == 1
    assert pending[0].user_id == "pending"
    assert pending[0].scope == "pending_consolidation"


def test_lifecycle_extracted_item_lands_in_user_when_tenant_known(tmp_path: Path):
    lifecycle, store = _make_lifecycle(tmp_path)

    item = {
        "type": "PREFERENCE",
        "content": "User wants concise commit messages",
        "subject": "user",
        "predicate": "commit_style",
        "importance": 0.62,
    }
    lifecycle._save_extracted_item(item, tenant=("alice", "proj-a"))

    visible = store.load_all_memories(
        scope="user",
        scope_owner="",
        user_id="alice",
        workspace_id="proj-a",
    )
    other_user = store.load_all_memories(
        scope="user",
        scope_owner="",
        user_id="bob",
        workspace_id="proj-a",
    )
    pending = store.load_all_memories(
        scope="pending_consolidation",
        scope_owner="",
        user_id=None,
        workspace_id=None,
        include_inactive=True,
    )
    assert len(visible) == 1
    assert visible[0].user_id == "alice"
    assert visible[0].workspace_id == "proj-a"
    assert other_user == []
    assert pending == []


def test_lifecycle_resolve_tenant_accepts_registered_default(tmp_path: Path):
    """desktop / CLI 单用户场景：session_tenants 登记 default 是合法身份，
    不能被 lifecycle 误判为共享桶而拒绝。"""
    lifecycle, store = _make_lifecycle(tmp_path)
    store.upsert_session_tenant("sess-desktop", "default", "default")

    tenant = lifecycle._resolve_tenant_for_session("sess-desktop")
    assert tenant == ("default", "default")

    item = {
        "type": "FACT",
        "content": "Desktop single-user fact",
        "subject": "user",
        "predicate": "city",
        "importance": 0.55,
    }
    lifecycle._save_extracted_item(item, tenant=tenant)

    visible = store.load_all_memories(
        scope="user", scope_owner="", user_id="default", workspace_id="default"
    )
    pending = store.load_all_memories(
        scope="pending_consolidation",
        scope_owner="",
        user_id=None,
        workspace_id=None,
        include_inactive=True,
    )
    assert len(visible) == 1
    assert visible[0].content == "Desktop single-user fact"
    assert pending == []


def test_lifecycle_resolve_tenant_rejects_placeholder_identities(tmp_path: Path):
    """anonymous / legacy / system / 空 是显式占位身份，不能当成有效归属。"""
    lifecycle, store = _make_lifecycle(tmp_path)
    for placeholder in ("anonymous", "legacy", "system"):
        store.upsert_session_tenant(f"sess-{placeholder}", placeholder, "default")
        assert lifecycle._resolve_tenant_for_session(f"sess-{placeholder}") is None
    # 表里没登记的 session：同样返回 None
    assert lifecycle._resolve_tenant_for_session("sess-unknown") is None


# ----------------------------------------------------------------------
# Phase 2b.1: _GlobalStoreSource 跨用户检索过滤
# ----------------------------------------------------------------------


class _FakeStore:
    """轻量假 store，捕获 search_semantic 调用并按 user_id 隔离返回。"""

    def __init__(self) -> None:
        self.last_kwargs: dict | None = None
        self._data = {
            ("alice", "proj-a"): [
                SemanticMemory(
                    type=MemoryType.FACT,
                    priority=MemoryPriority.LONG_TERM,
                    content="alice secret note",
                )
            ],
            ("bob", "proj-a"): [
                SemanticMemory(
                    type=MemoryType.FACT,
                    priority=MemoryPriority.LONG_TERM,
                    content="bob secret note",
                )
            ],
        }

    def search_semantic(
        self, query, *, limit=8, scope=None, scope_owner=None, user_id=None, workspace_id=None, **_
    ):
        self.last_kwargs = {
            "query": query,
            "limit": limit,
            "scope": scope,
            "scope_owner": scope_owner,
            "user_id": user_id,
            "workspace_id": workspace_id,
        }
        return list(self._data.get((user_id, workspace_id), []))


def test_isolated_memory_md_seed_phase_2b3():
    """Phase 2b.3：seed 内容包含 profile name + id，并明确说明是独立记忆。"""
    from openakita.agents.factory import AgentFactory
    from openakita.agents.profile import AgentProfile, AgentType

    profile = AgentProfile(
        id="my-coder",
        name="My Coder",
        description="d",
        type=AgentType.CUSTOM,
        created_by="u",
        memory_mode="isolated",
    )
    seed = AgentFactory._isolated_memory_md_seed(profile)
    assert "My Coder" in seed
    assert "my-coder" in seed
    assert "独立记忆" in seed or "isolated" in seed.lower()
    # 不应该硬编码全局 OpenAkita 身份标记
    assert "OpenAkita 的全局记忆" not in seed


def test_apply_memory_isolation_seeds_md_when_missing(tmp_path: Path, monkeypatch):
    """Phase 2b.3：isolated agent 首次启动时，profile_dir/identity/MEMORY.md
    应该被自动 seed，而**不**回退到全局 settings.memory_path。"""
    from unittest.mock import MagicMock

    from openakita.agents.factory import AgentFactory
    from openakita.agents.profile import AgentProfile, AgentType

    # 准备 profile 目录
    profile_dir = tmp_path / "agents" / "profiles" / "test-iso"
    profile_dir.mkdir(parents=True)

    fake_store = MagicMock()
    fake_store.ensure_profile_dir.return_value = profile_dir
    monkeypatch.setattr("openakita.agents.profile.get_profile_store", lambda: fake_store)

    profile = AgentProfile(
        id="test-iso",
        name="Test Iso",
        description="d",
        type=AgentType.CUSTOM,
        created_by="u",
        memory_mode="isolated",
        memory_inherit_global=False,
    )

    fake_agent = MagicMock()
    fake_agent.brain = MagicMock()
    fake_agent.memory_manager = MagicMock()
    fake_agent.memory_manager.store = MagicMock()
    monkeypatch.setattr(
        "openakita.memory.manager.MemoryManager",
        lambda **kwargs: MagicMock(
            _current_owner=lambda: ("default", "default"),
            retrieval_engine=MagicMock(_external_sources=[]),
        ),
    )

    # 关键断言：执行前 MEMORY.md 不存在
    md_path = profile_dir / "identity" / "MEMORY.md"
    assert not md_path.exists()

    AgentFactory._apply_memory_isolation(fake_agent, profile)

    # 执行后应该自动 seed
    assert md_path.exists()
    seed = md_path.read_text(encoding="utf-8")
    assert "Test Iso" in seed
    assert "test-iso" in seed


def test_apply_memory_isolation_does_not_overwrite_existing_md(tmp_path: Path, monkeypatch):
    """Phase 2b.3：已经存在的 MEMORY.md 不能被 seed 覆盖（防止抹掉用户数据）。"""
    from unittest.mock import MagicMock

    from openakita.agents.factory import AgentFactory
    from openakita.agents.profile import AgentProfile, AgentType

    profile_dir = tmp_path / "agents" / "profiles" / "test-iso2"
    (profile_dir / "identity").mkdir(parents=True)
    md_path = profile_dir / "identity" / "MEMORY.md"
    existing_content = "# 用户辛苦编辑过的内容\n\n- 偏好A\n- 偏好B\n"
    md_path.write_text(existing_content, encoding="utf-8")

    fake_store = MagicMock()
    fake_store.ensure_profile_dir.return_value = profile_dir
    monkeypatch.setattr("openakita.agents.profile.get_profile_store", lambda: fake_store)

    profile = AgentProfile(
        id="test-iso2",
        name="Test Iso 2",
        description="d",
        type=AgentType.CUSTOM,
        created_by="u",
        memory_mode="isolated",
        memory_inherit_global=False,
    )

    fake_agent = MagicMock()
    fake_agent.brain = MagicMock()
    fake_agent.memory_manager = MagicMock()
    fake_agent.memory_manager.store = MagicMock()
    monkeypatch.setattr(
        "openakita.memory.manager.MemoryManager",
        lambda **kwargs: MagicMock(
            _current_owner=lambda: ("default", "default"),
            retrieval_engine=MagicMock(_external_sources=[]),
        ),
    )

    AgentFactory._apply_memory_isolation(fake_agent, profile)

    assert md_path.read_text(encoding="utf-8") == existing_content


def test_agent_profile_memory_isolation_alias_phase_2b2():
    """Phase 2b.2：AgentProfile 同时支持新名 memory_isolation 和旧名 memory_mode。"""
    from openakita.agents.profile import AgentProfile, AgentType

    # 1) 默认值
    p = AgentProfile(id="t1", name="t1", description="d", type=AgentType.CUSTOM, created_by="u")
    assert p.memory_mode == "shared"
    assert p.memory_isolation == "shared"

    # 2) 写新名同步到旧字段
    p.memory_isolation = "isolated"
    assert p.memory_mode == "isolated"
    assert p.memory_isolation == "isolated"

    # 3) to_dict 同时输出两个键，方便前端逐步迁移
    d = p.to_dict()
    assert d["memory_mode"] == "isolated"
    assert d["memory_isolation"] == "isolated"

    # 4) from_dict 只给新名也能正确还原
    p2 = AgentProfile.from_dict(
        {
            "id": "t2",
            "name": "t2",
            "description": "d",
            "type": "custom",
            "created_by": "u",
            "memory_isolation": "isolated",
        }
    )
    assert p2.memory_mode == "isolated"

    # 5) 同时给两个键，新名优先（与 to_dict 顺序一致）
    p3 = AgentProfile.from_dict(
        {
            "id": "t3",
            "name": "t3",
            "description": "d",
            "type": "custom",
            "created_by": "u",
            "memory_mode": "shared",
            "memory_isolation": "isolated",
        }
    )
    assert p3.memory_mode == "isolated"

    # 6) 历史 JSON 文件（只有旧名）继续工作
    p4 = AgentProfile.from_dict(
        {
            "id": "t4",
            "name": "t4",
            "description": "d",
            "type": "custom",
            "created_by": "u",
            "memory_mode": "isolated",
        }
    )
    assert p4.memory_mode == "isolated"
    assert p4.memory_isolation == "isolated"


def test_get_stats_owner_filter_phase_2b5_audit(tmp_path: Path):
    """三次审计：get_stats 也是 LLM 工具可触达接口，counts 必须按 owner 收敛。

    多用户 IM 部署下不收敛会让 alice 看到"系统总记忆 1000"从而推断出存在
    其他用户（信息泄漏，不是内容泄漏，但仍是 leak）。
    """
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.store.upsert_session_tenant("sess-alice", "alice", "proj-a")
    mm.store.upsert_session_tenant("sess-bob", "bob", "proj-a")

    for content in ("alice fact 1", "alice fact 2", "alice fact 3"):
        mm.store.save_semantic(
            SemanticMemory(
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                content=content,
            ),
            scope="user",
            scope_owner="",
            user_id="alice",
            workspace_id="proj-a",
        )
    for content in ("bob fact 1", "bob fact 2"):
        mm.store.save_semantic(
            SemanticMemory(
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                content=content,
            ),
            scope="user",
            scope_owner="",
            user_id="bob",
            workspace_id="proj-a",
        )
    if hasattr(mm, "_reload_from_sqlite"):
        mm._reload_from_sqlite()

    # 不传 owner → 兼容旧行为，看到所有 5 条
    all_stats = mm.get_stats()
    assert all_stats["total"] == 5

    # 传 alice owner → 只看到 alice 的 3 条
    alice_stats = mm.get_stats(user_id="alice", workspace_id="proj-a")
    assert alice_stats["total"] == 3

    # 传 bob owner → 只看到 bob 的 2 条
    bob_stats = mm.get_stats(user_id="bob", workspace_id="proj-a")
    assert bob_stats["total"] == 2

    # 不存在的用户 → 空
    ghost_stats = mm.get_stats(user_id="ghost", workspace_id="proj-a")
    assert ghost_stats["total"] == 0


def test_iter_owned_session_ids_phase_2b5_audit(tmp_path: Path):
    """二次审计：iter_owned_session_ids 是 JSONL/react_traces 文件级过滤的核心
    依赖；只能返回该 user_id（可选 workspace）的 session_id 集合。"""
    storage = MemoryStorage(tmp_path / "openakita.db")
    storage.upsert_session_tenant("sess-alice-1", "alice", "proj-a")
    storage.upsert_session_tenant("sess-alice-2", "alice", "proj-b")
    storage.upsert_session_tenant("sess-bob-1", "bob", "proj-a")

    alice_all = set(storage.iter_owned_session_ids(user_id="alice"))
    assert alice_all == {"sess-alice-1", "sess-alice-2"}

    alice_proj_a = set(storage.iter_owned_session_ids(user_id="alice", workspace_id="proj-a"))
    assert alice_proj_a == {"sess-alice-1"}

    # 不存在的 user → 空集
    assert storage.iter_owned_session_ids(user_id="ghost") == []


def test_trace_memory_blocks_cross_owner_access(tmp_path: Path):
    """二次审计：trace_memory 是按显式 ID 直读的接口。必须做 owner 校验，否则
    LLM 拿到别人的 memory_id 直接就能读完整内容。"""
    from unittest.mock import MagicMock

    from openakita.memory.manager import MemoryManager
    from openakita.tools.handlers.memory import MemoryHandler

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.store.upsert_session_tenant("sess-alice", "alice", "proj-a")
    mm.store.upsert_session_tenant("sess-bob", "bob", "proj-a")

    bob_mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="bob 的内部秘密",
    )
    mm.store.save_semantic(
        bob_mem, scope="user", scope_owner="", user_id="bob", workspace_id="proj-a"
    )
    if hasattr(mm, "_reload_from_sqlite"):
        mm._reload_from_sqlite()

    mm._current_user_id = "alice"
    mm._current_workspace_id = "proj-a"

    fake_agent = MagicMock()
    fake_agent.memory_manager = mm
    handler = MemoryHandler(fake_agent)

    out = handler._trace_from_memory(mm.store, bob_mem.id)
    assert "未找到记忆" in out, f"alice 不应该能读到 bob 的 memory，但 trace 返回: {out!r}"


def test_stem_matches_session_allow_set():
    """三次审计：allow-set 的边界匹配契约。

    必须用**边界感知**匹配，否则 alice 的 sid="user_alice" 会误命中
    bob 的 stem="trace_user_alice2_<ts>"（user_alice 是 user_alice2 的子串）。
    """
    from openakita.tools.handlers.memory import MemoryHandler

    fn = MemoryHandler._stem_matches_session_allow_set

    # 空 allow-set / None → 默认拒绝（owner 已知但没 owned session，安全侧）
    assert fn("trace_anything_123.json", set()) is False
    assert fn("trace_anything_123.json", None) is False

    # 正常命中：session_id 在 stem 里被 _ 分隔包围
    allowed = {"im_telegram__chat__user_alice"}
    assert fn("trace_im_telegram__chat__user_alice_1716000000.json", allowed) is True
    assert fn("trace_im_telegram__chat__user_bob_1716000000.json", allowed) is False

    # ★ 边界绕过攻击：alice 的 sid 是 bob 文件名里 sid 的**严格前缀**
    # 子串匹配会让 alice 误读到 bob 的文件 —— 这就是修复的关键。
    allowed_alice = {"user_alice"}
    assert fn("trace_user_alice_1716000000", allowed_alice) is True  # 真 alice
    assert fn("trace_user_alice2_1716000000", allowed_alice) is False  # bob 的伪装
    assert fn("trace_xuser_alice_1716000000", allowed_alice) is False  # 前缀也要拒
    assert fn("trace_user_alicex_1716000000", allowed_alice) is False  # 后缀也要拒

    # 桌面 desktop 格式 session 也要正确边界
    allowed_desk = {"20260517_103000_abc"}
    assert fn("trace_20260517_103000_abc_1716000000", allowed_desk) is True
    assert fn("trace_20260517_103000_abc1_1716000000", allowed_desk) is False
    # session_id 出现在 stem 开头（罕见但合法）
    assert fn("20260517_103000_abc.jsonl", allowed_desk) is True
    # session_id 出现在 stem 结尾
    assert fn("conversation_20260517_103000_abc", allowed_desk) is True

    # 含非 ASCII 字符的 session_id 也要工作
    allowed_unicode = {"用户_alice"}
    assert fn("trace_用户_alice_1716", allowed_unicode) is True
    assert fn("trace_用户_alice2_1716", allowed_unicode) is False


def test_search_episodes_tenant_filter_phase_2b5(tmp_path: Path):
    """Phase 2b.5：search_episodes 带 user_id/workspace_id 时通过 JOIN session_tenants
    只返回该租户的 episode；不传则保持旧的全库扫描行为，向后兼容。"""
    storage = MemoryStorage(tmp_path / "openakita.db")
    storage.upsert_session_tenant("sess-alice", "alice", "proj-a")
    storage.upsert_session_tenant("sess-bob", "bob", "proj-a")

    storage.save_episode(
        {
            "id": "ep-alice-1",
            "session_id": "sess-alice",
            "summary": "alice 写了一份 PRD",
            "started_at": "2026-05-15T10:00:00",
            "outcome": "completed",
        }
    )
    storage.save_episode(
        {
            "id": "ep-bob-1",
            "session_id": "sess-bob",
            "summary": "bob 改了一个 bug",
            "started_at": "2026-05-15T11:00:00",
            "outcome": "completed",
        }
    )
    # 老数据：v3 之前没有 session_tenants 登记，session 也不在表里
    storage.save_episode(
        {
            "id": "ep-orphan-1",
            "session_id": "sess-orphan",
            "summary": "古老 v2 时期遗留的 episode",
            "started_at": "2026-05-15T09:00:00",
            "outcome": "completed",
        }
    )

    # 不传过滤 → 全库（兼容旧调用）
    all_eps = storage.search_episodes(limit=10)
    assert len(all_eps) == 3

    alice_eps = storage.search_episodes(user_id="alice", workspace_id="proj-a", limit=10)
    assert {ep["id"] for ep in alice_eps} == {"ep-alice-1"}

    bob_eps = storage.search_episodes(user_id="bob", workspace_id="proj-a", limit=10)
    assert {ep["id"] for ep in bob_eps} == {"ep-bob-1"}

    # 只传 workspace 也能工作（同 workspace 多用户 → 取并集）
    proj_eps = storage.search_episodes(workspace_id="proj-a", limit=10)
    assert {ep["id"] for ep in proj_eps} == {"ep-alice-1", "ep-bob-1"}

    # 未登记的 orphan session 在带 tenant filter 时被自然排除
    assert "ep-orphan-1" not in {ep["id"] for ep in alice_eps}
    assert "ep-orphan-1" not in {ep["id"] for ep in bob_eps}


def test_search_turns_tenant_filter_phase_2b5(tmp_path: Path):
    """Phase 2b.5：search_turns 同样应该按 (user_id, workspace_id) 过滤。"""
    storage = MemoryStorage(tmp_path / "openakita.db")
    storage.upsert_session_tenant("sess-alice", "alice", "proj-a")
    storage.upsert_session_tenant("sess-bob", "bob", "proj-a")

    storage.save_turn(
        session_id="sess-alice",
        turn_index=0,
        role="user",
        content="今天的 PRD 写完了吗 内容是什么",
        timestamp=datetime.now().isoformat(),
    )
    storage.save_turn(
        session_id="sess-bob",
        turn_index=0,
        role="user",
        content="今天的 PRD bug 是怎么修的",
        timestamp=datetime.now().isoformat(),
    )

    all_rows = storage.search_turns(keyword="PRD", days_back=30)
    assert len(all_rows) == 2

    alice_rows = storage.search_turns(
        keyword="PRD", days_back=30, user_id="alice", workspace_id="proj-a"
    )
    assert len(alice_rows) == 1
    assert alice_rows[0]["session_id"] == "sess-alice"

    bob_rows = storage.search_turns(
        keyword="PRD", days_back=30, user_id="bob", workspace_id="proj-a"
    )
    assert len(bob_rows) == 1
    assert bob_rows[0]["session_id"] == "sess-bob"


@pytest.mark.asyncio
async def test_daily_consolidator_dedup_does_not_cross_tenants(tmp_path: Path):
    """Phase 3：dedup 必须按 (user_id, workspace_id) 分组。

    场景：alice 和 bob 都说了"我喜欢用简体中文"，没有理由让它们互相 dedup —— 这是
    多用户 IM 部署下最容易被忽视的真 bug。
    """
    from openakita.memory.daily_consolidator import DailyConsolidator
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )

    # 显式登记两个 tenant
    mm.store.upsert_session_tenant("sess-alice", "alice", "proj-a")
    mm.store.upsert_session_tenant("sess-bob", "bob", "proj-a")

    # 各自塞一条**几乎相同**的偏好记忆到 user scope
    alice_mem = SemanticMemory(
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        content="我喜欢用简体中文回答",
        importance_score=0.8,
    )
    bob_mem = SemanticMemory(
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        content="我喜欢用简体中文回答",
        importance_score=0.8,
    )
    mm.store.save_semantic(
        alice_mem, scope="user", scope_owner="", user_id="alice", workspace_id="proj-a"
    )
    mm.store.save_semantic(
        bob_mem, scope="user", scope_owner="", user_id="bob", workspace_id="proj-a"
    )
    # 刷新内存缓存
    if hasattr(mm, "_reload_from_sqlite"):
        mm._reload_from_sqlite()

    # 在没有向量库的环境下，dedup 走字符串前缀匹配兜底路径。两条 content 完全相同。
    # 改造后的实现应按 tenant 分组，alice 和 bob 各自只有 1 条，互不见，不会被删。
    consolidator = DailyConsolidator(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        memory_manager=mm,
        brain=None,
    )
    deleted = await consolidator._cleanup_duplicate_memories()
    assert deleted == 0, "alice 和 bob 的同质偏好不能被跨租户合并"

    # 验证两条都还在
    alice_left = mm.store.load_all_memories(
        scope="user", scope_owner="", user_id="alice", workspace_id="proj-a"
    )
    bob_left = mm.store.load_all_memories(
        scope="user", scope_owner="", user_id="bob", workspace_id="proj-a"
    )
    assert len(alice_left) == 1
    assert len(bob_left) == 1


@pytest.mark.asyncio
async def test_daily_consolidator_dedup_still_works_within_one_tenant(tmp_path: Path):
    """Phase 3：跨租户被禁，但**同一个**租户内的重复仍然要被去掉。"""
    from openakita.memory.daily_consolidator import DailyConsolidator
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.store.upsert_session_tenant("sess-alice", "alice", "proj-a")

    # 同一租户两条完全一样的偏好
    for content in ["我喜欢用简体中文回答", "我喜欢用简体中文回答"]:
        mm.store.save_semantic(
            SemanticMemory(
                type=MemoryType.PREFERENCE,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                importance_score=0.8,
            ),
            scope="user",
            scope_owner="",
            user_id="alice",
            workspace_id="proj-a",
            skip_dedup=True,  # 跳过写入侧 dedup，强制造两条
        )
    if hasattr(mm, "_reload_from_sqlite"):
        mm._reload_from_sqlite()

    consolidator = DailyConsolidator(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        memory_manager=mm,
        brain=None,
    )
    deleted = await consolidator._cleanup_duplicate_memories()
    assert deleted == 1, "同租户内的完全重复应该被合掉"
    remaining = mm.store.load_all_memories(
        scope="user", scope_owner="", user_id="alice", workspace_id="proj-a"
    )
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_global_store_source_blocks_cross_user():
    from openakita.agents.factory import _GlobalStoreSource

    store = _FakeStore()

    src_alice = _GlobalStoreSource(store, lambda: ("alice", "proj-a"))
    out_alice = await src_alice.retrieve("anything", limit=5)
    assert len(out_alice) == 1
    assert "alice secret note" in out_alice[0]["content"]
    assert store.last_kwargs["user_id"] == "alice"
    assert store.last_kwargs["workspace_id"] == "proj-a"
    assert store.last_kwargs["scope"] == "user"

    src_bob = _GlobalStoreSource(store, lambda: ("bob", "proj-a"))
    out_bob = await src_bob.retrieve("anything", limit=5)
    assert len(out_bob) == 1
    assert "bob secret note" in out_bob[0]["content"]

    # 共享 / 占位身份必须直接拒绝，不能裸跨用户查
    for owner in [
        ("default", "default"),
        ("anonymous", "default"),
        ("", "default"),
        ("legacy", "default"),
        ("system", "default"),
    ]:
        store.last_kwargs = None
        src = _GlobalStoreSource(store, lambda owner=owner: owner)
        out = await src.retrieve("anything", limit=5)
        assert out == []
        assert store.last_kwargs is None


# ======================================================================
# UnifiedStore observer (v4.1 cache-coherence refactor)
# ======================================================================


def test_unified_store_observer_fires_on_save_update_delete(tmp_path: Path):
    """UnifiedStore must invoke registered observers after each committed
    semantic-memory mutation, with the correct (kind, payload) tuple."""
    store = UnifiedStore(tmp_path / "obs.db", backend_type="fts5")

    events: list[tuple[str, object]] = []
    store.register_observer(lambda kind, payload: events.append((kind, payload)))

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="observer fixture v1",
    )
    store.save_semantic(mem, scope="user", user_id="alice", workspace_id="proj")
    # update
    store.update_semantic(mem.id, {"content": "observer fixture v2"})
    # delete
    store.delete_semantic(mem.id)

    kinds = [e[0] for e in events]
    assert kinds == ["upsert", "upsert", "delete"]

    # upsert payloads are SemanticMemory; delete payload is the id string
    assert events[0][1].id == mem.id and events[0][1].content == "observer fixture v1"
    assert events[1][1].id == mem.id and events[1][1].content == "observer fixture v2"
    assert events[2][1] == mem.id


def test_unified_store_observer_isolation_on_exception(tmp_path: Path):
    """A raising observer must not break the write or other observers."""
    store = UnifiedStore(tmp_path / "obs2.db", backend_type="fts5")

    survived: list[tuple[str, object]] = []

    def bad(_kind, _payload):
        raise RuntimeError("intentional")

    store.register_observer(bad)
    store.register_observer(lambda k, p: survived.append((k, p)))

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.SHORT_TERM,
        content="isolation case",
    )
    saved_id = store.save_semantic(mem, scope="user", user_id="alice", workspace_id="default")
    assert saved_id == mem.id
    assert store.get_semantic(saved_id) is not None  # DB write succeeded
    assert len(survived) == 1
    assert survived[0][0] == "upsert"


def test_unified_store_observer_skips_event_on_dedup_hit(tmp_path: Path):
    """When ``save_semantic`` returns an existing id via the dedup shortcut,
    no second upsert event fires (the cache already mirrors the original)."""
    store = UnifiedStore(tmp_path / "obs3.db", backend_type="fts5")

    first = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="A clear duplicate sentence body that the dedup check will catch on rewrite.",
    )
    store.save_semantic(first, scope="user", user_id="alice", workspace_id="proj")

    events: list[tuple[str, object]] = []
    store.register_observer(lambda k, p: events.append((k, p)))

    near_dup = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content=first.content,  # identical content → dedup short-circuit
    )
    returned = store.save_semantic(near_dup, scope="user", user_id="alice", workspace_id="proj")

    # The dedup branch returns the existing id and must NOT fire upsert
    # (cache already has the row).
    if returned == first.id:
        assert events == [], f"unexpected events: {events!r}"
    else:
        # If the heuristic didn't catch this case, the test still has a
        # well-defined invariant: any returned id must have produced exactly
        # one upsert for that id.
        assert events and events[0][0] == "upsert"


def test_memory_manager_cache_auto_synced_by_observer(tmp_path: Path):
    """Writes through ``store`` (bypassing MemoryManager.add_memory) must show
    up in ``_memories`` automatically — this is the v4.1 contract that
    eliminates the previous ``_sync_json`` / ``_reload_from_sqlite`` ceremony.
    """
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.start_session("sess-obs", user_id="alice", workspace_id="proj")

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="lifecycle-style direct write",
    )
    # Direct write to the underlying store; this is the same pattern that
    # LifecycleManager uses internally. **No** _reload_from_sqlite call.
    mm.store.save_semantic(mem, scope="user", user_id="alice", workspace_id="proj")

    # Cache must see it immediately
    assert mem.id in mm._memories
    assert mm.get_memory(mem.id) is not None


def test_memory_manager_delete_works_for_uncached_rows(tmp_path: Path):
    """Regression for the user-reported bug: ``mm.delete_memory`` used to
    return False without touching DB when the id was not in ``_memories``.

    Today the cache is auto-synced by the observer, so this exact scenario is
    harder to construct, but we still verify the observer **path** works and
    that delete returns True when DB had the row."""
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.start_session("sess-del", user_id="alice", workspace_id="proj")

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="row to be deleted via mm",
    )
    mm.store.save_semantic(mem, scope="user", user_id="alice", workspace_id="proj")
    assert mem.id in mm._memories  # observer placed it

    # Simulate the pre-v4.1 ghost: row in DB, missing from cache. With the
    # observer pattern this is *only* reachable by explicitly poking the
    # internals — but if it ever happens, delete_memory must still work.
    with mm._memories_lock:
        mm._memories.pop(mem.id)
    assert mem.id not in mm._memories

    assert mm.delete_memory(mem.id) is True
    # DB row should be gone
    assert mm.store.get_semantic(mem.id) is None
    # Cache stays clean (observer already popped on the underlying delete;
    # delete_memory's self-heal would have done the same).
    assert mem.id not in mm._memories


def test_memory_manager_observer_drops_cache_ghost_on_external_delete(
    tmp_path: Path,
):
    """When some out-of-band caller (lifecycle, batch tool, plugin) deletes
    a memory directly through ``store.delete_semantic`` without going through
    ``MemoryManager.delete_memory``, the cache mirror must drop the entry
    automatically — no manual ``_reload_from_sqlite`` needed."""
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.start_session("sess-ghost", user_id="alice", workspace_id="proj")

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="row deleted by a non-manager caller",
    )
    mm.store.save_semantic(mem, scope="user", user_id="alice", workspace_id="proj")
    assert mem.id in mm._memories

    # Simulate LifecycleManager's pattern (line ~634 in lifecycle.py)
    mm.store.delete_semantic(mem.id)

    # Cache must be drained by the observer — no ghost.
    assert mem.id not in mm._memories
    assert mm.store.get_semantic(mem.id) is None


def test_unified_store_observer_dispatch_thread_safety(tmp_path: Path):
    """The observer infrastructure itself (register + _fire) must be safe
    against concurrent dispatch and registration.

    This is a *focused* test on the observer mechanism — no DB writes, so
    we are isolated from pre-existing MemoryStorage thread-safety quirks
    (`MemoryStorage.get_memory` is unsynchronized; that is a separate,
    Path A-orthogonal concern). What we want to prove here is:
    1. Concurrent ``_fire`` calls never deadlock or skip observers.
    2. Late ``register_observer`` calls do not corrupt the observer list
       while ``_fire`` is iterating.
    """
    import threading

    from openakita.memory.unified_store import UnifiedStore

    store = UnifiedStore(tmp_path / "obs_mt.db", backend_type="fts5")

    counters = [0, 0, 0, 0]
    counter_locks = [threading.Lock() for _ in counters]

    def make_observer(idx: int):
        def fn(_kind, _payload):
            with counter_locks[idx]:
                counters[idx] += 1

        return fn

    for i in range(len(counters)):
        store.register_observer(make_observer(i))

    n_threads = 8
    events_per_thread = 200
    barrier = threading.Barrier(n_threads)

    def firer(_tid: int):
        barrier.wait()
        for i in range(events_per_thread):
            store._fire("upsert" if i % 2 == 0 else "delete", f"id-{i}")

    threads = [threading.Thread(target=firer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * events_per_thread
    for i, c in enumerate(counters):
        assert c == expected, f"observer #{i} got {c} events, expected {expected}"


def test_memory_manager_cache_coherence_under_concurrent_writes(tmp_path: Path):
    """End-to-end concurrent ``store.save_semantic`` → observer →
    ``_memories`` coherence. Uses ``skip_dedup=True`` to side-step a
    pre-existing thread-safety hole in ``MemoryStorage.get_memory`` (called
    by ``_check_semantic_duplicate``) that is unrelated to Path A.
    """
    import threading

    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.start_session("sess-mt", user_id="alice", workspace_id="proj")

    n_threads = 6
    n_per_thread = 20
    barrier = threading.Barrier(n_threads)
    saved_ids: list[str] = []
    saved_lock = threading.Lock()

    def worker(tid: int):
        barrier.wait()
        for i in range(n_per_thread):
            mem = SemanticMemory(
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                content=f"t{tid}-i{i}-{i * 7919 % 9973}",  # ensure non-duplicate
            )
            mm.store.save_semantic(
                mem,
                scope="user",
                user_id="alice",
                workspace_id="proj",
                skip_dedup=True,
            )
            with saved_lock:
                saved_ids.append(mem.id)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(saved_ids) == n_threads * n_per_thread
    for mid in saved_ids:
        assert mid in mm._memories, f"observer missed id {mid} under contention"

    # Concurrent deletes — each id deleted by exactly one thread.
    delete_barrier = threading.Barrier(n_threads)
    chunks = [saved_ids[i::n_threads] for i in range(n_threads)]

    def deleter(tid: int):
        delete_barrier.wait()
        for mid in chunks[tid]:
            mm.store.delete_semantic(mid)

    threads = [threading.Thread(target=deleter, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for mid in saved_ids:
        assert mid not in mm._memories, f"observer left ghost {mid} after concurrent delete"


def test_memory_manager_update_reflected_in_cache_via_observer(tmp_path: Path):
    """Updates issued through ``store.update_semantic`` (without going through
    ``MemoryManager.add_memory``) must refresh the cached object."""
    from openakita.memory.manager import MemoryManager

    mm = MemoryManager(
        data_dir=tmp_path / "memory",
        memory_md_path=tmp_path / "MEMORY.md",
        search_backend="fts5",
    )
    mm.start_session("sess-upd", user_id="alice", workspace_id="proj")

    mem = SemanticMemory(
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        content="original content body",
    )
    mm.store.save_semantic(mem, scope="user", user_id="alice", workspace_id="proj")
    assert mm._memories[mem.id].content == "original content body"

    ok = mm.store.update_semantic(mem.id, {"content": "rewritten body"})
    assert ok is True
    # Observer should have replaced the cached object with a fresh one.
    cached_after = mm._memories[mem.id]
    assert cached_after.content == "rewritten body"
