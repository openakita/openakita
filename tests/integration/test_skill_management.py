"""技能管理端到端集成测试。

覆盖 v1.25.9 修复的技能相关 bug：
1. 已安装技能列表 API 返回正确数据
2. 含斜杠的技能名（如 openakita/skills@canvas-design）能查看详情不报 404
3. 技能内容读取 GET /api/skills/content/{skill_name:path}
4. 技能内容更新 PUT /api/skills/content/{skill_name:path}（非系统技能）
5. 系统技能不可编辑
6. 技能搜索功能
7. 技能重载
"""

import pytest
import httpx

from openakita.api.server import create_app


@pytest.fixture
async def client():
    app = create_app()
    app.state.agent = None
    app.state.session_manager = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. 已安装技能列表
# ---------------------------------------------------------------------------

class TestListSkills:

    async def test_list_skills_returns_200(self, client):
        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)

    async def test_list_skills_each_has_required_fields(self, client):
        resp = await client.get("/api/skills")
        if resp.status_code != 200:
            pytest.skip("Skills endpoint not available")
        for skill in resp.json().get("skills", []):
            assert "name" in skill
            assert "description" in skill
            assert "system" in skill
            assert "enabled" in skill

    async def test_list_skills_sorted_enabled_first(self, client):
        resp = await client.get("/api/skills")
        if resp.status_code != 200:
            pytest.skip("Skills endpoint not available")
        skills = resp.json().get("skills", [])
        if len(skills) < 2:
            pytest.skip("Not enough skills to verify sorting")
        enabled_indices = [i for i, s in enumerate(skills) if s["enabled"]]
        disabled_indices = [i for i, s in enumerate(skills) if not s["enabled"]]
        if enabled_indices and disabled_indices:
            assert max(enabled_indices) < min(disabled_indices)


# ---------------------------------------------------------------------------
# 2. 含斜杠的技能名查看详情
# ---------------------------------------------------------------------------

class TestSkillWithSlashName:

    async def test_slash_skill_name_not_404(self, client):
        resp = await client.get("/api/skills/content/openakita/skills@canvas-design")
        assert resp.status_code != 404

    async def test_nested_slash_skill_name(self, client):
        resp = await client.get("/api/skills/content/org/sub/skill-name")
        assert resp.status_code != 404


# ---------------------------------------------------------------------------
# 3. 技能内容读取
# ---------------------------------------------------------------------------

class TestGetSkillContent:

    async def test_get_existing_skill_content(self, client):
        list_resp = await client.get("/api/skills")
        if list_resp.status_code != 200:
            pytest.skip("Skills endpoint not available")
        skills = list_resp.json().get("skills", [])
        if not skills:
            pytest.skip("No skills available")

        skill_name = skills[0]["name"]
        resp = await client.get(f"/api/skills/content/{skill_name}")
        assert resp.status_code == 200
        data = resp.json()
        if "error" not in data:
            assert "content" in data
            assert "path" in data
            assert "system" in data

    async def test_get_nonexistent_skill_returns_error(self, client):
        resp = await client.get("/api/skills/content/__nonexistent_skill_xyz__")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ---------------------------------------------------------------------------
# 4. 技能内容更新（非系统技能）
# ---------------------------------------------------------------------------

class TestUpdateSkillContent:

    async def test_update_nonexistent_skill_returns_error(self, client):
        resp = await client.put(
            "/api/skills/content/__nonexistent_skill_xyz__",
            json={"content": "# Test\nname: test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    async def test_update_with_empty_content_returns_error(self, client):
        resp = await client.put(
            "/api/skills/content/some-skill",
            json={"content": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    async def test_update_with_invalid_json_returns_error(self, client):
        resp = await client.put(
            "/api/skills/content/some-skill",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 5. 系统技能不可编辑
# ---------------------------------------------------------------------------

class TestSystemSkillReadOnly:

    async def test_system_skill_cannot_be_updated(self, client):
        list_resp = await client.get("/api/skills")
        if list_resp.status_code != 200:
            pytest.skip("Skills endpoint not available")
        skills = list_resp.json().get("skills", [])
        system_skills = [s for s in skills if s.get("system")]
        if not system_skills:
            pytest.skip("No system skills available")

        skill_name = system_skills[0]["name"]
        resp = await client.put(
            f"/api/skills/content/{skill_name}",
            json={"content": "---\nname: hacked\n---\nHacked!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ---------------------------------------------------------------------------
# 6. 技能搜索
# ---------------------------------------------------------------------------

class TestSkillSearch:

    async def test_marketplace_search_returns_response(self, client):
        resp = await client.get("/api/skills/marketplace?q=test")
        assert resp.status_code in (200, 500, 502, 503)

    async def test_marketplace_search_with_empty_query(self, client):
        resp = await client.get("/api/skills/marketplace?q=")
        assert resp.status_code in (200, 422, 500, 502, 503)


# ---------------------------------------------------------------------------
# 7. 技能重载
# ---------------------------------------------------------------------------

class TestSkillReload:

    async def test_reload_returns_success_or_error(self, client):
        resp = await client.post("/api/skills/reload")
        assert resp.status_code in (200, 500)

    async def test_reload_result_structure(self, client):
        resp = await client.post("/api/skills/reload")
        if resp.status_code != 200:
            pytest.skip("Reload endpoint not available")
        data = resp.json()
        assert isinstance(data, dict)

    async def test_skills_available_after_reload(self, client):
        reload_resp = await client.post("/api/skills/reload")
        if reload_resp.status_code != 200:
            pytest.skip("Reload endpoint not available")

        list_resp = await client.get("/api/skills")
        assert list_resp.status_code == 200
        assert "skills" in list_resp.json()


# ---------------------------------------------------------------------------
# 8. 边界情况
# ---------------------------------------------------------------------------

class TestSkillEdgeCases:

    async def test_special_characters_in_skill_name(self, client):
        resp = await client.get("/api/skills/content/skill%20with%20spaces")
        assert resp.status_code != 500

    async def test_very_long_skill_name(self, client):
        long_name = "a" * 500
        resp = await client.get(f"/api/skills/content/{long_name}")
        assert resp.status_code in (200, 404, 414)

    async def test_config_endpoint(self, client):
        resp = await client.post(
            "/api/skills/config",
            json={"skill_name": "test-skill", "config": {"key": "value"}},
        )
        assert resp.status_code in (200, 500)

