"""L3 Integration Tests: FastAPI /api/chat SSE endpoint and control routes."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.api.server import create_app


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.initialized = True
    agent.state = MagicMock()
    agent.state.has_active_task = False
    agent.state.is_task_cancelled = False
    agent.brain = MagicMock()
    agent.brain.model = "mock-model"
    agent.settings = MagicMock()
    agent.settings.max_iterations = 10
    agent.session_manager = None

    async def fake_stream(*args, **kwargs):
        yield "Hello from mock agent"

    agent.chat_with_session_stream = fake_stream
    agent.chat_with_session = AsyncMock(return_value="Hello from mock agent")
    return agent


@pytest.fixture
def app(mock_agent, monkeypatch):
    from openakita.api.routes import chat as chat_routes

    monkeypatch.setattr(chat_routes, "_chat_endpoint_names", lambda: {"mock-main"})
    return create_app(
        agent=mock_agent,
        shutdown_event=asyncio.Event(),
    )


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


class TestRootEndpoint:
    async def test_root_returns_status(self, client):
        resp = await client.get("/", follow_redirects=True)
        assert resp.status_code == 200


class TestHealthEndpoint:
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200


class TestChatEndpoint:
    async def test_chat_returns_sse(self, client):
        resp = await client.post(
            "/api/chat",
            json={"message": "Hello", "conversation_id": "test-conv-1"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    async def test_chat_empty_message(self, client):
        resp = await client.post(
            "/api/chat",
            json={"message": "", "conversation_id": "test-conv-1"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "empty_message"

    async def test_chat_requires_main_endpoint(self, client, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        monkeypatch.setattr(chat_routes, "_chat_endpoint_names", lambda: set())

        resp = await client.post(
            "/api/chat",
            json={"message": "Hello", "conversation_id": "test-conv-1"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "no_chat_endpoints_configured"
        assert "\u4e3b\u804a\u5929" in data["message"]

    async def test_chat_rejects_non_chat_endpoint(self, client):
        resp = await client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "conversation_id": "test-conv-1",
                "endpoint": "compiler-only",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "unknown_chat_endpoint"
        assert data["endpoint"] == "compiler-only"


class TestChatControlEndpoints:
    async def test_cancel_endpoint(self, client, mock_agent):
        mock_agent.state.cancel_task = MagicMock()
        resp = await client.post(
            "/api/chat/cancel",
            json={"conversation_id": "test-conv-1", "reason": "user stopped"},
        )
        assert resp.status_code == 200

    async def test_skip_endpoint(self, client, mock_agent):
        mock_agent.state.skip_current_step = MagicMock()
        resp = await client.post(
            "/api/chat/skip",
            json={"conversation_id": "test-conv-1"},
        )
        assert resp.status_code == 200

    async def test_answer_endpoint(self, client):
        resp = await client.post(
            "/api/chat/answer",
            json={"conversation_id": "test-conv-1", "answer": "Yes"},
        )
        assert resp.status_code == 200

    async def test_insert_endpoint(self, client, mock_agent):
        mock_agent.state.insert_user_message = AsyncMock()
        resp = await client.post(
            "/api/chat/insert",
            json={"conversation_id": "test-conv-1", "message": "new info"},
        )
        assert resp.status_code == 200


class TestShutdownEndpoint:
    async def test_shutdown_sets_event(self, client, app):
        resp = await client.post("/api/shutdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "shutting_down"
        assert app.state.shutdown_event.is_set()

