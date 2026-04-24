"""End-to-end API tests for org prompt system.

Tests the full chain: frontend API → backend processing → prompt generation.
Validates:
1. custom_prompt modification via PUT org → reflected in prompt-preview
2. ROLE.md writing via identity API → overrides custom_prompt in preview
3. prompt-preview returns new enriched structure (tool_summary, etc.)
4. Tool carrying rules reflected in prompt-preview's tool_summary
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
from openakita.orgs.runtime import OrgRuntime


@pytest.fixture()
async def app_client(tmp_data_dir: Path):
    from openakita.api.routes.orgs import router as org_router, inbox_router
    from fastapi import FastAPI

    app = FastAPI()
    manager = OrgManager(tmp_data_dir)
    runtime = OrgRuntime(manager)

    app.state.org_manager = manager
    app.state.org_runtime = runtime

    app.include_router(org_router)
    app.include_router(inbox_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, manager, runtime


def _create_test_org(manager: OrgManager, **kwargs) -> dict:
    from tests.orgs.conftest import make_org, make_node, make_edge
    nodes = kwargs.pop("nodes", None)
    if nodes is None:
        nodes = [
            make_node("ceo", "CEO", 0, "管理层", external_tools=["research", "planning"]),
            make_node("cto", "CTO", 1, "技术部", external_tools=["filesystem", "research"]),
            make_node("dev", "开发", 2, "技术部"),
        ]
    edges = kwargs.pop("edges", None)
    if edges is None:
        edges = [make_edge("ceo", "cto"), make_edge("cto", "dev")]
    org = make_org(nodes=nodes, edges=edges, **kwargs)
    created = manager.create(org.to_dict())
    return {"org": created, "org_id": created.id}


# ---------------------------------------------------------------------------
# 1. custom_prompt modification via API
# ---------------------------------------------------------------------------


class TestCustomPromptViaAPI:
    async def test_update_custom_prompt_reflected_in_preview(self, app_client):
        """PUT org with new custom_prompt → prompt-preview shows it."""
        client, manager, runtime = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        assert resp.status_code == 200
        initial = resp.json()
        assert "CUSTOM_MARKER" not in initial["full_prompt"]

        org_data = (await client.get(f"/api/orgs/{org_id}")).json()
        for n in org_data["nodes"]:
            if n["id"] == "ceo":
                n["custom_prompt"] = "CUSTOM_MARKER_测试自定义提示词"
                break
        resp = await client.put(f"/api/orgs/{org_id}", json=org_data)
        assert resp.status_code == 200

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        assert resp.status_code == 200
        updated = resp.json()
        assert "CUSTOM_MARKER_测试自定义提示词" in updated["full_prompt"]
        assert "你的组织角色" in updated["full_prompt"]

    async def test_empty_custom_prompt_uses_auto_generated(self, app_client):
        """Empty custom_prompt → auto-generated role description used."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/cto/prompt-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_level"] == 0
        assert "CTO" in data["role_text"]


# ---------------------------------------------------------------------------
# 2. ROLE.md via identity API
# ---------------------------------------------------------------------------


class TestRoleMdViaAPI:
    async def test_write_role_md_overrides_custom_prompt(self, app_client):
        """Write ROLE.md via identity API → preview uses ROLE.md, not custom_prompt."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        org_data = (await client.get(f"/api/orgs/{org_id}")).json()
        for n in org_data["nodes"]:
            if n["id"] == "cto":
                n["custom_prompt"] = "CUSTOM_SHOULD_BE_OVERRIDDEN"
                break
        await client.put(f"/api/orgs/{org_id}", json=org_data)

        resp = await client.put(
            f"/api/orgs/{org_id}/nodes/cto/identity",
            json={"ROLE.md": "ROLE_MD_TAKES_PRIORITY_角色定义文件"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/orgs/{org_id}/nodes/cto/prompt-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "ROLE_MD_TAKES_PRIORITY_角色定义文件" in data["full_prompt"]
        assert "CUSTOM_SHOULD_BE_OVERRIDDEN" not in data["full_prompt"]
        assert data["identity_level"] >= 1

    async def test_delete_role_md_falls_back(self, app_client):
        """Delete ROLE.md → falls back to custom_prompt."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        org_data = (await client.get(f"/api/orgs/{org_id}")).json()
        for n in org_data["nodes"]:
            if n["id"] == "cto":
                n["custom_prompt"] = "FALLBACK_CUSTOM_PROMPT"
                break
        await client.put(f"/api/orgs/{org_id}", json=org_data)

        await client.put(
            f"/api/orgs/{org_id}/nodes/cto/identity",
            json={"ROLE.md": "TEMP_ROLE"},
        )
        resp = await client.get(f"/api/orgs/{org_id}/nodes/cto/prompt-preview")
        assert "TEMP_ROLE" in resp.json()["full_prompt"]

        await client.put(
            f"/api/orgs/{org_id}/nodes/cto/identity",
            json={"ROLE.md": None},
        )
        resp = await client.get(f"/api/orgs/{org_id}/nodes/cto/prompt-preview")
        data = resp.json()
        assert "FALLBACK_CUSTOM_PROMPT" in data["full_prompt"]
        assert "TEMP_ROLE" not in data["full_prompt"]
        assert data["identity_level"] == 0

    async def test_soul_md_not_in_preview(self, app_client):
        """Writing SOUL.md should NOT inject its content into the prompt."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.put(
            f"/api/orgs/{org_id}/nodes/ceo/identity",
            json={"SOUL.md": "SOUL_CONTENT_SHOULD_NOT_APPEAR"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        data = resp.json()
        assert "SOUL_CONTENT_SHOULD_NOT_APPEAR" not in data["full_prompt"]
        assert data["soul_agent_injected"] is False
        assert "soul_agent_note" in data


# ---------------------------------------------------------------------------
# 3. prompt-preview enriched structure
# ---------------------------------------------------------------------------


class TestPromptPreviewStructure:
    async def test_preview_returns_tool_summary(self, app_client):
        """prompt-preview must return tool_summary with expanded tools."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        assert resp.status_code == 200
        data = resp.json()

        assert "tool_summary" in data
        ts = data["tool_summary"]
        assert "keep_tools" in ts
        assert "get_tool_info" in ts["keep_tools"]
        assert "external_tools_config" in ts
        assert "research" in ts["external_tools_config"]
        assert "blocked_conflict_tools" in ts
        assert "delegate_to_agent" in ts["blocked_conflict_tools"]

    async def test_preview_returns_lean_prompt_structure(self, app_client):
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        data = resp.json()

        assert "lean_prompt_structure" in data
        structure = data["lean_prompt_structure"]
        assert any("组织上下文" in s for s in structure if s)
        assert any("运行环境" in s for s in structure if s)

    async def test_preview_shows_identity_level_desc(self, app_client):
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/ceo/prompt-preview")
        data = resp.json()
        assert "identity_level_desc" in data
        assert "Level 0" in data["identity_level_desc"]

    async def test_preview_node_without_external_tools(self, app_client):
        """Node without external_tools: tool_summary should show empty expanded."""
        client, manager, _ = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/dev/prompt-preview")
        data = resp.json()
        assert data["tool_summary"]["external_tools_expanded"] == []
        assert data["tool_summary"]["external_tools_config"] == []


# ---------------------------------------------------------------------------
# 4. Tool carrying validation via prompt-preview
# ---------------------------------------------------------------------------


class TestToolCarryingViaAPI:
    async def test_conflict_tools_not_in_expanded(self, app_client):
        """Even if conflict tools are in external_tools, they're excluded."""
        client, manager, _ = app_client
        from tests.orgs.conftest import make_node, make_edge, make_org
        org = make_org(
            nodes=[
                make_node("boss", "Boss", 0, "HQ", external_tools=[
                    "research", "delegate_to_agent", "spawn_agent",
                ]),
                make_node("worker", "Worker", 1, "Team"),
            ],
            edges=[make_edge("boss", "worker")],
        )
        created = manager.create(org.to_dict())

        resp = await client.get(
            f"/api/orgs/{created.id}/nodes/boss/prompt-preview"
        )
        data = resp.json()
        expanded = data["tool_summary"]["external_tools_expanded"]
        assert "delegate_to_agent" not in expanded
        assert "spawn_agent" not in expanded
        assert "web_search" in expanded

    async def test_skills_category_expansion(self, app_client):
        """skills category should expand to skill tools."""
        client, manager, _ = app_client
        from tests.orgs.conftest import make_node, make_edge, make_org
        org = make_org(
            nodes=[
                make_node("n1", "SkillUser", 0, "Team", external_tools=["skills"]),
            ],
            edges=[],
        )
        created = manager.create(org.to_dict())

        resp = await client.get(
            f"/api/orgs/{created.id}/nodes/n1/prompt-preview"
        )
        data = resp.json()
        expanded = data["tool_summary"]["external_tools_expanded"]
        assert "run_skill_script" in expanded
        assert "list_skills" in expanded

    async def test_grant_tools_updates_preview(self, app_client):
        """After granting tools, prompt-preview should reflect new tools."""
        client, manager, runtime = app_client
        ctx = _create_test_org(manager)
        org_id = ctx["org_id"]

        resp = await client.get(f"/api/orgs/{org_id}/nodes/dev/prompt-preview")
        initial_expanded = resp.json()["tool_summary"]["external_tools_expanded"]
        assert len(initial_expanded) == 0

        org_data = (await client.get(f"/api/orgs/{org_id}")).json()
        for n in org_data["nodes"]:
            if n["id"] == "dev":
                n["external_tools"] = ["filesystem"]
                break
        await client.put(f"/api/orgs/{org_id}", json=org_data)

        resp = await client.get(f"/api/orgs/{org_id}/nodes/dev/prompt-preview")
        updated = resp.json()["tool_summary"]["external_tools_expanded"]
        assert "read_file" in updated
        assert "write_file" in updated

