"""L3 Integration Tests: FastAPI /api/chat SSE endpoint and control routes."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.profile import AgentProfile, AgentType, ProfileStore
from openakita.api.server import create_app
from openakita.core.agent import Agent


def _parse_sse_events(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


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
def profile_store(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "agents")
    monkeypatch.setattr("openakita.agents.profile.get_profile_store", lambda: store)
    return store


@pytest.fixture
def app(mock_agent):
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

    async def test_chat_rejects_external_cli_primary_profile(
        self,
        client,
        profile_store,
        monkeypatch,
    ):
        from openakita.api.routes import chat as chat_routes

        profile_store.save(
            AgentProfile(
                id="claude-code-pair",
                name="Claude Code Pair",
                type=AgentType.EXTERNAL_CLI,
                cli_provider_id=CliProviderId.CLAUDE_CODE,
            )
        )
        monkeypatch.setattr(
            chat_routes,
            "_get_agent_for_session",
            AsyncMock(side_effect=AssertionError("agent construction should not run")),
        )

        resp = await client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "conversation_id": "conv-external-cli",
                "agent_profile_id": "claude-code-pair",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "primary_chat_profile_unsupported"
        assert resp.json()["agent_profile_id"] == "claude-code-pair"

    async def test_chat_initialize_failure_does_not_raise_unboundlocal(
        self,
        client,
        monkeypatch,
    ):
        from openakita.api.routes import chat as chat_routes

        class _FailingAgent(Agent):
            def __init__(self):
                self.brain = MagicMock()
                self._initialized = False

            async def initialize(self, *args, **kwargs):
                raise RuntimeError("boom during initialize")

        monkeypatch.setattr(
            chat_routes,
            "_get_agent_for_session",
            AsyncMock(return_value=_FailingAgent()),
        )

        resp = await client.post(
            "/api/chat",
            json={"message": "Hello", "conversation_id": "conv-init-fail"},
        )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert any(event["type"] == "error" for event in events)
        assert not any("UnboundLocalError" in event.get("message", "") for event in events)
        assert events[-1]["type"] == "done"


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
