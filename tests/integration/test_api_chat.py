"""L3 Integration Tests: FastAPI /api/chat SSE endpoint and control routes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.api.server import create_app


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.initialized = True
    agent._initialized = True
    agent.state = MagicMock()
    agent.state.has_active_task = False
    agent.state.is_task_cancelled = False
    agent.brain = MagicMock()
    agent.brain.model = "mock-model"
    agent.settings = MagicMock()
    agent.settings.max_iterations = 10
    agent.session_manager = None
    agent.last_stream_kwargs = {}

    async def fake_stream(*args, **kwargs):
        agent.last_stream_kwargs = kwargs
        yield {"type": "text_delta", "content": "Hello from mock agent"}
        yield {"type": "done"}

    agent.chat_with_session_stream = fake_stream
    agent.chat_with_session = AsyncMock(return_value="Hello from mock agent")
    agent.insert_user_message = AsyncMock(return_value=True)
    return agent


@pytest.fixture
def app(mock_agent, monkeypatch):
    from openakita.api.routes import chat as chat_routes

    monkeypatch.setattr(chat_routes, "_chat_endpoint_names", lambda: {"mock-main"})
    monkeypatch.setattr(chat_routes, "_resolve_agent", lambda agent: agent)
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

    async def test_chat_without_mode_uses_agent_default(self, client, mock_agent, monkeypatch):
        captured_kwargs = {}

        async def fake_stream(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield {"type": "text_delta", "content": "Hello from mock agent"}
            yield {"type": "done"}

        monkeypatch.setattr(mock_agent, "chat_with_session_stream", fake_stream)

        resp = await client.post(
            "/api/chat",
            json={"message": "Hello", "conversation_id": "test-conv-no-mode"},
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert "Hello from mock agent" in resp.text
        assert captured_kwargs["mode"] == "agent"
        assert captured_kwargs["plan_mode"] is False

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

    async def test_chat_ignores_stale_endpoint(self, client, mock_agent):
        resp = await client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "conversation_id": "test-conv-1",
                "endpoint": "compiler-only",
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert "Hello from mock agent" in resp.text
        assert mock_agent.last_stream_kwargs["endpoint_override"] is None

    async def test_chat_startup_error_returns_structured_retryable_json(self, client, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        async def fail_agent_init(*args, **kwargs):
            raise RuntimeError("agent pool unavailable")

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fail_agent_init)

        resp = await client.post(
            "/api/chat",
            json={"message": "Hello", "conversation_id": "test-conv-1"},
        )

        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "chat_startup_failed"
        assert data["stage"] == "agent_init"
        assert data["retryable"] is True
        assert "聊天服务" in data["message"]
        assert "agent pool unavailable" in data["detail"]

    async def test_generate_title_thinking_only_response_falls_back(self, client, mock_agent, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        monkeypatch.setattr(chat_routes, "_resolve_agent", lambda agent: mock_agent)
        mock_agent.brain.think_lightweight = AsyncMock(
            return_value=SimpleNamespace(content="<think>\n只生成了思考内容", usage={})
        )

        resp = await client.post(
            "/api/sessions/generate-title",
            json={"message": "你好", "conversation_id": "test-conv-title"},
        )

        assert resp.status_code == 200
        assert resp.json()["title"] == "你好"


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

