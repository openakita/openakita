"""Tool handlers 统一刷新路径的单元测试。

验证所有会改动技能状态的工具处理器都正确调用 ``Agent.propagate_skill_change``，
不再走任何旧的「半套刷新」路径（如手工重建 catalog / 通知 pool）。

覆盖：
- ``SkillsHandler._install_skill`` (INSTALL)
- ``SkillsHandler._load_skill`` (LOAD, rescan=False)
- ``SkillsHandler._reload_skill`` (RELOAD, rescan=False)
- ``SkillsHandler._manage_skill_enabled`` (ENABLE / DISABLE, rescan=False) + 原子写入
- ``SkillsHandler._uninstall_skill`` (UNINSTALL, rescan=False) + allowlist 同步移除
- ``SkillStoreHandler._install`` (STORE_INSTALL)
- ``AgentPackageHandler._try_reload_skills`` (INSTALL)
- ``AgentHubHandler._try_reload_skills`` (INSTALL)

Watcher 回调 ``Agent._on_skills_dir_changed`` 的测试也包含在此（HOT_RELOAD）。
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(dir_: Path, name: str, description: str = "desc") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: {description}
            ---

            # {name}
            Body.
            """
        ),
        encoding="utf-8",
    )
    return dir_


def _make_agent_for_skills_handler() -> SimpleNamespace:
    agent = SimpleNamespace()
    agent.propagate_skill_change = MagicMock()
    agent.skill_loader = MagicMock()
    agent.skill_registry = MagicMock()
    agent.skill_manager = SimpleNamespace(install_skill=AsyncMock(return_value="ok"))
    agent._skill_activation = MagicMock()
    return agent


# ---------------------------------------------------------------------------
# SkillsHandler._install_skill
# ---------------------------------------------------------------------------


class TestSkillsHandlerInstall:
    async def test_install_calls_propagate_with_install(self):
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        agent = _make_agent_for_skills_handler()
        handler = SkillsHandler(agent)

        result = await handler._install_skill(
            {"source": "github:foo/bar"}
        )
        assert result == "ok"
        agent.skill_manager.install_skill.assert_awaited_once()
        agent.propagate_skill_change.assert_called_once_with(SkillEvent.INSTALL)


# ---------------------------------------------------------------------------
# SkillsHandler._load_skill
# ---------------------------------------------------------------------------


class TestSkillsHandlerLoad:
    def test_load_success_triggers_propagate_load_rescan_false(
        self, tmp_path: Path, monkeypatch
    ):
        from openakita.config import settings as real_settings
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        _write_skill(tmp_path / "skills" / "newbie", "newbie")

        agent = _make_agent_for_skills_handler()
        agent.skill_registry.get.return_value = None
        loaded_skill = SimpleNamespace(
            metadata=SimpleNamespace(name="newbie", description="d", system=False)
        )
        agent.skill_loader.load_skill.return_value = loaded_skill

        handler = SkillsHandler(agent)
        out = handler._load_skill({"skill_name": "newbie"})
        assert "技能加载成功" in out

        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.LOAD, rescan=False
        )

    def test_load_nonexistent_dir_no_propagate(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)

        agent = _make_agent_for_skills_handler()
        handler = SkillsHandler(agent)
        out = handler._load_skill({"skill_name": "missing"})
        assert "技能目录不存在" in out
        agent.propagate_skill_change.assert_not_called()

    def test_load_already_exists_no_propagate(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        _write_skill(tmp_path / "skills" / "dup", "dup")

        agent = _make_agent_for_skills_handler()
        agent.skill_registry.get.return_value = SimpleNamespace(name="dup")

        handler = SkillsHandler(agent)
        out = handler._load_skill({"skill_name": "dup"})
        assert "已存在" in out
        agent.propagate_skill_change.assert_not_called()

    def test_load_loader_returns_none_no_propagate(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        _write_skill(tmp_path / "skills" / "badfmt", "badfmt")

        agent = _make_agent_for_skills_handler()
        agent.skill_registry.get.return_value = None
        agent.skill_loader.load_skill.return_value = None

        handler = SkillsHandler(agent)
        out = handler._load_skill({"skill_name": "badfmt"})
        assert "失败" in out
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# SkillsHandler._run_skill_script diagnostics
# ---------------------------------------------------------------------------


class TestSkillsHandlerRunScriptDiagnostics:
    def test_missing_module_hint_points_to_skill_dir(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        skill_dir = tmp_path / "skills" / "news-searcher"
        skill_dir.mkdir(parents=True)
        (tmp_path / "news_searcher.py").write_text("# misplaced module\n", encoding="utf-8")

        agent = _make_agent_for_skills_handler()
        agent.skill_registry.get.return_value = SimpleNamespace(
            skill_id="news-searcher",
            skill_dir=skill_dir,
            python_dependencies=[],
            python_env="",
        )
        agent.skill_loader.run_script.return_value = (
            False,
            "STDERR:\nModuleNotFoundError: No module named 'news_searcher'",
        )

        handler = SkillsHandler(agent)
        out = handler._run_skill_script(
            {"skill_name": "news-searcher", "script_name": "scripts/main.py"}
        )

        assert "Python 模块导入失败" in out
        assert "工作区根目录" in out
        assert str(skill_dir / "news_searcher.py") in out


# ---------------------------------------------------------------------------
# SkillsHandler._reload_skill
# ---------------------------------------------------------------------------


class TestSkillsHandlerReload:
    def test_reload_calls_propagate_reload_rescan_false(self):
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        agent = _make_agent_for_skills_handler()
        agent.skill_loader.get_skill.return_value = SimpleNamespace(name="x")
        reloaded = SimpleNamespace(
            metadata=SimpleNamespace(name="x", description="d", system=False)
        )
        agent.skill_loader.reload_skill.return_value = reloaded

        handler = SkillsHandler(agent)
        out = handler._reload_skill({"skill_name": "x"})
        assert "重新加载成功" in out
        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.RELOAD, rescan=False
        )

    def test_reload_skill_not_loaded_no_propagate(self):
        from openakita.tools.handlers.skills import SkillsHandler

        agent = _make_agent_for_skills_handler()
        agent.skill_loader.get_skill.return_value = None

        handler = SkillsHandler(agent)
        out = handler._reload_skill({"skill_name": "ghost"})
        assert "未加载" in out
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# SkillsHandler._manage_skill_enabled
# ---------------------------------------------------------------------------


class TestManageSkillEnabled:
    def test_enable_writes_allowlist_and_propagates_enable(
        self, tmp_path: Path, monkeypatch
    ):
        from openakita.config import settings as real_settings
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        (tmp_path / "data").mkdir(exist_ok=True)

        agent = _make_agent_for_skills_handler()
        # registry.get 返回一个非 system skill
        skill = SimpleNamespace(
            skill_id="foo", name="foo", system=False, disabled=False
        )
        agent.skill_registry.get.return_value = skill
        agent.skill_registry.list_all.return_value = [skill]
        # loader._loaded_skills 也包含 foo
        agent.skill_loader._loaded_skills = {
            "foo": SimpleNamespace(metadata=SimpleNamespace(system=False))
        }

        handler = SkillsHandler(agent)
        out = handler._manage_skill_enabled(
            {"changes": [{"skill_name": "foo", "enabled": True}], "reason": "test"}
        )
        assert "外部技能启用列表已更新" in out
        assert "foo" in out and "启用" in out

        # 最新写入的 allowlist 文件应包含 foo
        import json

        cfg = json.loads(
            (tmp_path / "data" / "skills.json").read_text(encoding="utf-8")
        )
        assert "foo" in cfg["external_allowlist"]

        # ENABLE（不是 DISABLE）+ rescan=False
        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.ENABLE, rescan=False
        )

    def test_disable_uses_disable_action(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        (tmp_path / "data").mkdir(exist_ok=True)
        # 预置一个 allowlist 让 foo 在其中（disable 时才能从中移除）
        (tmp_path / "data" / "skills.json").write_text(
            '{"version":1,"external_allowlist":["foo"]}', encoding="utf-8"
        )

        agent = _make_agent_for_skills_handler()
        skill = SimpleNamespace(
            skill_id="foo", name="foo", system=False, disabled=False
        )
        agent.skill_registry.get.return_value = skill
        agent.skill_registry.list_all.return_value = [skill]
        agent.skill_loader._loaded_skills = {
            "foo": SimpleNamespace(metadata=SimpleNamespace(system=False))
        }

        handler = SkillsHandler(agent)
        out = handler._manage_skill_enabled(
            {"changes": [{"skill_name": "foo", "enabled": False}]}
        )
        assert "外部技能启用列表已更新" in out
        assert "foo" in out and "禁用" in out
        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.DISABLE, rescan=False
        )

    def test_empty_changes_no_propagate(self):
        from openakita.tools.handlers.skills import SkillsHandler

        agent = _make_agent_for_skills_handler()
        handler = SkillsHandler(agent)
        out = handler._manage_skill_enabled({"changes": []})
        assert "未指定" in out
        agent.propagate_skill_change.assert_not_called()

    def test_system_skill_skipped_no_propagate(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)

        agent = _make_agent_for_skills_handler()
        sys_skill = SimpleNamespace(
            skill_id="sys", name="sys", system=True, disabled=False
        )
        agent.skill_registry.get.return_value = sys_skill
        agent.skill_registry.list_all.return_value = [sys_skill]
        agent.skill_loader._loaded_skills = {}

        handler = SkillsHandler(agent)
        out = handler._manage_skill_enabled(
            {"changes": [{"skill_name": "sys", "enabled": False}]}
        )
        assert "未执行" in out
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# SkillsHandler._uninstall_skill
# ---------------------------------------------------------------------------


class TestUninstallHandler:
    def test_uninstall_external_skill_propagates_and_removes_from_allowlist(
        self, tmp_path: Path, monkeypatch
    ):
        from openakita.config import settings as real_settings
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skills import SkillsHandler

        # skills_path 映射到 tmp_path/skills
        skills_root = tmp_path / "skills"
        skills_root.mkdir(parents=True)
        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        monkeypatch.setattr(
            type(real_settings), "skills_path",
            property(lambda self: skills_root), raising=False,
        )
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "skills.json").write_text(
            '{"version":1,"external_allowlist":["foo"]}', encoding="utf-8"
        )

        skill_dir = skills_root / "foo"
        _write_skill(skill_dir, "foo")

        agent = _make_agent_for_skills_handler()
        skill = SimpleNamespace(
            skill_id="foo",
            name="foo",
            system=False,
            skill_dir=skill_dir,
        )
        # 第一次 get（查找）返回 skill；第二次 get（卸载后确认）返回 skill 以便 unregister
        agent.skill_registry.get.side_effect = [skill, skill]

        handler = SkillsHandler(agent)
        out = handler._uninstall_skill({"skill_name": "foo"})
        assert "已卸载" in out
        assert not skill_dir.exists(), "skill 目录应已删除"

        # allowlist 已移除 foo
        import json

        cfg = json.loads(
            (tmp_path / "data" / "skills.json").read_text(encoding="utf-8")
        )
        assert "foo" not in cfg["external_allowlist"]

        # 最终统一刷新调用（rescan=False，因为 registry.unregister 已单独处理）
        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.UNINSTALL, rescan=False
        )

    def test_uninstall_system_skill_blocked_no_propagate(self, tmp_path, monkeypatch):
        from openakita.config import settings as real_settings
        from openakita.tools.handlers.skills import SkillsHandler

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)

        agent = _make_agent_for_skills_handler()
        agent.skill_registry.get.return_value = SimpleNamespace(
            skill_id="sys", name="sys", system=True
        )
        handler = SkillsHandler(agent)

        out = handler._uninstall_skill({"skill_name": "sys"})
        assert "不可卸载" in out
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# SkillStoreHandler._install
# ---------------------------------------------------------------------------


class TestSkillStoreInstall:
    async def test_install_triggers_store_install_event(self, monkeypatch):
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.skill_store import SkillStoreHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock()

        handler = SkillStoreHandler(agent)

        # 打桩 client：get_detail / install_skill
        fake_client = SimpleNamespace()
        fake_client.get_detail = AsyncMock(
            return_value={
                "skill": {
                    "installUrl": "github:foo/bar",
                    "name": "foo",
                    "trustLevel": "community",
                }
            }
        )
        fake_client.install_skill = AsyncMock(return_value=Path("/tmp/foo"))
        handler._get_client = lambda: fake_client

        result = await handler._install({"skill_id": "foo"})
        assert "安装成功" in result
        agent.propagate_skill_change.assert_called_once_with(
            SkillEvent.STORE_INSTALL
        )

    async def test_install_missing_id_no_propagate(self):
        from openakita.tools.handlers.skill_store import SkillStoreHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock()
        handler = SkillStoreHandler(agent)

        out = await handler._install({})
        assert "需要指定" in out
        agent.propagate_skill_change.assert_not_called()

    async def test_install_client_failure_no_propagate(self):
        from openakita.tools.handlers.skill_store import SkillStoreHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock()

        handler = SkillStoreHandler(agent)
        fake_client = SimpleNamespace()
        fake_client.get_detail = AsyncMock(side_effect=RuntimeError("network"))
        handler._get_client = lambda: fake_client

        out = await handler._install({"skill_id": "foo"})
        assert "无法连接" in out
        agent.propagate_skill_change.assert_not_called()


# ---------------------------------------------------------------------------
# AgentPackageHandler / AgentHubHandler ._try_reload_skills
# ---------------------------------------------------------------------------


class TestAgentPackageReloadSkills:
    def test_agent_package_try_reload_triggers_propagate_install(self):
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.agent_package import AgentPackageHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock()

        handler = AgentPackageHandler.__new__(AgentPackageHandler)
        handler.agent = agent
        handler._try_reload_skills()

        agent.propagate_skill_change.assert_called_once_with(SkillEvent.INSTALL)

    def test_agent_package_without_propagate_is_noop(self):
        from openakita.tools.handlers.agent_package import AgentPackageHandler

        agent = SimpleNamespace()  # 没有 propagate_skill_change 属性
        handler = AgentPackageHandler.__new__(AgentPackageHandler)
        handler.agent = agent
        handler._try_reload_skills()  # 不应抛

    def test_agent_package_propagate_failure_is_swallowed(self):
        from openakita.tools.handlers.agent_package import AgentPackageHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock(side_effect=RuntimeError("boom"))
        handler = AgentPackageHandler.__new__(AgentPackageHandler)
        handler.agent = agent
        handler._try_reload_skills()  # 不应抛


class TestAgentHubReloadSkills:
    def test_agent_hub_try_reload_triggers_propagate_install(self):
        from openakita.skills.events import SkillEvent
        from openakita.tools.handlers.agent_hub import AgentHubHandler

        agent = SimpleNamespace()
        agent.propagate_skill_change = MagicMock()

        handler = AgentHubHandler.__new__(AgentHubHandler)
        handler.agent = agent
        handler._try_reload_skills()

        agent.propagate_skill_change.assert_called_once_with(SkillEvent.INSTALL)


# ---------------------------------------------------------------------------
# Watcher 回调：Agent._on_skills_dir_changed → propagate_skill_change(HOT_RELOAD)
# ---------------------------------------------------------------------------


class TestWatcherCallback:
    def test_on_skills_dir_changed_calls_propagate_hot_reload(self):
        import types

        from openakita.core.agent import Agent
        from openakita.skills.events import SkillEvent

        fake = SimpleNamespace()
        fake.propagate_skill_change = MagicMock()
        fake._on_skills_dir_changed = types.MethodType(
            Agent._on_skills_dir_changed, fake
        )

        fake._on_skills_dir_changed()
        fake.propagate_skill_change.assert_called_once_with(SkillEvent.HOT_RELOAD)

    def test_on_skills_dir_changed_swallows_exceptions(self):
        import types

        from openakita.core.agent import Agent

        fake = SimpleNamespace()
        fake.propagate_skill_change = MagicMock(side_effect=RuntimeError("fail"))
        fake._on_skills_dir_changed = types.MethodType(
            Agent._on_skills_dir_changed, fake
        )

        fake._on_skills_dir_changed()  # 不应抛
