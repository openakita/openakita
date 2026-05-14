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


class TestChatSyncEndpoint:
    """C14 / R4-6: non-SSE chat endpoint with 202+poll deferred-approval flow."""

    async def test_sync_returns_completed_json(self, client, mock_agent, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)
        mock_agent.chat_with_session = AsyncMock(return_value="reply from agent")

        resp = await client.post(
            "/api/chat/sync",
            json={"message": "Hello", "conversation_id": "sync-conv-1"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["conversation_id"] == "sync-conv-1"
        assert body["message"] == "reply from agent"
        assert "request_id" in body
        assert resp.headers.get("content-type", "").startswith("application/json")

    async def test_sync_returns_202_on_deferred_approval(self, client, mock_agent, monkeypatch):
        from openakita.api.routes import chat as chat_routes
        from openakita.core.policy_v2 import DeferredApprovalRequired

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)

        async def _raise_deferred(*_args, **_kwargs):
            raise DeferredApprovalRequired(
                "owner approval required",
                pending_id="pending_abc123",
                unattended_strategy="defer_to_inbox",
            )

        mock_agent.chat_with_session = _raise_deferred

        resp = await client.post(
            "/api/chat/sync",
            json={"message": "rm -rf /", "conversation_id": "sync-conv-2"},
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["approval_id"] == "pending_abc123"
        assert body["approval_url"] == "/api/pending_approvals/pending_abc123"
        assert body["resolve_url"] == "/api/pending_approvals/pending_abc123/resolve"
        assert body["unattended_strategy"] == "defer_to_inbox"
        # Location header for REST-style 202 + Location handoff
        assert resp.headers.get("location") == "/api/pending_approvals/pending_abc123"

    async def test_sync_empty_message_returns_400(self, client):
        resp = await client.post(
            "/api/chat/sync",
            json={"message": "", "conversation_id": "sync-conv-3"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "empty_message"

    async def test_sync_no_endpoints_returns_400(self, client, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        monkeypatch.setattr(chat_routes, "_chat_endpoint_names", lambda: set())
        resp = await client.post(
            "/api/chat/sync",
            json={"message": "Hello", "conversation_id": "sync-conv-4"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "no_chat_endpoints_configured"

    async def test_sync_runtime_error_returns_503(self, client, mock_agent, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("llm endpoint down")

        mock_agent.chat_with_session = _boom

        resp = await client.post(
            "/api/chat/sync",
            json={"message": "Hello", "conversation_id": "sync-conv-5"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"] == "chat_startup_failed"
        assert body["stage"] == "chat_with_session"
        assert body["retryable"] is True

    async def test_sync_auto_generates_conversation_id(self, client, mock_agent, monkeypatch):
        from openakita.api.routes import chat as chat_routes

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)
        mock_agent.chat_with_session = AsyncMock(return_value="ok")

        resp = await client.post("/api/chat/sync", json={"message": "Hi"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"].startswith("api_sync_")

    async def test_sync_returns_409_when_conversation_busy(
        self, client, mock_agent, monkeypatch
    ):
        """C14 re-audit D5: concurrent sync on same conv_id must 409, not
        race into chat_with_session and corrupt session state.

        Pre-fix this test would either pass via accident (two parallel
        completions interleaving) or assert race-condition behavior; with
        the lifecycle.start lock the second caller deterministically gets
        409 before chat_with_session is even reached.
        """
        import asyncio as _aio

        from openakita.api.routes import chat as chat_routes
        from openakita.api.routes.conversation_lifecycle import get_lifecycle_manager

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)

        gate = _aio.Event()

        async def _slow_chat(*_args, **_kwargs):
            await gate.wait()
            return "ok"

        mock_agent.chat_with_session = _slow_chat

        # Pre-acquire the lifecycle lock from a different "client" so the
        # next request sees a conflict deterministically. This sidesteps
        # the need to actually race two coroutines.
        lifecycle = get_lifecycle_manager()
        await lifecycle.start("sync-busy-conv", "external_client")

        try:
            resp = await client.post(
                "/api/chat/sync",
                json={"message": "Hi", "conversation_id": "sync-busy-conv"},
            )
            assert resp.status_code == 409, (
                f"expected 409 (conversation_busy), got {resp.status_code}: "
                f"{resp.text}"
            )
            body = resp.json()
            assert body["error"] == "conversation_busy"
            assert body["conversation_id"] == "sync-busy-conv"
            assert body["busy_client_id"] == "external_client"
        finally:
            gate.set()
            await lifecycle.finish("sync-busy-conv")

    async def test_sync_releases_lifecycle_on_completion(
        self, client, mock_agent, monkeypatch
    ):
        """C14 re-audit D5: a successful sync must release the busy-lock
        so a follow-up call can proceed (no leak on happy path)."""
        from openakita.api.routes import chat as chat_routes
        from openakita.api.routes.conversation_lifecycle import get_lifecycle_manager

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)
        mock_agent.chat_with_session = AsyncMock(return_value="reply")

        resp1 = await client.post(
            "/api/chat/sync",
            json={"message": "first", "conversation_id": "sync-release-conv"},
        )
        assert resp1.status_code == 200

        lifecycle = get_lifecycle_manager()
        busy_status = await lifecycle.get_busy_status("sync-release-conv")
        assert busy_status.get("busy") is False, (
            "lifecycle.finish() must release the lock on happy path; "
            f"got busy_status={busy_status}"
        )

        resp2 = await client.post(
            "/api/chat/sync",
            json={"message": "second", "conversation_id": "sync-release-conv"},
        )
        assert resp2.status_code == 200, (
            "second call on same conv_id must succeed (lock released)"
        )

    async def test_sync_releases_lifecycle_on_error(
        self, client, mock_agent, monkeypatch
    ):
        """C14 re-audit D5: exception path must also release the lock —
        otherwise a single 5xx would permanently busy-out the conversation.
        """
        from openakita.api.routes import chat as chat_routes
        from openakita.api.routes.conversation_lifecycle import get_lifecycle_manager

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("kaboom")

        mock_agent.chat_with_session = _boom

        resp = await client.post(
            "/api/chat/sync",
            json={"message": "x", "conversation_id": "sync-error-conv"},
        )
        assert resp.status_code == 503

        lifecycle = get_lifecycle_manager()
        busy_status = await lifecycle.get_busy_status("sync-error-conv")
        assert busy_status.get("busy") is False, (
            "lifecycle.finish() must run via finally even on error; "
            f"got busy_status={busy_status}"
        )

    async def test_sync_releases_lifecycle_on_deferred_approval(
        self, client, mock_agent, monkeypatch
    ):
        """C14 re-audit D5: 202 deferred path must also release the lock,
        otherwise the conversation stays busy until process restart."""
        from openakita.api.routes import chat as chat_routes
        from openakita.api.routes.conversation_lifecycle import get_lifecycle_manager
        from openakita.core.policy_v2 import DeferredApprovalRequired

        async def fake_get_agent(*args, **kwargs):
            return mock_agent

        monkeypatch.setattr(chat_routes, "_get_agent_for_session", fake_get_agent)

        async def _defer(*_args, **_kwargs):
            raise DeferredApprovalRequired(
                "needs owner",
                pending_id="pending_xyz",
                unattended_strategy="defer_to_inbox",
            )

        mock_agent.chat_with_session = _defer

        resp = await client.post(
            "/api/chat/sync",
            json={"message": "delete db", "conversation_id": "sync-defer-conv"},
        )
        assert resp.status_code == 202

        lifecycle = get_lifecycle_manager()
        busy_status = await lifecycle.get_busy_status("sync-defer-conv")
        assert busy_status.get("busy") is False, (
            "lifecycle.finish() must run after 202 deferred path; "
            f"got busy_status={busy_status}"
        )


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

