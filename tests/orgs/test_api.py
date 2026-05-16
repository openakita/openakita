"""Tests for org API routes — endpoint integration tests.

These tests use httpx.AsyncClient against the FastAPI app.
They verify request/response contracts without running actual LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from httpx import ASGITransport, AsyncClient
except ImportError:
    pytest.skip("httpx not installed", allow_module_level=True)

from openakita.orgs.manager import OrgManager


@pytest.fixture()
async def app_client(tmp_data_dir: Path):
    """Create a test FastAPI app with OrgManager wired up."""
    from openakita.api.routes.orgs import router as org_router, inbox_router
    from fastapi import FastAPI

    app = FastAPI()
    manager = OrgManager(tmp_data_dir)

    from openakita.orgs.runtime import OrgRuntime
    runtime = OrgRuntime(manager)

    app.state.org_manager = manager
    app.state.org_runtime = runtime

    app.include_router(org_router)
    app.include_router(inbox_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, manager, runtime


class TestOrgCRUDRoutes:
    async def test_list_orgs_empty(self, app_client):
        client, _, _ = app_client
        resp = await client.get("/api/orgs")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_org(self, app_client):
        client, _, _ = app_client
        resp = await client.post("/api/orgs", json={"name": "API测试", "description": "测试描述"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "API测试"
        assert "id" in data

    async def test_get_org(self, app_client):
        client, manager, _ = app_client
        org = manager.create({"name": "读取测试"})
        resp = await client.get(f"/api/orgs/{org.id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "读取测试"

    async def test_get_nonexistent_org(self, app_client):
        client, _, _ = app_client
        resp = await client.get("/api/orgs/fake_id")
        assert resp.status_code == 404

    async def test_update_org(self, app_client):
        client, manager, _ = app_client
        org = manager.create({"name": "旧名"})
        resp = await client.put(f"/api/orgs/{org.id}", json={"name": "新名"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"

    async def test_create_duplicate_name_returns_409(self, app_client):
        """组织名字必须全局唯一 — 创建时碰到同名应直接 409，
        以保证聊天/IM 端可以放心按名字调用。"""
        client, manager, _ = app_client
        manager.create({"name": "唯一名"})
        resp = await client.post("/api/orgs", json={"name": "唯一名"})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "org_name_conflict"
        assert detail["name"] == "唯一名"
        assert detail["conflict_org_id"]

    async def test_update_to_existing_name_returns_409(self, app_client):
        client, manager, _ = app_client
        manager.create({"name": "已被占用"})
        other = manager.create({"name": "另一个"})
        resp = await client.put(f"/api/orgs/{other.id}", json={"name": "已被占用"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "org_name_conflict"

    async def test_duplicate_with_conflict_name_returns_409(self, app_client):
        client, manager, _ = app_client
        manager.create({"name": "目标占用名"})
        src = manager.create({"name": "源组织"})
        resp = await client.post(
            f"/api/orgs/{src.id}/duplicate", json={"name": "目标占用名"}
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "org_name_conflict"

    async def test_from_template_with_conflict_override_returns_409(self, app_client):
        client, manager, _ = app_client
        # 准备一个模板，并先用同名占住
        seed = manager.create({"name": "模板源"})
        manager.save_as_template(seed.id, "tpl-conflict")
        manager.create({"name": "已被占用-tpl"})
        resp = await client.post(
            "/api/orgs/from-template",
            json={"template_id": "tpl-conflict", "name": "已被占用-tpl"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "org_name_conflict"

    async def test_delete_org(self, app_client):
        client, manager, _ = app_client
        org = manager.create({"name": "删除"})
        resp = await client.delete(f"/api/orgs/{org.id}")
        assert resp.status_code == 200

        resp2 = await client.get(f"/api/orgs/{org.id}")
        assert resp2.status_code == 404


class TestTemplateRoutes:
    async def test_list_templates(self, app_client):
        client, manager, _ = app_client
        from openakita.orgs.templates import ensure_builtin_templates
        ensure_builtin_templates(manager._templates_dir)

        resp = await client.get("/api/orgs/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3

    async def test_create_from_template(self, app_client):
        client, manager, _ = app_client
        from openakita.orgs.templates import ensure_builtin_templates
        ensure_builtin_templates(manager._templates_dir)

        resp = await client.post(
            "/api/orgs/from-template",
            json={"template_id": "startup-company", "name": "新公司"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "新公司"
        assert len(data.get("nodes", [])) > 0


class TestNodeScheduleRoutes:
    async def test_schedule_crud(self, app_client):
        client, manager, _ = app_client
        from .conftest import make_org
        org = manager.create(make_org().to_dict())
        nid = org.nodes[0].id

        resp = await client.get(f"/api/orgs/{org.id}/nodes/{nid}/schedules")
        assert resp.status_code == 200
        assert resp.json() == []

        resp = await client.post(
            f"/api/orgs/{org.id}/nodes/{nid}/schedules",
            json={"name": "巡检", "schedule_type": "interval", "interval_s": 600, "prompt": "检查"},
        )
        assert resp.status_code == 201
        sched = resp.json()
        assert sched["name"] == "巡检"

        resp = await client.get(f"/api/orgs/{org.id}/nodes/{nid}/schedules")
        assert len(resp.json()) == 1


class TestPolicyRoutes:
    async def test_policy_write_and_read(self, app_client):
        client, manager, _ = app_client
        from .conftest import make_org
        org = manager.create(make_org().to_dict())

        resp = await client.put(
            f"/api/orgs/{org.id}/policies/test-rule.md",
            json={"content": "# 测试规则\n\n正文内容"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/orgs/{org.id}/policies/test-rule.md")
        assert resp.status_code == 200
        assert "测试规则" in resp.json().get("content", "")

    async def test_policy_list(self, app_client):
        client, manager, _ = app_client
        from .conftest import make_org
        org = manager.create(make_org().to_dict())
        manager.invalidate_cache(org.id)

        from openakita.orgs.policies import OrgPolicies
        policies = OrgPolicies(manager._org_dir(org.id))
        policies.write_policy("a.md", "# A")

        resp = await client.get(f"/api/orgs/{org.id}/policies")
        assert resp.status_code == 200
        assert any(p["filename"] == "a.md" for p in resp.json())


class TestLifecycleRoutes:
    async def test_start_org(self, app_client):
        client, manager, runtime = app_client
        from .conftest import make_org
        org = manager.create(make_org().to_dict())

        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()

        try:
            resp = await client.post(f"/api/orgs/{org.id}/start")
            assert resp.status_code == 200

            resp = await client.post(f"/api/orgs/{org.id}/stop")
            assert resp.status_code == 200
        finally:
            await runtime.shutdown()


class TestInboxRoutes:
    async def test_global_inbox(self, app_client):
        client, manager, runtime = app_client
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            resp = await client.get("/api/org-inbox")
            assert resp.status_code == 200
            data = resp.json()
            assert "messages" in data
        finally:
            await runtime.shutdown()

    async def test_unread_count(self, app_client):
        client, _, runtime = app_client
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            resp = await client.get("/api/org-inbox/unread-count")
            assert resp.status_code == 200
            assert "total_unread" in resp.json()
        finally:
            await runtime.shutdown()


