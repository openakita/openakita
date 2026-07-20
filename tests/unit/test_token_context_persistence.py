from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.token_stats import router
from openakita.config import settings
from openakita.sessions.manager import SessionManager


def _snapshot(conversation_id: str, tokens: int) -> dict:
    return {
        "conversation_id": conversation_id,
        "context_tokens": tokens,
        "context_limit": 1000,
        "remaining_tokens": 1000 - tokens,
        "percent": tokens / 10,
        "updated_at": 123.0,
        "source": "provider",
        "endpoint_name": "primary",
        "model": "test-model",
    }


def test_context_route_restores_independent_snapshots_after_restart(tmp_path):
    storage = tmp_path / "sessions"
    manager = SessionManager(storage_path=storage)
    first = manager.get_session("desktop", "conv-1", "desktop_user")
    second = manager.get_session("desktop", "conv-2", "desktop_user")
    first.set_metadata("context_usage", _snapshot("conv-1", 111))
    second.set_metadata("context_usage", _snapshot("conv-2", 222))
    manager.persist()

    restored = SessionManager(storage_path=storage)
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = restored
    app.state.agent_pool = None
    app.state.agent = None
    client = TestClient(app)

    first_response = client.get("/api/stats/tokens/context?conversation_id=conv-1")
    second_response = client.get("/api/stats/tokens/context?conversation_id=conv-2")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["context_tokens"] == 111
    assert second_response.json()["context_tokens"] == 222
    assert first_response.json()["conversation_id"] == "conv-1"
    assert second_response.json()["conversation_id"] == "conv-2"


def test_context_route_backfills_missing_snapshot_from_persisted_history(tmp_path):
    storage = tmp_path / "sessions"
    manager = SessionManager(storage_path=storage)
    session = manager.get_session("desktop", "legacy-conv", "desktop_user")
    session.context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    manager.persist()

    context_manager = SimpleNamespace(
        estimate_messages_tokens=lambda _messages: 77,
        get_max_context_tokens=lambda conversation_id=None: 1000,
    )
    agent = SimpleNamespace(
        context_manager=context_manager,
        brain=SimpleNamespace(
            get_current_model_info=lambda conversation_id=None: {
                "name": "primary",
                "model": "test-model",
            },
            _llm_client=SimpleNamespace(endpoints=[]),
        ),
    )
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = SessionManager(storage_path=storage)
    app.state.agent_pool = SimpleNamespace(get_existing=lambda _conversation_id: agent)
    app.state.agent = None
    client = TestClient(app)

    response = client.get("/api/stats/tokens/context?conversation_id=legacy-conv")

    assert response.status_code == 200
    assert response.json()["context_tokens"] == 77
    assert response.json()["source"] == "persisted_history_estimate"
    restored_session = app.state.session_manager.get_session(
        "desktop",
        "legacy-conv",
        "desktop_user",
        create_if_missing=False,
    )
    assert restored_session.get_metadata("context_usage")["context_tokens"] == 77


def test_context_route_recalculates_persisted_limit_after_config_change(tmp_path, monkeypatch):
    storage = tmp_path / "sessions"
    manager = SessionManager(storage_path=storage)
    session = manager.get_session("desktop", "stale-limit", "desktop_user")
    session.set_metadata("context_usage", _snapshot("stale-limit", 111))
    manager.persist()

    endpoint = {
        "name": "primary",
        "provider": "openai",
        "model": "test-model",
        "context_window": 20000,
        "max_tokens": 1000,
        "enabled": True,
    }
    endpoint_manager = SimpleNamespace(list_endpoints=lambda _kind: [endpoint])
    from openakita.api.routes import config as config_routes

    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: endpoint_manager)
    monkeypatch.setattr(settings, "context_max_window", 10000)

    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = SessionManager(storage_path=storage)
    app.state.agent_pool = None
    app.state.agent = None
    client = TestClient(app)

    response = client.get("/api/stats/tokens/context?conversation_id=stale-limit")

    assert response.status_code == 200
    data = response.json()
    assert data["context_tokens"] == 111
    assert data["context_limit"] == 8550
    assert data["remaining_tokens"] == 8439
    assert data["raw_context_window"] == 20000
    assert data["effective_context_window"] == 10000
    assert data["output_reserve"] == 1000
    refreshed_session = app.state.session_manager.get_session(
        "desktop",
        "stale-limit",
        "desktop_user",
        create_if_missing=False,
    )
    assert refreshed_session.get_metadata("context_usage")["context_limit"] == 8550
