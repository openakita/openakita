from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.sessions import router
from openakita.sessions import SessionManager


class _RecordingAgentPool:
    def __init__(self, *, error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.error = error

    async def get_or_create(self, session_id, profile):
        self.calls.append((session_id, profile.id))
        if self.error is not None:
            raise self.error
        return object()


def _stale_todo() -> dict:
    return {
        "id": "plan_703",
        "taskSummary": "卸载 thirdparty 备份软件并清理残留",
        "status": "in_progress",
        "steps": [
            {"id": "step_1", "description": "停止服务", "status": "in_progress"},
            {"id": "step_2", "description": "卸载程序", "status": "pending"},
        ],
    }


def _client_with_session(tmp_path, message_count: int = 120) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    for i in range(message_count):
        role = "user" if i % 2 == 0 else "assistant"
        session.add_message(role, f"msg-{i}")
    app.state.session_manager = manager
    return TestClient(app)


def test_history_defaults_to_recent_window(tmp_path):
    client = _client_with_session(tmp_path, 120)

    resp = client.get("/api/sessions/conv1/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 120
    assert len(body["messages"]) == 80
    assert body["messages"][0]["content"] == "msg-40"
    assert body["messages"][-1]["content"] == "msg-119"
    assert body["start_index"] == 40
    assert body["end_index"] == 119
    assert body["has_more_before"] is True


def test_history_can_page_before_stable_index(tmp_path):
    client = _client_with_session(tmp_path, 120)

    resp = client.get("/api/sessions/conv1/history", params={"limit": 30, "before": 40})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 120
    assert len(body["messages"]) == 30
    assert body["messages"][0]["content"] == "msg-10"
    assert body["messages"][-1]["content"] == "msg-39"
    assert body["start_index"] == 10
    assert body["end_index"] == 39
    assert body["has_more_before"] is True


def test_history_strips_non_ui_system_summaries(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("system", "[历史背景，非当前任务] very large summary")
    session.add_message("user", "visible")
    app.state.session_manager = manager

    body = TestClient(app).get("/api/sessions/conv1/history").json()

    assert body["total"] == 1
    assert [m["content"] for m in body["messages"]] == ["visible"]


def test_history_filters_near_duplicate_user_messages(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.context.messages = [
        {"role": "user", "content": "same prompt", "timestamp": "2026-06-25T18:06:53.993669"},
        {"role": "user", "content": "same prompt", "timestamp": "2026-06-25T18:06:53.999736"},
        {"role": "assistant", "content": "done", "timestamp": "2026-06-25T18:07:00"},
    ]
    app.state.session_manager = manager

    body = TestClient(app).get("/api/sessions/conv1/history").json()

    assert body["total"] == 2
    assert [m["content"] for m in body["messages"]] == ["same prompt", "done"]


def test_history_backfill_skips_near_duplicate_turns(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.context.messages = [
        {"role": "user", "content": "same prompt", "timestamp": "2026-06-25T18:06:53.993669"},
    ]
    manager.set_turn_loader(
        lambda _safe_id: [
            {
                "role": "user",
                "content": "same prompt",
                "timestamp": "2026-06-25T18:06:53.999736",
            },
            {"role": "assistant", "content": "done", "timestamp": "2026-06-25T18:07:00"},
        ]
    )
    app.state.session_manager = manager

    body = TestClient(app).get("/api/sessions/conv1/history").json()

    assert body["total"] == 2
    assert [m["content"] for m in body["messages"]] == ["same prompt", "done"]
    assert [m["content"] for m in session.context.messages].count("same prompt") == 1


def test_history_reconciles_later_completion_only_todo_event(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    stale = _stale_todo()
    session.add_message("user", "卸载 thirdparty")
    session.add_message(
        "assistant",
        "开始计划",
        todo=stale,
        progress_events=[
            {"type": "todo_created", "plan": stale},
            {"type": "todo_step_updated", "stepId": "step_1", "status": "in_progress"},
        ],
    )
    session.add_message("user", "这是工作软件，不要卸载")
    session.add_message(
        "assistant",
        "计划已关闭",
        progress_events=[
            {"type": "todo_completed"},
            {
                "type": "todo_step_updated",
                "stepId": "step_1",
                "status": "completed",
                "result": "已取消",
            },
            {
                "type": "todo_step_updated",
                "stepId": "step_2",
                "status": "completed",
                "result": "已取消",
            },
        ],
    )
    app.state.session_manager = manager

    body = TestClient(app).get("/api/sessions/conv1/history").json()
    created = next(m for m in body["messages"] if m["content"] == "开始计划")
    plan_part = next(p for p in created["parts"] if p["kind"] == "plan")

    assert created["todo"]["status"] == "completed"
    assert [step["status"] for step in created["todo"]["steps"]] == ["completed", "completed"]
    assert plan_part["todo"]["status"] == "completed"
    assert body["active_todo"] is None


def test_session_list_returns_conversation_ui_state(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "hello")
    session.set_metadata("selected_endpoint", "deepseek")
    session.set_metadata("pinned", True)
    session.set_metadata(
        "ui_org_state",
        {"orgMode": True, "orgId": "org_company", "orgNodeId": "pm"},
    )
    app.state.session_manager = manager

    body = TestClient(app).get("/api/sessions").json()

    assert body["sessions"][0]["endpointId"] == "deepseek"
    assert body["sessions"][0]["pinned"] is True
    assert body["sessions"][0]["orgMode"] is True
    assert body["sessions"][0]["orgId"] == "org_company"
    assert body["sessions"][0]["orgNodeId"] == "pm"


def test_update_session_ui_state_persists_conversation_selection(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "hello")
    app.state.session_manager = manager

    resp = TestClient(app).post(
        "/api/sessions/conv1/ui-state",
        json={
            "endpointId": "minimax",
            "orgMode": True,
            "orgId": "org_ops",
            "orgNodeId": None,
        },
    )

    assert resp.status_code == 200
    assert session.get_metadata("selected_endpoint") == "minimax"
    assert session.get_metadata("ui_org_state") == {
        "orgMode": True,
        "orgId": "org_ops",
        "orgNodeId": "",
    }


def test_update_session_ui_state_does_not_create_empty_session(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    app.state.session_manager = manager

    resp = TestClient(app).post(
        "/api/sessions/missing/ui-state",
        json={"endpointId": "minimax", "orgMode": False},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "session_not_found"}
    assert (
        manager.get_session("desktop", "missing", "desktop_user", create_if_missing=False) is None
    )


def test_update_session_title_persists_and_list_prefers_manual_title(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "first message fallback")
    app.state.session_manager = manager

    client = TestClient(app)
    resp = client.patch(
        "/api/sessions/conv1/title",
        json={"title": "  Custom   Title  ", "titleManuallySet": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["title"] == "Custom Title"
    assert body["titleGenerated"] is False
    assert body["titleManuallySet"] is True
    assert body["pinned"] is False
    assert session.get_metadata("conversation_title") == "Custom Title"
    assert session.get_metadata("title_manually_set") is True
    assert session.get_metadata("title_generated") is False

    body = client.get("/api/sessions").json()

    assert body["sessions"][0]["title"] == "Custom Title"
    assert body["sessions"][0]["titleManuallySet"] is True
    assert body["sessions"][0]["titleGenerated"] is False

    reloaded = SessionManager(storage_path=tmp_path)
    persisted = reloaded.get_session("desktop", "conv1", "desktop_user", create_if_missing=False)
    assert persisted is not None
    assert persisted.get_metadata("conversation_title") == "Custom Title"


def test_update_session_title_does_not_create_missing_session(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    app.state.session_manager = manager

    resp = TestClient(app).patch(
        "/api/sessions/missing/title",
        json={"title": "Custom Title"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "session_not_found"}
    assert (
        manager.get_session("desktop", "missing", "desktop_user", create_if_missing=False) is None
    )


def test_create_session_persists_empty_conversation_as_list_item(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    app.state.session_manager = manager

    client = TestClient(app)
    resp = client.post(
        "/api/sessions",
        json={
            "conversationId": "draft1",
            "title": "新对话",
            "titleManuallySet": False,
            "agentProfileId": "research",
            "endpointId": "deepseek",
            "endpointPolicy": "require",
            "orgMode": True,
            "orgId": "org_ops",
            "orgNodeId": "pm",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["created"] is True
    assert body["agentPrewarmed"] is False
    assert body["id"] == "draft1"
    assert body["title"] == "新对话"
    assert body["titleGenerated"] is False
    assert body["titleManuallySet"] is False
    assert body["pinned"] is False
    assert body["messageCount"] == 0
    assert body["lastMessage"] == ""
    assert body["agentProfileId"] == "research"
    assert body["endpointId"] == "deepseek"
    assert body["endpointPolicy"] == "require"
    assert body["orgMode"] is True
    assert body["orgId"] == "org_ops"
    assert body["orgNodeId"] == "pm"

    listed = client.get("/api/sessions").json()["sessions"]
    assert [s["id"] for s in listed] == ["draft1"]
    assert listed[0]["messageCount"] == 0

    session = manager.get_session("desktop", "draft1", "desktop_user", create_if_missing=False)
    assert session is not None
    assert session.context.agent_profile_id == "research"
    assert session.get_metadata("conversation_title") == "新对话"
    assert session.get_metadata("selected_endpoint") == "deepseek"
    assert session.get_metadata("endpoint_policy") == "require"
    assert session.get_metadata("ui_org_state") == {
        "orgMode": True,
        "orgId": "org_ops",
        "orgNodeId": "pm",
    }

    reloaded = SessionManager(storage_path=tmp_path)
    persisted = reloaded.get_session("desktop", "draft1", "desktop_user", create_if_missing=False)
    assert persisted is not None
    assert persisted.get_metadata("conversation_title") == "新对话"
    assert persisted.context.agent_profile_id == "research"


def test_create_session_prewarms_agent_once_for_new_desktop_conversation(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = SessionManager(storage_path=tmp_path)
    pool = _RecordingAgentPool()
    app.state.agent_pool = pool
    client = TestClient(app)

    first = client.post("/api/sessions", json={"conversationId": "draft-prewarm"})
    second = client.post("/api/sessions", json={"conversationId": "draft-prewarm"})

    assert first.status_code == 200
    assert first.json()["agentPrewarmed"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["agentPrewarmed"] is False
    assert pool.calls == [("draft-prewarm", "default")]


def test_create_session_succeeds_when_agent_prewarm_fails(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = SessionManager(storage_path=tmp_path)
    pool = _RecordingAgentPool(error=RuntimeError("prewarm failed"))
    app.state.agent_pool = pool

    response = TestClient(app).post(
        "/api/sessions",
        json={"conversationId": "draft-prewarm-failure"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["created"] is True
    assert response.json()["agentPrewarmed"] is False
    assert pool.calls == [("draft-prewarm-failure", "default")]


def test_update_session_title_does_not_create_missing_session_with_legacy_query(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    app.state.session_manager = manager

    resp = TestClient(app).patch(
        "/api/sessions/missing/title",
        params={"create_if_missing": True},
        json={"title": "Custom Title"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "session_not_found"}
    assert (
        manager.get_session("desktop", "missing", "desktop_user", create_if_missing=False) is None
    )


def test_update_session_pin_persists_and_list_returns_pinned(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "hello")
    session.set_metadata("conversation_title", "Pinned Title")
    session.set_metadata("title_manually_set", True)
    session.set_metadata("title_generated", False)
    app.state.session_manager = manager

    resp = TestClient(app).patch(
        "/api/sessions/conv1/pin",
        json={"pinned": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["pinned"] is True
    assert body["title"] == "Pinned Title"
    assert body["titleManuallySet"] is True
    assert body["titleGenerated"] is False
    assert session.get_metadata("pinned") is True
    assert TestClient(app).get("/api/sessions").json()["sessions"][0]["pinned"] is True

    reloaded = SessionManager(storage_path=tmp_path)
    persisted = reloaded.get_session("desktop", "conv1", "desktop_user", create_if_missing=False)
    assert persisted is not None
    assert persisted.get_metadata("pinned") is True


def test_update_session_pin_does_not_create_missing_session_by_default(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    app.state.session_manager = manager

    resp = TestClient(app).patch(
        "/api/sessions/missing/pin",
        json={"pinned": True},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "session_not_found"}
    assert (
        manager.get_session("desktop", "missing", "desktop_user", create_if_missing=False) is None
    )


def test_generate_title_preserves_manual_session_title(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "first message fallback")
    session.set_metadata("conversation_title", "Manual Title")
    session.set_metadata("title_manually_set", True)
    session.set_metadata("title_generated", False)
    app.state.session_manager = manager

    resp = TestClient(app).post(
        "/api/sessions/generate-title",
        json={"message": "new prompt", "conversation_id": "conv1"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "title": "Manual Title",
        "titleGenerated": False,
        "titleManuallySet": True,
    }
    assert session.get_metadata("conversation_title") == "Manual Title"
    assert session.get_metadata("title_manually_set") is True
    assert session.get_metadata("title_generated") is False
