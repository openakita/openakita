from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.sessions import router
from openakita.sessions import SessionManager


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
