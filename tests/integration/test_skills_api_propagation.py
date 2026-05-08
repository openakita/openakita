"""API 路由与 ``Agent.propagate_skill_change`` 的集成测试。

验证 FastAPI 路由在各路径上都会正确调用统一刷新入口：
- ``POST /api/skills/install`` → propagate_skill_change("install", rescan=True)
- ``POST /api/skills/uninstall`` → propagate_skill_change("uninstall", rescan=True)
- ``POST /api/skills/reload`` → propagate_skill_change("reload", rescan=...)
- ``PUT /api/skills/content/{name}`` → propagate_skill_change("content_update", rescan=False)
- ``POST /api/config/skills`` → propagate_skill_change(SkillEvent.ENABLE, rescan=False)

以及 ``notify_skills_changed`` 回调会清空 GET /api/skills 的模块缓存。

所有外部依赖（bridge.install_skill / uninstall_skill、SkillLoader.reload_skill、
parser / shutil）都被 monkeypatch 屏蔽，保证测试不依赖网络与真实 git。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from openakita.api.server import create_app

# ---------------------------------------------------------------------------
# FakeAgent：只提供路由实际触达的属性
# ---------------------------------------------------------------------------


def _make_fake_agent(tmp_path: Path) -> SimpleNamespace:
    agent = SimpleNamespace()
    agent.propagate_skill_change = MagicMock()
    agent.skill_loader = MagicMock()
    agent.skill_loader.reload_skill = MagicMock(return_value=True)
    agent.skill_loader.get_skill = MagicMock(return_value=None)
    agent.skill_loader.load_all = MagicMock(return_value=0)
    agent.skill_loader.compute_effective_allowlist = MagicMock(return_value=None)
    agent.skill_registry = MagicMock()
    agent.skill_registry.list_all = MagicMock(return_value=[])
    agent.brain = None
    # _resolve_agent 的兜底路径：当 app.state.agent 不是真正的 Agent 实例时，
    # 会尝试 getattr(agent, "_local_agent", None)。设为自身即可返回 fake。
    agent._local_agent = agent
    return agent


@pytest.fixture
async def app_with_fake_agent(tmp_path: Path, monkeypatch):
    """创建 app 并绑定 fake agent + 把 project_root 重定向到 tmp_path。"""
    from openakita.config import settings as real_settings

    monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
    (tmp_path / "data").mkdir(exist_ok=True)

    app = create_app()
    agent = _make_fake_agent(tmp_path)
    app.state.agent = agent
    app.state.session_manager = None
    return app, agent


@pytest.fixture
async def client(app_with_fake_agent):
    app, _ = app_with_fake_agent
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# /api/skills/install
# ---------------------------------------------------------------------------


class TestInstallRoute:
    async def test_install_triggers_propagate_with_install_action(
        self, app_with_fake_agent, client, monkeypatch, tmp_path: Path
    ):
        """成功安装后应调 propagate_skill_change("install")。"""
        _, agent = app_with_fake_agent

        # 桥接到 stub install（不跑真实 git clone）
        def fake_install(workspace, url, *, category=None):
            skill_dir = tmp_path / "workspaces" / "default" / "skills" / "my-new-skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-new-skill\ndescription: test\n---\n# body",
                encoding="utf-8",
            )

        monkeypatch.setattr(
            "openakita.setup_center.bridge.install_skill", fake_install
        )
        monkeypatch.setattr(
            "openakita.setup_center.bridge._resolve_skills_dir",
            lambda ws: tmp_path / "workspaces" / "default" / "skills",
        )

        resp = await client.post("/api/skills/install", json={"url": "github:foo/bar"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

        agent.propagate_skill_change.assert_called()
        call_args = agent.propagate_skill_change.call_args
        assert call_args.args[0] == "install"
        # install 路径默认 rescan=True
        assert call_args.kwargs.get("rescan", True) is True

    async def test_install_missing_url_does_not_propagate(
        self, app_with_fake_agent, client
    ):
        _, agent = app_with_fake_agent
        resp = await client.post("/api/skills/install", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()

    async def test_install_error_does_not_propagate(
        self, app_with_fake_agent, client, monkeypatch
    ):
        _, agent = app_with_fake_agent
        monkeypatch.setattr(
            "openakita.setup_center.bridge.install_skill",
            MagicMock(side_effect=RuntimeError("clone failed")),
        )
        resp = await client.post("/api/skills/install", json={"url": "x"})
        assert resp.status_code == 200
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()

    async def test_install_structured_error_keeps_specific_message(
        self, app_with_fake_agent, client, monkeypatch
    ):
        from openakita.setup_center.bridge import SkillInstallError

        _, agent = app_with_fake_agent
        monkeypatch.setattr(
            "openakita.setup_center.bridge.install_skill",
            MagicMock(
                side_effect=SkillInstallError(
                    "skill_residual_cleanup_failed",
                    "无法清理残留技能目录：C:/x/skills/demo",
                )
            ),
        )

        resp = await client.post("/api/skills/install", json={"url": "github:x/demo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error_code"] == "skill_residual_cleanup_failed"
        assert "无法清理残留技能目录" in data["error"]
        agent.propagate_skill_change.assert_not_called()


class TestHubSkillInstallRoute:
    async def test_store_install_updates_allowlist_and_uses_unified_propagation(
        self, app_with_fake_agent, client, monkeypatch, tmp_path: Path
    ):
        """Store install must not bypass allowlist + runtime propagation."""
        _, agent = app_with_fake_agent
        skills_json = tmp_path / "data" / "skills.json"
        skills_json.write_text(
            json.dumps({"version": 1, "external_allowlist": ["existing"]}),
            encoding="utf-8",
        )
        skill_dir = tmp_path / "workspaces" / "default" / "skills" / "store-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: store-skill\ndescription: test\n---\n# body",
            encoding="utf-8",
        )

        class FakeSkillClient:
            async def get_detail(self, skill_id: str):
                return {
                    "skill": {
                        "id": skill_id,
                        "name": "Store Skill",
                        "installUrl": "owner/repo@store-skill",
                        "trustLevel": "community",
                    }
                }

            async def install_skill(self, install_url: str, *, skill_id: str):
                assert install_url == "owner/repo@store-skill"
                assert skill_id == "store-skill"
                return skill_dir

            async def close(self):
                return None

        monkeypatch.setattr(
            "openakita.api.routes.hub._get_skill_client",
            lambda: FakeSkillClient(),
        )

        resp = await client.post("/api/hub/skills/store-skill/install")

        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_dir"] == str(skill_dir)
        saved = json.loads(skills_json.read_text(encoding="utf-8"))
        assert saved["external_allowlist"] == ["existing", "store-skill"]
        agent.propagate_skill_change.assert_called_once()
        call_args = agent.propagate_skill_change.call_args
        assert getattr(call_args.args[0], "value", call_args.args[0]) == "store_install"
        assert call_args.kwargs.get("rescan", True) is True

# ---------------------------------------------------------------------------
# /api/skills/uninstall
# ---------------------------------------------------------------------------


class TestUninstallRoute:
    async def test_uninstall_triggers_propagate(
        self, app_with_fake_agent, client, monkeypatch
    ):
        _, agent = app_with_fake_agent
        monkeypatch.setattr(
            "openakita.setup_center.bridge.uninstall_skill",
            MagicMock(return_value=None),
        )
        resp = await client.post(
            "/api/skills/uninstall", json={"skill_id": "some-skill"}
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

        agent.propagate_skill_change.assert_called_once()
        assert agent.propagate_skill_change.call_args.args[0] == "uninstall"

    async def test_uninstall_missing_id(self, app_with_fake_agent, client):
        _, agent = app_with_fake_agent
        resp = await client.post("/api/skills/uninstall", json={})
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# /api/skills/reload
# ---------------------------------------------------------------------------


class TestReloadRoute:
    async def test_reload_all_uses_rescan_true(self, app_with_fake_agent, client):
        _, agent = app_with_fake_agent
        resp = await client.post("/api/skills/reload", json={})
        assert resp.status_code == 200
        agent.propagate_skill_change.assert_called_once()
        call = agent.propagate_skill_change.call_args
        assert call.args[0] == "reload"
        assert call.kwargs.get("rescan") is True

    async def test_reload_all_reports_partial_success_for_skipped_skills(
        self, app_with_fake_agent, client
    ):
        _, agent = app_with_fake_agent
        agent.skill_loader.last_load_issues = [
            {
                "skill_id": "bad-skill",
                "path": "D:/OpenAkita/workspaces/default/skills/bad-skill",
                "error": "name must be lowercase alphanumeric with hyphens",
            }
        ]

        resp = await client.post("/api/skills/reload", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["partial"] is True
        assert data["skipped_count"] == 1
        assert data["skipped_skills"][0]["skill_id"] == "bad-skill"
        assert "error" not in data
        agent.propagate_skill_change.assert_called_once()

    async def test_reload_single_uses_rescan_false(self, app_with_fake_agent, client):
        _, agent = app_with_fake_agent
        agent.skill_loader.reload_skill.return_value = True
        resp = await client.post(
            "/api/skills/reload", json={"skill_name": "foo"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("reloaded") == ["foo"]
        agent.propagate_skill_change.assert_called_once()
        call = agent.propagate_skill_change.call_args
        assert call.args[0] == "reload"
        assert call.kwargs.get("rescan") is False

    async def test_reload_single_not_found_does_not_propagate(
        self, app_with_fake_agent, client
    ):
        _, agent = app_with_fake_agent
        agent.skill_loader.reload_skill.return_value = None
        resp = await client.post(
            "/api/skills/reload", json={"skill_name": "ghost"}
        )
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# PUT /api/skills/content/{name}
# ---------------------------------------------------------------------------


class TestUpdateContentRoute:
    async def test_valid_update_triggers_propagate_with_content_update(
        self, app_with_fake_agent, client, tmp_path: Path
    ):
        _, agent = app_with_fake_agent

        # 构造一个假 skill：非系统，可编辑
        skill_path = tmp_path / "fake-skill" / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(
            "---\nname: fake\ndescription: old\n---\n# body",
            encoding="utf-8",
        )

        fake_skill = SimpleNamespace(
            path=skill_path,
            metadata=SimpleNamespace(system=False, name="fake", description="old"),
        )
        agent.skill_loader.get_skill.return_value = fake_skill
        agent.skill_loader.reload_skill.return_value = True

        new_content = "---\nname: fake\ndescription: new\n---\n# new body"
        resp = await client.put(
            "/api/skills/content/fake",
            json={"content": new_content},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert data.get("reloaded") is True

        agent.propagate_skill_change.assert_called_once()
        call = agent.propagate_skill_change.call_args
        assert call.args[0] == "content_update"
        assert call.kwargs.get("rescan") is False

        # 磁盘内容已被写入
        assert "description: new" in skill_path.read_text(encoding="utf-8")

    async def test_system_skill_not_editable_no_propagate(
        self, app_with_fake_agent, client, tmp_path: Path
    ):
        _, agent = app_with_fake_agent
        skill_path = tmp_path / "sys-skill" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("old", encoding="utf-8")

        agent.skill_loader.get_skill.return_value = SimpleNamespace(
            path=skill_path,
            metadata=SimpleNamespace(system=True, name="sys"),
        )

        resp = await client.put(
            "/api/skills/content/sys",
            json={"content": "---\nname: sys\ndescription: hacked\n---\nhack"},
        )
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()
        # 原文件未被改写
        assert skill_path.read_text(encoding="utf-8") == "old"

    async def test_empty_content_no_propagate(self, app_with_fake_agent, client):
        _, agent = app_with_fake_agent
        resp = await client.put(
            "/api/skills/content/some",
            json={"content": ""},
        )
        assert "error" in resp.json()
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/config/skills（allowlist 写入后触发 ENABLE 刷新，rescan=False）
# ---------------------------------------------------------------------------


class TestConfigSkillsRoute:
    async def test_write_allowlist_triggers_propagate_enable_rescan_false(
        self, app_with_fake_agent, client
    ):
        _, agent = app_with_fake_agent
        payload = {"content": {"version": 1, "external_allowlist": ["a", "b"]}}
        resp = await client.post("/api/config/skills", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

        agent.propagate_skill_change.assert_called_once()
        call = agent.propagate_skill_change.call_args
        # action 是 SkillEvent.ENABLE（枚举值为 "enable"）
        action = call.args[0]
        assert str(action).endswith("enable") or getattr(action, "value", action) == "enable"
        assert call.kwargs.get("rescan") is False


# ---------------------------------------------------------------------------
# 跨层缓存失效：notify_skills_changed 清空 GET /api/skills 缓存
# ---------------------------------------------------------------------------


class TestGetSkillsCacheInvalidation:
    async def test_cache_cleared_on_notify(self, app_with_fake_agent, client):
        """直接触发 notify_skills_changed，应清空 _skills_cache。"""
        from openakita.api.routes import skills as skills_route
        from openakita.skills.events import notify_skills_changed

        skills_route._skills_cache = {"skills": [{"stale": True}]}
        notify_skills_changed("install")
        assert skills_route._skills_cache is None

