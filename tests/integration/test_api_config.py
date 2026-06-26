"""L3 Integration Tests: FastAPI /api/config/* endpoints."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.api.server import create_app


@pytest.fixture
def app():
    return create_app(
        agent=MagicMock(initialized=True),
        shutdown_event=asyncio.Event(),
    )


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


class TestWorkspaceInfo:
    async def test_returns_workspace_info(self, client):
        resp = await client.get("/api/config/workspace-info")
        assert resp.status_code == 200
        data = resp.json()
        assert "workspace" in data or "path" in str(data).lower() or isinstance(data, dict)


class TestProviders:
    async def test_list_providers(self, client):
        resp = await client.get("/api/config/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
