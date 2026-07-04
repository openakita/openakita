"""Tests for the owner-bucket merge endpoint / manager method.

Covers merging the historical ``user_id='default'`` bucket into the canonical
desktop identity (``desktop_user``): dry-run reporting, content dedup, identity
slot conflict resolution (keep newer), idempotency, and the REST endpoint.
"""

import time
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.memory import router as memory_router
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


def _put(
    manager: MemoryManager,
    content: str,
    *,
    user_id: str,
    subject: str = "",
    predicate: str = "",
    when: datetime | None = None,
) -> str:
    mem = _memory(content, subject=subject, predicate=predicate)
    if when is not None:
        mem.created_at = when
        mem.updated_at = when
    return manager.store.save_semantic(
        mem,
        scope="user",
        scope_owner="",
        user_id=user_id,
        workspace_id="default",
        skip_dedup=True,
    )


def _active_contents(manager: MemoryManager, user_id: str) -> list[str]:
    return [
        m.content
        for m in manager.store.load_all_memories(
            scope="user", scope_owner="", user_id=user_id, workspace_id="default"
        )
    ]


def _memory_client(manager: MemoryManager) -> TestClient:
    app = FastAPI()
    app.include_router(memory_router)
    app.state.agent = SimpleNamespace(memory_manager=manager)
    return TestClient(app)


def test_merge_dry_run_reports_without_writing(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年", user_id="default")
    _put(manager, "项目使用 FastAPI 作为后端框架", user_id="default")

    report = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=True
    )

    assert report["dry_run"] is True
    assert report["source_total"] == 2
    assert report["merged"] == 2
    assert report["skipped"] == 0
    # Nothing moved: default bucket still holds both, desktop_user still empty.
    assert len(_active_contents(manager, "default")) == 2
    assert _active_contents(manager, "desktop_user") == []


def test_merge_moves_general_facts_into_target(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年", user_id="default")
    _put(manager, "项目使用 FastAPI 作为后端框架", user_id="default")

    report = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )

    assert report["merged"] == 2
    assert report["skipped"] == 0
    assert _active_contents(manager, "default") == []
    assert sorted(_active_contents(manager, "desktop_user")) == sorted(
        ["用户之前在苏州工作过三年", "项目使用 FastAPI 作为后端框架"]
    )


def test_merge_skips_content_duplicate_without_duplicating(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年时间很长", user_id="desktop_user")
    _put(manager, "用户之前在苏州工作过三年时间很长", user_id="default")
    _put(manager, "另一条完全不同的历史事实记录内容", user_id="default")

    report = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )

    assert report["merged"] == 1
    assert report["skipped"] == 1
    contents = _active_contents(manager, "desktop_user")
    # The duplicate is not duplicated in the target bucket.
    assert contents.count("用户之前在苏州工作过三年时间很长") == 1
    assert "另一条完全不同的历史事实记录内容" in contents
    assert _active_contents(manager, "default") == []


def test_merge_identity_conflict_keeps_newer_target(tmp_path):
    manager = _manager(tmp_path)
    # ``updated_at`` is stamped at save time, so save order == recency order.
    # Source (stale) first, target (fresh) last → target is the newer value.
    _put(manager, "用户名字是张三", user_id="default", subject="用户", predicate="姓名")
    time.sleep(0.05)
    _put(manager, "用户名字是小红", user_id="desktop_user", subject="用户", predicate="姓名")

    report = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )

    assert report["conflicts"] == 1
    assert report["skipped"] == 1
    assert report["superseded"] == 0
    # Newer target value wins; only one active slot value remains.
    assert _active_contents(manager, "desktop_user") == ["用户名字是小红"]
    assert _active_contents(manager, "default") == []


def test_merge_identity_conflict_newer_source_supersedes_target(tmp_path):
    manager = _manager(tmp_path)
    # Target (stale) first, source (fresh) last → source is the newer value.
    _put(manager, "用户名字是小红", user_id="desktop_user", subject="用户", predicate="姓名")
    time.sleep(0.05)
    _put(manager, "用户名字是张三", user_id="default", subject="用户", predicate="姓名")

    report = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )

    assert report["conflicts"] == 1
    assert report["superseded"] == 1
    assert _active_contents(manager, "desktop_user") == ["用户名字是张三"]


def test_merge_is_idempotent(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年", user_id="default")
    _put(manager, "项目使用 FastAPI 作为后端框架", user_id="default")

    first = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )
    assert first["merged"] == 2

    second = manager.merge_owner_memories(
        from_user_id="default", to_user_id="desktop_user", dry_run=False
    )
    assert second["source_total"] == 0
    assert second["merged"] == 0
    assert second["skipped"] == 0
    assert second["superseded"] == 0


def test_merge_same_owner_is_noop(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年", user_id="desktop_user")
    report = manager.merge_owner_memories(
        from_user_id="desktop_user", to_user_id="desktop_user", dry_run=False
    )
    assert report["source_total"] == 0
    assert report.get("reason") == "source and target owner are identical"


def test_merge_owner_endpoint_dry_run_then_execute(tmp_path):
    manager = _manager(tmp_path)
    _put(manager, "用户之前在苏州工作过三年", user_id="default")
    _put(manager, "项目使用 FastAPI 作为后端框架", user_id="default")

    client = _memory_client(manager)

    # Before merge the desktop panel (desktop_user) sees nothing.
    assert client.get("/api/memories").json()["total"] == 0

    dry = client.post("/api/memories/merge-owner", json={})
    assert dry.status_code == 200
    dry_body = dry.json()
    assert dry_body["dry_run"] is True
    assert dry_body["source_total"] == 2
    assert dry_body["merged"] == 2
    # Dry run wrote nothing.
    assert client.get("/api/memories").json()["total"] == 0

    real = client.post("/api/memories/merge-owner", json={"dry_run": False})
    assert real.status_code == 200
    real_body = real.json()
    assert real_body["dry_run"] is False
    assert real_body["merged"] == 2

    listing = client.get("/api/memories").json()
    assert listing["total"] == 2
    contents = {m["content"] for m in listing["memories"]}
    assert contents == {"用户之前在苏州工作过三年", "项目使用 FastAPI 作为后端框架"}


def test_merge_owner_endpoint_503_without_manager():
    app = FastAPI()
    app.include_router(memory_router)
    client = TestClient(app)
    res = client.post("/api/memories/merge-owner", json={})
    assert res.status_code == 503
