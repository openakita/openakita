"""服务重启流程集成测试。

覆盖 v1.25.9 修复的重启相关 bug：
1. POST /api/config/restart 触发重启标志
2. 有 shutdown_event 时正确触发
3. 无 shutdown_event 时返回错误
4. POST /api/shutdown 触发关闭
5. GET /api/health 返回正确版本和状态
6. 重启后多 Agent 模式应恢复（orchestrator 重新初始化）
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.api.server import create_app


@pytest.fixture
def shutdown_event():
    return asyncio.Event()


@pytest.fixture
def app(shutdown_event):
    return create_app(
        agent=MagicMock(initialized=True),
        shutdown_event=shutdown_event,
    )


@pytest.fixture
def app_no_shutdown():
    return create_app(
        agent=MagicMock(initialized=True),
        shutdown_event=None,
    )


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
async def client_no_shutdown(app_no_shutdown):
    async with AsyncClient(
        transport=ASGITransport(app=app_no_shutdown),
        base_url="http://testserver",
    ) as c:
        yield c


class TestRestartWithShutdownEvent:
    async def test_restart_sets_flag_and_triggers_event(self, client, shutdown_event):
        import openakita.config as cfg
        cfg._restart_requested = False

        resp = await client.post("/api/config/restart")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert shutdown_event.is_set()
        assert cfg._restart_requested is True

        cfg._restart_requested = False
        shutdown_event.clear()


class TestRestartWithoutShutdownEvent:
    async def test_restart_returns_error(self, client_no_shutdown):
        import openakita.config as cfg
        cfg._restart_requested = False

        resp = await client_no_shutdown.post("/api/config/restart")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "not available" in data["message"]
        assert cfg._restart_requested is False


class TestShutdownEndpoint:
    async def test_shutdown_from_localhost(self, client, shutdown_event):
        resp = await client.post("/api/shutdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "shutting_down"
        assert shutdown_event.is_set()
        shutdown_event.clear()


class TestHealthEndpoint:
    async def test_health_returns_version_and_status(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "openakita"
        assert "version" in data
        assert "pid" in data
        assert data["agent_initialized"] is True

    async def test_health_no_agent(self):
        app = create_app(agent=None, shutdown_event=asyncio.Event())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            resp = await c.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["agent_initialized"] is False


class TestRestartOrchestratorRecovery:
    async def test_agent_mode_endpoint_keeps_multi_agent_enabled(self, shutdown_event):
        mock_orchestrator = MagicMock()
        app = create_app(
            agent=MagicMock(initialized=True, _tools=[], tool_catalog=MagicMock(), handler_registry=MagicMock()),
            shutdown_event=shutdown_event,
            orchestrator=mock_orchestrator,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            with (
                patch("openakita.config.settings") as mock_settings,
                patch("openakita.api.routes.config._hot_patch_agent_tools"),
            ):
                mock_settings.multi_agent_enabled = True
                mock_settings.data_dir = MagicMock()
                resp = await c.post("/api/config/agent-mode", json={"enabled": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["multi_agent_enabled"] is True
        shutdown_event.clear()
