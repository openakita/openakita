from __future__ import annotations

from pathlib import Path

import pytest

try:
    from httpx import ASGITransport, AsyncClient
except ImportError:  # pragma: no cover
    pytest.skip("httpx not installed", allow_module_level=True)

from openakita.orgs.manager import OrgManager
from openakita.orgs.models import NodeStatus, Organization
from openakita.orgs.runtime import OrgRuntime
from tests.orgs.conftest import make_org


@pytest.mark.asyncio
async def test_get_org_prefers_runtime_node_status_snapshot(tmp_path: Path):
    from fastapi import FastAPI

    from openakita.api.routes.orgs import router as org_router

    manager = OrgManager(tmp_path / "data")
    runtime = OrgRuntime(manager)
    created = manager.create(make_org().to_dict())

    stale = Organization.from_dict(created.to_dict())
    active = Organization.from_dict(created.to_dict())
    target = active.get_node("node_cto")
    assert target is not None
    target.status = NodeStatus.BUSY

    manager._cache[created.id] = stale
    runtime._active_orgs[created.id] = active

    app = FastAPI()
    app.state.org_manager = manager
    app.state.org_runtime = runtime
    app.include_router(org_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/orgs/{created.id}")

    assert response.status_code == 200
    nodes = {node["id"]: node for node in response.json()["nodes"]}
    assert nodes["node_cto"]["status"] == "busy"
